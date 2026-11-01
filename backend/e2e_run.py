"""End-to-end run of the HireOS pipeline on this machine. Nothing is sent anywhere real.

  cd backend && venv/bin/python e2e_run.py --out <empty dir> [--skip-linkedin]

Real: the API (uvicorn, in this process), the database (a copy of ./swarm.db, never the file
itself), model calls on the user's own model, scoring + resume writing, the Discord bot's message
and button handlers, the apply worker driving Chromium, and read-only LinkedIn lookups.
Fake: Discord's REST API (a recorder that has the server's channels), the employer (a
Greenhouse-style form on 127.0.0.1 that records what is submitted), and the user (this script,
answering the way a person would: replies, plain messages, button taps).

Only the jobs this script creates are ever claimed for applying (by id), and each one's URL must
be the local form before the worker is started. The report lands in <out>/report.md + report.json.
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--user", type=int, default=1)
ap.add_argument("--skip-linkedin", action="store_true")
args = ap.parse_args()
OUT = Path(args.out).resolve()
OUT.mkdir(parents=True, exist_ok=True)
API_PORT, ATS_PORT = 8791, 8792
ATS = f"http://127.0.0.1:{ATS_PORT}"

# A copy, via SQLite's backup API (consistent even if the app has the file open). "test" in the
# name keeps the real bots and the scheduler off (main's startup guards).
DB = OUT / "e2e_test.db"
src = sqlite3.connect("file:swarm.db?mode=ro", uri=True)
dst = sqlite3.connect(DB)
src.backup(dst)
src.close()
dst.close()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{DB}", "OUTPUT_DIR": str(OUT / "output"), "CHROMA_PATH": str(OUT / "chroma"),
    "LOG_DIR": str(OUT / "logs"), "DISCORD_BOT_TOKEN": "e2e", "TELEGRAM_BOT_TOKEN": "", "SCHEDULER": "",
    "GMAIL_ADDRESS": "", "GMAIL_APP_PASSWORD": "", "LOCAL_LLM_URL": "", "MAIL_LLM": "",
    "WORKER_RUNS": str(OUT / "runs"), "WORKER_PROFILE": str(OUT / "browser"), "WORKER_DISPLAY": ":250",
    "WORKER_SCREEN_URL": "", "HIREOS_CREDS": str(OUT / "no-creds.json"), "HIREOS_URL": f"http://127.0.0.1:{API_PORT}",
    "APPLY_POLL_S": "1", "APPLY_ASK_MIN": "3",
})
# The model Fly runs with (fly.toml), unless the user saved one: the copy then routes like production.
fly = Path("../fly.toml").read_text() if Path("../fly.toml").exists() else ""
mo = re.search(r'DEFAULT_LLM\s*=\s*"([^"]+)"', fly)
if mo:
    os.environ["DEFAULT_LLM"] = mo.group(1)
sys.path.insert(0, os.getcwd())

from loguru import logger  # noqa: E402
ERRORS = []
logger.add(lambda m: ERRORS.append(m.record["message"][:300]), level="ERROR")
logger.add(str(OUT / "app.log"), level="INFO")

import uvicorn  # noqa: E402
import apply_worker  # noqa: E402
import discord_bot as dc  # noqa: E402
import discovery  # noqa: E402
import main  # noqa: E402
import scheduler  # noqa: E402
import telegram_bot as tg  # noqa: E402
from database import Job, ResumeVersion, SessionLocal  # noqa: E402

STEPS = []          # [name, ok, detail, seconds]
YOU = {"french": "None", "office": "Montreal", "authorized": "Yes", "team": "Research", "sponsor": "No"}   # your answers
T0 = time.monotonic()


def step(name, ok, detail=""):
    STEPS.append([name, bool(ok), str(detail)[:600], round(time.monotonic() - T0)])
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)
    return ok


# ── fake Discord REST: the server's channels, every message kept ──────────────

GUILD, ME = "4242", "777"
CHANNELS = {"resume-review": "1001", "approvals": "1002", "alerts": "1003", "new-jobs": "1004", "general": "1005"}
NAME = {v: k for k, v in CHANNELS.items()}


class FakeDiscord:
    def __init__(self):
        self.n, self.msgs, self.lock = 9000, [], threading.Lock()

    def __call__(self, method, path, **kw):
        if method == "GET" and re.fullmatch(r"/guilds/\d+/channels", path):
            return [{"id": i, "name": n, "type": 0} for n, i in CHANNELS.items()]
        if path.endswith("/typing"):
            return {}
        mo = re.fullmatch(r"/channels/(\d+)/messages(?:/(\d+))?", path)
        if not mo:
            return {}
        body = kw.get("json") or json.loads(kw["data"]["payload_json"])
        with self.lock:
            if method == "POST":
                self.n += 1
                ids = [b["custom_id"] for row in body.get("components") or [] for b in row["components"] if b.get("custom_id")]
                self.msgs.append({"id": str(self.n), "ch": NAME.get(mo.group(1), mo.group(1)), "text": body.get("content", ""),
                                  "buttons": ids, "file": (body.get("attachments") or [{}])[0].get("filename"),
                                  "silent": bool(body.get("flags")), "by": "bot", "t": round(time.monotonic() - T0), "edits": 0})
                return {"id": str(self.n)}
            m = next((x for x in self.msgs if x["id"] == mo.group(2)), None)
            if m:
                m["text"], m["edits"] = body.get("content", m["text"]), m["edits"] + 1
                if "components" in body:
                    m["buttons"] = []
            return {"id": mo.group(2)}

    def user(self, ch, text, reply_to=None):
        with self.lock:
            self.n += 1
            self.msgs.append({"id": str(self.n), "ch": ch, "text": text, "buttons": [], "file": None, "silent": False,
                              "by": "you", "reply_to": reply_to, "t": round(time.monotonic() - T0), "edits": 0})
            return str(self.n)

    def wait(self, ch, pattern, after=0, timeout=300):
        """The first bot message in `ch` matching `pattern`, posted after message id `after`."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                hit = next((m for m in self.msgs if m["by"] == "bot" and m["ch"] == ch and int(m["id"]) > int(after)
                            and re.search(pattern, m["text"], re.I | re.S)), None)
            if hit:
                return hit
            time.sleep(0.5)
        return None

    def last_id(self):
        return str(self.n)


D = FakeDiscord()
dc._request, dc.TOKEN = D, "e2e"


def say(ch, text, reply_to=None):
    """You type in a channel (optionally replying to a message), as the gateway would deliver it."""
    mid = D.user(ch, text, reply_to and reply_to["id"])
    d = {"id": mid, "channel_id": CHANNELS[ch], "guild_id": GUILD, "author": {"id": ME}, "content": text}
    if reply_to:
        d["message_reference"] = {"message_id": reply_to["id"]}
        d["referenced_message"] = {"content": reply_to["text"]}
    dc._handle("message", d)
    return mid


def tap(msg, custom_id):
    D.user(msg["ch"], f"[taps {custom_id}]")
    dc._handle("button", {"channel_id": CHANNELS[msg["ch"]], "guild_id": GUILD, "member": {"user": {"id": ME}},
                          "data": {"custom_id": custom_id}, "message": {"id": msg["id"], "content": msg["text"]}})


# ── fake employer: a Greenhouse-style form that records submissions ───────────

SUBMITTED = {}
FORMS = {}          # key -> [(field name, label, options)]


def form_html(key):
    rows = "".join(
        f'<div><label for="{n}">{lab} *</label><select id="{n}" name="{n}" required><option value="">Select...</option>'
        + "".join(f"<option>{o}</option>" for o in opts) + "</select></div>" for n, lab, opts in FORMS[key])
    return f"""<!doctype html><html><head><title>E2E Robotics - Apply</title></head><body>
<h1>Senior Applied AI Engineer</h1><p>E2E Robotics · Toronto</p>
<form method="post" action="/e2e/jobs/{key}/submit" enctype="multipart/form-data">
<div><label for="first_name">First Name *</label><input id="first_name" name="first_name" required></div>
<div><label for="last_name">Last Name *</label><input id="last_name" name="last_name" required></div>
<div><label for="email">Email *</label><input id="email" name="email" type="email" required></div>
<div><label for="phone">Phone *</label><input id="phone" name="phone" type="tel" required></div>
<div><label for="resume">Resume/CV *</label><input id="resume" name="resume" type="file" required></div>
<div><label for="q_auth">Are you legally authorized to work in Canada? *</label><select id="q_auth" name="q_auth" required>
<option value="">Select...</option><option>Yes</option><option>No</option></select></div>
{rows}
<button type="submit">Submit application</button></form></body></html>"""


class ATSHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, html="", loc=None):
        self.send_response(code)
        if loc:
            self.send_header("Location", loc)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self):
        mo = re.fullmatch(r"/e2e/jobs/(\w+)(/confirmation)?", self.path)
        if not mo or mo.group(1) not in FORMS:
            return self._send(404, "not found")
        if mo.group(2):
            return self._send(200, "<html><body><h1>Thank you for applying!</h1>Your application has been submitted.</body></html>")
        self._send(200, form_html(mo.group(1)))

    def do_POST(self):
        mo = re.fullmatch(r"/e2e/jobs/(\w+)/submit", self.path)
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = BytesParser(policy=email_policy).parsebytes(
            f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body)
        fields = {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            fields[name] = part.get_filename() or part.get_content().strip()
        SUBMITTED[mo.group(1)] = fields
        self._send(303, loc=f"/e2e/jobs/{mo.group(1)}/confirmation")


# ── servers ───────────────────────────────────────────────────────────────────

threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", ATS_PORT), ATSHandler).serve_forever, daemon=True).start()
LOOP = asyncio.new_event_loop()
server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=API_PORT, log_level="warning", loop="asyncio"))
threading.Thread(target=lambda: LOOP.run_until_complete(server.serve()), daemon=True).start()
while not server.started:
    time.sleep(0.2)
dc.LOOP = LOOP


def on_loop(fn):
    """Run fn() on the API's loop (where the scheduler lives) and return its result."""
    async def go():
        return fn()
    return asyncio.run_coroutine_threadsafe(go(), LOOP).result(30)


db = SessionLocal()
user = db.get(main.User, args.user)
os.environ["HIREOS_TOKEN"] = main._create_token(user.email)
main._put_setting(db, user.id, "worker_platforms", "*")          # the worker takes the e2e jobs (copy only)
db.commit()


def fresh(jid):
    db.expire_all()
    return db.get(Job, jid)


def apply_once(jid):
    """What the watch loop does when HireOS hands out this job; claimed by id, local form only."""
    job = fresh(jid)
    assert job.url.startswith(ATS), f"refusing to apply: #{jid} is {job.url}"
    api = apply_worker.HireOS()
    claim = api.post("/apply/next", {"job_id": jid, "worker": True})
    if not claim:
        step(f"#{jid} claimed by the worker", False, f"apply/next gave nothing (status {job.status})")
        return None
    t = threading.Thread(target=apply_worker.apply_claimed, args=(api, claim, True), daemon=True)
    t.start()
    return t


def run():
    # 1 ── link Discord in #resume-review; channels are found by name
    say("resume-review", main.discord_link(db, user)["say"])
    step("Discord linked in #resume-review", D.wait("resume-review", r"^Linked to HireOS", timeout=5))
    say("resume-review", "status")
    st = D.wait("resume-review", r"Messages go to:", timeout=10)
    step("channels found by name, no setup", st and all(f"{k} <#{CHANNELS[k]}>" in st["text"] for k in ("approvals", "alerts", "new-jobs")),
         st and st["text"].splitlines()[-1])

    # 2 ── a job on the employer's own Greenhouse-style form, scored and given a resume for real
    ref = db.query(Job).filter(Job.user_id == user.id, Job.match_score >= 85, Job.job_description.isnot(None)) \
        .order_by(Job.match_score.desc(), Job.id.desc()).first()
    FORMS["a"] = [("q_fr", "What is your current level of proficiency in French?", ["None", "Basic", "Intermediate", "Fluent"]),
                  ("q_office", "Which E2E Robotics office would you work from?", ["Toronto", "Montreal"])]
    FORMS["b"] = FORMS["a"] + [("q_team", "Which E2E Robotics team do you want to join?", ["Platform", "Research"])]
    jobs = {}
    for key, title in (("a", ref.title), ("b", ref.title + " II")):
        j = Job(user_id=user.id, company=f"E2E Robotics {key.upper()}", title=title, location="Toronto, Ontario, Canada",
                url=f"{ATS}/e2e/jobs/{key}", platform="greenhouse", status="found", job_description=ref.job_description)
        db.add(j)
        db.commit()
        jobs[key] = j.id
    A, B = jobs["a"], jobs["b"]
    before = D.last_id()
    s = on_loop(lambda: scheduler.Scheduler(main))
    scheduler.CURRENT = s
    on_loop(lambda: s.start_scoring(user.id, [A], lambda d: main._chat_jobs(d, user.id, {"since_hours": 1, "limit": 5})))
    prog = D.wait("new-jobs", r"Scoring", after=before, timeout=60)
    step("scoring progress posted silently in #new-jobs", prog and prog["silent"], prog and prog["text"][:120])
    done = D.wait("new-jobs", r"from the last \d|^No jobs", after=before, timeout=900)
    a = fresh(A)
    step("scored and resume written (real model)", a.match_score is not None and
         db.query(ResumeVersion).filter_by(job_id=A).first(), f"score {a.match_score}; results: {(done or {}).get('text', '')[:150]}")
    prog = next((m for m in D.msgs if prog and m["id"] == prog["id"]), None)
    step("progress message updated in place, closed with a summary", prog and prog["edits"] > 0 and "done" in prog["text"].lower(),
         prog and f"{prog['edits']} edits; {prog['text'].splitlines()[0][:100]}")
    rv = db.query(ResumeVersion).filter_by(job_id=A).order_by(ResumeVersion.version.desc()).first()
    if not rv:
        return step("cannot continue without a resume", False)
    # B reuses A's resume (same posting text): the second apply run is what's tested there, not writing
    db.add(ResumeVersion(job_id=B, version=1, content_md=rv.content_md, pdf_path=rv.pdf_path, critic_notes=rv.critic_notes))
    jb = fresh(B)
    jb.match_score, jb.status = a.match_score, "pending"
    db.commit()

    # 3 ── the card, Approve, and the apply-stage messages in #approvals
    before = D.last_id()
    dc.push_new()
    card = D.wait("resume-review", rf"#{A}\s", after=before, timeout=30)
    step("resume card in #resume-review with Approve/Skip/Edit", card and f"a:{A}" in card["buttons"] and card["file"],
         card and card["text"].splitlines()[0])
    if not card:
        return
    if fresh(A).status != "pending":
        fresh(A).status = "pending"
        db.commit()
    tap(card, f"a:{A}")
    step("card closed on Approve, no buttons left", "Approved" in card["text"] and not card["buttons"], card["text"].splitlines()[-1])
    appr = D.wait("approvals", rf"Approved #{A}", after=before, timeout=10)
    step("'Approved' posted in #approvals", appr, appr and appr["text"])

    before = D.last_id()
    worker = apply_once(A)
    m = D.wait("approvals", rf"Applying to #{A}", after=before, timeout=60)
    step("'Applying' in #approvals", m, m and m["text"][:100])
    # You answer whatever the form asks: the first by replying with the option's number, the rest as plain messages.
    asked, last = [], before
    while True:
        q = D.wait("approvals", rf"#{A}.*(The form asks: |Ready to submit)", after=last, timeout=300)
        if not q or "Ready to submit" in q["text"]:
            break
        label = re.search(r"The form asks: (.*)", q["text"]).group(1)
        opts = re.search(r"Options: (.*)", q["text"])
        opts = re.findall(r"\d+ ([^·]+?)(?= · |$)", opts.group(1)) if opts else []
        want = next((v for k, v in YOU.items() if k in label.lower()), opts[0] if opts else "n/a")
        asked.append(label)
        if len(asked) == 1:
            say("approvals", str(opts.index(want) + 1) if want in opts else want, reply_to=q)
        else:
            say("approvals", want)
        r = D.wait("approvals", r"continuing|isn't one of", after=q["id"], timeout=20)
        step(f"{'reply-tag answer by number' if len(asked) == 1 else 'plain answer, no reply tag'} understood: "
             f"{label[:50]} = {want}", r and "continuing" in r["text"], r and r["text"])
        last = r["id"] if r else q["id"]
    step("questions asked in #approvals instead of a hand-off (incl. French)", any("French" in a for a in asked), asked)
    sub = q if q and "Ready to submit" in q["text"] else D.wait("approvals", rf"#{A}.*Ready to submit", after=before, timeout=60)
    step("Submit card in #approvals with the review screenshot", sub and f"y:{A}" in sub["buttons"] and sub["file"],
         sub and sub["text"].replace("\n", " | ")[:200])
    if not sub:
        return
    tap(sub, f"y:{A}")                                        # the local form only (checked in apply_once)
    done = D.wait("approvals", rf"Submitted: #{A}", after=sub["id"], timeout=120)
    worker.join(60)
    step("'Submitted' in #approvals, job applied", done and fresh(A).status == "applied", done and done["text"].replace("\n", " | "))
    got = SUBMITTED.get("a") or {}
    step("employer received your answers + resume", got.get("q_fr") and got.get("q_office") and got.get("resume"),
         {k: v for k, v in got.items() if k not in ("email", "phone")})
    p = main._application_profile(db, user.id)
    saved = {x["q"]: x["a"] for x in p.get("custom_qa") or []}
    step("answers saved to the profile, clean question text", any("E2E Robotics office" in q for q in saved),
         {q[:60]: a for q, a in saved.items() if "E2E" in q or "French" in q})

    # 4 ── no answer in time: hand-off in #alerts, reply to it, the agent starts over and finishes
    card_b = next((m for m in D.msgs if m["ch"] == "resume-review" and m["text"].startswith(f"#{B} ")), None)
    if not step("job 2 card was sent; you tap Approve", card_b and f"a:{B}" in card_b["buttons"]):
        return
    tap(card_b, f"a:{B}")
    apply_worker.ASK_MIN = 0.15                               # ~9 s, so the question times out
    before = D.last_id()
    worker = apply_once(B)
    if not worker:
        return
    qt = D.wait("approvals", rf"#{B}.*The form asks: .*team", after=before, timeout=300)
    step("job 2: only the new question is asked (French/office reused)", qt and not D.wait(
        "approvals", rf"#{B}.*The form asks: .*(French|office)", after=before, timeout=1), qt and qt["text"].replace("\n", " | ")[:200])
    ho = D.wait("alerts", rf"Needs you: #{B}", after=before, timeout=120)
    worker.join(30)
    apply_worker.ASK_MIN = 3
    step("unanswered in time → hand-off in #alerts asking for the answer", ho and "Reply to this message" in ho["text"],
         ho and ho["text"].replace("\n", " | ")[:300])
    if not ho:
        return
    say("alerts", "Research", reply_to=ho)
    ok = D.wait("alerts", r"Saved: .*goes back to the agent", after=ho["id"], timeout=20)
    step("reply to the hand-off saved + job re-approved", ok and fresh(B).status == "approved", ok and ok["text"])
    before = D.last_id()
    worker = apply_once(B)
    if not worker:
        return
    sub = D.wait("approvals", rf"#{B}.*Ready to submit", after=before, timeout=300)
    again = D.wait("approvals", rf"#{B}.*The form asks", after=before, timeout=1)
    step("second run fills every answer itself, no question", sub and not again, sub and sub["text"][:120])
    if sub:
        tap(sub, f"y:{B}")
        done = D.wait("approvals", rf"Submitted: #{B}", after=sub["id"], timeout=120)
        worker.join(60)
        step("job 2 submitted with the hand-off answer", done and (SUBMITTED.get("b") or {}).get("q_team") == "Research",
             SUBMITTED.get("b", {}).get("q_team"))

    # 5 ── chat: memory across turns, and no claims an action didn't make
    before = D.last_id()
    c1 = D.wait("resume-review", r".", after=say("resume-review", "how many jobs came in today?"), timeout=120)
    c2 = D.wait("resume-review", r".", after=say("resume-review", "and which of those scored above 80?"), timeout=120)
    step("follow-up keeps context: the 80+ list shows #A or says it was left out (applied)",
         c2 and re.search(r"80", c2["text"]) and (f"#{A} " in c2["text"] or "more already applied" in c2["text"]),
         f"1: {(c1 or {}).get('text', '')[:100]} || 2: {(c2 or {}).get('text', '')[:160]}")
    c3 = D.wait("resume-review", r".", after=say("resume-review", f"I see that job #{A} (E2E Robotics A) is waiting for your "
                "input on the question: 'What is your current level of proficiency in French?' — None"), timeout=120)
    step("pasted text about a job with no open question: nothing claimed", c3 and not re.search(
        r"has been recorded|been saved|can now proceed|answer .* recorded", c3["text"], re.I), c3 and c3["text"][:200])

    # 6 ── a job the agent couldn't route: paste its apply link
    li = Job(user_id=user.id, company="E2E Robotics C", title=ref.title, location="Toronto, Ontario, Canada",
             url="https://www.linkedin.com/jobs/view/4999999999", platform="linkedin", status="pending",
             match_score=90, job_description=ref.job_description)
    db.add(li)
    db.commit()
    db.add(ResumeVersion(job_id=li.id, version=1, content_md=rv.content_md, pdf_path=rv.pdf_path, critic_notes=rv.critic_notes))
    db.commit()
    before = D.last_id()
    dc.push_new()
    card = D.wait("resume-review", rf"#{li.id}\s", after=before, timeout=30)
    step("unroutable card says why and how to fix it", card and "Reply to this card with it" in card["text"],
         card and card["text"].splitlines()[2][:200])
    if card:
        say("resume-review", "https://job-boards.greenhouse.io/e2erobotics/jobs/4000001", reply_to=card)
        r = D.wait("resume-review", rf"#{li.id} is on Greenhouse", after=card["id"], timeout=20)
        boards = json.loads(main._setting(db, user.id, tg.BOARDS_KEY) or "{}")
        step("pasted link moves the job to Greenhouse + board remembered", r and fresh(li.id).platform == "greenhouse"
             and discovery.board_of(boards.get("e2e robotics c", "")) == ("greenhouse", "e2erobotics"), r and r["text"])


def linkedin_check(n=15):
    """Read-only: where find_ats sends the latest real LinkedIn jobs from the copy."""
    import httpx
    rows = db.query(Job).filter(Job.user_id == user.id, Job.url.like("%linkedin.com/jobs/view%")).order_by(Job.id.desc()).limit(n).all()
    t = discovery.targets(main, db, user.id) or {}

    async def go():
        out = []
        async with httpx.AsyncClient(headers=discovery.UA, follow_redirects=True, timeout=12) as c:
            for j in rows:
                try:
                    r = await discovery.find_ats(c, j, t)
                except Exception as e:
                    r = {"route": f"error {type(e).__name__}"}
                out.append((j.id, j.company, r.get("route"), r.get("platform") or "", (r.get("url") or r.get("careers_url") or "")[:90]))
        return out
    return asyncio.run(go())


try:
    run()
except Exception as e:
    import traceback
    step("harness crashed", False, traceback.format_exc()[-500:])
li_rows = [] if args.skip_linkedin else linkedin_check()

# ── report ────────────────────────────────────────────────────────────────────
passed = sum(1 for s in STEPS if s[1])
lines = [f"# HireOS end-to-end run — {datetime.now():%Y-%m-%d %H:%M}", "",
         f"**{passed}/{len(STEPS)} checks passed** in {round((time.monotonic() - T0) / 60, 1)} min. "
         f"Model: `{main.resolve_llm(db, user.id)}`. Database: a copy of swarm.db (`{DB.name}`).", "",
         "| # | Check | Result | Detail | at s |", "|---|---|---|---|---|"]
lines += [f"| {i} | {s[0]} | {'PASS' if s[1] else '**FAIL**'} | {s[2].replace('|', '/')[:300]} | {s[3]} |" for i, s in enumerate(STEPS, 1)]
lines += ["", "## Discord transcript (fake Discord, every message)", "", "| at s | channel | who | message |", "|---|---|---|---|"]
lines += [f"| {m['t']} | #{m['ch']} | {m['by']}{' (silent)' if m['silent'] else ''} | "
          f"{m['text'].replace(chr(10), ' ⏎ ').replace('|', '/')[:260]}{' 📎' + m['file'] if m['file'] else ''}"
          f"{' [' + ', '.join(m['buttons']) + ']' if m['buttons'] else ''}{' ✎' + str(m['edits']) if m['edits'] else ''} |"
          for m in D.msgs]
if li_rows:
    from collections import Counter
    lines += ["", "## LinkedIn jobs: where the agent can apply (live, read-only)", "",
              "Routes: " + ", ".join(f"{k}: {v}" for k, v in Counter(r[2] for r in li_rows).items()), "",
              "| job | company | route | platform | link |", "|---|---|---|---|---|"]
    lines += [f"| #{r[0]} | {r[1][:30]} | {r[2]} | {r[3]} | {r[4]} |" for r in li_rows]
if ERRORS:
    lines += ["", "## Errors logged by the app", ""] + [f"- {e}" for e in ERRORS[:30]]
(OUT / "report.md").write_text("\n".join(lines))
(OUT / "report.json").write_text(json.dumps({"steps": STEPS, "messages": D.msgs, "linkedin": li_rows, "errors": ERRORS,
                                             "submitted": SUBMITTED}, indent=2, default=str))
print(f"\n{passed}/{len(STEPS)} passed — report: {OUT / 'report.md'}")
server.should_exit = True
os._exit(0 if passed == len(STEPS) else 1)

"""Apply worker (Stage 2): fills job applications in a real browser, without OpenClaw.

  python apply_worker.py --dry-run <job_id>   fill everything, stop on Review; job status untouched
  python apply_worker.py --apply <job_id>     review mode: claim the (approved) job, fill, check the
                                              Review page, send it to Telegram with Submit/Cancel,
                                              submit only on Submit, report the result to HireOS
  python apply_worker.py --watch              the service: --apply each approved worker-platform job
                                              as HireOS hands it out (caps, cooldowns, pause all apply)
Workday (workday.py) and Greenhouse / Lever / Ashby (ats_form.py) are filled here; any other site is
handed to the user on Telegram with the link, to apply themselves.
Every step is screenshotted, the run is a Playwright trace (`playwright show-trace
<dir>/trace-*.zip`, the password is never in it), and the outcome goes to Telegram.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright

# HireOS URL + token; the Workday account vault (ats-account.json) sits next to it.
CREDS = Path(os.getenv("HIREOS_CREDS", "~/.config/hireos/hireos.json")).expanduser()
RUNS = Path(os.getenv("WORKER_RUNS", "./output/worker_runs"))
PROFILE = Path(os.getenv("WORKER_PROFILE", "./output/worker_profile"))   # keeps Workday signed in
TIME_BOX_MIN = {"workday": 45}                                            # others: 25
SUPPORTED = {"myworkdayjobs.com": "workday", "greenhouse.io": "greenhouse", "lever.co": "lever", "ashbyhq.com": "ashby"}
# The noVNC screen (X display :99, see worker/): when it's up, the browser runs there so the user can
# look at it, and act on it, from their phone (screen_url, e.g. Tailscale Serve, tailnet only).
SCREEN_DISPLAY = os.getenv("WORKER_DISPLAY", ":99")
BOT_WALL = re.compile(r"access denied|incapsula|attention required|request unsuccessful|are you a robot", re.I)


class HandOff(Exception):
    """The run can't go on without Girijesh (or at all): CAPTCHA, bot wall, stuck, unanswered."""
    def __init__(self, kind: str, reason: str):
        super().__init__(f"{kind}: {reason}")
        self.kind, self.reason = kind, reason


def creds() -> dict:
    """HireOS URL, token and the screen link: environment (server) over the CREDS file (PC)."""
    c = json.loads(CREDS.read_text()) if CREDS.exists() else {}
    return {"url": os.getenv("HIREOS_URL") or c.get("url", ""), "token": os.getenv("HIREOS_TOKEN") or c.get("token", ""),
            "screen_url": os.getenv("WORKER_SCREEN_URL") or c.get("screen_url", "")}


class HireOS:
    def __init__(self):
        c = creds()
        if not (c["url"] and c["token"]):
            raise SystemExit(f"no HireOS URL/token: set HIREOS_URL + HIREOS_TOKEN, or write {CREDS}")
        self.url = c["url"].rstrip("/")
        self.h = {"Authorization": f"Bearer {c['token']}"}

    def get(self, path: str, **kw):
        r = httpx.get(f"{self.url}/api{path}", headers=self.h, timeout=60, **kw)
        r.raise_for_status()
        return r

    def post(self, path: str, body: dict, timeout: float = 600):
        r = httpx.post(f"{self.url}/api{path}", headers=self.h, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json() if r.status_code != 204 else None


def captcha_challenge(page) -> bool:
    """A CAPTCHA a person has to solve is showing. The invisible reCAPTCHA badge these sites always
    carry, and its challenge frame parked off-screen, don't count. Never solved, only reported."""
    return bool(page.evaluate(r"""() => [...document.querySelectorAll('iframe')].some(f => {
      if (!/recaptcha|hcaptcha|challenges\.cloudflare/.test(f.src || '') || /size=invisible/.test(f.src)) return false;
      const r = f.getBoundingClientRect();
      if (r.width < 100 || r.height <= 64 || r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight * 4) return false;
      for (let e = f; e; e = e.parentElement) {
        const s = getComputedStyle(e);
        if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') return false;
      }
      return true;
    })"""))


def screen_up() -> bool:
    """The display answers: ':99' is a local socket, 'host:99' is TCP 6099."""
    import socket
    host, n = SCREEN_DISPLAY.rsplit(":", 1)
    n = n.split(".")[0]
    try:
        if host:
            socket.create_connection((host, 6000 + int(n)), timeout=1).close()
        else:
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(1)
                s.connect(f"/tmp/.X11-unix/X{n}")
        return True
    except (OSError, ValueError):
        return False


def platform_of(job: dict):
    """The form filler for a job, or None when the worker can't fill its site."""
    host = (urlparse(job.get("url") or "").hostname or "")
    by_host = next((p for h, p in SUPPORTED.items() if host.endswith(h)), None)
    if by_host:
        return by_host
    p = (job.get("platform") or "").lower()          # e.g. Greenhouse embedded on the company's own site
    return p if p in SUPPORTED.values() and p != "workday" else None


def page_fingerprint(url: str, labels: list) -> str:
    """Same URL and same set of fields = no progress."""
    return hashlib.sha1((url.split("?")[0] + "|" + "|".join(sorted(labels))).encode()).hexdigest()[:12]


class Run:
    """One job in one browser page: screenshots, guards, answers, and the run record."""

    def __init__(self, api: HireOS, job: dict, page, out: Path, platform: str):
        self.api, self.job, self.page, self.out, self.platform = api, job, page, out, platform
        self.started, self.step, self.seen = time.monotonic(), 0, {}
        self.screen = ""   # the screen's URL when the browser is on it: the user can act there
        self.record = {"job_id": job["id"], "company": job["company"], "title": job["title"],
                       "url": job["url"], "pages": [], "filled": [], "unanswered": [], "errors": []}

    def shot(self, name: str) -> Path:
        self.step += 1
        p = self.out / f"{self.step:02d}-{re.sub(r'[^a-z0-9]+', '-', name.lower())}.png"
        self.page.screenshot(path=str(p), full_page=True)
        self.last_shot = p
        return p

    def check(self, labels: list = ()):
        """Raise HandOff on anything the worker must not push through."""
        limit = TIME_BOX_MIN.get(self.platform, 25)
        if time.monotonic() - self.started > limit * 60:
            raise HandOff("time box", f"over {limit} min")
        if captcha_challenge(self.page):
            raise HandOff("captcha", "CAPTCHA on the page")
        title = self.page.title() or ""
        body = self.page.locator("body").inner_text(timeout=5000)[:2000] if self.page.locator("body").count() else ""
        if BOT_WALL.search(title) or (len(body) < 600 and BOT_WALL.search(body)):
            raise HandOff("bot wall", title or body[:120])
        if labels:
            fp = page_fingerprint(self.page.url, list(labels))
            self.seen[fp] = self.seen.get(fp, 0) + 1
            if self.seen[fp] >= 3:
                raise HandOff("stuck", "same page three times")

    def ask(self, kind: str, reason: str, minutes: int = 15) -> str:
        """Ask the user mid-form and wait: their answer ('done' for a Done tap), 'cancel' or 'timeout'."""
        return ask_and_wait(self.api, self, {"reason": reason, "kind": kind, "url": self.screen}, minutes)

    def answers(self, questions: list) -> dict:
        """HireOS answers from the profile / resume only; None = no grounded answer."""
        if not questions:
            return {}
        r = self.api.post("/agent/answer-questions", {"job_id": self.job["id"], "questions": questions,
                                                       "guidance": getattr(self, "guidance", "")})
        return {a["id"]: a.get("answer") for a in r.get("answers", [])}


def notify(api: HireOS, text: str, photo: Path = None, doc: Path = None, doc_name: str = ""):
    body = {"text": text}
    if photo and photo.exists():
        body["photo_b64"] = base64.b64encode(photo.read_bytes()).decode()
    if doc and doc.exists():
        body["doc_b64"], body["doc_name"] = base64.b64encode(doc.read_bytes()).decode(), doc_name or doc.name
    try:
        api.post("/notify", body, timeout=60)
    except httpx.HTTPError as e:
        print(f"notify failed: {e}", file=sys.stderr)


def b64(path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode() if path and Path(path).exists() else ""


def review_name(run) -> str:
    return re.sub(r"[^a-z0-9]+", "_", f"review {run.job['company']} {run.job['id']}".lower()).strip("_") + ".pdf"


def ask_and_wait(api: HireOS, run, body: dict, minutes: int) -> str:
    """Ask on Telegram / Discord and wait: the answer, 'cancel' (skipped) or 'timeout'."""
    api.post(f"/apply/{run.job['id']}/wait", {**body, "minutes": minutes})
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        time.sleep(10)
        st = api.get(f"/apply/{run.job['id']}/ask").json()
        if st["status"] == "applying":
            return st.get("answer") or ""
        if st["status"] != "waiting_human":
            return "cancel"
    return "timeout"


def wait_for_submit(api: HireOS, run, minutes: int = 30) -> str:
    """Send the checked Review page to Telegram and wait: 'submit', 'cancel' or 'timeout'."""
    r = run.record
    reason = f"All {r.get('review_checked', 0)} values checked against what was entered."
    if r.get("consented"):
        reason += " Agreed for you: " + "; ".join(r["consented"])[:300] + "."
    if r.get("cover_letter"):   # its own message first, so it can be read before Submit
        notify(api, f"Cover letter attached to #{run.job['id']} {run.job['company']}:", doc=run.out / r["cover_letter"])
        reason += f" Cover letter attached: {r['cover_letter']} (sent above)."
    if r.get("skipped"):
        reason += " Left out: " + "; ".join(r["skipped"])[:300] + "."
    a = ask_and_wait(api, run, {"reason": reason, "kind": "submit", "url": run.screen,
                                "photo_b64": b64(getattr(run, "review_top", None)),
                                "doc_b64": b64(getattr(run, "review_pdf", None)),
                                "doc_name": review_name(run)}, minutes)
    return a if a in ("submit", "timeout") else "cancel"


# Jobs whose Submit button may have been clicked in this process. Any error after that point is
# needs_human, never failed: "failed" re-queues the job, and a second run would apply twice.
CLICKED: set = set()


def crash_outcome(job_id: int, error: str) -> dict:
    if job_id in CLICKED:
        return {"outcome": "needs_human", "notes": f"error after Submit was clicked — check whether the "
                                                   f"application went through before retrying: {error}"[:500]}
    return {"outcome": "failed", "notes": error[:500]}


class Unsupported(Exception):
    """A job on a site the worker can't fill: the user applies themselves."""


def run_job(job_id: int, headless: bool, live: bool, claimed: bool = False) -> dict:
    import ats_form
    import workday
    api = HireOS()
    if live and not claimed:   # claim it like any applier: approved, has a resume, under the caps, not in cooldown
        claim = api.post("/apply/next", {"job_id": job_id, "worker": True})
        if not claim:
            raise SystemExit(f"#{job_id} can't be claimed (not approved, already applied, capped or not a worker platform)")
    job = api.get(f"/jobs/{job_id}").json()
    platform = platform_of(job)
    if not platform:
        raise Unsupported(f"#{job_id} is on a site the worker can't fill yet ({urlparse(job.get('url') or '').hostname or 'no URL'})")
    filler = workday if platform == "workday" else ats_form
    out = RUNS / f"{job_id}-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    pdf = api.get(f"/jobs/{job_id}/resume.pdf")
    name = re.search(r'filename="?([^";]+)', pdf.headers.get("content-disposition", ""))
    resume = out / (name.group(1) if name else "resume.pdf")
    resume.write_bytes(pdf.content)

    PROFILE.mkdir(parents=True, exist_ok=True)
    CLICKED.discard(job_id)
    result = None   # what to report to HireOS (live only)
    with sync_playwright() as p:
        on_screen = headless and screen_up()   # --headed keeps the local display
        if on_screen:   # sized to the screen (900x1200, portrait for a phone)
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE), headless=False, env={**os.environ, "DISPLAY": SCREEN_DISPLAY}, no_viewport=True,
                args=["--window-position=0,0", "--window-size=900,1200"])
        else:
            ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=headless,
                                                       viewport={"width": 1280, "height": 1000})
        ctx.tracing.start(screenshots=True, snapshots=True)
        ctx.tracing.start_chunk()   # chunks, so sign-in can pause tracing around the password
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        run = Run(api, job, page, out, platform)
        run.screen = creds()["screen_url"] if on_screen else ""
        outcome = "review"
        try:
            filler.fill(run, resume)
            checked = run.record.get("review_checked", 0)
            if not live:
                run.record["outcome"] = f"reached Review, all {checked} values checked — stopped before Submit (dry run)"
            else:
                decision = wait_for_submit(api, run)
                if decision == "submit":
                    CLICKED.add(job_id)
                    confirmed = filler.submit(run)
                    outcome = "submitted" if confirmed else "unconfirmed"
                    run.record["outcome"] = "submitted — confirmation page seen" if confirmed else \
                        f"Submit clicked but no confirmation seen — check your email or {job['url']} before retrying"
                    result = {"outcome": "submitted" if confirmed else "needs_human",
                              "notes": run.record["outcome"]}
                else:
                    outcome = decision
                    run.record["outcome"] = "cancelled by you" if decision == "cancel" else \
                        "no Submit within 30 min — the job stays waiting; tap Submit to restart it"
        except HandOff as h:
            outcome = h.kind
            run.record["outcome"] = f"handed off: {h.kind} — {h.reason}"
            try:   # the page may be gone (bot wall, closed target): the hand-off still stands
                run.shot(f"handoff-{h.kind}")
            except Exception:
                pass
            result = None if h.kind == "cancelled" else \
                {"outcome": "skipped" if h.kind == "closed" else "needs_human", "notes": run.record["outcome"]}
        except Exception as e:   # a crash still leaves its screenshot, trace and record
            outcome = "error"
            run.record["outcome"] = f"error: {type(e).__name__}: {e}"[:500]
            try:
                run.shot("error")
            except Exception:
                pass
            result = crash_outcome(job_id, run.record["outcome"])
        finally:
            workday.trace_stop(ctx, out)
            run.record["minutes"] = round((time.monotonic() - run.started) / 60, 1)
            (out / "run.json").write_text(json.dumps(run.record, indent=2))
            ctx.close()

    r = run.record
    if live and result:   # HireOS records it and messages him (Telegram) with the notes
        r["outcome_reported"] = result["outcome"]
        answers = [{"label": x.split(" = ", 1)[0], "answer": x.split(" = ", 1)[1]} for x in r["filled"] if " = " in x]
        api.post(f"/apply/{job_id}/result", {**result, "answers": answers[:200],
                                             "screenshot_b64": b64(getattr(run, "last_shot", None))})
    lines = [f"{'Apply' if live else 'Dry run'} #{job_id} {job['company']} · {job['title']}", r["outcome"],
             f"{len(r['pages'])} pages · {len(r['filled'])} fields filled · {r['minutes']} min"]
    if r.get("consented"):
        lines.append("Agreed for you: " + "; ".join(r["consented"]))
    if r.get("skipped"):
        lines.append("Left out: " + "; ".join(r["skipped"]))
    if r["unanswered"]:
        lines.append("No answer for: " + "; ".join(r["unanswered"][:8]))
    if r["errors"]:
        lines.append("Form errors: " + "; ".join(r["errors"][:5]))
    if not (live and result):   # live outcomes are messaged by HireOS from the result
        notify(api, "\n".join(lines), getattr(run, "review_top", None) or getattr(run, "last_shot", None),
               getattr(run, "review_pdf", None), review_name(run))
    print("\n".join(lines) + f"\nrun dir: {out}")
    return {"outcome": outcome, **r}


IDLE_S, CAPPED_S = 60, 600


def watch(headless: bool):
    """Forever: take the next approved job HireOS gives the worker and apply in review mode.
    One at a time (one browser profile). ponytail: polling; a push from approve_job if 60 s is too slow."""
    api = HireOS()
    while True:
        try:
            r = httpx.post(f"{api.url}/api/apply/next", headers=api.h, timeout=60, json={"worker": True})
        except httpx.HTTPError as e:
            print(f"watch: HireOS unreachable: {e}", file=sys.stderr, flush=True)
            time.sleep(IDLE_S)
            continue
        if r.status_code in (423, 429):   # paused, or every queued platform at today's cap
            time.sleep(CAPPED_S)
            continue
        if r.status_code != 200:
            time.sleep(IDLE_S)
            continue
        job = r.json()
        print(f"watch: #{job['job_id']} {job['company']}", flush=True)
        if platform_of(job):
            notify(api, f"Applying to #{job['job_id']} {job['company']} · {job['title']} now. "
                        "The filled form comes here for Submit in about 10 minutes.")
        try:
            run_job(job["job_id"], headless, live=True, claimed=True)
        except (Unsupported, SystemExit) as e:   # a site the worker can't fill: the user applies
            api.post(f"/apply/{job['job_id']}/result", {"outcome": "needs_human", "notes":
                     f"{e}. Apply yourself: {job.get('url')} — your tailored resume is on the card above."})
        except Exception as e:   # never let one job stop the service
            print(f"watch: #{job['job_id']} crashed: {e!r}", file=sys.stderr, flush=True)
            try:
                api.post(f"/apply/{job['job_id']}/result", crash_outcome(job["job_id"], f"worker crashed: {e!r}"))
            except httpx.HTTPError:
                pass


def dry_run(job_id: int, headless: bool) -> dict:
    return run_job(job_id, headless, live=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", type=int, metavar="JOB_ID", help="fill and stop on Review (nothing submitted)")
    g.add_argument("--apply", type=int, metavar="JOB_ID", help="review mode: fill, then submit only when you tap Submit")
    g.add_argument("--watch", action="store_true", help="service: apply each approved job as it comes")
    ap.add_argument("--headed", action="store_true", help="show the browser (needs DISPLAY)")
    a = ap.parse_args()
    import apply_worker   # one module object, so workday.py raises the same HandOff class we catch
    if a.watch:
        apply_worker.watch(headless=not a.headed)
    else:
        apply_worker.run_job(a.apply or a.dry_run, headless=not a.headed, live=bool(a.apply))

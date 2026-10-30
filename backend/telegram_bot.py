"""Telegram front end: resume review cards (PDF + what changed) with Approve / Skip / Edit,
and apply outcomes. Long-polls getUpdates from inside the API process.

A chat is linked to a user with a one-time code: POST /api/telegram/link returns
t.me/<bot>?start=<code>. Every action resolves the job through the chat's own user,
so a chat can only ever touch its owner's jobs.
"""
import asyncio
import functools
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

import apply_queue
from fastapi import HTTPException
from loguru import logger

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
CHAT_KEY, CODE_KEY, LINKED_AT_KEY = "telegram_chat_id", "telegram_link_code", "telegram_linked_at"
SENT_META = "tg_sent_version"
CARDS_META = "tg_card_msgs"     # [[message_id, card text]]: closed when the job is decided
REVIEW_STATUSES = ("pending", "approved", "found", "needs_human")
BATCH = 3                      # cards per push, so a backlog never floods the chat
CODE_TTL = timedelta(minutes=15)
NOTIFY = {"submitted": "Submitted", "needs_human": "Needs you", "failed": "Failed"}

# ponytail: in memory — a restart forgets a pending "Edit", the user just taps Edit again
_edit_waiting: dict = {}       # chat_id -> (job_id, since): text messages are edit instructions until
EDIT_MIN = 30                  # Approve/Skip, /done, or EDIT_MIN minutes of quiet


# ── Pure helpers ──────────────────────────────────────────────────────────────

def new_link_code(now: datetime) -> tuple:
    """(code, stored value). The value carries its own expiry."""
    code = secrets.token_urlsafe(12)
    return code, f"{code}|{(now + CODE_TTL).isoformat()}"


def code_matches(stored: str, code: str, now: datetime) -> bool:
    try:
        want, expires = stored.split("|", 1)
        return secrets.compare_digest(want, code) and now < datetime.fromisoformat(expires)
    except (ValueError, AttributeError):
        return False


def _plain(s: str) -> str:
    return re.sub(r"[*_`]", "", s or "").strip()


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _bullets(md: str) -> list:
    return [_plain(l.strip()[2:]) for l in (md or "").splitlines() if l.strip()[:2] in ("- ", "* ")]


def _numbers(text: str) -> set:
    """Figures a resume claims (10M, 40%, 14, 3x), normalised so formatting doesn't matter."""
    return {re.sub(r"[\s,]", "", n.lower()).rstrip(".") for n in re.findall(r"\d[\d,.]*\s?[%xkmb+]?", _plain(text), re.I)
            if not re.fullmatch(r"(19|20)\d\d", n.strip())}   # years are dates, not claims


def changes_summary(md: str, master_md: str, notes: dict = None, limit: int = 4) -> list:
    """Phone-sized review notes for a resume version.
    Edited version: its find/replace patches. Tailored version: the summary opener, what
    it leads with, and whether every figure exists in the master resume (the claim most
    worth checking before applying)."""
    edits = (notes or {}).get("edits")
    if edits:
        return ["Changed:"] + [f"• {_short(_plain(e.get('find', '')), 70)}\n  → {_short(_plain(e.get('replace', '')), 70)}"
                               for e in edits[:limit]]
    lines = []
    summary = next((l for l in (md or "").splitlines()[1:]
                    if l.strip() and l.strip()[0] not in "#-*|" and "@" not in l), "")
    if summary:
        lines.append(f"Opens: {_short(_plain(summary).split('. ')[0], 140)}")
    labels = [b.split(":", 1)[0] for b in _bullets(md) if ":" in b[:60]]
    if labels:
        more = f" (+{len(labels) - limit})" if len(labels) > limit else ""
        lines.append("Leads with: " + " · ".join(labels[:limit]) + more)
    claimed, known = _numbers("\n".join(_bullets(md)) + summary), _numbers(master_md)
    unknown = sorted(claimed - known)
    if claimed:
        lines.append(f"Figures: all {len(claimed)} match your master resume" if not unknown
                     else f"Check figures not in master: {', '.join(unknown[:6])}")
    return lines


def card_text(job, version: int, changes: list) -> str:
    score = f"score {job.match_score:g}" if job.match_score is not None else ""
    meta = " · ".join(x for x in (score, job.location or "", f"resume v{version}") if x)
    why = apply_queue.manual_reason(job)     # known before approving: approving won't get it applied for you
    manual = [f"⚠️ You'll apply to this one yourself: {why}"] if why else []
    return "\n".join([f"#{job.id}  {job.company} · {job.title}", meta, *manual,
                      *([""] + changes if changes else [])])


def buttons(job_id: int, status: str = "pending") -> dict:
    """Approve / Skip / Edit; none once approved (a re-sent card of an approved job is just the resume)."""
    if status in ("approved", "applying", "waiting_human", "applied"):
        return None
    return {"inline_keyboard": [[{"text": "Approve", "callback_data": f"a:{job_id}"},
                                 {"text": "Skip", "callback_data": f"s:{job_id}"},
                                 {"text": "Edit", "callback_data": f"e:{job_id}"}]]}


def ask_message(job, kind: str, text: str, url: str = None) -> tuple:
    """(text, reply_markup) for the applier needing the user mid-form.
    question / code: the user replies with it. captcha / wait: the user acts, then taps Done."""
    head = f"#{job.id} {job.company} · {job.title}"
    if kind == "question":
        return (f"{head}\nThe form asks: {text}\n\nReply to this message with your answer "
                "(it's saved for future forms), or reply skip.",
                {"force_reply": True, "input_field_placeholder": "Your answer, or skip"})
    if kind == "code":   # one-time code: typed into the form, never saved
        return (f"{head}\n{text}\n\nReply to this message with the code, or reply skip.",
                {"force_reply": True, "input_field_placeholder": "The code, or skip"})
    if kind == "submit":   # the Review page as a photo; nothing is sent to the employer until Submit
        rows = [[{"text": "Submit", "callback_data": f"y:{job.id}"}, {"text": "Cancel", "callback_data": f"k:{job.id}"}]]
        if url and url.startswith("https://"):   # the filled form, live on the screen
            rows.append([{"text": "Open screen", "url": url}])
        return (f"{head}\nReady to submit. {text}\nCheck the screenshot, then tap Submit — or Cancel to stop.",
                {"inline_keyboard": rows})
    row = [{"text": "Done", "callback_data": f"d:{job.id}"}, {"text": "Skip job", "callback_data": f"k:{job.id}"}]
    lead = "CAPTCHA — solve it on the screen, then tap Done." if kind == "captcha" else f"Needs you: {text}"
    rows = ([[{"text": "Open screen", "url": url}]] if url and url.startswith("https://") else []) + [row]
    return f"{head}\n{lead}" + (f"\n{url}" if url and not url.startswith("https://") else ""), {"inline_keyboard": rows}


def retry_button(job_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "Retry", "callback_data": f"r:{job_id}"}]]}


# ── Database ──────────────────────────────────────────────────────────────────

def _m():
    import main   # loaded by then; imported lazily because main imports this module
    return main


def cards_due(db, user_id: int, since: datetime = None, limit: int = BATCH, sent_key: str = SENT_META) -> list:
    """(job, latest ResumeVersion) pairs not yet sent to Telegram, best match first.
    With `since`, only versions created after it (new work, not the old backlog)."""
    m = _m()
    out = []
    jobs = db.query(m.Job).filter(m.Job.user_id == user_id, m.Job.status.in_(REVIEW_STATUSES)) \
        .order_by(m.Job.match_score.desc().nullslast()).all()
    for job in jobs:
        rv = db.query(m.ResumeVersion).filter_by(job_id=job.id).order_by(m.ResumeVersion.version.desc()).first()
        if not rv or not rv.pdf_path or (job.meta or {}).get(sent_key) == rv.version:
            continue
        if since and rv.created_at and rv.created_at < since:
            continue
        out.append((job, rv))
        if len(out) >= limit:
            break
    return out


def _user_for_chat(db, chat_id: int):
    m = _m()
    row = db.query(m.Settings).filter(m.Settings.key == CHAT_KEY, m.Settings.value == str(chat_id)).first()
    return db.query(m.User).get(row.user_id) if row else None


def link_chat(db, code: str, chat_id: int, now: datetime):
    """Bind chat_id to the user whose unexpired code this is. Returns the user or None."""
    m = _m()
    for row in db.query(m.Settings).filter(m.Settings.key == CODE_KEY).all():
        if code_matches(row.value, code, now):
            # one chat, one user: drop this chat from anyone it was linked to before
            db.query(m.Settings).filter(m.Settings.key == CHAT_KEY, m.Settings.value == str(chat_id)).delete()
            m._put_setting(db, row.user_id, CHAT_KEY, str(chat_id))
            m._put_setting(db, row.user_id, LINKED_AT_KEY, now.isoformat())
            db.delete(row)
            db.commit()
            return db.query(m.User).get(row.user_id)
    return None


def open_question(db, user_id: int, reply_to=None, key: str = "msg"):
    """The waiting job a text message answers: the one whose question it replies to
    (`key`: where the ask stored that chat's message id), else the only open question."""
    m = _m()
    jobs = [j for j in db.query(m.Job).filter(m.Job.user_id == user_id, m.Job.status == "waiting_human").all()
            if ((j.meta or {}).get("ask") or {}).get("kind") in ("question", "code")]
    if reply_to:
        return next((j for j in jobs if j.meta["ask"].get(key) == reply_to), None)
    return jobs[0] if len(jobs) == 1 else (AMBIGUOUS if jobs else None)


# open_question's answer when several questions are open and the message replies to none of them
AMBIGUOUS = "Several questions are waiting for you — reply to the one you're answering (swipe it, or right-click › Reply)."


def answer(db, user, job, text: str, now: datetime) -> str:
    """The user answered (or tapped Done/Skip). A question's answer is saved to the
    Application Profile. Within the deadline the waiting run continues; after it, the job is
    re-approved so the watchdog starts a fresh run."""
    m = _m()
    if job.status != "waiting_human":
        return f"#{job.id} isn't waiting any more ({job.status})."
    ask = dict((job.meta or {}).get("ask") or {})
    if text.strip().lower() == "skip":
        m.apply_result(job.id, {"outcome": "skipped", "notes": "Skipped by you in Telegram"}, db, user)
        return f"Skipped #{job.id}."
    if ask.get("kind") == "question":
        p = m._application_profile(db, user.id)
        qa = [x for x in p.get("custom_qa") or [] if (x.get("q") or "").lower() != ask["q"].lower()]
        m.put_application_profile({**p, "custom_qa": qa + [{"q": ask["q"], "a": text.strip()}]}, db, user)
    job.meta = {**(job.meta or {}), "ask": {**ask, "answer": text.strip()}}
    db.commit()
    if ask.get("deadline") and now < datetime.fromisoformat(ask["deadline"]):
        m.apply_continue(job.id, db, user)
        return f"Submitting #{job.id} now." if ask.get("kind") == "submit" else f"Got it — #{job.id} is continuing."
    m.approve_job(job.id, db, user)
    return f"Got it — saved. #{job.id} will restart shortly."


# ── Telegram API ──────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def username() -> str:
    return httpx.get(f"{API}/getMe", timeout=10).json()["result"]["username"]


def send(db, user_id: int, text: str, markup: dict = None, silent: bool = False, html: bool = False):
    """Message from sync code (endpoints run in a thread pool). Returns the message id, or
    None when the bot is off, the user has no linked chat, or Telegram failed. silent: no sound."""
    chat = _m()._setting(db, user_id, CHAT_KEY) if TOKEN else None
    if not chat:
        return None
    try:
        body = httpx.post(f"{API}/sendMessage", timeout=10, json={
            "chat_id": chat, "text": text[:4000], **({"reply_markup": markup} if markup else {}),
            **({"disable_notification": True} if silent else {}), **({"parse_mode": "HTML"} if html else {})}).json()
        return (body.get("result") or {}).get("message_id")
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"[telegram] send failed: {e}")
        return None


def edit(db, user_id: int, message_id, text: str, html: bool = False):
    """Replace a message's text (a progress line updating in place). Errors are only logged."""
    chat = _m()._setting(db, user_id, CHAT_KEY) if TOKEN else None
    if not (chat and message_id):
        return
    try:
        httpx.post(f"{API}/editMessageText", timeout=10,
                   json={"chat_id": chat, "message_id": message_id, "text": text[:4000],
                         **({"parse_mode": "HTML"} if html else {})})
    except httpx.HTTPError as e:
        logger.warning(f"[telegram] edit failed: {e}")


def send_photo(db, user_id: int, png: bytes, caption: str, markup: dict = None):
    """A screenshot with a caption (Telegram caps captions at 1024 chars)."""
    chat = _m()._setting(db, user_id, CHAT_KEY) if TOKEN else None
    if not chat:
        return None
    try:
        data = {"chat_id": chat, "caption": caption[:1024], **({"reply_markup": json.dumps(markup)} if markup else {})}
        body = httpx.post(f"{API}/sendPhoto", data=data, files={"photo": ("screen.png", png, "image/png")}, timeout=30).json()
        return (body.get("result") or {}).get("message_id")
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"[telegram] photo failed: {e}")
        return None


def send_document(db, user_id: int, data: bytes, filename: str, caption: str = ""):
    """A file (e.g. the Review page as PDF: photos get downscaled until text is unreadable)."""
    chat = _m()._setting(db, user_id, CHAT_KEY) if TOKEN else None
    if not chat:
        return None
    try:
        body = httpx.post(f"{API}/sendDocument", data={"chat_id": chat, "caption": caption[:1024]},
                          files={"document": (filename, data, "application/pdf" if filename.endswith(".pdf") else
                                              "application/octet-stream")}, timeout=60).json()
        return (body.get("result") or {}).get("message_id")
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"[telegram] document failed: {e}")
        return None


def notify(db, user_id: int, text: str):
    send(db, user_id, text)


def close_cards(db, user_id: int, job, note: str):
    """Every card sent for this job: buttons off, the outcome appended (no stale Approve)."""
    chat = _m()._setting(db, user_id, CHAT_KEY) if TOKEN else None
    for msg_id, text in (job.meta or {}).get(CARDS_META) or []:
        try:
            httpx.post(f"{API}/editMessageText", json={"chat_id": chat, "message_id": msg_id,
                                                        "text": f"{text}\n\n{note}"[:4096]}, timeout=15)
        except httpx.HTTPError:
            pass
    if chat:
        job.meta = {k: v for k, v in (job.meta or {}).items() if k != CARDS_META}
        db.commit()


def notify_outcome(db, user_id: int, job, outcome: str, notes: str = None):
    if outcome == "submitted":
        close_cards(db, user_id, job, "✅ Submitted")
    if outcome in NOTIFY:
        notify(db, user_id, f"{NOTIFY[outcome]}: #{job.id} {job.company} · {job.title}" + (f"\n{notes}" if notes else ""))


async def _call(c: httpx.AsyncClient, method: str, **data):
    r = await c.post(f"{API}/{method}", json=data)
    body = r.json()
    if not body.get("ok"):
        logger.warning(f"[telegram] {method}: {body.get('description')}")
    return body


async def send_card(c, db, user, chat_id: int, job, rv):
    m = _m()
    changes = changes_summary(rv.content_md, m.get_master_resume(db, user.id), rv.critic_notes)
    pdf = Path(rv.pdf_path)
    if pdf.exists():
        with pdf.open("rb") as f:
            await c.post(f"{API}/sendDocument", data={"chat_id": chat_id},
                         files={"document": (m.resume_filename(job, rv), f, "application/pdf")})
    text = card_text(job, rv.version, changes)
    markup = buttons(job.id, job.status)
    sent = await _call(c, "sendMessage", chat_id=chat_id, text=text, **({"reply_markup": markup} if markup else {}))
    cards = ((job.meta or {}).get(CARDS_META) or []) + [[(sent.get("result") or {}).get("message_id"), text]]
    job.meta = {**(job.meta or {}), SENT_META: rv.version, CARDS_META: [x for x in cards if x[0]][-10:]}
    db.commit()


# ── Handlers ──────────────────────────────────────────────────────────────────

HELP = ("Write anything to ask HireOS (e.g. \"what jobs came in today?\").\nCommands:\n/review — next resumes to review\n/review <job number> — re-send a card still waiting for you\n/status — queue counts\n"
        "Buttons on each resume: Approve queues it for applying, Skip drops it, "
        "Edit lets you type a change (e.g. \"remove the Tableau line\").")


async def handle(c, upd: dict):
    m = _m()
    db = m.SessionLocal()
    try:
        if "callback_query" in upd:
            return await _on_button(c, db, upd["callback_query"])
        msg = upd.get("message") or {}
        chat_id, text = msg.get("chat", {}).get("id"), (msg.get("text") or "").strip()
        if not chat_id or msg.get("chat", {}).get("type") != "private":
            return
        if text.startswith("/start "):
            user = link_chat(db, text.split(" ", 1)[1].strip(), chat_id, datetime.utcnow())
            reply = (f"Linked to HireOS ({user.email}). New resumes will arrive here.\n\n{HELP}" if user
                     else "That link expired or was already used. Get a new one in HireOS.")
            return await _call(c, "sendMessage", chat_id=chat_id, text=reply)
        user = _user_for_chat(db, chat_id)
        if not user:
            return await _call(c, "sendMessage", chat_id=chat_id,
                               text="This chat isn't linked yet. Open the Telegram link from HireOS.")
        if re.fullmatch(r"/review\s+#?\d+", text):   # re-send one card, even if sent before
            jid = int(re.search(r"\d+", text).group(0))
            job = db.query(m.Job).filter_by(id=jid, user_id=user.id).first()
            rv = job and db.query(m.ResumeVersion).filter_by(job_id=jid).order_by(m.ResumeVersion.version.desc()).first()
            if job and job.status not in REVIEW_STATUSES:   # applied, skipped, …: nothing to decide
                await _call(c, "sendMessage", chat_id=chat_id, text=f"#{jid} {job.company} is {job.status}, nothing to review.")
            elif rv and rv.pdf_path:
                await send_card(c, db, user, chat_id, job, rv)
            else:
                await _call(c, "sendMessage", chat_id=chat_id, text=f"No resume for #{jid}.")
        elif text == "/review":
            cards = cards_due(db, user.id)
            for job, rv in cards:
                await send_card(c, db, user, chat_id, job, rv)
            if not cards:
                await _call(c, "sendMessage", chat_id=chat_id, text="Nothing waiting for review.")
        elif text == "/status":
            counts = dict(db.query(m.Job.status, m.func.count()).filter(m.Job.user_id == user.id).group_by(m.Job.status).all())
            import scheduler
            lines = [f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])] or ["No jobs."]
            await _call(c, "sendMessage", chat_id=chat_id,
                        text="\n".join(lines + [scheduler.progress_line(user.id)]).strip())
        elif text == "/done":
            _edit_waiting.pop(chat_id, None)
            await _call(c, "sendMessage", chat_id=chat_id, text="Edit mode closed.")
        elif chat_id not in _editing(chat_id) and text and not text.startswith("/") and \
                (job := open_question(db, user.id, (msg.get("reply_to_message") or {}).get("message_id"))):
            await _call(c, "sendMessage", chat_id=chat_id,
                        text=job if job == AMBIGUOUS else answer(db, user, job, text, datetime.utcnow()))
        elif chat_id in _editing(chat_id) and text and not text.startswith("/"):
            _edit_waiting[chat_id] = (_edit_waiting[chat_id][0], time.monotonic())
            await _on_edit(c, db, user, chat_id, _edit_waiting[chat_id][0], text)
        elif text and not text.startswith("/"):
            await _call(c, "sendChatAction", chat_id=chat_id, action="typing")
            reply = await chat_reply(db, user, text)
            await _call(c, "sendMessage", chat_id=chat_id, text=reply.replace("**", "")[:4000])
        else:
            await _call(c, "sendMessage", chat_id=chat_id, text=HELP)
    finally:
        db.close()


def button_action(db, user, job, action: str, chat_key) -> tuple:
    """What a button does, for any chat: (outcome text to show on the message, prompt to send).
    `chat_key` identifies the chat for edit mode (Telegram chat id, Discord "dc:<channel>")."""
    m = _m()
    if action in ("a", "s", "e") and job.status not in REVIEW_STATUSES:   # a card from before the job was decided
        return f"#{job.id} {job.company} is already {job.status}.", None
    if action in ("a", "s"):
        _edit_waiting.pop(chat_key, None)
    if action == "a":
        try:
            m.approve_job(job.id, db, user)
            return "Approved — queued for applying.", None
        except HTTPException as e:
            return f"Not approved: {e.detail}", None
    if action == "s":
        old, job.status = job.status, "withdrawn"
        db.commit()
        m._log_event(db, job.id, "status_change", "Skipped in chat", old_value=old,
                     new_value="withdrawn", source="user", user_id=user.id)
        return "Skipped.", None
    if action in ("d", "k", "y"):
        return answer(db, user, job, {"d": "done", "k": "skip", "y": "submit"}[action], datetime.utcnow()), None
    if action == "r":
        try:
            m.approve_job(job.id, db, user)
            return f"Retrying #{job.id} shortly.", None
        except HTTPException as e:
            return f"Can't retry: {e.detail}", None
    if action == "e" and job.status in m.RESUME_LOCKED:
        return f"#{job.id} is {job.status}: its resume is locked.", None
    if action == "e":
        _edit_waiting[chat_key] = (job.id, time.monotonic())
        return None, (f"What should change in the #{job.id} {job.company} resume? Send one change per "
                      f"message, as many as you like. Approve, Skip or done ends editing.")
    return None, None


async def _on_button(c, db, q: dict):
    chat_id = q["message"]["chat"]["id"]
    await _call(c, "answerCallbackQuery", callback_query_id=q["id"])
    user = _user_for_chat(db, chat_id)
    action, _, jid = (q.get("data") or "").partition(":")
    if not user or not jid.isdigit():
        return
    m = _m()
    try:
        job = m.get_owned_job(db, int(jid), user)
    except HTTPException:
        return await _call(c, "sendMessage", chat_id=chat_id, text=f"Job #{jid} not found.")
    done, prompt = button_action(db, user, job, action, chat_id)
    if prompt:
        return await _call(c, "sendMessage", chat_id=chat_id, text=prompt)
    if done:   # replace the buttons with the outcome so a card can't be acted on twice
        if "caption" in q["message"] or "photo" in q["message"]:
            await _call(c, "editMessageCaption", chat_id=chat_id, message_id=q["message"]["message_id"],
                        caption=f"{q['message'].get('caption', '')}\n\n{done}"[:1024])
        else:
            await _call(c, "editMessageText", chat_id=chat_id, message_id=q["message"]["message_id"],
                        text=f"{q['message'].get('text', '')}\n\n{done}")


async def chat_reply(db, user, text: str) -> str:
    """Free text that isn't a command, an answer or an edit: HireOS's chat assistant (the web app's
    floating chat), on the user's own default model."""
    m = _m()
    r = await m.chat(m.ChatMessage(message=text), db, user)     # the user's model (chat resolves it)
    return r.reply


def _editing(chat_id: int) -> dict:
    """The live edit sessions (drops this chat's if it went quiet)."""
    if chat_id in _edit_waiting and time.monotonic() - _edit_waiting[chat_id][1] > EDIT_MIN * 60:
        _edit_waiting.pop(chat_id)
    return _edit_waiting


async def _on_edit(c, db, user, chat_id: int, job_id: int, instruction: str):
    m = _m()
    await _call(c, "sendMessage", chat_id=chat_id, text="Editing… (about a minute)")
    try:
        await m.edit_resume(job_id, {"instruction": instruction}, db, user)
    except HTTPException as e:
        if e.status_code == 409:                   # approved meanwhile: edit mode ends
            _edit_waiting.pop(chat_id, None)
            return await _call(c, "sendMessage", chat_id=chat_id, text=f"Not changed: {e.detail}")
        return await _call(c, "sendMessage", chat_id=chat_id,
                           text=f"Not changed: {e.detail}\n\nSend another change, or /done.")
    job = m.get_owned_job(db, job_id, user)
    rv = db.query(m.ResumeVersion).filter_by(job_id=job_id).order_by(m.ResumeVersion.version.desc()).first()
    await send_card(c, db, user, chat_id, job, rv)
    await _call(c, "sendMessage", chat_id=chat_id, text="Send another change, or tap Approve.")


async def _push_new(c):
    """Send resumes created since each chat was linked (older backlog comes via /review)."""
    m = _m()
    db = m.SessionLocal()
    try:
        for row in db.query(m.Settings).filter(m.Settings.key == CHAT_KEY).all():
            linked = m._setting(db, row.user_id, LINKED_AT_KEY)
            since = datetime.fromisoformat(linked) if linked else datetime.utcnow()
            user = db.query(m.User).get(row.user_id)
            for job, rv in cards_due(db, row.user_id, since=since):
                await send_card(c, db, user, int(row.value), job, rv)
    finally:
        db.close()


async def run():
    """Long-poll loop. ponytail: polling, one process; switch to setWebhook when hosted with >1 worker."""
    offset, last_push = 0, 0.0
    async with httpx.AsyncClient(timeout=40) as c:
        await _call(c, "setMyCommands", commands=[{"command": "review", "description": "Next resumes to review"},
                                                  {"command": "status", "description": "Queue counts"}])
        while True:
            try:
                r = await c.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 25})
                for upd in r.json().get("result", []):
                    offset = upd["update_id"] + 1
                    try:
                        await handle(c, upd)
                    except Exception:
                        logger.exception("[telegram] update failed")
                if time.monotonic() - last_push > 60:
                    last_push = time.monotonic()
                    await _push_new(c)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[telegram] poll error: {e}")
                await asyncio.sleep(5)

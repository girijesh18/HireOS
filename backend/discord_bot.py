"""Discord front end: the same resume cards, buttons, questions and edits as the Telegram bot,
in one Discord channel (a server channel or a DM with the bot).

Linking: POST /api/discord/link returns a one-time code; typing `link <code>` in a channel
binds that Discord user to the HireOS user. New resumes and alerts go to that channel; the user
is answered in any channel of the same server (e.g. chat in #new-jobs, cards in #resume-review).
Anyone else is ignored -- like OpenClaw's allowlist.

Sending uses the REST API (sync httpx, like telegram_bot.send); receiving uses the gateway
websocket. Needs the bot's Message Content intent (Developer Portal > Bot) to read messages
in a server channel.
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime

import httpx
from fastapi import HTTPException
from loguru import logger

import telegram_bot as tg

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
API = "https://discord.com/api/v10"
GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
INTENTS = (1 << 9) | (1 << 12) | (1 << 15)    # server messages, DMs, message content
FATAL_CLOSE = {4004: "the bot token was rejected", 4013: "invalid intents",
               4014: "the Message Content intent is off (Developer Portal > Bot > Privileged Gateway Intents)"}
CHANNEL_KEY, USER_KEY, GUILD_KEY, CODE_KEY, LINKED_AT_KEY = \
    "discord_channel_id", "discord_user_id", "discord_guild_id", "discord_link_code", "discord_linked_at"
SENT_META = "dc_sent_version"
CARDS_META = "dc_card_msgs"     # [[message_id, card text]]: closed when the job is decided
LIMIT = 2000                    # Discord's message length

HELP = ("Write anything to ask HireOS (e.g. \"what jobs came in today?\"). "
        "Commands: review — next resumes to review · review <job number> — re-send a card · "
        "status — queue counts · done — leave edit mode.\n"
        "Buttons on each resume: Approve queues it for applying, Skip drops it, "
        "Edit lets you type a change (e.g. \"remove the Tableau line\").")


LOOP = None                     # the API's event loop: handlers run in threads, async work goes back to it


def _m():
    import main
    return main


def on_loop(coro, timeout: float = 300):
    """Run a coroutine on the API's loop from a handler thread and wait for it, so tasks it
    starts (resume generation, research) keep running after the reply."""
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)


# ── Pure helpers ──────────────────────────────────────────────────────────────

def components(markup: dict) -> list:
    """Telegram inline_keyboard markup -> Discord button rows (callback_data -> custom_id,
    url -> link button). force_reply has no Discord equivalent: the user replies to the message."""
    rows = []
    for row in (markup or {}).get("inline_keyboard") or []:
        btns = []
        for b in row[:5]:
            if b.get("url"):
                btns.append({"type": 2, "style": 5, "label": b["text"][:80], "url": b["url"]})
            else:
                style = 4 if b["callback_data"][:1] in ("s", "k") else 1   # Skip/Cancel red, the rest blue
                btns.append({"type": 2, "style": style, "label": b["text"][:80], "custom_id": b["callback_data"]})
        rows.append({"type": 1, "components": btns})
    return rows[:5]


def text_for(text: str, markup: dict = None) -> str:
    """Telegram wording -> Discord wording: there is no force-reply, so say how to answer."""
    text = text.replace("/done", "done").replace("/review", "review")
    if (markup or {}).get("force_reply"):
        text = text.replace("Reply to this message with", "Reply to this message (right-click › Reply) with")
    return text[:LIMIT]


def command(text: str):
    """("review", None) | ("review", 45) | ("status", None) | ("done", None) | ("help", None) | None."""
    t = text.strip().lower().lstrip("/!")
    if m := re.fullmatch(r"review\s+#?(\d+)", t):
        return "review", int(m.group(1))
    return (t, None) if t in ("review", "status", "done", "help") else None


# ── Database ──────────────────────────────────────────────────────────────────

def channel_of(db, user_id: int):
    return _m()._setting(db, user_id, CHANNEL_KEY) if TOKEN else None


def user_for(db, channel_id: str, author_id: str, guild_id: str = None):
    """The HireOS user this Discord user linked -- in the linked channel, or any channel of the same server."""
    m = _m()
    for row in db.query(m.Settings).filter(m.Settings.key == USER_KEY, m.Settings.value == str(author_id)).all():
        if m._setting(db, row.user_id, CHANNEL_KEY) == str(channel_id) or \
                (guild_id and m._setting(db, row.user_id, GUILD_KEY) == str(guild_id)):
            return db.query(m.User).get(row.user_id)
    return None


def link(db, code: str, channel_id: str, author_id: str, now: datetime, guild_id: str = None):
    """Bind channel + Discord user to the user whose unexpired code this is. Returns the user or None."""
    m = _m()
    for row in db.query(m.Settings).filter(m.Settings.key == CODE_KEY).all():
        if tg.code_matches(row.value, code, now):
            db.query(m.Settings).filter(m.Settings.key == CHANNEL_KEY, m.Settings.value == str(channel_id)).delete()
            m._put_setting(db, row.user_id, CHANNEL_KEY, str(channel_id))
            m._put_setting(db, row.user_id, USER_KEY, str(author_id))
            m._put_setting(db, row.user_id, GUILD_KEY, str(guild_id or ""))   # a DM has no server
            m._put_setting(db, row.user_id, LINKED_AT_KEY, now.isoformat())
            db.delete(row)
            db.commit()
            return db.query(m.User).get(row.user_id)
    return None


# ── REST (sync: endpoints run in a thread pool) ───────────────────────────────

def _request(method: str, path: str, **kw):
    """One REST call; waits out a single rate limit. Returns the JSON body or None."""
    for _ in range(2):
        try:
            r = httpx.request(method, f"{API}{path}", headers={"Authorization": f"Bot {TOKEN}"}, timeout=30, **kw)
        except httpx.HTTPError as e:
            logger.warning(f"[discord] {method} {path}: {e}")
            return None
        if r.status_code == 429:
            time.sleep(min(float(r.json().get("retry_after", 1)), 10))
            continue
        if r.status_code >= 400:
            logger.warning(f"[discord] {method} {path}: {r.status_code} {r.text[:200]}")
            return None
        return r.json() if r.content else {}
    return None


SUPPRESS_NOTIFICATIONS = 1 << 12     # message flag: delivered without a push or sound


def post(channel_id: str, text: str, markup: dict = None, file: tuple = None, silent: bool = False):
    """A message (optionally with buttons and one file: (name, bytes, mime)). Returns its id or None."""
    body = {"content": text_for(text, markup), "components": components(markup),
            "allowed_mentions": {"parse": []}, **({"flags": SUPPRESS_NOTIFICATIONS} if silent else {})}
    if file:
        body["attachments"] = [{"id": 0, "filename": file[0]}]
        r = _request("POST", f"/channels/{channel_id}/messages", data={"payload_json": json.dumps(body)},
                     files={"files[0]": file})
    else:
        r = _request("POST", f"/channels/{channel_id}/messages", json=body)
    return (r or {}).get("id")


def close_message(channel_id: str, message_id: str, text: str):
    """Replace a message's text and drop its buttons, so it can't be acted on twice."""
    _request("PATCH", f"/channels/{channel_id}/messages/{message_id}",
             json={"content": text[:LIMIT], "components": []})


def send(db, user_id: int, text: str, markup: dict = None, silent: bool = False):
    """Same contract as telegram_bot.send: the message id, or None when the bot is off or not linked."""
    ch = channel_of(db, user_id)
    return post(ch, text, markup, silent=silent) if ch else None


def edit(db, user_id: int, message_id, text: str):
    """Replace a message's text (a progress line updating in place)."""
    ch = channel_of(db, user_id)
    if ch and message_id:
        _request("PATCH", f"/channels/{ch}/messages/{message_id}", json={"content": text[:LIMIT]})


def send_photo(db, user_id: int, png: bytes, caption: str, markup: dict = None):
    ch = channel_of(db, user_id)
    return post(ch, caption, markup, ("screen.png", png, "image/png")) if ch else None


def send_document(db, user_id: int, data: bytes, filename: str, caption: str = ""):
    ch = channel_of(db, user_id)
    mime = "application/pdf" if filename.endswith(".pdf") else "application/octet-stream"
    return post(ch, caption, None, (filename, data, mime)) if ch else None


def close_cards(db, user_id: int, job, note: str):
    ch = channel_of(db, user_id)
    if not ch:
        return
    for msg_id, text in (job.meta or {}).get(CARDS_META) or []:
        close_message(ch, msg_id, f"{text}\n\n{note}")
    job.meta = {k: v for k, v in (job.meta or {}).items() if k != CARDS_META}
    db.commit()


def notify_outcome(db, user_id: int, job, outcome: str, notes: str = None):
    if outcome == "submitted":
        close_cards(db, user_id, job, "✅ Submitted")
    if outcome in tg.NOTIFY:
        send(db, user_id, f"{tg.NOTIFY[outcome]}: #{job.id} {job.company} · {job.title}" + (f"\n{notes}" if notes else ""))


def send_card(db, user, channel_id: str, job, rv):
    """The resume PDF and the card with Approve / Skip / Edit."""
    m = _m()
    changes = tg.changes_summary(rv.content_md, m.get_master_resume(db, user.id), rv.critic_notes)
    text = tg.card_text(job, rv.version, changes)
    pdf = rv.pdf_path and os.path.exists(rv.pdf_path) and open(rv.pdf_path, "rb").read()
    msg = post(channel_id, text, tg.buttons(job.id, job.status),
               (m.resume_filename(job, rv), pdf, "application/pdf") if pdf else None)
    cards = ((job.meta or {}).get(CARDS_META) or []) + ([[msg, text]] if msg else [])
    job.meta = {**(job.meta or {}), SENT_META: rv.version, CARDS_META: cards[-10:]}
    db.commit()


# ── Handlers ──────────────────────────────────────────────────────────────────

def on_message(db, d: dict) -> None:
    """A message in any channel the bot can see. Only the linked user in the linked channel is served."""
    if (d.get("author") or {}).get("bot"):
        return
    m = _m()
    channel, author, text = d["channel_id"], d["author"]["id"], (d.get("content") or "").strip()
    if lm := re.fullmatch(r"!?link\s+(\S+)", text, re.I):
        user = link(db, lm.group(1), channel, author, datetime.utcnow(), d.get("guild_id"))
        post(channel, f"Linked to HireOS ({user.email}). New resumes will arrive here.\n\n{HELP}" if user
             else "That code expired or was already used. Get a new one in HireOS.")
        return
    user = user_for(db, channel, author, d.get("guild_id"))
    if not user:
        return          # someone else, or an unlinked channel: stay silent (the bot may sit in a busy server)
    key = f"dc:{channel}"
    cmd = command(text)
    reply_to = (d.get("message_reference") or {}).get("message_id")
    if cmd and cmd[0] == "review" and cmd[1]:
        jid = cmd[1]
        job = db.query(m.Job).filter_by(id=jid, user_id=user.id).first()
        rv = job and db.query(m.ResumeVersion).filter_by(job_id=jid).order_by(m.ResumeVersion.version.desc()).first()
        if job and job.status not in tg.REVIEW_STATUSES:
            post(channel, f"#{jid} {job.company} is {job.status}, nothing to review.")
        elif rv and rv.pdf_path:
            send_card(db, user, channel, job, rv)
        else:
            post(channel, f"No resume for #{jid}.")
    elif cmd and cmd[0] == "review":
        cards = tg.cards_due(db, user.id, sent_key=SENT_META)
        for job, rv in cards:
            send_card(db, user, channel, job, rv)
        if not cards:
            post(channel, "Nothing waiting for review.")
    elif cmd and cmd[0] == "status":
        counts = dict(db.query(m.Job.status, m.func.count()).filter(m.Job.user_id == user.id).group_by(m.Job.status).all())
        import scheduler
        lines = [f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])] or ["No jobs."]
        post(channel, "\n".join(lines + [scheduler.progress_line(user.id)]).strip())
    elif cmd and cmd[0] == "done":
        tg._edit_waiting.pop(key, None)
        post(channel, "Edit mode closed.")
    elif cmd and cmd[0] == "help":
        post(channel, HELP)
    elif text and key not in tg._editing(key) and (job := tg.open_question(db, user.id, reply_to, key="dmsg")):
        post(channel, job if job == tg.AMBIGUOUS else tg.answer(db, user, job, text, datetime.utcnow()))
    elif text and key in tg._editing(key):
        tg._edit_waiting[key] = (tg._edit_waiting[key][0], time.monotonic())
        on_edit(db, user, channel, tg._edit_waiting[key][0], text)
    elif text:
        _request("POST", f"/channels/{channel}/typing")
        post(channel, on_loop(tg.chat_reply(db, user, text)))
    else:
        post(channel, HELP)


def on_edit(db, user, channel: str, job_id: int, instruction: str):
    m = _m()
    post(channel, "Editing… (about a minute)")
    try:
        on_loop(m.edit_resume(job_id, {"instruction": instruction}, db, user))
    except HTTPException as e:
        if e.status_code == 409:                   # approved meanwhile: edit mode ends
            tg._edit_waiting.pop(f"dc:{channel}", None)
            post(channel, f"Not changed: {e.detail}")
            return
        post(channel, f"Not changed: {e.detail}\n\nSend another change, or done.")
        return
    job = m.get_owned_job(db, job_id, user)
    rv = db.query(m.ResumeVersion).filter_by(job_id=job_id).order_by(m.ResumeVersion.version.desc()).first()
    send_card(db, user, channel, job, rv)
    post(channel, "Send another change, or tap Approve.")


def on_button(db, d: dict) -> None:
    """A button press (already acknowledged). Same actions as the Telegram buttons."""
    m = _m()
    channel, msg = d["channel_id"], d.get("message") or {}
    author = ((d.get("member") or {}).get("user") or d.get("user") or {}).get("id")
    user = user_for(db, channel, author, d.get("guild_id"))
    action, _, jid = (d.get("data") or {}).get("custom_id", "").partition(":")
    if not user or not jid.isdigit():
        return
    try:
        job = m.get_owned_job(db, int(jid), user)
    except HTTPException:
        post(channel, f"Job #{jid} not found.")
        return
    done, prompt = tg.button_action(db, user, job, action, f"dc:{channel}")
    if prompt:
        post(channel, prompt)
    if done:
        close_message(channel, msg["id"], f"{msg.get('content', '')}\n\n{done}")


def _handle(kind: str, d: dict):
    """Runs in a worker thread: handlers use sync DB and REST calls."""
    db = _m().SessionLocal()
    try:
        on_message(db, d) if kind == "message" else on_button(db, d)
    except Exception:
        logger.exception(f"[discord] {kind} failed")
    finally:
        db.close()


def push_new():
    """Send resumes created since the channel was linked (older backlog comes via `review`)."""
    m = _m()
    db = m.SessionLocal()
    try:
        for row in db.query(m.Settings).filter(m.Settings.key == CHANNEL_KEY).all():
            linked = m._setting(db, row.user_id, LINKED_AT_KEY)
            since = datetime.fromisoformat(linked) if linked else datetime.utcnow()
            user = db.query(m.User).get(row.user_id)
            for job, rv in tg.cards_due(db, row.user_id, since=since, sent_key=SENT_META):
                send_card(db, user, row.value, job, rv)
    finally:
        db.close()


# ── Gateway ───────────────────────────────────────────────────────────────────

class Fatal(Exception):
    """Discord refused the connection for good (bad token, intents): retrying won't help."""


async def _ack(d: dict):
    """Button presses must be acknowledged within 3 s; the work happens after."""
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(f"{API}/interactions/{d['id']}/{d['token']}/callback", json={"type": 6})


async def _session():
    import websockets
    async with websockets.connect(GATEWAY, max_size=2 ** 23) as ws:
        hello = json.loads(await ws.recv())
        every = hello["d"]["heartbeat_interval"] / 1000
        seq = None

        async def beat():
            while True:
                await asyncio.sleep(every)
                await ws.send(json.dumps({"op": 1, "d": seq}))

        hb = asyncio.create_task(beat())
        try:
            await ws.send(json.dumps({"op": 2, "d": {"token": TOKEN, "intents": INTENTS,
                                                     "properties": {"os": "linux", "browser": "hireos", "device": "hireos"}}}))
            async for raw in ws:
                p = json.loads(raw)
                seq = p.get("s") if p.get("s") is not None else seq
                if p["op"] == 1:
                    await ws.send(json.dumps({"op": 1, "d": seq}))
                elif p["op"] in (7, 9):        # reconnect / invalid session: start a fresh session
                    return
                elif p["op"] == 0 and p["t"] == "READY":
                    logger.info(f"[discord] connected as {p['d']['user']['username']}")
                elif p["op"] == 0 and p["t"] == "MESSAGE_CREATE":
                    asyncio.create_task(asyncio.to_thread(_handle, "message", p["d"]))
                elif p["op"] == 0 and p["t"] == "INTERACTION_CREATE" and p["d"].get("type") == 3:
                    await _ack(p["d"])
                    asyncio.create_task(asyncio.to_thread(_handle, "button", p["d"]))
        except websockets.ConnectionClosed:
            pass
        finally:
            hb.cancel()
        if ws.close_code in FATAL_CLOSE:   # a clean close can carry the refusal too
            raise Fatal(FATAL_CLOSE[ws.close_code])


async def _pusher():
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(push_new)
        except Exception as e:
            logger.warning(f"[discord] push failed: {e!r}")


async def run():
    """Gateway loop, reconnecting after drops. ponytail: one process; two would answer twice."""
    global LOOP
    LOOP = asyncio.get_running_loop()
    pusher = asyncio.create_task(_pusher())
    try:
        while True:
            try:
                await _session()
            except Fatal as e:
                logger.error(f"[discord] stopped: {e}")
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[discord] gateway error: {e!r}")
            await asyncio.sleep(5)
    finally:
        pusher.cancel()

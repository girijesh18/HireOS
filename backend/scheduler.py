"""Background jobs inside HireOS (they used to run in OpenClaw's apply-coach plugin).

Every TICK seconds:
  - HireOS errors, local LLM down/up, missing or failed DB backups -> admins' Telegram
  - apply claims with no result (the run died), jobs waiting on the user for a day -> owner's Telegram
  - every MAIL_EVERY: Gmail application emails -> job statuses + one Telegram line each
  - weekdays at DISCOVERY_HOURS (user's timezone): a discovery run (discovery.py) for each user
    with discovery targets; its errors go to that user
After every tick: a ping to HEALTHCHECK_URL (a dead-man's switch such as healthchecks.io), so a HireOS
that is down, stuck or on a machine that is off is alerted from outside.
Runs only when SCHEDULER=on (the local instance): the Gmail mailbox, the LLM and the backups
are this machine's. Every alert is deduplicated, so a stuck problem doesn't spam.
"""
import asyncio
import imaplib
from concurrent.futures import ThreadPoolExecutor
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Callable, Dict, Optional

import httpx
from loguru import logger

TICK = 120
MAIL_EVERY = 15 * 60
HOUR = 3600
PROGRESS_EVERY = 600       # seconds between "still working" messages during a scoring or discovery run
JOB_SECONDS = 210          # one job scored + resume + review + ATS on OpenRouter qwen (measured ~3-4 min)
DISCOVERY_HOURS = (8, 16)
DISCOVERY_LAST_KEY = "discovery_last_run"     # "YYYY-MM-DD HH" local: one run per slot, across restarts
MAIL_ICON = {"received": "📨", "rejected": "❌", "interview": "🎤", "assessment": "📝", "offer": "🎉"}


def enabled() -> bool:
    return os.getenv("SCHEDULER", "").lower() == "on" and "test" not in os.getenv("DATABASE_URL", "")


class Alerts:
    """At most one message per key per interval; health checks alert after `after` failures
    in a row and say so once when they recover. Process memory: a restart may repeat one."""
    def __init__(self, send: Callable, now: Callable = time.time):
        self.send, self.now = send, now
        self.last: Dict[str, float] = {}
        self.fails: Dict[str, int] = {}
        self.down: set = set()

    def notify(self, key: str, text: str, every: float = HOUR, retry_job_id: int = None) -> bool:
        t = self.last.get(key)
        if t is not None and self.now() - t < every:
            return False
        self.last[key] = self.now()
        self.send(text, retry_job_id)
        return True

    def check(self, key: str, ok: bool, down_text: str, up_text: str, after: int = 2):
        if ok:
            self.fails[key] = 0
            if key in self.down:
                self.down.discard(key)
                self.last.pop(key, None)
                self.send(up_text, None)
            return
        self.fails[key] = self.fails.get(key, 0) + 1
        if self.fails[key] >= after:
            self.down.add(key)
            self.notify(key, down_text, 3 * HOUR)


def mail_update_text(u: dict) -> str:
    job = f"#{u['job_id']} {u['company']} · {u['title']}" if u.get("job_id") else "No matching job in HireOS"
    change = ""
    if u.get("job_id"):
        if u.get("new") != u.get("old"):
            change = f" ({u['old']} → {u['new']})"
        elif u.get("title_matched") is False:
            change = " (company matches, title doesn't — status not changed)"
    return f"{MAIL_ICON.get(u['kind'], '📧')} {job} — {u['kind']}{change}\n“{u['subject']}” from {u['from']}"[:1900]


def discovery_slot(now_utc: datetime, tz: str) -> Optional[str]:
    """The current discovery slot ("YYYY-MM-DD HH" in the user's timezone), or None outside them."""
    try:
        local = now_utc.astimezone(ZoneInfo(tz))
    except (KeyError, ValueError):
        local = now_utc.astimezone(ZoneInfo("America/Toronto"))
    return local.strftime("%Y-%m-%d %H") if local.weekday() < 5 and local.hour in DISCOVERY_HOURS else None


def minutes_text(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} min" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def progress_text(p: dict, now: float) -> str:
    """One line on a run in progress: how far, how many good fits, roughly how long is left."""
    if not p["total"]:
        return f"⏳ {p['what']}: finding new jobs (started {minutes_text(now - p['started'])} ago)."
    left = (now - p["started"]) / max(p["done"], 1) * (p["total"] - p["done"])
    return (f"⏳ {p['what']}: {p['done']}/{p['total']} jobs done, {p['fits']} good fit(s) so far "
            f"(resume ready) — about {minutes_text(left)} left.")


def progress_line(uid: int) -> str:
    """The user's run in progress as one line, or "" (no run, or no scheduler on this instance)."""
    s = CURRENT
    return progress_text(s.progress[uid], s.now()) if s and uid in s.progress else ""


def discovery_done_text(summary: dict) -> str:
    """Sent after every run: what it found and scored, what is left, what went wrong."""
    outcomes = [o for _, o in summary["prepared"]]
    text = (f"{'🟠' if summary['errors'] else '✅'} Discovery done: {summary['added']} new, {len(outcomes)} scored — "
            f"{outcomes.count('resume ready')} resume(s) ready to review, {outcomes.count('below min score')} below your min score"
            + (f", {summary['waiting']} still unscored (next run, or ask me to score them)" if summary.get("waiting") else "") + ".")
    errs = summary["errors"]
    if errs:
        text += f"\n{len(errs)} problem(s):\n" + "\n".join(f"• {e[:200]}" for e in errs[:8]) + \
            (f"\n… and {len(errs) - 8} more" if len(errs) > 8 else "")
    return text


def llm_up(url: str) -> bool:
    base = url.rstrip("/").removesuffix("/v1")
    try:
        return httpx.get(f"{base}/health", timeout=10).status_code == 200
    except httpx.HTTPError:
        return False


def mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


class Scheduler:
    def __init__(self, main, now: Callable = time.time):
        self.m = main                              # the main module: DB, settings, Telegram
        self.now = now
        self.started = now()
        self.since = datetime.now(timezone.utc).isoformat()
        self.last_mail = 0.0
        self.ops = Path(os.getenv("OPS_DIR") or Path(os.getenv("OUTPUT_DIR", "./output")) / "ops")
        self.backup_alert_seen = mtime(self.ops / "backup.alert")
        self.admin_alerts = Alerts(self._to_admins, now)
        self.user_alerts: Dict[int, Alerts] = {}
        self.discovering: set = set()
        self.queued_scoring: dict = {}     # user -> (job ids, then): a score request made during a run
        self.progress: dict = {}           # user -> the run in progress (progress_text), for updates and "status"
        # Telegram/Discord calls are blocking HTTP (10 s timeouts): they go to one thread, in order,
        # so a slow chat service never stalls the API's event loop. tick() waits for them, off the loop.
        self.outbox = ThreadPoolExecutor(1, thread_name_prefix="scheduler-send")
        self.sending: list = []

    # ── where messages go ──
    def _linked(self, db, user_id: int) -> bool:
        return bool(self.m._setting(db, user_id, self.m.telegram_bot.CHAT_KEY)
                    or self.m._setting(db, user_id, self.m.discord_bot.CHANNEL_KEY))

    def _admins(self, db) -> list:
        users = db.query(self.m.User).filter(self.m.User.email.isnot(None)).all()
        return [u.id for u in users if self.m.is_admin(u) and self._linked(db, u.id)]

    def _send(self, user_id: int, text: str, retry_job_id: Optional[int]):
        logger.info(f"[scheduler] -> user {user_id}: {text[:120]!r}")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:                       # already off the event loop
            return self._deliver(user_id, text, retry_job_id)
        self.sending.append(loop.run_in_executor(self.outbox, self._deliver, user_id, text, retry_job_id))

    def _deliver(self, user_id: int, text: str, retry_job_id: Optional[int]):
        db = self.m.SessionLocal()
        try:
            markup = self.m.telegram_bot.retry_button(retry_job_id) if retry_job_id else None
            self.m._chat_send(db, user_id, text[:3900], markup)
        finally:
            db.close()

    def _to_admins(self, text: str, retry_job_id: Optional[int]):
        db = self.m.SessionLocal()
        try:
            admins = self._admins(db)
        finally:
            db.close()
        if not admins:
            logger.warning(f"[scheduler] no admin with Telegram linked; alert dropped: {text[:200]}")
        for uid in admins:
            self._send(uid, text, retry_job_id)

    def _user(self, user_id: int) -> Alerts:
        if user_id not in self.user_alerts:
            self.user_alerts[user_id] = Alerts(lambda t, r: self._send(user_id, t, r), self.now)
        return self.user_alerts[user_id]

    # ── checks ──
    def check_errors(self):
        errors = [e for e in list(self.m.OPS_ERRORS) if e["at"] > self.since]
        if errors:
            self.since = errors[-1]["at"]
        for e in errors:
            if e["where"].startswith("scheduler:"):   # its own send failures: the log has them
                continue
            self.admin_alerts.notify(f"err:{e['where']}:{e['message'][:80]}", f"🔴 HireOS error ({e['where']}): {e['message']}")

    def check_llm(self):
        url = os.getenv("LOCAL_LLM_URL")
        if not url:
            return
        self.admin_alerts.check("llm", llm_up(url),
                                "🔴 Local LLM (llama.cpp :8080) is not answering — HireOS can't score, tailor or answer. Start it: `~/llm/llm.sh q27b`.",
                                "✅ Local LLM is answering again.")

    def check_backups(self):
        if not self.ops.is_dir():                  # no backups on this machine
            return
        ok = mtime(self.ops / "backup.ok")
        if self.now() - self.started > 2 * HOUR and self.now() - ok > 2 * HOUR:
            age = f"{round((self.now() - ok) / HOUR)}h" if ok else "ever"
            self.admin_alerts.notify("backup-stale", f"🔴 No HireOS database backup for {age} — is the hourly cron installed? (scripts/backup_db.sh)", 6 * HOUR)
        bad = mtime(self.ops / "backup.alert")
        if bad > self.backup_alert_seen:
            self.backup_alert_seen = bad
            last = (self.ops / "backup.alert").read_text().strip().split("\n")[-1]
            self.admin_alerts.notify(f"backup-alert:{bad}", f"🔴 {last}")

    def check_stuck(self, db):
        now = datetime.utcnow()
        keys = (self.m.telegram_bot.CHAT_KEY, self.m.discord_bot.CHANNEL_KEY)
        for (uid,) in db.query(self.m.Settings.user_id).filter(self.m.Settings.key.in_(keys)).distinct().all():
            stale, waiting = self.m.stuck_jobs(db, uid, now)
            a = self._user(uid)
            for w in waiting:
                a.notify(f"wait:{w['job_id']}", f"⏸️ #{w['job_id']} {w['company']} has been waiting for you since {w['since']} ({w['reason']}). "
                         "Answer its message above, or tap Retry to start it over.", 24 * HOUR, w["job_id"])
            for s in stale:
                a.notify(f"stale:{s['job_id']}", f"🟠 #{s['job_id']} {s['company']} was claimed at {s['claimed_at']} and never got a result — the apply run died.",
                         6 * HOUR, s["job_id"])

    async def scan_mail(self, db):
        uid = os.getenv("GMAIL_USER_ID")
        if not uid or self.now() - self.last_mail < MAIL_EVERY:
            return
        uid = int(uid)
        self.last_mail = self.now()
        days = 3 if self.m._setting(db, uid, "mail_updates_seen") else 30   # first scan looks back a month
        try:
            out = await self.m.scan_mail_updates(db, uid, days)
        except (ValueError, imaplib.IMAP4.error, OSError) as e:
            self._user(uid).notify("mail-updates", f"🟠 Gmail status scan failed: {str(e)[:200]}", 6 * HOUR)
            return
        for u in out["updates"]:
            self._user(uid).send(mail_update_text(u), None)
        logger.info(f"[scheduler] mail scan: checked {out['checked']}, updates {len(out['updates'])}")

    def check_discovery(self, db, now_utc: datetime = None):
        import discovery
        now_utc = now_utc or datetime.now(timezone.utc)
        for (uid,) in db.query(self.m.Settings.user_id).filter(self.m.Settings.key == discovery.TARGETS_KEY).all():
            slot = discovery_slot(now_utc, self.m._apply_config(db, uid)["timezone"])
            if not slot or uid in self.discovering or self.m._setting(db, uid, DISCOVERY_LAST_KEY) == slot:
                continue
            self.m._put_setting(db, uid, DISCOVERY_LAST_KEY, slot)     # claimed before it starts: never twice a slot
            db.commit()
            self.start_discovery(uid)

    def start_discovery(self, uid: int) -> bool:
        """Run discovery for one user in the background; False if one is already running."""
        if uid in self.discovering:
            return False
        self.discovering.add(uid)
        asyncio.get_running_loop().create_task(self._discover(uid))
        return True

    def start_discovery_threadsafe(self, uid: int) -> bool:
        """start_discovery from a sync endpoint (thread pool)."""
        if uid in self.discovering:
            return False
        self.loop.call_soon_threadsafe(self.start_discovery, uid)
        return True

    def start_scoring(self, uid: int, job_ids: list, then) -> str:
        """Score these jobs in the background, then send the user then(db). While a discovery or
        scoring run is going for them it waits for that one ("queued"): both would prepare the same jobs."""
        if uid in self.discovering:
            self.queued_scoring[uid] = (job_ids, then)
            return "queued"
        self.discovering.add(uid)
        asyncio.get_running_loop().create_task(self._score(uid, job_ids, then))
        return "started"

    def _tracker(self, uid: int, what: str):
        """progress(done, total, outcome) for a run: keeps self.progress current and sends an update
        every PROGRESS_EVERY seconds (not after the last job: the results message follows)."""
        p = self.progress[uid] = {"what": what, "done": 0, "total": 0, "fits": 0,
                                  "started": self.now(), "sent": self.now()}

        def update(done, total, outcome):
            p.update(done=done, total=total, fits=p["fits"] + (outcome == "resume ready"))
            if done < total and self.now() - p["sent"] >= PROGRESS_EVERY:
                p["sent"] = self.now()
                self._send(uid, progress_text(p, self.now()), None)
        return update

    async def _score(self, uid: int, job_ids: list, then):
        import discovery
        try:
            s = await discovery.score(self.m, uid, job_ids, self._tracker(uid, "Scoring"))
            db = self.m.SessionLocal()
            try:
                text = then(db)
            finally:
                db.close()
            if s["errors"]:
                text += f"\n⚠️ {len(s['errors'])} couldn't be scored: " + "; ".join(s["errors"][:3])
            self._send(uid, text, None)
        except Exception as e:
            logger.warning(f"[scheduler] scoring user {uid} failed: {e!r}")
            self._send(uid, f"🔴 Scoring failed: {str(e)[:300]}", None)
        finally:
            self._finished(uid)

    def _finished(self, uid: int):
        self.discovering.discard(uid)
        self.progress.pop(uid, None)
        if uid in self.queued_scoring:
            self.start_scoring(uid, *self.queued_scoring.pop(uid))

    async def _discover(self, uid: int):
        import discovery
        try:
            self._send(uid, "🔎 Discovery run started: new postings first, then scoring. "
                            f"Progress here every {PROGRESS_EVERY // 60} min; say status any time.", None)
            s = await discovery.run(self.m, uid, progress=self._tracker(uid, "Discovery"))
            logger.info(f"[scheduler] discovery user {uid}: added {s['added']}, prepared {s['prepared']}, "
                        f"waiting {s['waiting']}, errors {len(s['errors'])}")
            self._send(uid, discovery_done_text(s), None)
        except Exception as e:
            logger.warning(f"[scheduler] discovery user {uid} failed: {e!r}")
            self._user(uid).send(f"🔴 Discovery run failed: {str(e)[:300]}", None)
        finally:
            self._finished(uid)

    async def tick(self):
        self.check_errors()
        await asyncio.to_thread(self.check_llm)
        self.check_backups()
        db = self.m.SessionLocal()
        try:
            self.check_stuck(db)
            self.check_discovery(db)
            await self.scan_mail(db)
        finally:
            db.close()
            sending, self.sending = self.sending, []
            await asyncio.gather(*sending, return_exceptions=True)


def ping(ok: bool):
    """Tell the dead-man's switch this tick ran (or failed). No URL set: nothing to do."""
    url = os.getenv("HEALTHCHECK_URL")
    if not url:
        return
    try:
        httpx.get(url.rstrip("/") + ("" if ok else "/fail"), timeout=10)
    except httpx.HTTPError as e:                   # the switch alerts on missing pings anyway
        logger.warning(f"[scheduler] healthcheck ping failed: {e!r}")


CURRENT: Optional[Scheduler] = None             # the running instance, for the manual-run endpoint


async def run(main):
    global CURRENT
    s = CURRENT = Scheduler(main)
    s.loop = asyncio.get_running_loop()
    await asyncio.sleep(30)                        # let the API finish starting
    while True:
        ok = True
        try:
            await s.tick()
        except Exception as e:                     # one bad tick must not end the loop
            ok = False
            logger.warning(f"[scheduler] tick failed: {e!r}")
        await asyncio.to_thread(ping, ok)
        await asyncio.sleep(TICK)

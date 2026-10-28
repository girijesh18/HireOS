"""Background jobs (scheduler.py): alert dedupe, health checks, stuck jobs, backups, Gmail updates.

Run: cd backend && python test_scheduler.py
"""
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///./test_scheduler.db"
os.environ["LOG_DIR"] = "./logs"
os.environ["ADMIN_EMAILS"] = "admin@x.com"

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import main
import scheduler
import telegram_bot as tg
from database import Base, Job, SessionLocal, User, engine, init_db


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_alerts_dedupe_and_recovery():
    sent, clock = [], Clock()
    a = scheduler.Alerts(lambda t, r: sent.append(t), clock)
    assert a.notify("k", "one") and not a.notify("k", "again")          # same key within the hour
    clock.t += 3601
    assert a.notify("k", "later")
    a.check("llm", False, "down", "up")
    assert sent[-1] == "later"                                           # one failure: no alert yet
    a.check("llm", False, "down", "up")
    a.check("llm", False, "down", "up")
    assert sent.count("down") == 1                                       # alerted once while down
    a.check("llm", True, "down", "up")
    a.check("llm", True, "down", "up")
    assert sent.count("up") == 1


def test_mail_update_text():
    u = {"kind": "interview", "job_id": 7, "company": "TD", "title": "DS", "old": "applied", "new": "interview",
         "subject": "Next steps", "from": "hr@td.com"}
    assert scheduler.mail_update_text(u) == "🎤 #7 TD · DS — interview (applied → interview)\n“Next steps” from hr@td.com"
    assert "status not changed" in scheduler.mail_update_text({**u, "new": "applied", "title_matched": False})
    assert scheduler.mail_update_text({**u, "job_id": None}).startswith("🎤 No matching job in HireOS — interview\n")


def test_tick():
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    admin, user, unlinked = (User(email=e, password_hash="x") for e in ("admin@x.com", "u@x.com", "n@x.com"))
    db.add_all([admin, user, unlinked])
    db.commit()
    for u, chat in ((admin, "1"), (user, "2")):
        main._put_setting(db, u.id, tg.CHAT_KEY, chat)
    old = (datetime.utcnow() - timedelta(hours=30)).isoformat()
    stale = Job(user_id=user.id, company="Dead", title="DS", status="applying", meta={"apply_claimed_at": old})
    wait = Job(user_id=user.id, company="Wait", title="DS", status="waiting_human", meta={"waiting_since": old, "waiting_reason": "Salary?"})
    fresh = Job(user_id=user.id, company="Busy", title="DS", status="applying", meta={"apply_claimed_at": datetime.utcnow().isoformat()})
    other = Job(user_id=unlinked.id, company="Nobody", title="DS", status="applying", meta={"apply_claimed_at": old})
    db.add_all([stale, wait, fresh, other])
    db.commit()

    sent = []
    real = (tg.send, main.scan_mail_updates)
    tg.send = lambda db, uid, text, markup=None: sent.append((uid, text, markup))
    calls = []

    async def fake_scan(db, uid, days):
        calls.append((uid, days))
        return {"checked": 2, "updates": [{"kind": "rejected", "job_id": stale.id, "company": "Dead", "title": "DS",
                                           "old": "applying", "new": "rejected", "subject": "Sorry", "from": "hr"}]}
    main.scan_mail_updates = fake_scan
    ops = Path(tempfile.mkdtemp())
    os.environ.update(OPS_DIR=str(ops), GMAIL_USER_ID=str(user.id), LOCAL_LLM_URL="http://127.0.0.1:9/v1")  # nothing listens on :9
    clock = Clock()
    try:
        s = scheduler.Scheduler(main, clock)
        main.OPS_ERRORS.append({"at": datetime.now(timezone.utc).isoformat(), "where": "main:prepare", "message": "boom"})
        main.OPS_ERRORS.append({"at": datetime.now(timezone.utc).isoformat(), "where": "scheduler:tick", "message": "own"})
        asyncio.run(s.tick())
        texts = [(uid, t) for uid, t, _ in sent]
        assert [uid for uid, t in texts if "boom" in t] == [admin.id]                # admins only
        assert not any("own" in t for _, t in texts)                                 # its own errors aren't re-sent
        assert not any("LLM" in t for _, t in texts)                                 # one failed check isn't an alert
        mine = [t for uid, t in texts if uid == user.id]
        assert any(t.startswith(f"🟠 #{stale.id} Dead was claimed") for t in mine), mine
        assert any(t.startswith(f"⏸️ #{wait.id} Wait has been waiting") for t in mine)
        assert not any("Busy" in t or "Nobody" in t for _, t in texts)                # fresh claim; unlinked user
        assert [m for uid, t, m in sent if "Dead was claimed" in t][0] == tg.retry_button(stale.id)
        assert calls == [(user.id, 30)]                                              # first scan: a month back
        assert f"❌ #{stale.id} Dead · DS — rejected (applying → rejected)\n“Sorry” from hr" in mine
        assert not any("backup" in t for _, t in texts)                              # just started: no stale-backup alert

        n = len(sent)
        clock.t += 300
        asyncio.run(s.tick())
        assert [t for _, t, _ in sent[n:]] == ["🔴 Local LLM (llama.cpp :8080) is not answering — HireOS can't score, tailor or answer. Start it: `~/llm/llm.sh q27b`."]
        assert calls == [(user.id, 30)]                                              # mail waits 15 min

        clock.t += 3 * 3600
        (ops / "backup.alert").write_text("old\n2026 HireOS backup FAILED: disk full\n")
        os.utime(ops / "backup.alert", (clock.t + 10, clock.t + 10))
        n = len(sent)
        asyncio.run(s.tick())
        new = [t for _, t, _ in sent[n:]]
        assert "🔴 2026 HireOS backup FAILED: disk full" in new, new
        assert any(t.startswith("🔴 No HireOS database backup for ever") for t in new)
        assert len(calls) == 2
    finally:
        tg.send, main.scan_mail_updates = real
        for k in ("OPS_DIR", "GMAIL_USER_ID", "LOCAL_LLM_URL"):
            os.environ.pop(k)
        db.close()


def test_discovery_slots():
    toronto = lambda y, mo, d, h: datetime(y, mo, d, h, 5, tzinfo=timezone.utc) + timedelta(hours=4)   # EDT = UTC-4
    assert scheduler.discovery_slot(toronto(2026, 10, 27, 8), "America/Toronto") == "2026-10-27 08"   # Tuesday 08:05
    assert scheduler.discovery_slot(toronto(2026, 10, 27, 16), "America/Toronto") == "2026-10-27 16"
    assert scheduler.discovery_slot(toronto(2026, 10, 27, 9), "America/Toronto") is None
    assert scheduler.discovery_slot(toronto(2026, 10, 25, 8), "America/Toronto") is None               # Sunday
    assert scheduler.discovery_slot(toronto(2026, 10, 27, 8), "Not/AZone") == "2026-10-27 08"         # bad tz: Toronto

    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    u, other = User(email="d@x.com", password_hash="x"), User(email="e@x.com", password_hash="x")
    db.add_all([u, other])
    db.commit()
    main._put_setting(db, u.id, "discovery_targets", "{}")
    db.commit()
    started = []
    s = scheduler.Scheduler(main)
    s.start_discovery = lambda uid: started.append(uid) or True
    at = toronto(2026, 10, 27, 8)
    s.check_discovery(db, at)
    s.check_discovery(db, at + timedelta(minutes=20))                 # same slot: not again
    assert started == [u.id]                                          # only users with targets
    s.check_discovery(SessionLocal(), at + timedelta(minutes=20))     # survives a restart (stored)
    assert started == [u.id]
    s.check_discovery(db, toronto(2026, 10, 27, 16))
    assert started == [u.id, u.id]
    db.close()


def test_discovery_run_reports_errors():
    sent = []
    s = scheduler.Scheduler(main)
    s._user = lambda uid: type("A", (), {"send": lambda self, t, r: sent.append((uid, t))})()
    import discovery
    real = discovery.run

    async def fake(m, uid):
        return {"added": 2, "prepared": [(1, "resume ready")], "waiting": 0, "errors": [f"e{i}" for i in range(10)]}

    async def boom(m, uid):
        raise RuntimeError("db locked")

    async def go(fn):
        discovery.run = fn
        assert s.start_discovery(7) and not s.start_discovery(7)      # one run per user at a time
        while 7 in s.discovering:
            await asyncio.sleep(0.01)
    try:
        asyncio.run(go(fake))
        asyncio.run(go(boom))
    finally:
        discovery.run = real
    assert sent[0] == (7, "🟠 Discovery run: 2 new, 1 prepared, 10 problem(s):\n• e0\n• e1\n• e2\n• e3\n• e4\n• e5\n• e6\n• e7\n… and 2 more")
    assert sent[1] == (7, "🔴 Discovery run failed: db locked")


def test_off_by_default():
    assert not scheduler.enabled()                     # tests and Fly: never runs


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_healthcheck_ping(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler.httpx, "get", lambda url, timeout: calls.append(url))
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    scheduler.ping(True)
    assert calls == []                                       # unset: no request
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc.example/abc/")
    scheduler.ping(True)
    scheduler.ping(False)
    assert calls == ["https://hc.example/abc", "https://hc.example/abc/fail"]

    def down(url, timeout):
        raise scheduler.httpx.ConnectError("no route")
    monkeypatch.setattr(scheduler.httpx, "get", down)
    scheduler.ping(True)                                     # a failed ping never raises


def test_sends_never_block_the_event_loop():
    """A slow Telegram send runs in the outbox thread: the loop keeps serving meanwhile."""
    import threading
    import time as t
    s = scheduler.Scheduler(main)
    loop_thread, send_threads = threading.get_ident(), []

    def slow(user_id, text, retry_job_id):
        send_threads.append(threading.get_ident())
        t.sleep(0.3)
    s._deliver = slow

    async def go():
        ticks = 0

        async def heartbeat():                        # stands in for API requests
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1
        hb = asyncio.create_task(heartbeat())
        s._send(1, "a", None)
        s._send(1, "b", None)
        await asyncio.gather(*s.sending)
        hb.cancel()
        return ticks
    ticks = asyncio.run(go())
    assert len(send_threads) == 2 and loop_thread not in send_threads
    assert ticks >= 15, ticks                            # ~0.6 s of sends, the loop kept running

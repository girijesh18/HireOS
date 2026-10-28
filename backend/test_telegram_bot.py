"""Telegram bot: link codes, change summaries, which cards are due.

Run: cd backend && python test_telegram_bot.py
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_telegram.db"
os.environ["LOG_DIR"] = "./logs"

from datetime import datetime, timedelta

import main
import telegram_bot as tg
from database import Base, Job, ResumeVersion, SessionLocal, User, engine, init_db


def test_link_code():
    now = datetime(2026, 10, 26, 12, 0)
    code, stored = tg.new_link_code(now)
    assert tg.code_matches(stored, code, now + timedelta(minutes=14))
    assert not tg.code_matches(stored, code, now + timedelta(minutes=16))      # expired
    assert not tg.code_matches(stored, code + "x", now)                         # wrong code
    assert not tg.code_matches("garbage", code, now)


def test_changes_summary():
    master = "# Name\n## Summary\nLead data scientist.\n- **Churn:** Built churn model saving $2M in 2021\n- Led team of 4"
    ok = "# Name\nme@x.com\n## Summary\nLead data scientist. Builds things.\n- **Churn:** Saved **$2M** with a churn model\n- **Team:** Led 4 analysts"
    out = tg.changes_summary(ok, master)
    assert out == ["Opens: Lead data scientist", "Leads with: Churn · Team",
                   "Figures: all 2 match your master resume"], out
    invented = tg.changes_summary(ok.replace("$2M", "$10M"), master)
    assert invented[-1] == "Check figures not in master: 10m", invented               # invented figure flagged
    edited = tg.changes_summary("x", master, {"edits": [{"find": "**Tableau**", "replace": "Power BI"}]})
    assert edited == ["Changed:", "• Tableau\n  → Power BI"]


def test_resume_filename():
    job = Job(company="Sun Life Financial, Inc.")
    rv = ResumeVersion(content_md="# Girijesh Singh\nme@x.com\n## Summary")
    assert main.resume_filename(job, rv) == "girijesh_singh_sun_life_financial_inc_resume.pdf"
    assert main.resume_filename(Job(company=None), ResumeVersion(content_md="")) == "resume.pdf"


def test_cards_due_and_link():
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    u, other = User(email="a@b.com", password_hash="x"), User(email="c@d.com", password_hash="x")
    db.add_all([u, other])
    db.commit()
    old, new, applied = (Job(user_id=u.id, company=c, title="DS", status=s, match_score=sc)
                         for c, s, sc in (("Old", "pending", 90), ("New", "pending", 80), ("Done", "applied", 95)))
    theirs = Job(user_id=other.id, company="Theirs", title="DS", status="pending", match_score=99)
    db.add_all([old, new, applied, theirs])
    db.commit()
    cutoff = datetime.utcnow()
    for job, at in ((old, cutoff - timedelta(days=1)), (new, cutoff + timedelta(seconds=1)),
                    (applied, cutoff + timedelta(seconds=1)), (theirs, cutoff + timedelta(seconds=1))):
        db.add(ResumeVersion(job_id=job.id, user_id=job.user_id, version=1, content_md="- x", pdf_path="/tmp/x.pdf", created_at=at))
    db.commit()

    assert [j.company for j, _ in tg.cards_due(db, u.id)] == ["Old", "New"]            # best score first, own jobs only
    assert [j.company for j, _ in tg.cards_due(db, u.id, since=cutoff)] == ["New"]     # backlog excluded
    new.meta = {tg.SENT_META: 1}
    db.commit()
    assert [j.company for j, _ in tg.cards_due(db, u.id, since=cutoff)] == []          # already sent

    code, stored = tg.new_link_code(datetime.utcnow())
    main._put_setting(db, u.id, tg.CODE_KEY, stored)
    db.commit()
    assert tg.link_chat(db, "wrong", 42, datetime.utcnow()) is None
    assert tg.link_chat(db, code, 42, datetime.utcnow()).id == u.id
    assert tg._user_for_chat(db, 42).id == u.id
    assert tg.link_chat(db, code, 42, datetime.utcnow()) is None                        # single use
    db.close()


def test_ask_answer_flow():
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    u = User(email="a@b.com", password_hash="x")
    db.add(u)
    db.commit()
    jobs = [Job(user_id=u.id, company=f"C{i}", title="DS", status="applying") for i in range(3)]
    db.add_all(jobs)
    db.commit()
    q, late, cap = jobs
    main.apply_wait(q.id, {"reason": "Salary expectation?", "kind": "question"}, db, u)
    assert tg.open_question(db, u.id).id == q.id                      # only open question: plain text answers it
    now = datetime.utcnow()
    assert tg.answer(db, u, q, "120k", now).endswith("continuing.")
    assert main.apply_ask_status(q.id, db, u) == {"job_id": q.id, "status": "applying", "kind": "question", "answer": "120k"}
    assert {"q": "Salary expectation?", "a": "120k"} in main._application_profile(db, u.id)["custom_qa"]

    main.apply_wait(late.id, {"reason": "Notice period?", "kind": "question", "minutes": 1}, db, u)
    assert "restart" in tg.answer(db, u, late, "2 weeks", now + timedelta(minutes=5))
    assert db.get(Job, late.id).status == "approved"                  # past the deadline: fresh run

    main.apply_wait(cap.id, {"reason": "CAPTCHA", "kind": "captcha", "url": "https://screen"}, db, u)
    text, markup = tg.ask_message(cap, "captcha", "CAPTCHA", "https://screen")
    assert markup["inline_keyboard"][0][0]["url"] == "https://screen"
    assert tg.open_question(db, u.id) is None                         # a CAPTCHA isn't answered by text
    tg.answer(db, u, cap, "skip", now)
    assert db.get(Job, cap.id).status == "pending"
    assert "isn't waiting" in tg.answer(db, u, cap, "done", now)
    db.close()


def test_security_code_reply():
    """D: the emailed code, replied as plain text, goes to the waiting run; never into the profile."""
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    u = User(email="a@b.com", password_hash="x")
    db.add(u)
    db.commit()
    job = Job(user_id=u.id, company="Movable Ink", title="MLE", status="applying")
    db.add(job)
    db.commit()
    reason = "After Submit, Greenhouse emailed you a security code — nothing is sent until it's entered."
    main.apply_wait(job.id, {"reason": reason, "kind": "code", "url": "https://screen"}, db, u)
    text, markup = tg.ask_message(job, "code", reason)
    assert "Reply to this message with the code" in text and markup.get("force_reply")
    assert tg.open_question(db, u.id).id == job.id                    # a plain message (no reply-to) answers it
    assert tg.answer(db, u, job, "ABCD1234", datetime.utcnow()).endswith("continuing.")
    assert main.apply_ask_status(job.id, db, u)["answer"] == "ABCD1234"
    assert not main._application_profile(db, u.id).get("custom_qa"), "a one-time code is never saved as an answer"
    db.close()


def test_worker_platforms_and_submit():
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    u = User(email="a@b.com", password_hash="x")
    db.add(u)
    db.commit()
    wd, gh = Job(user_id=u.id, company="TD", title="DS", status="approved", platform="workday", match_score=90), \
        Job(user_id=u.id, company="Shop", title="DS", status="approved", platform="greenhouse", match_score=80)
    db.add_all([wd, gh])
    db.commit()
    for j in (wd, gh):
        db.add(ResumeVersion(job_id=j.id, user_id=u.id, version=1, content_md="# A B", pdf_path=__file__))   # must exist
    main._put_setting(db, u.id, "worker_platforms", "workday")
    db.commit()
    assert main.apply_next({}, db, u)["job_id"] == gh.id                       # OpenClaw never gets Workday
    assert main.apply_next({"worker": True}, db, u)["job_id"] == wd.id         # the worker only gets Workday
    main.apply_wait(wd.id, {"reason": "All 30 values checked.", "kind": "submit"}, db, u)
    text, markup = tg.ask_message(wd, "submit", "x")
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == [f"y:{wd.id}", f"k:{wd.id}"]
    assert tg.answer(db, u, wd, "submit", datetime.utcnow()) == f"Submitting #{wd.id} now."
    wd.meta = {**(wd.meta or {}), tg.CARDS_META: [[11, "card"]]}
    db.commit()
    main._put_setting(db, u.id, tg.CHAT_KEY, "42")
    db.commit()
    calls, real = [], (tg.httpx.post, tg.TOKEN)
    tg.httpx.post, tg.TOKEN = (lambda url, **kw: calls.append((url, kw["json"]))), "t"
    try:
        tg.close_cards(db, u.id, wd, "✅ Submitted")
        tg.close_cards(db, u.id, wd, "✅ Submitted")                            # nothing left to close
    finally:
        tg.httpx.post, tg.TOKEN = real
    assert len(calls) == 1 and calls[0][0].endswith("/editMessageText"), calls
    assert calls[0][1] == {"chat_id": "42", "message_id": 11, "text": "card\n\n✅ Submitted"}
    assert main.apply_ask_status(wd.id, db, u)["answer"] == "submit"
    db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

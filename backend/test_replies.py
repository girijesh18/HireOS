"""Replies that answer something the bot sent: option answers, a hand-off's missing answer, a pasted
apply link, chat answers that name a job, saved answers reused without the model, and board_of.

Run: cd backend && python test_replies.py
"""
import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_replies.db"
os.environ["LOG_DIR"] = "./logs"
os.environ["CHROMA_PATH"] = "./test_chroma"

from datetime import datetime, timedelta

import discovery
import main
import telegram_bot as tg
from database import Base, Job, ResumeVersion, SessionLocal, User, engine, init_db

NOW = datetime(2026, 10, 31, 12, 0)


def setup():
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    u = User(email="a@b.com", password_hash="x")
    db.add(u)
    db.commit()
    return db, u


def job(db, u, **kw):
    j = Job(user_id=u.id, company=kw.pop("company", "Osedea"), title="AI Dev", **kw)
    db.add(j)
    db.commit()
    return j


def test_pick_option():
    opts = ["None", "Basic", "Intermediate", "Fluent / Native"]
    assert tg.pick_option(opts, "1") == "None" and tg.pick_option(opts, " none ") == "None"
    assert tg.pick_option(opts, "fluent") == "Fluent / Native"
    assert tg.pick_option(opts, "9") is None and tg.pick_option(opts, "Spanish") is None
    assert tg.pick_option(["Yes", "Yes, with sponsorship"], "yes") == "Yes"          # exact beats partial
    assert tg.pick_option([], "  anything ") == "anything" and tg.pick_option([], "") is None


def test_honeypot_is_not_a_company_name():
    import workday
    assert not workday.HONEYPOT.search("Which E2E Robotics office would you work from?")
    assert workday.HONEYPOT.search("Are you a robot?") and workday.HONEYPOT.search("Leave this field blank")


def test_board_of():
    assert discovery.board_of("https://job-boards.greenhouse.io/acme/jobs/123") == ("greenhouse", "acme")
    assert discovery.board_of("https://boards.greenhouse.io/embed/job_board?for=acme") == ("greenhouse", "acme")
    assert discovery.board_of("https://jobs.lever.co/acme/abc-123/apply") == ("lever", "acme")
    assert discovery.board_of("https://jobs.ashbyhq.com/acme/uuid") == ("ashby", "acme")
    assert discovery.board_of("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/X_1") == \
        ("workday", "https://acme.wd5.myworkdayjobs.com/Careers")
    assert discovery.board_of("https://acme.com/careers") is None and discovery.board_of("") is None


def test_open_question_options():
    """A waiting question with options: a non-option keeps it waiting; the number saves the option."""
    db, u = setup()
    j = job(db, u, status="waiting_human", meta={"ask": {"q": "French?", "label": "French?", "kind": "question",
                                                       "options": ["None", "Fluent"], "dmsg": "55",
                                                       "deadline": (NOW + timedelta(minutes=5)).isoformat()}})
    main.apply_continue = lambda jid, db, user: setattr(db.get(Job, jid), "status", "applying")
    text, hit = tg.route_reply(db, u, "Spanish", "55", "dmsg", NOW)
    assert hit.id == j.id and "isn't one of the options" in text and db.get(Job, j.id).status == "waiting_human"
    text, _ = tg.route_reply(db, u, "1", None, "dmsg", NOW)                       # no reply tag: the only question
    assert "continuing" in text and db.get(Job, j.id).meta["ask"]["answer"] == "None"
    assert main._application_profile(db, u.id)["custom_qa"] == [{"q": "French?", "a": "None"}]
    db.close()


def test_handoff_reply_and_plain_option():
    db, u = setup()
    approved = []
    real = main.approve_job
    main.approve_job = lambda jid, db, user: approved.append(jid) or setattr(db.get(Job, jid), "status", "approved")
    try:
        j = job(db, u, status="needs_human", meta={"pending_questions": [
            {"label": "French?", "options": ["None", "Fluent"]}, {"label": "Why us?", "options": []}],
            tg.HANDOFF_META: {"dmsgs": ["70"], "at": NOW.isoformat()}})
        assert tg.route_reply(db, u, "what jobs came in today?", None, "dmsg", NOW) == (None, None)   # chat, not an answer
        text, hit = tg.route_reply(db, u, "none", None, "dmsg", NOW)              # a valid option, no reply tag
        assert hit.id == j.id and text.startswith("Saved: French? = None.") and "Why us?" in text and not approved
        assert tg.route_reply(db, u, "I like robots", None, "dmsg", NOW) == (None, None)   # free text needs the reply tag
        tg.remember_handoff(db, db.get(Job, j.id), "dmsg", "71")
        text, _ = tg.route_reply(db, u, "I like robots", "71", "dmsg", NOW)
        assert "goes back to the agent" in text and approved == [j.id]
        qa = {x["q"]: x["a"] for x in main._application_profile(db, u.id)["custom_qa"]}
        assert qa == {"French?": "None", "Why us?": "I like robots"}
        late = NOW + tg.REPLY_WINDOW + timedelta(minutes=1)
        db.get(Job, j.id).meta = {**db.get(Job, j.id).meta, "pending_questions": [{"label": "X", "options": ["A"]}],
                                  tg.HANDOFF_META: {"dmsgs": ["71"], "at": NOW.isoformat()}}
        db.get(Job, j.id).status = "needs_human"
        db.commit()
        assert tg.route_reply(db, u, "A", None, "dmsg", late) == (None, None)      # too long after the hand-off
    finally:
        main.approve_job = real
    db.close()


def test_apply_link_on_card():
    db, u = setup()
    j = job(db, u, url="https://www.linkedin.com/jobs/view/1", platform="linkedin", status="pending",
            meta={"dc_card_msgs": [["80", "card"]]})
    text, _ = tg.route_reply(db, u, "here: https://jobs.lever.co/osedea/abc-1).", "80", "dmsg", NOW)
    j = db.get(Job, j.id)
    assert "is on Lever" in text and j.url == "https://jobs.lever.co/osedea/abc-1" and j.platform == "lever"
    assert "[apply link from you: was https://www.linkedin.com/jobs/view/1]" in j.notes
    import json
    assert json.loads(main._setting(db, u.id, tg.BOARDS_KEY)) == {"osedea": "https://jobs.lever.co/osedea/abc-1"}
    text, _ = tg.route_reply(db, u, "https://acme.icims.com/jobs/5", "80", "dmsg", NOW)
    assert "apply there yourself" in text
    db.close()


def test_chat_answer_only_when_asked():
    db, u = setup()
    j = job(db, u, status="applied")
    assert "no open question" in main._chat_answer(db, u, j.id, "None") and \
        not main._application_profile(db, u.id).get("custom_qa")
    assert "Which job" in main._chat_answer(db, u, None, "None")
    db.close()


def test_saved_answer_skips_the_model():
    db, u = setup()
    j = job(db, u, job_description="JD")
    main.put_application_profile({"custom_qa": [{"q": "What is your French level?", "a": "None"}]}, db, u)
    main.get_master_resume = lambda db, uid: "resume"
    out = asyncio.run(main.answer_questions({"job_id": j.id, "questions": [
        {"id": "q1", "label": "What is your French level", "type": "select", "options": ["None", "Fluent"]}]}, db, u))
    assert out == {"answers": [{"id": "q1", "answer": "None", "drafted": False}]}    # no model call needed
    db.close()


def test_outcome_text_says_how_to_unblock():
    db, u = setup()
    j = job(db, u, url="https://www.linkedin.com/jobs/view/1", platform="linkedin")
    assert "Reply to this message with it" in tg.outcome_text(j, "needs_human", "handed off")
    j.meta = {"pending_questions": [{"label": "French?", "options": ["None", "Fluent"]}]}
    t = tg.outcome_text(j, "needs_human", "unanswered")
    assert "The form asks: French?? Options: 1 None · 2 Fluent." not in t and "Options: 1 None · 2 Fluent" in t
    db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

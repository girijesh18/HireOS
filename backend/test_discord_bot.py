"""Discord bot: markup mapping, linking + allowlist, messages, buttons, fan-out with Telegram.
Discord's REST API is replaced by a recorder; nothing leaves the machine.

Run: cd backend && python test_discord_bot.py
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_discord.db"
os.environ["LOG_DIR"] = "./logs"

from datetime import datetime

import discord_bot as dc
import main
import telegram_bot as tg
from database import Base, Job, ResumeVersion, SessionLocal, User, engine, init_db

CH, ME, STRANGER, GUILD = "900", "100", "666", "77"


class Rest:
    """Records Discord REST calls; every message gets a fresh id."""
    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, method, path, **kw):
        self.n += 1
        self.calls.append((method, path, kw))
        return {"id": f"m{self.n}"}

    def texts(self):
        out = []
        for method, _, kw in self.calls:
            if not (kw.get("json") or kw.get("data")):
                continue                                         # e.g. the typing indicator
            body = kw.get("json") or __import__("json").loads(kw["data"]["payload_json"])
            out.append(body["content"])
        return out


def setup():
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    u = User(email="a@b.com", password_hash="x")
    db.add(u)
    db.commit()
    rest = Rest()
    dc._request, dc.TOKEN = rest, "t"
    return db, u, rest


def linked(db, u, rest):
    code, stored = tg.new_link_code(datetime.utcnow())
    main._put_setting(db, u.id, dc.CODE_KEY, stored)
    db.commit()
    dc.on_message(db, {"channel_id": CH, "guild_id": GUILD, "author": {"id": ME}, "content": f"link {code}"})
    assert rest.texts()[-1].startswith("Linked to HireOS (a@b.com)"), rest.texts()
    rest.calls.clear()


def msg(text, author=ME, reply_to=None):
    d = {"channel_id": CH, "guild_id": GUILD, "author": {"id": author}, "content": text}
    if reply_to:
        d["message_reference"] = {"message_id": reply_to}
    return d


def test_components_and_commands():
    rows = dc.components({"inline_keyboard": [[{"text": "Approve", "callback_data": "a:5"},
                                               {"text": "Skip", "callback_data": "s:5"}],
                                              [{"text": "Open screen", "url": "https://x"}]]})
    assert rows[0]["components"][0] == {"type": 2, "style": 1, "label": "Approve", "custom_id": "a:5"}
    assert rows[0]["components"][1]["style"] == 4                                    # Skip is red
    assert rows[1]["components"][0] == {"type": 2, "style": 5, "label": "Open screen", "url": "https://x"}
    assert dc.components({"force_reply": True}) == []
    assert "right-click › Reply" in dc.text_for("Reply to this message with your answer", {"force_reply": True})
    assert dc.command("review") == ("review", None) and dc.command("/review #45") == ("review", 45)
    assert dc.command("!status") == ("status", None) and dc.command("what jobs today?") is None


def test_link_and_allowlist():
    db, u, rest = setup()
    dc.on_message(db, msg("link wrong"))
    assert "expired" in rest.texts()[-1]
    linked(db, u, rest)
    dc.on_message(db, msg("status", author=STRANGER))                    # someone else in the server
    dc.on_message(db, {**msg("status"), "channel_id": "dm", "guild_id": None})   # me, outside the server
    dc.on_message(db, {**msg("status"), "channel_id": "x", "guild_id": "88"})    # me, in another server
    dc.on_message(db, {**msg("status"), "author": {"id": ME, "bot": True}})
    assert rest.calls == []                                              # all ignored, silently
    db.add(Job(user_id=u.id, company="A", title="DS", status="pending"))
    db.commit()
    dc.on_message(db, msg("status"))
    dc.on_message(db, {**msg("status"), "channel_id": "901"})            # another channel of the same server
    assert rest.texts() == ["pending: 1", "pending: 1"]
    assert rest.calls[1][1] == "/channels/901/messages"                  # answered where it was asked
    db.close()


def test_cards_buttons_and_edit_mode():
    db, u, rest = setup()
    linked(db, u, rest)
    job = Job(user_id=u.id, company="Shop", title="DS", status="pending", match_score=80)
    db.add(job)
    db.commit()
    db.add(ResumeVersion(job_id=job.id, user_id=u.id, version=1, content_md="# A B\n- x", pdf_path=__file__))
    db.commit()
    dc.on_message(db, msg("review"))
    method, path, kw = rest.calls[-1]
    assert path == f"/channels/{CH}/messages" and "files[0]" in kw["files"]       # PDF attached to the card
    assert [c["custom_id"] for c in __import__("json").loads(kw["data"]["payload_json"])["components"][0]["components"]] \
        == [f"a:{job.id}", f"s:{job.id}", f"e:{job.id}"]
    assert db.get(Job, job.id).meta[dc.SENT_META] == 1
    assert tg.cards_due(db, u.id) and not tg.cards_due(db, u.id, sent_key=dc.SENT_META)   # Telegram's marker is separate

    press = {"channel_id": CH, "guild_id": GUILD, "member": {"user": {"id": ME}}, "message": {"id": "m9", "content": "card"},
             "data": {"custom_id": f"e:{job.id}"}}
    dc.on_button(db, {**press, "member": {"user": {"id": STRANGER}}})             # not the linked user
    assert rest.calls[-1][1] == f"/channels/{CH}/messages" and len(rest.calls) == 1
    dc.on_button(db, press)
    assert "What should change" in rest.texts()[-1] and f"dc:{CH}" in tg._edit_waiting
    dc.on_message(db, msg("done"))
    assert f"dc:{CH}" not in tg._edit_waiting

    dc.on_button(db, {**press, "data": {"custom_id": f"a:{job.id}"}})
    method, path, kw = rest.calls[-1]
    assert (method, path) == ("PATCH", f"/channels/{CH}/messages/m9")
    assert kw["json"] == {"content": "card\n\nApproved — queued for applying.", "components": []}
    assert db.get(Job, job.id).status == "approved"
    db.get(Job, job.id).status = "applied"
    db.commit()
    dc.on_button(db, {**press, "data": {"custom_id": f"s:{job.id}"}})             # stale card: already decided
    assert rest.calls[-1][2]["json"]["content"].endswith("is already applied.")
    db.close()


def test_question_reply_and_fanout():
    db, u, rest = setup()
    linked(db, u, rest)
    main._put_setting(db, u.id, tg.CHAT_KEY, "42")                                # Telegram linked too
    db.commit()
    tg_sent, real = [], (tg.send, tg.TOKEN)
    tg.send, tg.TOKEN = (lambda db, uid, text, markup=None: tg_sent.append(text) or 7), "t"
    try:
        a, b = (Job(user_id=u.id, company=c, title="DS", status="applying") for c in ("A", "B"))
        db.add_all([a, b])
        db.commit()
        main.apply_wait(a.id, {"reason": "Salary?", "kind": "question"}, db, u)
        main.apply_wait(b.id, {"reason": "Notice?", "kind": "question"}, db, u)
        ask_a = db.get(Job, a.id).meta["ask"]
        assert ask_a["msg"] == 7 and ask_a["dmsg"], ask_a                         # both chats got it
        assert len(tg_sent) == 2 and "Salary?" in rest.texts()[0]
        dc.on_message(db, msg("120k"))                                            # two open questions, no reply-to
        assert rest.texts()[-1] == tg.AMBIGUOUS                                   # not guessed, not sent to chat
        dc.on_message(db, msg("120k", reply_to=ask_a["dmsg"]))                    # a reply picks the question
        assert rest.texts()[-1].endswith("continuing.")
        assert main.apply_ask_status(a.id, db, u)["answer"] == "120k"
        assert db.get(Job, b.id).status == "waiting_human"
        main.apply_result(a.id, {"outcome": "needs_human", "notes": "stuck"}, db, u)
        assert rest.texts()[-1].startswith(f"Needs you: #{a.id}") and tg_sent  # outcome to Discord too
    finally:
        tg.send, tg.TOKEN = real
    db.close()


def test_free_text_goes_to_chat():
    db, u, rest = setup()
    linked(db, u, rest)
    asked, real = [], (tg.chat_reply, dc.on_loop)

    async def fake_chat(db, user, text):
        asked.append((user.id, text))
        return "You have **3** new jobs."
    tg.chat_reply, dc.on_loop = fake_chat, (lambda coro: __import__("asyncio").run(coro))
    try:
        dc.on_message(db, msg("what jobs came in today?"))
    finally:
        tg.chat_reply, dc.on_loop = real
    assert asked == [(u.id, "what jobs came in today?")]
    assert rest.calls[0][1] == f"/channels/{CH}/typing" and rest.texts()[-1] == "You have **3** new jobs."
    db.close()


def test_chat_actions():
    """The chat's job actions run in code: the model only picks the action and its params."""
    import asyncio
    from datetime import timedelta
    db, u, rest = setup()
    now = datetime.utcnow()
    jobs = [Job(user_id=u.id, company="Old", title="DS", status="pending", match_score=95, created_at=now - timedelta(days=3)),
            Job(user_id=u.id, company="Hi", title="DS", status="pending", match_score=90, created_at=now),
            Job(user_id=u.id, company="Lo", title="DS", status="found", match_score=60, created_at=now),
            Job(user_id=u.id, company="NoCV", title="DS", status="found", created_at=now),
            Job(user_id=u.id, company="Sent", title="DS", status="applied", applied_at=now, created_at=now - timedelta(days=9))]
    other = User(email="z@z.com", password_hash="x")
    db.add_all(jobs + [other])
    db.commit()
    theirs = Job(user_id=other.id, company="Theirs", title="DS", status="pending")
    db.add(theirs)
    db.commit()
    for j in jobs[:3]:
        db.add(ResumeVersion(job_id=j.id, user_id=u.id, version=1, content_md="x"))
    db.commit()
    old, hi, lo, nocv, sent = jobs

    class Fake:
        def __init__(self, action):
            self.action = action

        async def dispatch(self, *a, **k):
            return self.action

    def ask(action):
        real = main.get_agent
        main.get_agent = lambda *a: Fake(action)
        try:
            return asyncio.run(main.chat(main.ChatMessage(message="x", llm="local"), db, u)).reply
        finally:
            main.get_agent = real

    today = ask({"action": "list_jobs", "params": {"since_hours": 24, "sort": "score", "limit": 20}})
    assert today.splitlines()[0] == "3 jobs from the last 24h:", today
    assert "· not scored yet" in today.splitlines()[3]
    good = ask({"action": "list_jobs", "params": {"since_hours": 24, "min_score": 50, "sort": "score", "limit": 1}})
    assert good.splitlines()[0] == "2 jobs from the last 24h scoring 50+ — top 1:", good    # Hi, Lo; not unscored
    assert f"#{hi.id} Hi — DS · pending · score 90 · " in good, good
    assert [l.split()[1] for l in today.splitlines()[1:]] == [f"#{hi.id}", f"#{lo.id}", f"#{nocv.id}"]   # best first, unscored last
    assert ask({"action": "list_jobs", "params": {"status": "pending"}}).count("•") == 2
    top = ask({"action": "list_jobs", "params": {"sort": "score"}})
    assert f"#{old.id}" in top and f"#{sent.id}" not in top                            # top = still open
    approved = ask({"action": "approve_jobs", "params": {"job_ids": [hi.id, nocv.id, theirs.id, sent.id]}})
    assert approved.splitlines() == [f"#{hi.id} Hi: approved", f"#{nocv.id} NoCV: no resume yet — not approved",
                                     f"#{theirs.id}: not found", f"#{sent.id} Sent: already applied"], approved
    assert db.get(Job, hi.id).status == "approved" and db.get(Job, theirs.id).status == "pending"
    status = ask({"action": "status_summary", "params": {"since_hours": 168}})
    assert status.splitlines()[0] == "Last 168h: 4 new jobs found, 1 applications submitted.", status
    assert f"Queued to apply (1): #{hi.id} Hi" in status and "Resumes to review: 1" in status
    assert "only on the instance" in ask({"action": "run_discovery"})               # no scheduler in tests
    db.close()


def test_off_without_token():
    db, u, rest = setup()
    linked(db, u, rest)
    dc.TOKEN = ""
    assert dc.send(db, u.id, "x") is None and rest.calls == []
    db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

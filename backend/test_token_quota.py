"""Token-metering tests.

Run: cd backend && python test_token_quota.py
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./test_token_quota.db"
os.environ["FREE_INPUT_TOKENS"] = "1000"
os.environ["FREE_OUTPUT_TOKENS"] = "400"
os.environ["BILLING_UNLIMITED_EMAILS"] = "owner@hireos.test"

import asyncio

import billing
from database import Base, SessionLocal, User, engine, init_db


def fresh_db():
    Base.metadata.drop_all(bind=engine)
    init_db()
    return SessionLocal()


def make_user(db, email="a@b.com", plan="free"):
    u = User(email=email, password_hash="x", plan=plan)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_migration_is_idempotent():
    db = fresh_db()
    init_db()          # a second startup must not blow up on existing columns
    init_db()
    u = make_user(db)
    assert u.free_input_tokens_used == 0
    assert u.free_output_tokens_used == 0
    db.close()


def test_consume_tracks_input_and_output_apart():
    db = fresh_db()
    u = make_user(db)
    billing.consume_tokens(db, u.id, 120, 30)
    billing.consume_tokens(db, u.id, 80, 20)
    db.refresh(u)
    assert u.free_input_tokens_used == 200, u.free_input_tokens_used
    assert u.free_output_tokens_used == 50, u.free_output_tokens_used
    db.close()


def test_either_budget_alone_blocks():
    db = fresh_db()
    a = make_user(db, "in@x.com")
    billing.consume_tokens(db, a.id, 1000, 0)
    db.refresh(a)
    assert not billing.has_token_quota(a), "input exhaustion must block"

    b = make_user(db, "out@x.com")
    billing.consume_tokens(db, b.id, 0, 400)
    db.refresh(b)
    assert not billing.has_token_quota(b), "output exhaustion must block"

    c = make_user(db, "fine@x.com")
    billing.consume_tokens(db, c.id, 999, 399)
    db.refresh(c)
    assert billing.has_token_quota(c), "just under both limits must pass"
    db.close()


def test_pro_and_unlimited_emails_are_never_metered():
    db = fresh_db()
    pro = make_user(db, "pro@x.com", plan="pro")
    billing.consume_tokens(db, pro.id, 99999, 99999)
    db.refresh(pro)
    assert pro.free_input_tokens_used == 0, "a pro user must not be charged"
    assert billing.has_token_quota(pro)
    assert billing.tokens_remaining(pro) is None

    owner = make_user(db, "owner@hireos.test")
    billing.consume_tokens(db, owner.id, 99999, 99999)
    db.refresh(owner)
    assert billing.has_token_quota(owner), "the unlimited-email escape hatch must hold"
    db.close()


def test_snapshot_reports_both_budgets():
    db = fresh_db()
    u = make_user(db)
    billing.consume_tokens(db, u.id, 250, 100)
    db.refresh(u)
    snap = billing.plan_snapshot(u)
    assert snap["token_limits"] == {"input": 1000, "output": 400}
    assert snap["tokens_used"] == {"input": 250, "output": 100}
    assert snap["tokens_remaining"] == {"input": 750, "output": 300}
    db.close()


def test_router_usage_hook_charges_the_right_user():
    """The hook fires from _complete_one, so a fake provider proves the wiring
    without touching a real API."""
    from llm_router import LLMRouter

    db = fresh_db()
    mine_id = make_user(db, "mine@x.com").id
    theirs_id = make_user(db, "theirs@x.com").id
    db.close()

    def meter(i, o):
        s = SessionLocal()
        try:
            billing.consume_tokens(s, mine_id, i, o)
        finally:
            s.close()

    router = LLMRouter(keys={"gemini": "fake"}, allow_env=False, on_usage=meter)

    async def fake_call(prompt, system=None, model=None, max_tokens=0, temperature=0):
        router._note_usage(700, 90)
        return "generated text"

    router._call_gemini = fake_call
    out = asyncio.run(router._complete_one("prompt", "gemini", None, 100, 0.3))
    assert out == "generated text"

    s = SessionLocal()
    charged = s.query(User).filter(User.id == mine_id).first()
    untouched = s.query(User).filter(User.id == theirs_id).first()
    assert charged.free_input_tokens_used == 700, charged.free_input_tokens_used
    assert charged.free_output_tokens_used == 90
    assert untouched.free_input_tokens_used == 0, "the other user must not be charged"
    s.close()


def test_usage_falls_back_to_an_estimate_when_the_provider_says_nothing():
    from llm_router import LLMRouter

    seen = []
    router = LLMRouter(keys={"gemini": "fake"}, allow_env=False,
                       on_usage=lambda i, o: seen.append((i, o)))

    async def silent_call(prompt, system=None, model=None, max_tokens=0, temperature=0):
        return "x" * 400          # no _note_usage: provider returned no usage block

    router._call_gemini = silent_call
    asyncio.run(router._complete_one("y" * 800, "gemini", None, 100, 0.3))
    assert seen == [(200, 100)], seen


def test_a_broken_meter_never_breaks_a_generation():
    from llm_router import LLMRouter

    def exploding(i, o):
        raise RuntimeError("db is on fire")

    router = LLMRouter(keys={"gemini": "fake"}, allow_env=False, on_usage=exploding)

    async def fake_call(prompt, system=None, model=None, max_tokens=0, temperature=0):
        return "the resume"

    router._call_gemini = fake_call
    assert asyncio.run(router._complete_one("p", "gemini", None, 100, 0.3)) == "the resume"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL  {name}: {e.__class__.__name__}: {e}")
    print("FAILED" if failures else "all passed")
    sys.exit(1 if failures else 0)

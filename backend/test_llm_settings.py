"""DEFAULT_LLM and MAIL_LLM: what a hosted instance (no local model) sets instead of the local llama.cpp.

Run: cd backend && python test_llm_settings.py
"""
import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_llm_settings.db"
os.environ["LOG_DIR"] = "./logs"

import mail_updates  # noqa: E402
from llm_router import LLMRouter  # noqa: E402


def test_default_llm_preference():
    r = LLMRouter(keys={"gemini": "g", "openrouter": "o"}, allow_env=False)
    os.environ.pop("DEFAULT_LLM", None)
    assert r.default_llm() == "gemini"                                    # unset: as before
    os.environ["DEFAULT_LLM"] = "openrouter:qwen/qwen3.8-27b"
    try:
        assert r.default_llm() == "openrouter:qwen/qwen3.8-27b"
        assert LLMRouter(keys={"gemini": "g"}, allow_env=False).default_llm() == "gemini", \
            "a preference the user has no key for is ignored"
    finally:
        os.environ.pop("DEFAULT_LLM")


def test_mail_llm():
    calls = []

    class R:
        async def complete(self, prompt, **kw):
            calls.append(kw)
            return '{"kind": "rejected"}'
    mail = {"from": "no-reply@acme.com", "subject": "Your application", "body": "Unfortunately..."}
    os.environ.pop("MAIL_LLM", None)
    assert asyncio.run(mail_updates.classify(R(), mail)) == "rejected"
    os.environ["MAIL_LLM"] = "openrouter:qwen/qwen3.8-27b"
    try:
        asyncio.run(mail_updates.classify(R(), mail))
    finally:
        os.environ.pop("MAIL_LLM")
    asyncio.run(mail_updates.classify(R(), mail, llm="gemini-2.5-pro"))           # the user's model, from the caller
    assert [(c["llm"], c["fallback"]) for c in calls] == \
        [("local", False), ("openrouter:qwen/qwen3.8-27b", False), ("gemini-2.5-pro", False)]


def test_one_model_per_user():
    """Settings › AI Model: the saved choice is the model everywhere a call doesn't name one."""
    import main
    from database import Base, SessionLocal, User, engine, init_db
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    u = User(email="a@b.com", password_hash="x")
    db.add(u)
    db.commit()
    main._put_setting(db, u.id, "gemini_api_key", "g")
    main._put_setting(db, u.id, "openrouter_api_key", "o")
    db.commit()
    os.environ.pop("DEFAULT_LLM", None)
    assert main.resolve_llm(db, u.id) == "gemini"                                  # nothing picked yet
    main._put_setting(db, u.id, main.DEFAULT_LLM_KEY, "openrouter:qwen/qwen3.8-27b")
    db.commit()
    assert main.resolve_llm(db, u.id) == "openrouter:qwen/qwen3.8-27b"
    assert main.resolve_llm(db, u.id, "gemini-2.5-pro") == "gemini-2.5-pro"        # an explicit choice still wins
    main._put_setting(db, u.id, main.DEFAULT_LLM_KEY, "nvidia:some/model")       # no NVIDIA key: ignored
    db.commit()
    assert main.resolve_llm(db, u.id) == "gemini"
    main._put_setting(db, u.id, main.DEFAULT_LLM_KEY, "gemini-2.5-pro")
    main._put_setting(db, u.id, "custom_nvidia_models", '[{"id": "qwen/qwen3.8-27b", "provider": "openrouter"}]')
    db.commit()
    p = main.get_providers(db, u)
    assert p["model"] == "gemini-2.5-pro" and p["custom"][0]["id"] == "qwen/qwen3.8-27b"
    assert set(p["available"]) >= {"gemini", "openrouter"}
    assert main.llm_provider("openrouter:qwen/x") == "openrouter" and main.llm_provider("gemini-2.5-flash") == "gemini"
    db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

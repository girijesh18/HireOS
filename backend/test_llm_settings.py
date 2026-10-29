"""DEFAULT_LLM and MAIL_LLM: what a hosted instance (no local model) sets instead of the local llama.cpp.

Run: cd backend && python test_llm_settings.py
"""
import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_llm_settings.db"
os.environ["CHROMA_PATH"] = "./test_chroma"           # chat() logs to a store; never the real one
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


def test_fallback_by_key_and_health():
    """Fallbacks: only providers with a key; one that just failed is skipped, one that answered goes first."""
    import llm_router
    llm_router._HEALTH.clear()
    r = LLMRouter(keys={"gemini": "g", "openrouter": "o", "nvidia": "n"}, allow_env=False)
    assert r._fallback_chain("openrouter:qwen/x") == ["openrouter:qwen/x", "gemini", "nvidia"]   # key order
    called = []

    async def gemini(prompt, **kw):
        called.append("gemini")
        raise RuntimeError("429")

    async def nvidia(prompt, **kw):
        called.append("nvidia")
        return "ok"

    async def openrouter(prompt, **kw):
        called.append("openrouter")
        raise RuntimeError("timeout")
    r._call_gemini, r._call_nvidia, r._call_openrouter = gemini, nvidia, openrouter
    assert asyncio.run(r.complete("hi", llm="openrouter:qwen/x")) == "ok"
    assert called == ["openrouter", "gemini", "nvidia"]
    assert r._fallback_chain("openrouter:qwen/x") == ["openrouter:qwen/x", "nvidia"]   # gemini down for a while
    llm_router._HEALTH["gemini"] = (False, llm_router.time.monotonic() - llm_router.DOWN_FOR - 1)
    assert r._fallback_chain("openrouter:qwen/x") == ["openrouter:qwen/x", "nvidia", "gemini"]   # retried later
    assert LLMRouter(keys={"nvidia": "n"}, allow_env=False)._fallback_chain("nvidia") == ["nvidia"]
    llm_router._HEALTH.clear()
    r.preferred = "openrouter:qwen/x"
    assert r._fallback_chain("nvidia") == ["nvidia", "gemini", "openrouter:qwen/x"], \
        "falling back to OpenRouter means the chosen model, not OpenRouter's default list"
    r.preferred = None
    called.clear()
    asyncio.run(r.complete("hi"))                                  # no model named: the router's default, not "gemini"
    assert called[0] == r.default_llm().split(":")[0] or called[0] == r.default_llm(), called
    llm_router._HEALTH.clear()


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
    assert main.resolve_llm(db, u.id, "local") == "openrouter:qwen/qwen3.8-27b"    # no local model here: the user's
    import llm_router
    main._put_setting(db, u.id, "local_llm_url", "http://pc:8080/v1")             # saved, but unreachable
    db.commit()
    assert main.resolve_llm(db, u.id, "local") == "local"
    llm_router._HEALTH["local"] = (False, llm_router.time.monotonic())             # it just failed
    assert main.resolve_llm(db, u.id, "local") == "openrouter:qwen/qwen3.8-27b"
    llm_router._HEALTH.clear()
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
    seen = []

    class Dispatcher:
        async def dispatch(self, message, context=None, llm=None):
            seen.append(llm)
            return {"action": "general_reply", "text": "hi"}
    real, main.get_agent = main.get_agent, (lambda *a: Dispatcher())
    try:
        asyncio.run(main.chat(main.ChatMessage(message="x"), db, u))    # web chat / Discord: no model named
    finally:
        main.get_agent = real
    assert seen == ["gemini-2.5-pro"], seen                                     # the saved choice, not "gemini"
    db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

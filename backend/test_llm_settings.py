"""DEFAULT_LLM and MAIL_LLM: what a hosted instance (no local model) sets instead of the local llama.cpp.

Run: cd backend && python test_llm_settings.py
"""
import asyncio
import os

import mail_updates
from llm_router import LLMRouter


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
    assert [(c["llm"], c["fallback"]) for c in calls] == [("local", False), ("openrouter:qwen/qwen3.8-27b", False)]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

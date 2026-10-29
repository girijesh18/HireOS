"""ATS scorer bridge (runs on the model that wrote the resume, never another provider), URL
tracking on the user's model, and the resume critic's retry on a reply that isn't JSON.

Run: cd backend && python test_ats_score.py
"""
import asyncio

import ats_score
from agents import JobDiscoveryAgent, ResumeCriticAgent
from llm_router import LLMRouter


def test_provider_env():
    r = LLMRouter(keys={"openrouter": "or-key", "gemini": "g-key"}, allow_env=False)
    env = ats_score._provider_env("openrouter:qwen/qwen3.8-27b", r)
    assert env == {"LLM_PROVIDER": "openai", "DEFAULT_MODEL": "qwen/qwen3.8-27b", "OPENAI_API_KEY": "or-key",
                   "OPENAI_BASE_URL": "https://openrouter.ai/api/v1"}, env
    assert ats_score._provider_env("gemini", r)["DEFAULT_MODEL"] == "gemini-2.5-flash"
    assert ats_score._provider_env("gemini-2.5-pro", r)["DEFAULT_MODEL"] == "gemini-2.5-pro"
    assert ats_score._provider_env("openrouter", r) is None, "bare provider: no model id to run"
    assert ats_score._provider_env("groq:llama-x", r) is None, "no Groq key: no score, not Gemini"
    assert ats_score._provider_env("claude", r) is None
    assert ats_score._provider_env("", r) is None


def test_track_url_reads_page_on_users_model():
    seen = {}

    class Router:
        async def complete(self, prompt, llm=None, **kw):
            seen["llm"], seen["prompt"] = llm, prompt
            return '{"company": "Acme", "title": "Data Scientist"}'
    agent = JobDiscoveryAgent(Router())

    async def page(url):
        return "Acme is hiring a Data Scientist in Toronto. " * 20
    agent._fetch_page = page
    data = asyncio.run(agent.analyze_url("https://boards.greenhouse.io/acme/jobs/1", llm="openrouter:qwen/x"))
    assert data == {"company": "Acme", "title": "Data Scientist", "url": "https://boards.greenhouse.io/acme/jobs/1"}
    assert seen["llm"] == "openrouter:qwen/x" and "Acme is hiring" in seen["prompt"]


def test_critic_retries_once_on_bad_json():
    replies = ['I think the resume is fine.', '{"verdict": "ok", "score": 7}']

    class Router:
        async def complete(self, prompt, **kw):
            return replies.pop(0)
    notes = asyncio.run(ResumeCriticAgent(Router()).critique("# R", "JD", "Acme", "DS", "master", llm="x"))
    assert notes["score"] == 7 and not replies


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

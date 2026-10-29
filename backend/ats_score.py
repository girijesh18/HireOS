"""
ATS scoring bridge: runs the vendored hiring-agent scorer (backend/hiring_agent)
against a generated resume PDF in an isolated subprocess (its own venv), so its
pinned deps never clash with HireOS's. Best-effort — any failure returns None
and the caller just skips the score.

Uses the model that generated the resume (Gemini, Ollama, or any OpenAI-compatible
provider: OpenRouter, Groq, Together, NVIDIA, OpenAI, a local server); otherwise no score.
"""

import os
import json
import logging
import subprocess

logger = logging.getLogger(__name__)

_DIR = os.path.join(os.path.dirname(__file__), "hiring_agent")
_PYTHON = os.path.join(_DIR, ".venv", "bin", "python")
_WRAPPER = os.path.join(_DIR, "evaluate.py")

_NVIDIA_BASE = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")


# OpenAI-compatible endpoints the scorer can call with LLM_PROVIDER=openai.
_OPENAI_COMPAT = {
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "nvidia": _NVIDIA_BASE,
    "openai": "https://api.openai.com/v1",
}


def _provider_env(model, router):
    """The scorer's provider env for the model that wrote the resume, or None when it can't run that
    model (no key, or a bare provider name with no model id). Never another provider: scoring with
    a model the user didn't choose spent Gemini's rate-limited free tier on every resume."""
    from llm_router import _provider, nvidia_model
    model = model or ""
    provider = _provider(model) if model else ""
    if provider == "minimax":
        provider = "nvidia"
    model_id = model.split(":", 1)[1] if ":" in model else ""

    if provider == "gemini":
        if not router.gemini_key:
            return None
        # resolve_llm can hand us the bare provider name ("gemini"), which is not a model id
        return {"LLM_PROVIDER": "gemini", "DEFAULT_MODEL": model_id or (model if "-" in model else "gemini-2.5-flash"),
                "GEMINI_API_KEY": router.gemini_key}
    if provider == "ollama":
        return {"LLM_PROVIDER": "ollama", "DEFAULT_MODEL": os.getenv("ATS_OLLAMA_MODEL", "gemma3:4b")}
    if provider == "local" and router.local_url:
        return {"LLM_PROVIDER": "openai", "DEFAULT_MODEL": model_id or "local",
                "OPENAI_API_KEY": "local", "OPENAI_BASE_URL": router.local_url.rstrip("/")}
    key = {"openrouter": router.openrouter_key, "groq": router.groq_key, "together": router.together_key,
           "nvidia": router.nvidia_key, "openai": router.openai_key}.get(provider)
    if provider == "nvidia" and not model_id:
        model_id = nvidia_model()
    if key and model_id:
        return {"LLM_PROVIDER": "openai", "DEFAULT_MODEL": model_id,
                "OPENAI_API_KEY": key, "OPENAI_BASE_URL": _OPENAI_COMPAT[provider]}
    return None     # claude, chat-editor, a bare provider name, no key: no score


def score_resume_pdf(pdf_path, model, router, github_token=None, timeout=300):
    """Return the ATS evaluation dict for a resume PDF, or None on any failure."""
    if not (os.path.exists(_PYTHON) and os.path.exists(pdf_path)):
        logger.warning("[ATS] venv python or PDF missing — run hiring_agent setup")
        return None

    provider_env = _provider_env(model, router)
    if not provider_env:
        logger.info(f"[ATS] no usable provider key for model={model!r} — skipping")
        return None

    env = {**os.environ, **provider_env}
    if github_token:
        env["GITHUB_TOKEN"] = github_token

    try:
        proc = subprocess.run(
            [_PYTHON, _WRAPPER, os.path.abspath(pdf_path)],
            cwd=_DIR, env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"[ATS] scoring timed out after {timeout}s")
        return None

    # Wrapper prints exactly one JSON line on stdout (last non-empty line).
    line = next((l for l in reversed(proc.stdout.splitlines()) if l.strip()), "")
    try:
        result = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"[ATS] unparseable output. stderr tail: {proc.stderr[-500:]}")
        return None
    if "error" in result:
        logger.warning(f"[ATS] scorer error: {result['error']}. stderr tail: {proc.stderr[-500:]}")
        return None
    return result


if __name__ == "__main__":  # smoke test: python ats_score.py <pdf> [model]
    import sys
    from llm_router import LLMRouter
    mdl = sys.argv[2] if len(sys.argv) > 2 else os.getenv("DEFAULT_LLM", "gemini-2.5-flash")
    out = score_resume_pdf(sys.argv[1], mdl, LLMRouter())        # keys from the environment
    print(json.dumps(out, indent=2) if out else "None")

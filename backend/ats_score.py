"""
ATS scoring bridge: runs the vendored hiring-agent scorer (backend/hiring_agent)
against a generated resume PDF in an isolated subprocess (its own venv), so its
pinned deps never clash with HireOS's. Best-effort — any failure returns None
and the caller just skips the score.

Uses whatever model generated the resume when the scorer supports its provider
(Gemini, NVIDIA/OpenAI-compatible, Ollama); otherwise falls back to Gemini.
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


def _provider_env(model, gemini_key, nvidia_key):
    """Map the resume's model to the scorer's provider env, or None if we can't
    score it (no usable key)."""
    m = (model or "").lower()

    def gemini_fallback():
        if gemini_key:
            return {"LLM_PROVIDER": "gemini", "DEFAULT_MODEL": "gemini-2.5-flash",
                    "GEMINI_API_KEY": gemini_key}
        return None

    if m.startswith("gemini"):
        if not gemini_key:
            return None
        # resolve_llm can hand us the bare provider name ("gemini"), which is not
        # a valid model id — map it to the default model instead of 404ing.
        model_id = model if "-" in m else "gemini-2.5-flash"
        return {"LLM_PROVIDER": "gemini", "DEFAULT_MODEL": model_id, "GEMINI_API_KEY": gemini_key}

    if m.startswith("nvidia") or m.startswith("minimax"):
        if not nvidia_key:
            return gemini_fallback()
        model_id = model.split(":", 1)[1] if ":" in model else "minimaxai/minimax-m3"
        return {"LLM_PROVIDER": "openai", "DEFAULT_MODEL": model_id,
                "OPENAI_API_KEY": nvidia_key, "OPENAI_BASE_URL": _NVIDIA_BASE}

    if m == "ollama":
        return {"LLM_PROVIDER": "ollama", "DEFAULT_MODEL": os.getenv("ATS_OLLAMA_MODEL", "gemma3:4b")}

    # groq / openrouter / together / claude / chat-editor / unknown → Gemini
    return gemini_fallback()


def score_resume_pdf(pdf_path, model=None, gemini_key=None, nvidia_key=None,
                     github_token=None, timeout=300):
    """Return the ATS evaluation dict for a resume PDF, or None on any failure."""
    if not (os.path.exists(_PYTHON) and os.path.exists(pdf_path)):
        logger.warning("[ATS] venv python or PDF missing — run hiring_agent setup")
        return None

    provider_env = _provider_env(model, gemini_key, nvidia_key)
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
    mdl = sys.argv[2] if len(sys.argv) > 2 else "gemini-2.5-flash"
    out = score_resume_pdf(
        sys.argv[1], model=mdl,
        gemini_key=os.getenv("GEMINI_API_KEY"),
        nvidia_key=os.getenv("NVIDIA_API_KEY"),
    )
    print(json.dumps(out, indent=2) if out else "None")

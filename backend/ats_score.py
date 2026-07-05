"""
ATS scoring bridge: runs the vendored hiring-agent scorer (backend/hiring_agent)
against a generated resume PDF in an isolated subprocess (its own venv), so its
pinned deps never clash with HireOS's. Best-effort — any failure returns None
and the caller just skips the score.
"""

import os
import json
import logging
import subprocess

logger = logging.getLogger(__name__)

_DIR = os.path.join(os.path.dirname(__file__), "hiring_agent")
_PYTHON = os.path.join(_DIR, ".venv", "bin", "python")
_WRAPPER = os.path.join(_DIR, "evaluate.py")


def score_resume_pdf(pdf_path, gemini_key=None, github_token=None, timeout=300):
    """Return the ATS evaluation dict for a resume PDF, or None on any failure.

    Uses the user's Gemini key (same provider HireOS uses); the vendored scorer
    reads LLM_PROVIDER/DEFAULT_MODEL/GEMINI_API_KEY from the env we pass.
    """
    if not gemini_key:
        logger.info("[ATS] no Gemini key — skipping scoring")
        return None
    if not (os.path.exists(_PYTHON) and os.path.exists(pdf_path)):
        logger.warning("[ATS] venv python or PDF missing — run hiring_agent setup")
        return None

    env = {
        **os.environ,
        "LLM_PROVIDER": "gemini",
        "DEFAULT_MODEL": os.getenv("ATS_MODEL", "gemini-2.5-flash"),
        "GEMINI_API_KEY": gemini_key,
    }
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
        logger.warning(f"[ATS] scorer error: {result['error']}")
        return None
    return result


if __name__ == "__main__":  # smoke test: python ats_score.py <pdf>
    import sys
    key = os.getenv("GEMINI_API_KEY")
    out = score_resume_pdf(sys.argv[1], gemini_key=key)
    print(json.dumps(out, indent=2) if out else "None")

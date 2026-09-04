"""Live model catalogues, straight from each provider's own API.

Every provider publishes the list of models a key can actually reach. Reading it
is the only way a model picker stays correct: hardcoded id tables rot on every
release (this repo shipped `claude-haiku-4-5-20251001` long after that id stopped
being the one to use), and a stale id fails at generation time, for the user, not
at deploy time, for us.

Each fetch returns (models, error). `error` is a string the admin panel shows
verbatim -- a rejected key is a normal, expected state here, not a 500.
"""
import os
import time
from typing import Dict, List, Optional, Tuple

import httpx
from loguru import logger

PROVIDERS = ["gemini", "anthropic", "openai", "openrouter", "nvidia"]

# Provider -> the Settings key holding the platform's key for it.
PLATFORM_KEY_SETTING = {p: f"platform_key_{p}" for p in PROVIDERS}

# Provider -> env var used to seed the platform key before one is saved in the DB.
PLATFORM_KEY_ENV = {
    "gemini": "PLATFORM_GEMINI_API_KEY",
    "anthropic": "PLATFORM_ANTHROPIC_API_KEY",
    "openai": "PLATFORM_OPENAI_API_KEY",
    "openrouter": "PLATFORM_OPENROUTER_API_KEY",
    "nvidia": "PLATFORM_NVIDIA_API_KEY",
}

# The provider name the LLM router dispatches on, which is not always our own.
ROUTER_PROVIDER = {"anthropic": "claude"}

# Which router key slot a platform key fills (LLMRouter.__init__ `keys` dict).
ROUTER_KEY_SLOT = {
    "gemini": "gemini",
    "anthropic": "anthropic",
    "openai": "openai",
    "openrouter": "openrouter",
    "nvidia": "nvidia",
}

DEFAULT_PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-2.5-flash"

_TIMEOUT = 20.0

# ponytail: process-local cache, 1h TTL. Model lists change weekly at most, and
# a cache table or a refresh job would be more moving parts than the thing they
# cache. Cleared per-provider by passing refresh=True.
_CACHE: Dict[str, Tuple[float, list, Optional[str]]] = {}
_TTL_SECONDS = 3600


def _cached(provider: str, key: str, refresh: bool):
    if refresh:
        return None
    hit = _CACHE.get(f"{provider}:{key[-6:]}")
    if hit and (time.time() - hit[0]) < _TTL_SECONDS:
        return hit[1], hit[2]
    return None


def _store(provider: str, key: str, models, error):
    _CACHE[f"{provider}:{key[-6:]}"] = (time.time(), models, error)
    return models, error


def _http_error(provider: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return f"{provider}: key rejected ({code})"
        # Gemini answers a malformed key with 400 API_KEY_INVALID rather than a
        # 401, and a bare "HTTP 400" reads as our bug rather than a wrong key.
        if code == 400 and "API_KEY" in exc.response.text.upper():
            return f"{provider}: key rejected (invalid API key)"
        return f"{provider}: HTTP {code}"
    return f"{provider}: {exc.__class__.__name__}: {exc}"


# ── Per-provider fetchers ─────────────────────────────────────────────────────

def _fetch_gemini(key: str):
    r = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": key, "pageSize": 200}, timeout=_TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for m in r.json().get("models", []):
        # The same endpoint returns embedding and vision-only models; only the
        # ones that can answer a prompt belong in a text-generation picker.
        if "generateContent" not in (m.get("supportedGenerationMethods") or []):
            continue
        mid = (m.get("name") or "").split("/", 1)[-1]
        if mid:
            out.append({"id": mid, "label": m.get("displayName") or mid, "extra": None})
    return out


def _fetch_anthropic(key: str):
    r = httpx.get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        params={"limit": 100}, timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return [
        {"id": m["id"], "label": m.get("display_name") or m["id"],
         "extra": f'{m["max_input_tokens"]:,} ctx' if m.get("max_input_tokens") else None}
        for m in r.json().get("data", []) if m.get("id")
    ]


# The /v1/models list also carries embeddings, audio, image and moderation
# models. Nothing in the response marks which ones take a chat completion, so
# the id prefix is the filter available.
_OPENAI_CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
_OPENAI_EXCLUDE = ("-audio", "-realtime", "-transcribe", "-tts", "-image", "-search",
                   "-instruct", "embedding", "moderation", "dall-e", "whisper")


def _fetch_openai(key: str):
    r = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"}, timeout=_TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        mid = m.get("id") or ""
        if not mid.startswith(_OPENAI_CHAT_PREFIXES):
            continue
        if any(bad in mid for bad in _OPENAI_EXCLUDE):
            continue
        out.append({"id": mid, "label": mid, "extra": None})
    return sorted(out, key=lambda m: m["id"])


def _price_per_million(raw) -> Optional[float]:
    """OpenRouter quotes dollars per single token, so the useful number is 1e6x."""
    try:
        return float(raw) * 1_000_000
    except (TypeError, ValueError):
        return None


def _fetch_openrouter(key: str):
    # The only one of the five that lists models without a key, and the only one
    # that publishes per-token prices -- worth surfacing in the picker.
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = httpx.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=_TIMEOUT)
    r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        mid = m.get("id")
        if not mid:
            continue
        pricing = m.get("pricing") or {}
        pin = _price_per_million(pricing.get("prompt"))
        pout = _price_per_million(pricing.get("completion"))
        # Both halves, because output is what the free-tier budget actually runs
        # out of first -- it is priced several times higher than input, so an
        # input-only figure understates the real cost of a model.
        if pin == 0 and (pout or 0) == 0:
            extra = "free"
        elif pin is not None and pout is not None:
            extra = f"${pin:.2f} in / ${pout:.2f} out per M"
        elif pin is not None:
            extra = f"${pin:.2f}/M in"
        else:
            extra = None
        out.append({"id": mid, "label": m.get("name") or mid, "extra": extra})
    return sorted(out, key=lambda m: m["id"])


def _fetch_nvidia(key: str):
    # build.nvidia.com is OpenAI-compatible, so the same /v1/models shape.
    r = httpx.get(
        "https://integrate.api.nvidia.com/v1/models",
        headers={"Authorization": f"Bearer {key}"}, timeout=_TIMEOUT,
    )
    r.raise_for_status()
    return sorted(
        ({"id": m["id"], "label": m["id"], "extra": None}
         for m in r.json().get("data", []) if m.get("id")),
        key=lambda m: m["id"],
    )


_FETCHERS = {
    "gemini": _fetch_gemini,
    "nvidia": _fetch_nvidia,
    "anthropic": _fetch_anthropic,
    "openai": _fetch_openai,
    "openrouter": _fetch_openrouter,
}


def list_models(provider: str, key: str, refresh: bool = False) -> Tuple[List[dict], Optional[str]]:
    """(models, error) for one provider. Never raises -- a bad key is a normal
    state the admin panel renders, not a server error."""
    if provider not in _FETCHERS:
        return [], f"Unknown provider: {provider}"
    # OpenRouter is the one catalogue that is public.
    if not key and provider != "openrouter":
        return [], "No API key configured"

    cached = _cached(provider, key or "", refresh)
    if cached is not None:
        return cached

    try:
        models = _FETCHERS[provider](key)
        return _store(provider, key or "", models, None)
    except Exception as e:
        err = _http_error(provider, e)
        logger.warning(f"[connectors] {err}")
        # Cache the failure too, so a wrong key doesn't mean a 20s timeout on
        # every page load. refresh=True is the way back out.
        return _store(provider, key or "", [], err)

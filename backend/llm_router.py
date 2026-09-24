"""
LLM Router — unified wrapper for all configured LLM providers.

Agents call:
    router = LLMRouter()
    text = await router.complete("Your prompt", llm="gemini")

Fan-out comparison:
    results = await router.compare("Your prompt", providers=["gemini","groq"])
"""
from __future__ import annotations

import os
import json
import time
import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger


class OutputTruncated(RuntimeError):
    """The model hit its output-token ceiling mid-answer.

    Raised instead of returning the partial text: for a resume, a silently
    truncated reply is saved as a document with its tail sections missing and
    nothing anywhere says so. Callers that can afford a bigger budget catch this
    and retry.
    """


def _openai_text(resp, provider: str) -> str:
    """Text from an OpenAI-shaped response, refusing a length-truncated one."""
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise OutputTruncated(f"{provider} hit max_tokens before finishing its answer.")
    return choice.message.content


# Preferred families for the bare "openrouter" choice, best first. Which free
# models OpenRouter serves changes constantly, so the ids are resolved from its
# live catalogue rather than hardcoded -- this default sat on
# meta-llama/llama-3.3-70b-instruct:free long after that id stopped existing,
# so every "OpenRouter (Free)" generation 404'd.
_OPENROUTER_PREFERRED = ("llama", "qwen", "deepseek", "gemma", "mistral")

# Served, but paid -- a working model beats a free 404.
_OPENROUTER_PAID_FALLBACK = "meta-llama/llama-3.3-70b-instruct"


def _openrouter_defaults(key: str, limit: int = 3) -> List[str]:
    """Free models OpenRouter actually serves right now, best first, with a paid
    id last. Several, not one: the free tier is a shared pool that answers 429
    on any given model most of the day, so a single pick is a coin flip."""
    ranked = []
    try:
        import connectors
        free = [m["id"] for m in connectors.list_models("openrouter", key or "")[0]
                if m.get("extra") == "free"]
        for family in _OPENROUTER_PREFERRED:
            ranked += [mid for mid in free if family in mid and mid not in ranked]
        ranked += [mid for mid in free if mid not in ranked]
    except Exception as e:
        logger.warning(f"[LLMRouter] openrouter catalogue lookup failed: {e}")
    return ranked[:limit] + [_OPENROUTER_PAID_FALLBACK]


class LLMRouter:
    """Route prompts to any configured LLM provider."""

    PROVIDER_MAP = {
        "gemini": "_call_gemini",
        "groq": "_call_groq",
        "openrouter": "_call_openrouter",
        "together": "_call_together",
        "ollama": "_call_ollama",
        "claude": "_call_claude",
        "anthropic": "_call_claude",
        "nvidia": "_call_nvidia",
        "minimax": "_call_nvidia",
        "openai": "_call_openai",
        "gpt": "_call_openai",
    }

    def __init__(self, keys: Optional[Dict[str, str]] = None, allow_env: bool = True,
                 on_usage=None):
        # Per-user (BYOK) keys. keys dict uses provider names: gemini, groq, openrouter,
        # together, anthropic, ollama_url, github_token, github_username.
        # allow_env=False enforces strict per-user isolation (no shared server key leakage)
        # for multi-tenant requests; allow_env=True is for server-level / health contexts.
        self._keys = {k: v for k, v in (keys or {}).items() if v}
        self._allow_env = allow_env
        # Called with (input_tokens, output_tokens) after every completed call.
        # Set only for routers running on the platform's key -- a user on their
        # own key is not metered, so there is nothing to charge.
        self.on_usage = on_usage
        self._last_usage = None

    def _note_usage(self, in_tokens, out_tokens) -> None:
        """Stash the provider's own token counts for _complete_one to report."""
        try:
            self._last_usage = (int(in_tokens or 0), int(out_tokens or 0))
        except (TypeError, ValueError):
            self._last_usage = None

    def _openai_result(self, resp, provider: str) -> str:
        """Text + usage from an OpenAI-shaped response (groq/openrouter/together/nvidia/openai)."""
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self._note_usage(getattr(usage, "prompt_tokens", 0),
                             getattr(usage, "completion_tokens", 0))
        return _openai_text(resp, provider)

    def _key(self, name: str, env: str, default: str = "") -> str:
        if self._keys.get(name):
            return self._keys[name]
        if self._allow_env:
            return os.getenv(env, default)
        return default

    # ── Key properties (per-user keys first, env as fallback) ─────────────────

    @property
    def gemini_key(self):
        return self._key("gemini", "GEMINI_API_KEY")

    @property
    def groq_key(self):
        return self._key("groq", "GROQ_API_KEY")

    @property
    def openrouter_key(self):
        return self._key("openrouter", "OPENROUTER_API_KEY")

    @property
    def together_key(self):
        return self._key("together", "TOGETHER_API_KEY")

    @property
    def anthropic_key(self):
        return self._key("anthropic", "ANTHROPIC_API_KEY")

    @property
    def ollama_url(self):
        return self._key("ollama_url", "OLLAMA_BASE_URL", "http://localhost:11434")

    @property
    def github_token(self):
        return self._key("github_token", "GITHUB_TOKEN")

    @property
    def github_username(self):
        return self._key("github_username", "GITHUB_USERNAME")

    @property
    def nvidia_key(self):
        return self._key("nvidia", "NVIDIA_API_KEY")

    @property
    def openai_key(self):
        return self._key("openai", "OPENAI_API_KEY")

    # ── Public API ────────────────────────────────────────────────────────────

    # Quality-ish preference order for graceful fallback. Only providers the user
    # has keys for are tried. Ollama is excluded — it's local and usually offline,
    # so auto-falling to it just adds a slow, confusing failure.
    FALLBACK_ORDER = ["gemini", "claude", "openai", "nvidia", "openrouter", "together", "groq"]

    def _fallback_chain(self, llm: str) -> List[str]:
        """The requested model first, then the user's other providers so a
        failing model gracefully degrades to the next best available one."""
        primary = (llm.split(":", 1)[0] if ":" in llm else llm.split("-")[0]).lower()
        avail = set(self.available_providers())
        chain = [llm]
        for p in self.FALLBACK_ORDER:
            if p in avail and p != primary:
                chain.append(p)  # bare provider name → its default model
        return chain

    async def complete(
        self,
        prompt: str,
        llm: str = "gemini",
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        fallback: bool = True,
    ) -> str:
        """Call an LLM and return the text. On failure, gracefully fall back to
        the next best available provider (unless fallback=False).

        A truncated answer is retried once with a bigger budget rather than
        falling back — the same ceiling truncates on every provider. Reasoning
        models spend thinking tokens out of this same budget, so a caller's
        nominal limit can be consumed before a single output token is written.
        """
        try:
            return await self._complete_chain(prompt, llm, system, max_tokens, temperature, fallback)
        except OutputTruncated as e:
            bumped = min(max(max_tokens * 2, self.MIN_RETRY_TOKENS), self.MAX_RETRY_TOKENS)
            if bumped <= max_tokens:
                raise
            logger.warning(f"[LLMRouter] {e} → retrying at max_tokens={bumped}")
            return await self._complete_chain(prompt, llm, system, bumped, temperature, fallback)

    # Retry budget for a truncated answer. The floor matters more than the
    # doubling: a 300- or 2000-token call has no headroom for thinking tokens.
    MIN_RETRY_TOKENS = 8000
    MAX_RETRY_TOKENS = 32000

    async def _complete_chain(self, prompt, llm, system, max_tokens, temperature, fallback) -> str:
        chain = self._fallback_chain(llm) if fallback else [llm]
        primary_err = None
        for i, model in enumerate(chain):
            try:
                return await self._complete_one(prompt, model, system, max_tokens, temperature)
            except OutputTruncated:
                raise      # a bigger budget is the fix, not a different provider
            except Exception as e:
                if primary_err is None:
                    primary_err = e  # keep the requested model's error — most relevant
                nxt = chain[i + 1] if i + 1 < len(chain) else None
                logger.warning(f"[LLMRouter] {model} failed: {e}" + (f" → falling back to {nxt}" if nxt else " (no more fallbacks)"))
        raise primary_err

    async def _complete_one(self, prompt, llm, system, max_tokens, temperature) -> str:
        """Single dispatch, no fallback."""
        # "provider:model-id" selects a specific model, e.g.
        # "nvidia:meta/llama-3.1-405b-instruct" from build.nvidia.com.
        # Bare "gemini-1.5-pro" → provider "gemini", model = whole string.
        if ":" in llm:
            provider, model = llm.split(":", 1)
            provider = provider.lower()
        else:
            provider = llm.lower().split("-")[0]
            model = llm
        method_name = self.PROVIDER_MAP.get(provider)
        if not method_name:
            raise ValueError(f"Unknown LLM provider: {llm}")
        method = getattr(self, method_name)
        logger.info(f"[LLMRouter] → {llm}")
        t0 = time.monotonic()
        self._last_usage = None
        result = await method(prompt, system=system, model=model, max_tokens=max_tokens, temperature=temperature)
        elapsed = round(time.monotonic() - t0, 2)

        # Single metering point for every agent in the app. Truncation and
        # JSON-schema retries come back through here too, which is right --
        # they cost real money.
        in_tok, out_tok = self._last_usage or (
            # ponytail: ~4 chars per token. Only reached when a provider returns
            # no usage block; it undercounts a little, so it never blocks wrongly.
            (len(prompt) + len(system or "")) // 4,
            len(result or "") // 4,
        )
        if self.on_usage:
            try:
                self.on_usage(in_tok, out_tok)
            except Exception as e:   # metering must never break a generation
                logger.warning(f"[LLMRouter] usage hook failed: {e}")
        logger.info(f"[LLMRouter] ← {llm} ({elapsed}s, {len(result)} chars, {in_tok} in / {out_tok} out tokens)")
        return result

    async def compare(
        self,
        prompt: str,
        providers: List[str],
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> List[Dict[str, Any]]:
        """Fan out to multiple providers concurrently, return all results."""
        tasks = []
        for p in providers:
            tasks.append(self._timed_complete(prompt, p, system, max_tokens))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        output = []
        for provider, result in zip(providers, results):
            if isinstance(result, Exception):
                output.append({"provider": provider, "text": None, "error": str(result), "latency_ms": None})
            else:
                output.append(result)
        return output

    async def _timed_complete(self, prompt, provider, system, max_tokens):
        t0 = time.monotonic()
        # no fallback: comparison must report each provider's real result/error
        text = await self.complete(prompt, llm=provider, system=system, max_tokens=max_tokens, fallback=False)
        return {
            "provider": provider,
            "text": text,
            "error": None,
            "latency_ms": round((time.monotonic() - t0) * 1000),
        }

    async def structured_complete(
        self,
        prompt: str,
        response_model: Any,
        llm: str = "gemini",
        system: Optional[str] = None,
        max_retries: int = 2,
        temperature: float = 0.1
    ) -> Any:
        """
        Forces the LLM to return valid JSON matching the Pydantic response_model.
        Uses a self-healing retry loop if the output fails validation.
        """
        schema_json = response_model.model_json_schema()
        schema_prompt = f"\n\nYou MUST return valid JSON. Your JSON must strictly adhere to the following schema:\n{json.dumps(schema_json, indent=2)}\n\nDo NOT wrap your response in markdown code blocks. Start directly with {{."
        
        current_prompt = prompt + schema_prompt
        
        for attempt in range(max_retries + 1):
            text = await self.complete(current_prompt, llm=llm, system=system, temperature=temperature)
            
            text = text.strip()
            for fence in ("```json", "```", "```md"):
                if text.startswith(fence):
                    text = text[len(fence):]
                    if text.endswith("```"):
                        text = text[:-3]
                    text = text.strip()
                    break
            
            try:
                parsed_dict = json.loads(text)
                return response_model(**parsed_dict)
            except Exception as e:
                if attempt == max_retries:
                    logger.error(f"[Self-Healing Failed] LLM failed to match schema after {max_retries} retries. Last error: {str(e)}")
                    raise ValueError(f"Failed to generate structured output: {e}")
                
                logger.warning(f"[Self-Healing] Attempt {attempt+1} failed. Prompting LLM to fix its mistake: {str(e)}")
                current_prompt += f"\n\nYOUR PREVIOUS OUTPUT FAILED VALIDATION WITH THIS ERROR:\n{str(e)}\n\nPlease fix the JSON and return ONLY the corrected JSON string matching the schema."

    # ── Gemini ────────────────────────────────────────────────────────────────

    async def _call_gemini(self, prompt: str, system=None, model="gemini", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.gemini_key:
            raise RuntimeError("GEMINI_API_KEY not configured. Go to Settings -> LLM Providers and paste your Gemini API key.")
        import google.generativeai as genai
        genai.configure(api_key=self.gemini_key)

        m = model.lower()
        if "3.5-flash" in m:
            model_name = "gemini-3.5-flash"
        elif "3.1-pro" in m:
            model_name = "gemini-3.1-pro-preview"
        elif "3.1-flash-lite" in m:
            model_name = "gemini-3.1-flash-lite"
        elif "3-flash" in m or "3.0-flash" in m or "3.1-flash" in m:
            model_name = "gemini-3-flash-preview"
        elif "2.5-pro" in m:
            model_name = "gemini-2.5-pro"
        elif "2.5-flash-lite" in m:
            model_name = "gemini-2.5-flash-lite"
        elif "2.5-flash" in m:
            model_name = "gemini-2.5-flash"
        elif "2.0-flash-lite" in m:
            model_name = "gemini-2.0-flash-lite"
        elif "2.0-flash" in m:
            model_name = "gemini-2.0-flash"
        # 1.5-* is retired by Google and no longer served -- anything still asking
        # for it falls through to the current default rather than 404-ing.
        else:
            model_name = "gemini-2.5-flash"

        logger.info(f"[Gemini] Using model: {model_name}")
        config = genai.types.GenerationConfig(max_output_tokens=max_tokens, temperature=temperature)
        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        loop = asyncio.get_event_loop()
        gmodel = genai.GenerativeModel(model_name)

        # Retry up to 3x on rate limit (free tier = 5 req/min)
        response = None
        for attempt in range(3):
            try:
                response = await loop.run_in_executor(
                    None, lambda: gmodel.generate_content(full_prompt, generation_config=config)
                )
                break
            except Exception as e:
                if "429" in str(e) or "ResourceExhausted" in str(e) or "Quota" in str(e):
                    # Give up before sleeping on the last attempt -- the old order
                    # burned 60s waiting for a retry it was never going to make.
                    if attempt == 2:
                        raise RuntimeError(
                            f"Gemini rate limit exceeded. Free tier allows 5 req/min. "
                            "Wait 60 seconds and try again."
                        )
                    wait = (attempt + 1) * 20
                    logger.warning(f"[Gemini] Rate limited (attempt {attempt+1}/3), retrying in {wait}s")
                    await asyncio.sleep(wait)
                else:
                    raise

        # finish_reason MAX_TOKENS=2 — thinking tokens can eat the budget on 2.5+/3.x models.
        # Raise rather than return the partial text: the caller retries with a bigger
        # budget, and a half-written resume never reaches the database.
        truncated = False
        try:
            truncated = bool(response and response.candidates
                             and int(response.candidates[0].finish_reason) == 2)
        except Exception:
            pass
        if truncated:
            raise OutputTruncated(
                f"Gemini ({model_name}) hit max_tokens ({max_tokens}) before finishing its answer."
            )

        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            self._note_usage(getattr(meta, "prompt_token_count", 0),
                             getattr(meta, "candidates_token_count", 0))

        # Handle blocked/truncated responses gracefully
        try:
            return response.text
        except ValueError:
            if response and response.candidates:
                parts = response.candidates[0].content.parts if response.candidates[0].content else []
                if parts:
                    return "".join(p.text for p in parts if hasattr(p, "text"))
            finish = response.candidates[0].finish_reason if response and response.candidates else "unknown"
            raise RuntimeError(f"Gemini returned no content (finish_reason={finish}). Try rephrasing the prompt.")

    # ── Groq ──────────────────────────────────────────────────────────────────

    async def _call_groq(self, prompt: str, system=None, model="groq", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.groq_key:
            raise RuntimeError("GROQ_API_KEY not configured.")
        from groq import AsyncGroq
        client = AsyncGroq(api_key=self.groq_key)

        model_name = model if model and model != "groq" else "llama-3.3-70b-versatile"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model_name, messages=messages,
            max_tokens=max_tokens, temperature=temperature
        )
        return self._openai_result(resp, "groq")

    # ── OpenRouter ────────────────────────────────────────────────────────────

    async def _call_openrouter(self, prompt: str, system=None, model="openrouter", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.openrouter_key:
            raise RuntimeError("OPENROUTER_API_KEY not configured.")
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self.openrouter_key, base_url="https://openrouter.ai/api/v1", timeout=90.0, max_retries=1)

        candidates = ([model] if model and model != "openrouter"
                      else _openrouter_defaults(self.openrouter_key))
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_error = None
        for model_name in candidates:
            try:
                resp = await client.chat.completions.create(
                    model=model_name, messages=messages,
                    max_tokens=max_tokens, temperature=temperature
                )
                return self._openai_result(resp, "openrouter")
            except OutputTruncated:
                raise            # a bigger budget is the fix, not another model
            except Exception as e:
                last_error = e
                logger.warning(f"[OpenRouter] {model_name} failed: {e}")
        raise last_error

    # ── Together AI ───────────────────────────────────────────────────────────

    async def _call_together(self, prompt: str, system=None, model="together", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.together_key:
            raise RuntimeError("TOGETHER_API_KEY not configured.")
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self.together_key, base_url="https://api.together.xyz/v1", timeout=90.0, max_retries=1)

        model_name = model if model and model != "together" else "meta-llama/Llama-3-70b-chat-hf"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model_name, messages=messages,
            max_tokens=max_tokens, temperature=temperature
        )
        return self._openai_result(resp, "together")

    # ── Claude (Anthropic) ───────────────────────────────────────────────────

    async def _call_claude(self, prompt: str, system=None, model="claude", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured. Go to Settings -> LLM Providers and paste your Anthropic API key.")
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=self.anthropic_key)

        # An exact id (from the admin model catalogue, e.g. "claude-opus-5") is
        # used verbatim -- the family match below is only for the bare aliases,
        # and rewriting a real id to a hardcoded one is how those go stale.
        m = (model or "").lower()
        if m.startswith("claude-") and m not in ("claude-opus", "claude-sonnet", "claude-haiku"):
            model_name = model
        elif "opus" in m:
            model_name = "claude-opus-4-7"
        elif "haiku" in m:
            model_name = "claude-haiku-4-5-20251001"
        else:
            model_name = "claude-sonnet-4-6"

        logger.info(f"[Claude] Using model: {model_name}")
        kwargs = dict(model=model_name, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
        if system:
            kwargs["system"] = system
        # temperature not supported on extended-thinking models; safe to omit if 1.0
        if temperature != 1.0:
            kwargs["temperature"] = temperature

        resp = await client.messages.create(**kwargs)
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise OutputTruncated(f"Claude ({model_name}) hit max_tokens ({max_tokens}) before finishing.")
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self._note_usage(getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0))
        return resp.content[0].text

    # ── OpenAI ────────────────────────────────────────────────────────────────

    async def _call_openai(self, prompt: str, system=None, model="openai", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.openai_key:
            raise RuntimeError("OPENAI_API_KEY not configured. Go to Settings -> LLM Providers and paste your OpenAI API key.")
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self.openai_key, timeout=90.0, max_retries=1)

        model_name = model if model and model not in ("openai", "gpt") else "gpt-4o-mini"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model_name, messages=messages,
            max_tokens=max_tokens, temperature=temperature
        )
        return self._openai_result(resp, "openai")

    # ── Ollama (local) ────────────────────────────────────────────────────────

    async def _call_ollama(self, prompt: str, system=None, model="ollama", max_tokens=4096, temperature=0.7, **_) -> str:
        import httpx
        base = self.ollama_url.rstrip("/")
        payload = {
            "model": "llama3",
            "prompt": f"{system}\n\n{prompt}" if system else prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            self._note_usage(data.get("prompt_eval_count", 0), data.get("eval_count", 0))
            return data["response"]

    # ── NVIDIA / Minimax ───────────────────────────────────────────────────────

    async def _call_nvidia(self, prompt: str, system=None, model="minimaxai/minimax-m3", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.nvidia_key:
            raise RuntimeError("NVIDIA_API_KEY not configured. Go to Settings -> LLM Providers and paste your NVIDIA API key.")
        from openai import AsyncOpenAI
        # ponytail: short cap + no retries so a slow/hung response fails fast
        # instead of blocking the request for the SDK-default 10 minutes. The 60s
        # default is tuned for the *fallback* leg, reached after the primary
        # already burned a minute. When NVIDIA is the platform's primary provider
        # the big calls (document analysis) legitimately run longer than that, so
        # the cap is tunable -- raise NVIDIA_TIMEOUT_SECONDS rather than editing
        # this, and keep it well under any upstream request timeout.
        timeout_s = float(os.getenv("NVIDIA_TIMEOUT_SECONDS", "60"))
        client = AsyncOpenAI(
            api_key=self.nvidia_key,
            base_url="https://integrate.api.nvidia.com/v1",
            timeout=timeout_s, max_retries=0,
        )

        model_name = model if "/" in model else "minimaxai/minimax-m3"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model_name, messages=messages,
            max_tokens=max_tokens, temperature=temperature
        )
        return self._openai_result(resp, "nvidia")

    def available_providers(self) -> List[str]:
        """Return list of providers that have credentials configured."""
        providers = []
        if self.gemini_key:
            providers.append("gemini")
        if self.anthropic_key:
            providers.append("claude")
        if self.openai_key:
            providers.append("openai")
        if self.groq_key:
            providers.append("groq")
        if self.openrouter_key:
            providers.append("openrouter")
        if self.together_key:
            providers.append("together")
        if self.nvidia_key:
            providers.append("nvidia")
        # Only offer Ollama when a host was actually configured. The hosted
        # deployment has no local Ollama, so advertising it unconditionally put a
        # dead option in every user's model dropdown. Set OLLAMA_BASE_URL (or the
        # ollama_url setting) to get it back for local runs.
        if self._key("ollama_url", "OLLAMA_BASE_URL"):
            providers.append("ollama")
        return providers

    def default_llm(self) -> str:
        """First configured provider — used when no model is explicitly selected.
        Honors 'use the one model the user has' instead of hardcoding gemini."""
        avail = self.available_providers()
        for p in ("gemini", "claude", "groq", "openrouter", "together", "nvidia"):
            if p in avail:
                return p
        return "ollama"

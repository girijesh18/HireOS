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
import time
import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger


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
    }

    def __init__(self, keys: Optional[Dict[str, str]] = None, allow_env: bool = True):
        # Per-user (BYOK) keys. keys dict uses provider names: gemini, groq, openrouter,
        # together, anthropic, ollama_url, github_token, github_username.
        # allow_env=False enforces strict per-user isolation (no shared server key leakage)
        # for multi-tenant requests; allow_env=True is for server-level / health contexts.
        self._keys = {k: v for k, v in (keys or {}).items() if v}
        self._allow_env = allow_env

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

    # ── Public API ────────────────────────────────────────────────────────────

    async def complete(
        self,
        prompt: str,
        llm: str = "gemini",
        system: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """Call a single LLM and return the text response."""
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
        result = await method(prompt, system=system, model=model, max_tokens=max_tokens, temperature=temperature)
        elapsed = round(time.monotonic() - t0, 2)
        logger.info(f"[LLMRouter] ← {llm} ({elapsed}s, {len(result)} chars)")
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
        text = await self.complete(prompt, llm=provider, system=system, max_tokens=max_tokens)
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
        elif "1.5-pro" in m:
            model_name = "gemini-1.5-pro"
        elif "1.5-flash" in m:
            model_name = "gemini-1.5-flash"
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
                    wait = (attempt + 1) * 20
                    logger.warning(f"[Gemini] Rate limited (attempt {attempt+1}/3), retrying in {wait}s")
                    await asyncio.sleep(wait)
                    if attempt == 2:
                        raise RuntimeError(
                            f"Gemini rate limit exceeded. Free tier allows 5 req/min. "
                            "Wait 60 seconds and try again."
                        )
                else:
                    raise

        # Warn on truncation (finish_reason MAX_TOKENS=2) — thinking tokens can eat the budget on 2.5+/3.x models
        try:
            if response and response.candidates and int(response.candidates[0].finish_reason) == 2:
                logger.warning(f"[Gemini] Output TRUNCATED (MAX_TOKENS) on {model_name}. Increase max_tokens.")
        except Exception:
            pass

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

        model_name = "llama-3.3-70b-versatile"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model_name, messages=messages,
            max_tokens=max_tokens, temperature=temperature
        )
        return resp.choices[0].message.content

    # ── OpenRouter ────────────────────────────────────────────────────────────

    async def _call_openrouter(self, prompt: str, system=None, model="openrouter", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.openrouter_key:
            raise RuntimeError("OPENROUTER_API_KEY not configured.")
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self.openrouter_key, base_url="https://openrouter.ai/api/v1", timeout=90.0, max_retries=1)

        model_name = "meta-llama/llama-3.3-70b-instruct:free"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model_name, messages=messages,
            max_tokens=max_tokens, temperature=temperature
        )
        return resp.choices[0].message.content

    # ── Together AI ───────────────────────────────────────────────────────────

    async def _call_together(self, prompt: str, system=None, model="together", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.together_key:
            raise RuntimeError("TOGETHER_API_KEY not configured.")
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self.together_key, base_url="https://api.together.xyz/v1", timeout=90.0, max_retries=1)

        model_name = "meta-llama/Llama-3-70b-chat-hf"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=model_name, messages=messages,
            max_tokens=max_tokens, temperature=temperature
        )
        return resp.choices[0].message.content

    # ── Claude (Anthropic) ───────────────────────────────────────────────────

    async def _call_claude(self, prompt: str, system=None, model="claude", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured. Go to Settings -> LLM Providers and paste your Anthropic API key.")
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=self.anthropic_key)

        m = model.lower()
        if "opus" in m:
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
        return resp.content[0].text

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
            return resp.json()["response"]

    # ── NVIDIA / Minimax ───────────────────────────────────────────────────────

    async def _call_nvidia(self, prompt: str, system=None, model="minimaxai/minimax-m3", max_tokens=4096, temperature=0.7, **_) -> str:
        if not self.nvidia_key:
            raise RuntimeError("NVIDIA_API_KEY not configured. Go to Settings -> LLM Providers and paste your NVIDIA API key.")
        from openai import AsyncOpenAI
        # ponytail: hard 90s cap + no retries so a slow/hung MiniMax response fails
        # fast instead of blocking the request for the SDK-default 10 minutes.
        client = AsyncOpenAI(
            api_key=self.nvidia_key,
            base_url="https://integrate.api.nvidia.com/v1",
            timeout=90.0, max_retries=1,
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
        return resp.choices[0].message.content

    def available_providers(self) -> List[str]:
        """Return list of providers that have credentials configured."""
        providers = []
        if self.gemini_key:
            providers.append("gemini")
        if self.anthropic_key:
            providers.append("claude")
        if self.groq_key:
            providers.append("groq")
        if self.openrouter_key:
            providers.append("openrouter")
        if self.together_key:
            providers.append("together")
        if self.nvidia_key:
            providers.append("nvidia")
        providers.append("ollama")  # Always show local as option
        return providers

    def default_llm(self) -> str:
        """First configured provider — used when no model is explicitly selected.
        Honors 'use the one model the user has' instead of hardcoding gemini."""
        avail = self.available_providers()
        for p in ("gemini", "claude", "groq", "openrouter", "together", "nvidia"):
            if p in avail:
                return p
        return "ollama"

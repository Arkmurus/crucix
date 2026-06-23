"""
Hybrid provider — routes between DeepSeek (complex) and Ollama (simple).
Falls back gracefully if one provider is unavailable.
"""
from __future__ import annotations

import logging
import time
from .provider import LLMProvider, LLMResult
from .openai_compat import OpenAICompatProvider
from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.llm.hybrid")


class HybridProvider(LLMProvider):
    name = "hybrid"

    def __init__(
        self,
        *,
        deepseek_key: str,
        deepseek_model: str = "deepseek-chat",
        deepseek_url: str = "https://api.deepseek.com/v1",
        ollama_model: str = "llama3.1:8b",
        ollama_url: str = "http://localhost:11434",
    ):
        self.primary = OpenAICompatProvider(
            name="deepseek",
            api_key=deepseek_key,
            model=deepseek_model,
            base_url=deepseek_url,
        )
        self.secondary = OpenAICompatProvider(
            name="ollama",
            api_key="",
            model=ollama_model,
            base_url=f"{ollama_url.rstrip('/')}/v1",
            default_timeout=120.0,
        )
        self._ollama_ok: bool | None = None
        self._ollama_checked: float = 0
        self.stats = {
            "deepseek_calls": 0,
            "ollama_calls": 0,
            "fallback_calls": 0,
            "deepseek_tokens": 0,
            "ollama_tokens": 0,
            "started_at": time.time(),
        }

    @property
    def is_configured(self) -> bool:
        return self.primary.is_configured

    async def _is_ollama_ready(self) -> bool:
        now = time.time()
        if self._ollama_ok is not None and now - self._ollama_checked < 300:
            return self._ollama_ok
        import httpx
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{self.secondary._base_url.replace('/v1','')}/api/tags")
                self._ollama_ok = r.is_success
        except Exception:
            self._ollama_ok = False
        self._ollama_checked = now
        return self._ollama_ok

    def _should_use_deepseek(self, system: str, user: str, max_tokens: int) -> bool:
        sl = system.lower()
        ul = user.lower()
        if "autonomous bd intelligence brain" in sl or "crucix" in sl:
            return True
        if "deep reasoning protocol" in sl or "6-step" in sl:
            return True
        if "generate" in ul and ("ideas" in ul or "strategy" in ul):
            return True
        if max_tokens > 2000:
            return True
        if any(k in ul for k in ("compliance", "itar", "export control", "compare", "versus", "which market")):
            return True
        return False

    @fail_wire(module="hybrid", gap_type="engine_failure")
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> LLMResult:
        use_ds = self._should_use_deepseek(system_prompt, user_message, max_tokens)

        if use_ds:
            try:
                r = await self.primary.complete(system_prompt, user_message, max_tokens=max_tokens, timeout=timeout)
                self.stats["deepseek_calls"] += 1
                self.stats["deepseek_tokens"] += r.input_tokens + r.output_tokens
                r.routed_via = "deepseek"
                return r
            except Exception as e:
                logger.warning(f"DeepSeek failed, trying Ollama: {e}")

        if await self._is_ollama_ready():
            try:
                r = await self.secondary.complete(system_prompt, user_message, max_tokens=max_tokens, timeout=120.0)
                self.stats["ollama_calls"] += 1
                self.stats["ollama_tokens"] += r.input_tokens + r.output_tokens
                r.routed_via = "ollama"
                return r
            except Exception as e:
                logger.warning(f"Ollama failed: {e}")

        # Final fallback
        r = await self.primary.complete(system_prompt, user_message, max_tokens=max_tokens, timeout=timeout)
        self.stats["fallback_calls"] += 1
        self.stats["deepseek_tokens"] += r.input_tokens + r.output_tokens
        r.routed_via = "deepseek-fallback"
        return r

    @fail_wire(module="hybrid", gap_type="engine_failure")
    def get_stats(self) -> dict:
        return {
            **self.stats,
            "total_calls": self.stats["deepseek_calls"] + self.stats["ollama_calls"] + self.stats["fallback_calls"],
        }

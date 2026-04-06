"""
LLM Fallback Chain — Automatic failover between providers.

Tries providers in priority order. If the primary fails, automatically
switches to the next available provider. Tracks reliability per provider.

Chain: DeepSeek → Anthropic → OpenAI → Gemini
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from .provider import LLMProvider, LLMResult
from .factory import create_llm_provider

logger = logging.getLogger("aria.llm.fallback")


class FallbackProvider(LLMProvider):
    name = "fallback"

    def __init__(self, providers: list[LLMProvider]):
        """Initialize with ordered list of providers (highest priority first)."""
        self.providers = [p for p in providers if p and p.is_configured]
        self._stats: dict[str, dict] = {}
        for p in self.providers:
            self._stats[p.name] = {"calls": 0, "failures": 0, "last_failure": 0}

        if self.providers:
            logger.info(
                "Fallback chain: %s",
                " → ".join(p.name for p in self.providers),
            )
        else:
            logger.warning("No LLM providers configured in fallback chain")

    @property
    def is_configured(self) -> bool:
        return len(self.providers) > 0

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> LLMResult:
        last_error = None

        for provider in self.providers:
            stats = self._stats.get(provider.name, {})

            # Skip provider if it failed recently (cooldown: 5 minutes)
            if stats.get("last_failure", 0) > time.time() - 300:
                recent_fails = stats.get("failures", 0)
                if recent_fails > 3:
                    logger.debug("Skipping %s (cooling down, %d recent failures)",
                                 provider.name, recent_fails)
                    continue

            try:
                stats["calls"] = stats.get("calls", 0) + 1
                result = await provider.complete(
                    system_prompt, user_message,
                    max_tokens=max_tokens, timeout=timeout,
                )
                # Success — reset failure count
                if stats.get("failures", 0) > 0:
                    logger.info("Provider %s recovered after %d failures",
                                provider.name, stats["failures"])
                stats["failures"] = 0
                result.routed_via = f"fallback:{provider.name}"
                return result

            except Exception as e:
                stats["failures"] = stats.get("failures", 0) + 1
                stats["last_failure"] = time.time()
                last_error = e
                logger.warning(
                    "Provider %s failed (attempt %d): %s — trying next",
                    provider.name, stats["failures"], str(e)[:200],
                )

        # All providers failed
        raise last_error or RuntimeError("All LLM providers failed")

    def get_stats(self) -> dict:
        """Get reliability stats for all providers."""
        return {
            name: {
                "calls": s.get("calls", 0),
                "failures": s.get("failures", 0),
                "reliability": round(
                    1 - (s.get("failures", 0) / max(s.get("calls", 1), 1)), 3
                ),
                "status": "cooling_down"
                    if s.get("last_failure", 0) > time.time() - 300 and s.get("failures", 0) > 3
                    else "active",
            }
            for name, s in self._stats.items()
        }


def create_fallback_chain(
    primary_provider: str,
    primary_key: str,
    primary_model: str = "",
    primary_base_url: str = "",
    fallback_keys: dict[str, str] | None = None,
) -> LLMProvider:
    """Create a fallback chain from environment config.

    fallback_keys: {"anthropic": "sk-...", "openai": "sk-...", "gemini": "..."}
    """
    import os

    providers = []

    # Primary
    primary = create_llm_provider(
        primary_provider, primary_key, primary_model, primary_base_url,
    )
    if primary and primary.is_configured:
        providers.append(primary)

    # Fallbacks from env vars (only if different from primary)
    fallback_configs = [
        ("anthropic", os.getenv("ANTHROPIC_API_KEY", ""), "claude-sonnet-4-6"),
        ("deepseek", os.getenv("DEEPSEEK_API_KEY", ""), "deepseek-chat"),
        ("openai", os.getenv("OPENAI_API_KEY", ""), "gpt-4o-mini"),
        ("gemini", os.getenv("GEMINI_API_KEY", ""), "gemini-2.5-flash"),
    ]

    # Also check explicit fallback keys
    if fallback_keys:
        for name, key in fallback_keys.items():
            if key:
                for i, (cfg_name, _, model) in enumerate(fallback_configs):
                    if cfg_name == name:
                        fallback_configs[i] = (name, key, model)

    for name, key, model in fallback_configs:
        if not key or name == primary_provider:
            continue
        fb = create_llm_provider(name, key, model)
        if fb and fb.is_configured:
            providers.append(fb)

    if len(providers) <= 1:
        # No fallbacks available, return primary directly
        return primary or providers[0] if providers else None

    return FallbackProvider(providers)

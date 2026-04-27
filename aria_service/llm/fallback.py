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

from .provider import LLMProvider, LLMResult, ProviderError
from .factory import create_llm_provider

logger = logging.getLogger("aria.llm.fallback")


class FallbackProvider(LLMProvider):
    name = "fallback"

    def __init__(self, providers: list[LLMProvider]):
        """Initialize with ordered list of providers (highest priority first)."""
        self.providers = [p for p in providers if p and p.is_configured]
        self._stats: dict[str, dict] = {}
        for p in self.providers:
            self._stats[p.name] = {
                "calls": 0, "failures": 0, "last_failure": 0,
                "cooldown_until": 0, "last_kind": "",
            }

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

    # Cooldown policy:
    #   - auth / billing failures: HARD cooldown 30 min — these don't fix themselves
    #   - rate_limit:              short 60s cooldown — transient
    #   - server / timeout / other: 60s cooldown after 2 consecutive failures
    # Any success resets the provider entirely. This prevents the old trap
    # where 3 transient failures within 5 min locked the whole chain into
    # "All LLM providers failed".
    _HARD_COOLDOWN_SECONDS = 1800
    _SOFT_COOLDOWN_SECONDS = 60

    def _cooldown_until(self, stats: dict) -> float:
        return stats.get("cooldown_until", 0)

    def _should_skip(self, stats: dict) -> bool:
        return self._cooldown_until(stats) > time.time()

    def _record_success(self, provider, stats: dict):
        if stats.get("failures", 0) > 0 or stats.get("cooldown_until", 0) > 0:
            logger.info("Provider %s recovered — resetting failure stats", provider.name)
        stats["failures"] = 0
        stats["cooldown_until"] = 0
        stats["last_kind"] = ""

    def _record_failure(self, provider, stats: dict, error: Exception):
        stats["failures"] = stats.get("failures", 0) + 1
        stats["last_failure"] = time.time()

        # Classify by ProviderError kind when possible
        kind = getattr(error, "kind", None) or "other"
        retryable = getattr(error, "retryable", True)
        stats["last_kind"] = kind

        now = time.time()
        if kind in ("auth", "billing") or not retryable:
            # F29 fix 2026-04-27: when N parallel calls all hit the same
            # billing/auth failure (live: 5 Anthropic POSTs in 196ms after
            # cold-start ingest), each independently triggers this branch
            # and emits an ERROR log. The cooldown is already set by the
            # first to land — debounce the rest so we get one ERROR per
            # cooldown event, not N. We still record the failure count so
            # health metrics stay accurate.
            existing_cooldown = stats.get("cooldown_until", 0)
            new_cooldown = now + self._HARD_COOLDOWN_SECONDS
            if existing_cooldown > now and (existing_cooldown - now) > self._HARD_COOLDOWN_SECONDS - 5:
                # A peer call set the cooldown within the last 5 seconds.
                # Don't re-set or re-log; the in-flight burst is racing.
                logger.debug(
                    "Provider %s HARD cooldown (%s) re-fired by burst peer; not re-logging",
                    provider.name, kind,
                )
            else:
                stats["cooldown_until"] = new_cooldown
                logger.error(
                    "Provider %s HARD cooldown (%s) for %ds: %s",
                    provider.name, kind, self._HARD_COOLDOWN_SECONDS, str(error)[:200],
                )
        elif kind == "rate_limit":
            stats["cooldown_until"] = now + self._SOFT_COOLDOWN_SECONDS
            logger.warning(
                "Provider %s rate-limited, soft cooldown %ds",
                provider.name, self._SOFT_COOLDOWN_SECONDS,
            )
        else:
            # Only cool down after 2 consecutive failures for soft errors.
            if stats["failures"] >= 2:
                stats["cooldown_until"] = now + self._SOFT_COOLDOWN_SECONDS
                logger.warning(
                    "Provider %s soft cooldown %ds after %d failures: %s",
                    provider.name, self._SOFT_COOLDOWN_SECONDS,
                    stats["failures"], str(error)[:200],
                )
            else:
                logger.warning(
                    "Provider %s failed (%d): %s — trying next",
                    provider.name, stats["failures"], str(error)[:200],
                )

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 90.0,  # bumped from 60s — DeepSeek needs more for complex queries
    ) -> LLMResult:
        """Try each provider in order. The caller's `timeout` is the
        budget for the PRIMARY provider. If the primary fails FAST (e.g.
        rate-limit, 500 error, quick connect refusal), the secondary
        provider gets whatever time is left in the caller's budget after
        accounting for elapsed time. If the primary fails SLOW (burns
        the whole budget on a timeout), we don't try the secondary at
        all — the outer caller's wall clock is already exhausted.

        Previous design (2026-04-11 first pass) split the timeout
        evenly — each provider got (caller_timeout * 0.9) / N. With
        N=2 and caller=75s that gave each provider ~34s, which is too
        short for a real LLM synthesis call on a 4KB context, and both
        providers then timed out mid-generation. Hanwha Redback
        incident (2026-04-11 22:21) surfaced this.

        New design: primary gets full budget, secondary gets remainder.
        """
        last_error = None
        import time as _t
        t_start = _t.monotonic()

        for provider in self.providers:
            stats = self._stats.get(provider.name, {})
            if self._should_skip(stats):
                logger.debug("Skipping %s (cooling down, %d recent failures)",
                             provider.name, stats.get("failures", 0))
                continue

            elapsed = _t.monotonic() - t_start
            remaining = max(0.0, timeout - elapsed)
            # Skip this provider if the outer budget is effectively spent.
            # 15s is the floor for any useful LLM call.
            if remaining < 15.0:
                logger.warning(
                    "Fallback budget exhausted (%.1fs remaining); skipping %s",
                    remaining, provider.name,
                )
                break
            # Each provider gets min(caller_timeout, remaining). The
            # primary sees the full caller_timeout; the secondary sees
            # whatever is left after the primary either succeeded fast
            # or failed fast.
            per_call = min(timeout, remaining)

            try:
                stats["calls"] = stats.get("calls", 0) + 1
                result = await provider.complete(
                    system_prompt, user_message,
                    max_tokens=max_tokens, timeout=per_call,
                )
                self._record_success(provider, stats)
                result.routed_via = f"fallback:{provider.name}"
                return result

            except Exception as e:
                self._record_failure(provider, stats, e)
                last_error = e

        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(
            "fallback",
            "all LLM providers failed — try again in a minute",
            kind="other", retryable=True, cause=last_error,
        )

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done=None,
    ):
        """Streaming with fallback — tries providers in order."""
        last_error = None

        for provider in self.providers:
            stats = self._stats.get(provider.name, {})
            if self._should_skip(stats):
                logger.debug("Skipping %s for stream (cooling down)", provider.name)
                continue

            try:
                stats["calls"] = stats.get("calls", 0) + 1
                async for chunk in provider.stream(
                    system_prompt, user_message,
                    max_tokens=max_tokens, timeout=timeout, on_done=on_done,
                ):
                    yield chunk
                self._record_success(provider, stats)
                return  # stream completed successfully

            except Exception as e:
                self._record_failure(provider, stats, e)
                last_error = e

        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(
            "fallback",
            "all LLM providers failed (stream) — try again in a minute",
            kind="other", retryable=True, cause=last_error,
        )

    def get_stats(self) -> dict:
        """Get reliability stats for all providers."""
        return {
            name: {
                "calls": s.get("calls", 0),
                "failures": s.get("failures", 0),
                "reliability": round(
                    1 - (s.get("failures", 0) / max(s.get("calls", 1), 1)), 3
                ),
                "status": "cooling_down" if s.get("cooldown_until", 0) > time.time() else "active",
                "cooldown_until": s.get("cooldown_until", 0),
                "last_kind": s.get("last_kind", ""),
            }
            for name, s in self._stats.items()
        }

    def get_health(self) -> dict:
        """Chain-level health summary.

        Consumers should prefer this over raw get_stats() when deciding
        "is the LLM layer working?". A cooling provider is the chain
        working AS DESIGNED — the right signal is whether ≥1 provider
        is available to serve the next request.
        """
        now = time.time()
        active: list[str] = []
        cooling: list[dict] = []
        for p in self.providers:
            s = self._stats.get(p.name, {})
            cd = s.get("cooldown_until", 0)
            if cd > now:
                cooling.append({
                    "name": p.name,
                    "reason": s.get("last_kind") or "unknown",
                    "seconds_remaining": int(cd - now),
                })
            else:
                active.append(p.name)
        chain_order = [p.name for p in self.providers]
        return {
            "active_providers": active,
            "cooling_providers": cooling,
            "resilient": len(active) > 0,
            "primary_active": bool(active and chain_order and active[0] == chain_order[0]),
            "serving_provider": active[0] if active else None,
            "chain_order": chain_order,
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

    # Fallbacks from env vars (only if different from primary).
    # Order is intentional — each entry is an independent billing domain,
    # auth path, and infrastructure provider. For ARIA to lose all LLM
    # access, ALL of the configured providers would have to fail at once.
    # Added groq 2026-04-17: 14,400 req/day free tier on Llama-3.1-70B
    # widens the "never wipes out" floor.
    fallback_configs = [
        ("anthropic", os.getenv("ANTHROPIC_API_KEY", ""), "claude-sonnet-4-6"),
        ("deepseek",  os.getenv("DEEPSEEK_API_KEY", ""),  "deepseek-chat"),
        ("groq",      os.getenv("GROQ_API_KEY", ""),      "llama-3.3-70b-versatile"),
        ("openai",    os.getenv("OPENAI_API_KEY", ""),    "gpt-4o-mini"),
        ("gemini",    os.getenv("GEMINI_API_KEY", ""),    "gemini-2.5-flash"),
    ]

    # Also check explicit fallback keys
    if fallback_keys:
        for name, key in fallback_keys.items():
            if key:
                for i, (cfg_name, _, model) in enumerate(fallback_configs):
                    if cfg_name == name:
                        fallback_configs[i] = (name, key, model)

    _dropped = []
    for name, key, model in fallback_configs:
        if name == primary_provider:
            continue
        if not key:
            _dropped.append((name, "missing API key"))
            continue
        fb = create_llm_provider(name, key, model)
        if fb and fb.is_configured:
            providers.append(fb)
        else:
            _dropped.append((name, "provider returned not-configured"))

    # Loudly announce the final state — ops needs to see both what's
    # active and what got silently dropped, because a missing
    # ANTHROPIC_API_KEY used to hide itself until DeepSeek hit a 402.
    if providers:
        logger.info("LLM fallback chain active: %s", " → ".join(p.name for p in providers))
    else:
        logger.error("LLM fallback chain EMPTY — no provider configured!")
    for name, reason in _dropped:
        logger.warning("LLM fallback '%s' skipped — %s. Set its env var to enable resilience.", name, reason)

    if len(providers) <= 1:
        # No fallbacks available, return primary directly
        return primary or providers[0] if providers else None

    return FallbackProvider(providers)

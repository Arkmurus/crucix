"""
LLM Fallback Chain — Automatic failover between providers.

Tries providers in priority order. If the primary fails, automatically
switches to the next available provider. Tracks reliability per provider.

Chain: DeepSeek → Anthropic → OpenAI → Gemini
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from .provider import LLMProvider, LLMResult, ProviderError
from .factory import create_llm_provider

logger = logging.getLogger("aria.llm.fallback")

# F68 fix 2026-04-28: HARD cooldowns (auth/billing) are mirrored to Redis
# so they survive restarts. Without this, every fly.io restart re-probed
# the failed backend and burned 5 calls before the in-process cooldown
# re-engaged. Soft cooldowns (rate_limit, server) are left in-memory only
# — those failure modes ARE often transient and re-probing is fine.
_REDIS_KEY_PREFIX = "crucix:aria:llm:cooldown:"


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
    #   - auth failures:           HARD cooldown 30 min — operator may rotate
    #                              the key in minutes; re-probe is acceptable
    #   - billing failures:        HARD cooldown 24h (R-F678) — top-ups are
    #                              not instant + the operator has explicitly
    #                              committed to NOT topping up Anthropic at
    #                              this stage (2026-05-18). 30-min re-probes
    #                              wasted 96 calls/day on a provider with no
    #                              credit. 24h slashes that to ≤2 wasted calls
    #                              per race window per day. Operator can
    #                              force-reset by setting the secret to fresh
    #                              billing and bouncing the machine, which
    #                              clears in-memory stats AND the redis-mirror
    #                              TTL has already expired.
    #   - rate_limit:              short 60s cooldown — transient
    #   - server / timeout / other: 60s cooldown after 2 consecutive failures
    # Any success resets the provider entirely. This prevents the old trap
    # where 3 transient failures within 5 min locked the whole chain into
    # "All LLM providers failed".
    _HARD_COOLDOWN_SECONDS = 1800
    _HARD_BILLING_COOLDOWN_SECONDS = 86400  # R-F678 (2026-05-18) — see note above
    _SOFT_COOLDOWN_SECONDS = 60

    @classmethod
    def _hard_cooldown_for_kind(cls, kind: str) -> int:
        """R-F678: pick the right HARD cooldown duration for the failure kind.
        Billing failures get the long lock; auth + non-retryable still use
        the 30-min lock so a key rotation can recover quickly."""
        if kind == "billing":
            return cls._HARD_BILLING_COOLDOWN_SECONDS
        return cls._HARD_COOLDOWN_SECONDS

    # F94 fix 2026-04-30: each non-cooling provider gets its own
    # `timeout`-second budget (not a slice of a shared wall clock).
    # Previous "primary gets full, secondary gets remainder" design left
    # the secondary with 0.0s when the primary either burned the budget
    # on a slow timeout OR fast-failed in a way the caller's timeout
    # already considered "elapsed". A fallback that gets 0s isn't a
    # fallback — it's a skip. We bound chain wall-clock with
    # _MAX_FALLBACK_ATTEMPTS so a pathological all-timeout cascade can't
    # run for `timeout * len(providers)` seconds.
    _MAX_FALLBACK_ATTEMPTS = 3
    _PROVIDER_MIN_BUDGET = 15.0  # floor below which a call isn't worth starting

    def _cooldown_until(self, stats: dict) -> float:
        return stats.get("cooldown_until", 0)

    def _should_skip(self, stats: dict) -> bool:
        return self._cooldown_until(stats) > time.time()

    def _fallback_chain_has_healthy_peer(self, failed_provider_name: str) -> bool:
        """R-F681 (2026-05-18) — True iff at least one provider OTHER than
        the one about to be cooled is currently servable (not in cooldown).
        Used to decide log-level on HARD billing cooldowns: per CLAUDE.md
        §14 (Fallback transparency), a cooled provider with a healthy
        fallback is operational, not degraded — and should NOT pollute
        the ERROR ledger that gates #3 reads from.

        Defensive: if `self.providers` is missing (legacy test fixtures
        that build via __new__ without the constructor), assume no
        healthy peer — falls through to ERROR which is the safe default
        for "system state unknown"."""
        providers = getattr(self, "providers", None) or []
        if not providers:
            return False
        now = time.time()
        for peer in providers:
            if peer.name == failed_provider_name:
                continue
            peer_stats = self._stats.get(peer.name, {})
            if peer_stats.get("cooldown_until", 0) <= now:
                return True
        return False

    def _record_success(self, provider, stats: dict):
        had_hard_cooldown = stats.get("last_kind") in ("auth", "billing") and stats.get("cooldown_until", 0) > 0
        if stats.get("failures", 0) > 0 or stats.get("cooldown_until", 0) > 0:
            logger.info("Provider %s recovered — resetting failure stats", provider.name)
        stats["failures"] = 0
        stats["cooldown_until"] = 0
        stats["last_kind"] = ""
        # F68: clear the Redis-mirrored cooldown so subsequent restarts
        # don't re-apply a stale cooldown after the operator topped up.
        if had_hard_cooldown:
            self._clear_redis_cooldown(provider.name)
        # R-F1059 — wire provider recovery to brain
        try:
            from ..intel.engine_wiring import wire_success as _ws
            _ws(
                module="llm_fallback",
                summary=f"Provider recovered: {provider.name}",
                detail=f"kind={stats.get('last_kind', 'unknown')}",
                source_id=f"llm_fallback:recovered:{provider.name}",
            )
        except Exception:
            pass

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
            #
            # R-F678 (2026-05-18): cooldown duration is now kind-specific —
            # billing failures get 24h; auth + non-retryable keep 30min.
            # The debounce window is "the cooldown was set within the last
            # 5 seconds, so we're in the burst race", which still works
            # because we compare against `hard_cooldown - 5` for the
            # appropriate kind.
            hard_cooldown = self._hard_cooldown_for_kind(kind)
            existing_cooldown = stats.get("cooldown_until", 0)
            new_cooldown = now + hard_cooldown
            if existing_cooldown > now and (existing_cooldown - now) > hard_cooldown - 5:
                # A peer call set the cooldown within the last 5 seconds.
                # Don't re-set or re-log; the in-flight burst is racing.
                logger.debug(
                    "Provider %s HARD cooldown (%s) re-fired by burst peer; not re-logging",
                    provider.name, kind,
                )
            else:
                stats["cooldown_until"] = new_cooldown
                # R-F681 (2026-05-18) — log-level depends on whether the
                # fallback chain still has a healthy provider. CLAUDE.md
                # §14: "When a provider cools down and a fallback serves,
                # ARIA reports 'operational', never 'degraded'. Cooling ≠
                # broken." Gate #3 (0 fly ERRORs/7d) was failing because
                # the once-per-24h billing cooldown ERROR-line was being
                # counted in the error ledger even though DeepSeek was
                # serving every chat call. Demote to WARNING when:
                #   - kind is "billing" (operator-pending top-up, not a
                #     surprise — explicitly deferred 2026-05-18), AND
                #   - at least one other provider in the chain is not
                #     currently cooling (so user requests still succeed)
                # Auth failures stay ERROR — those often need urgent
                # operator action (key rotation).
                healthy_peer_exists = self._fallback_chain_has_healthy_peer(
                    failed_provider_name=provider.name,
                )
                if kind == "billing" and healthy_peer_exists:
                    logger.warning(
                        "Provider %s HARD cooldown (%s) for %ds — fallback "
                        "chain still has healthy provider, system operational: %s",
                        provider.name, kind, hard_cooldown, str(error)[:200],
                    )
                else:
                    logger.error(
                        "Provider %s HARD cooldown (%s) for %ds: %s",
                        provider.name, kind, hard_cooldown, str(error)[:200],
                    )
                # F68: mirror to Redis so a fly restart / new VM honours
                # the cooldown instead of re-probing the failed backend.
                self._mirror_cooldown_to_redis(
                    provider.name, kind, new_cooldown,
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
        # R-F1059 — wire provider failure to brain (debounced: only on first
        # failure or when kind changes, not on every burst retry)
        try:
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(
                module="llm_fallback",
                detail=f"Provider {provider.name} failed: kind={kind} failures={stats['failures']} error={error}",
                gap_type="llm_provider_failure",
                source="llm_fallback",
            )
        except Exception:
            pass

    # ── F68: Redis cooldown mirror (HARD cooldowns only) ────────────────

    @staticmethod
    def _redis_key(provider_name: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{provider_name}"

    def _mirror_cooldown_to_redis(self, provider_name: str, kind: str, cooldown_until: float) -> None:
        """Fire-and-forget write of a HARD cooldown to Redis. Never blocks
        the LLM hot path — failures are logged at debug only."""
        async def _write():
            try:
                from ..intel import redis_store as rs
                payload = json.dumps({"until": cooldown_until, "kind": kind})
                # TTL trimmed to remaining seconds so the key auto-cleans
                # the moment the in-memory cooldown would have expired.
                ttl = max(1, int(cooldown_until - time.time()))
                await rs.set(self._redis_key(provider_name), payload, ex=ttl)
            except Exception as e:
                logger.debug("redis cooldown mirror failed (non-fatal): %s", e)

        try:
            asyncio.get_running_loop().create_task(_write())
        except RuntimeError:
            # No running loop — happens in tests where _record_failure is
            # exercised synchronously. Nothing to mirror; acceptable.
            pass

    def _clear_redis_cooldown(self, provider_name: str) -> None:
        async def _del():
            try:
                from ..intel import redis_store as rs
                await rs.delete(self._redis_key(provider_name))
            except Exception as e:
                logger.debug("redis cooldown clear failed (non-fatal): %s", e)

        try:
            asyncio.get_running_loop().create_task(_del())
        except RuntimeError:
            pass

    async def hydrate_from_redis(self) -> int:
        """Read mirrored HARD cooldowns from Redis and apply them to the
        in-memory _stats. Called once during lifespan startup. Returns
        the number of cooldowns rehydrated."""
        try:
            from ..intel import redis_store as rs
        except Exception:
            return 0
        count = 0
        now = time.time()
        for provider in self.providers:
            try:
                raw = await rs.get(self._redis_key(provider.name))
                if not raw:
                    continue
                data = json.loads(raw)
                until = float(data.get("until", 0))
                kind = str(data.get("kind", "billing"))
                if until <= now:
                    # Stale; let it expire on its own TTL
                    continue
                stats = self._stats.setdefault(provider.name, {
                    "calls": 0, "failures": 0, "last_failure": 0,
                    "cooldown_until": 0, "last_kind": "",
                })
                stats["cooldown_until"] = until
                stats["last_kind"] = kind
                count += 1
                logger.warning(
                    "Provider %s HARD cooldown (%s) rehydrated from Redis "
                    "— %ds remaining",
                    provider.name, kind, int(until - now),
                )
            except Exception as e:
                logger.debug(
                    "redis cooldown hydrate failed for %s (non-fatal): %s",
                    provider.name, e,
                )
        return count

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 90.0,  # bumped from 60s — DeepSeek needs more for complex queries
        prefer_provider: str = "",
    ) -> LLMResult:
        """Try each non-cooling provider with its OWN `timeout`-second
        budget, up to ``_MAX_FALLBACK_ATTEMPTS`` providers.

        ``prefer_provider`` (R-F1366): per-call reorder — the named provider
        is tried FIRST, the rest follow in normal chain order as fallback.
        Cooldowns are still respected; an unknown name is a no-op. Used by
        the coder path (/api/aria/coder/llm) to pin DeepSeek as its main
        LLM while the sovereign 14B holds the chain head for chat.

        Design history:
          - 2026-04-11: split caller_timeout evenly across providers.
            With N=2 and caller=75s, each got ~34s — too tight for a
            4KB synthesis call, both timed out (Hanwha incident).
          - 2026-04-11 fix: primary got full caller_timeout, secondary
            got the remainder. Worked for slow primaries that succeeded;
            broke for primaries that fast-failed in a way that left the
            secondary with <15s and triggered "Fallback budget
            exhausted" → no fallback attempted (F3 / 2026-04-30 logs).
          - 2026-04-30 (F94, this file): each provider gets a full
            ``timeout``-second call. Capped at ``_MAX_FALLBACK_ATTEMPTS``
            attempts so an all-timeout cascade can't run forever. The
            caller is responsible for any outer wall-clock guard.
        """
        last_error = None
        attempted = 0

        # R-F1366 — per-call provider preference (see docstring).
        order = self.providers
        if prefer_provider:
            preferred = [p for p in self.providers if p.name == prefer_provider]
            if preferred:
                order = preferred + [
                    p for p in self.providers if p.name != prefer_provider
                ]
            else:
                logger.debug(
                    "prefer_provider=%r not in chain %s — using normal order",
                    prefer_provider, [p.name for p in self.providers],
                )

        for provider in order:
            if attempted >= self._MAX_FALLBACK_ATTEMPTS:
                logger.warning(
                    "Fallback chain stopped after %d attempts; %d providers untried",
                    attempted, len(order) - attempted,
                )
                break

            stats = self._stats.get(provider.name, {})
            if self._should_skip(stats):
                logger.debug("Skipping %s (cooling down, %d recent failures)",
                             provider.name, stats.get("failures", 0))
                continue

            # Each non-cooling provider gets a full per-call budget.
            # If the caller passed an unreasonably small timeout, raise
            # to the floor — anything less isn't worth dialling out for.
            per_call = max(timeout, self._PROVIDER_MIN_BUDGET)

            try:
                attempted += 1
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
        """Streaming with fallback — tries providers in order.

        R-F402 (2026-05-13): enforce the same ``_MAX_FALLBACK_ATTEMPTS``
        cap that ``complete()`` (line 263) uses, so an all-timeout
        streaming cascade can't run forever. Each provider gets a full
        ``timeout``-second window; with 6 providers configured today
        and a default 120s timeout, an unbounded cascade could burn
        12 minutes before raising. The cap matches the non-streaming
        path's behaviour pinned in R-F94 (2026-04-30).
        """
        last_error = None
        attempted = 0

        for provider in self.providers:
            if attempted >= self._MAX_FALLBACK_ATTEMPTS:
                logger.warning(
                    "Stream fallback chain stopped after %d attempts; %d providers untried",
                    attempted, len(self.providers) - attempted,
                )
                break

            stats = self._stats.get(provider.name, {})
            if self._should_skip(stats):
                logger.debug("Skipping %s for stream (cooling down)", provider.name)
                continue

            try:
                attempted += 1
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

    # R-F93 (2026-05-09): ARIA-LLM (sovereign vLLM endpoint) takes
    # FIRST PRIORITY in the chain when ARIA_LLM_URL is set. This is
    # the Phase 4 transition point — once weights deploy and the URL
    # is set, ARIA-LLM serves all chat / DD / audit-grade calls with
    # the existing chain demoted to break-glass fallback.
    _aria_llm_url = (os.getenv("ARIA_LLM_URL") or "").strip()
    if _aria_llm_url:
        # ARIA-LLM speaks OpenAI-compatible API; reuse the OpenAICompatProvider.
        aria_llm = create_llm_provider(
            "openai",
            os.getenv("ARIA_LLM_KEY", "sovereign"),
            os.getenv("ARIA_LLM_MODEL", "aria-llm-v0.1"),
            base_url=_aria_llm_url.rstrip("/"),
        )
        if aria_llm and aria_llm.is_configured:
            # Rename so logs are clear about what's serving
            try:
                aria_llm.name = "aria_llm"
            except Exception:
                pass
            providers.append(aria_llm)
            logger.info(
                "ARIA-LLM (R-F93 sovereign) configured at %s — taking primary position",
                _aria_llm_url,
            )

    # Primary (configured via env vars in main.py — typically Anthropic)
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
    # R-F322 (2026-05-11): Anthropic moved to OPT-IN. Live fly logs at
    # 20:32:06 showed HTTP 400 "credit balance too low" with HARD
    # cooldown 1800s — Anthropic has been billing-exhausted for weeks
    # per memory, and every cycle still probes it before the fallback
    # cascades to DeepSeek. DeepSeek serves the load fine.
    # Default behaviour: Anthropic DISABLED unless ARIA_ANTHROPIC_ENABLED=1.
    # Operator tops up + sets the env to re-enable.
    _anthropic_enabled = (
        os.getenv("ARIA_ANTHROPIC_ENABLED", "").lower() in ("1", "true", "yes")
    )
    fallback_configs = [
        ("deepseek",  os.getenv("DEEPSEEK_API_KEY", ""),  "deepseek-chat"),
        ("groq",      os.getenv("GROQ_API_KEY", ""),      "llama-3.3-70b-versatile"),
        ("openai",    os.getenv("OPENAI_API_KEY", ""),    "gpt-4o-mini"),
        ("gemini",    os.getenv("GEMINI_API_KEY", ""),    "gemini-2.5-flash"),
    ]
    if _anthropic_enabled:
        # Operator explicitly re-enabled — prepend at the head
        fallback_configs.insert(
            0,
            ("anthropic", os.getenv("ANTHROPIC_API_KEY", ""), "claude-sonnet-4-6"),
        )

    # R-F87 (2026-05-09): self-hosted local LLM via Ollama / vLLM.
    # When OLLAMA_URL is set, register the local provider as an
    # additional fallback option. The tier-router (R-F87a) decides
    # which calls actually go to local vs cloud — this just makes
    # local available as a fallback in the chain.
    # Phase 1 of the independence roadmap.
    _ollama_url = (os.getenv("OLLAMA_URL") or "").strip()
    if _ollama_url:
        # R-F194 (2026-05-11) — independence posture controls.
        # ARIA_LOCAL_LLM_PRIMARY=1 → local LLM goes to the FRONT of the
        # fallback chain (right after sovereign ARIA-LLM if configured).
        # ARIA_LOCAL_LLM_PREFERRED=1 → local LLM goes ahead of paid
        # providers but behind the primary (e.g. Anthropic stays first).
        # Default → local stays at the back as break-glass.
        #
        # This is the operator-visible knob the independence roadmap
        # called for: "60-80% of LLM calls served by local/free providers"
        # (Phase 1 exit criterion). With ARIA_LOCAL_LLM_PREFERRED=1, all
        # student / research / autonomous tool dispatch goes local first.
        _local_primary = (os.getenv("ARIA_LOCAL_LLM_PRIMARY") or "").lower() in ("1", "true", "yes")
        _local_preferred = (os.getenv("ARIA_LOCAL_LLM_PREFERRED") or "").lower() in ("1", "true", "yes")
        _ollama_tuple = (
            "ollama",
            "local",
            (os.getenv("OLLAMA_MODEL") or "llama3.1:8b-instruct"),
        )
        if _local_primary:
            # Goes to position 0 of fallback_configs — even ahead of
            # the legacy primary in the post-sort. If primary IS ollama,
            # the dedupe loop below catches it.
            fallback_configs.insert(0, _ollama_tuple)
            logger.info("R-F194: ollama set as PRIMARY (ARIA_LOCAL_LLM_PRIMARY=1)")
        elif _local_preferred:
            # Position right after Anthropic. Anthropic stays customer-
            # facing chat / audit-grade; ollama serves the high-volume
            # autonomous + student + research workload first.
            fallback_configs.insert(1, _ollama_tuple)
            logger.info("R-F194: ollama PREFERRED over deepseek/groq/openai/gemini (ARIA_LOCAL_LLM_PREFERRED=1)")
        else:
            fallback_configs.append(_ollama_tuple)

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
        # R-F87: ollama needs the OLLAMA_URL forwarded so the factory
        # can build the OpenAI-compatible base_url. Other providers
        # ignore the ollama_url arg.
        if name == "ollama":
            fb = create_llm_provider(
                name, key, model,
                ollama_url=_ollama_url,
                ollama_model=model,
            )
        else:
            fb = create_llm_provider(name, key, model)
        if fb and fb.is_configured:
            providers.append(fb)
        else:
            _dropped.append((name, "provider returned not-configured"))

    # R-F194 follow-up (2026-05-11 verification): the insert(0) into
    # fallback_configs above doesn't actually put ollama at position 0
    # of the FINAL providers list because the configured primary
    # (typically anthropic) was added separately at the top of this
    # function (line ~439) before the fallback loop ran. So ollama
    # always landed at position 2 (after ARIA-LLM + primary) regardless
    # of which flag was set.
    #
    # Fix: when ARIA_LOCAL_LLM_PRIMARY=1, lift ollama to the front of
    # providers AFTER ARIA-LLM (R-F93) — that's the closest the chain
    # can come to "ollama is the front-line model" without rewriting
    # the caller signature. Sovereign ARIA-LLM still wins when set.
    if _ollama_url and _local_primary:
        try:
            ollama_idx = next(
                (i for i, p in enumerate(providers) if getattr(p, "name", "") == "ollama"),
                -1,
            )
            if ollama_idx > 0:
                aria_llm_present = bool(providers) and getattr(providers[0], "name", "") == "aria_llm"
                target_idx = 1 if aria_llm_present else 0
                if ollama_idx != target_idx:
                    p = providers.pop(ollama_idx)
                    providers.insert(target_idx, p)
                    logger.info(
                        "R-F194 PRIMARY: moved ollama to position %d (after %d sovereign provider%s)",
                        target_idx, target_idx, "s" if target_idx != 1 else "",
                    )
        except Exception as _re_e:
            logger.debug("R-F194 primary reorder failed (non-fatal): %s", _re_e)

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

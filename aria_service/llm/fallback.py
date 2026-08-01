"""
LLM Fallback Chain — Automatic failover between providers.

Tries providers in priority order. If the primary fails, automatically
switches to the next available provider. Tracks reliability per provider.

Chain: DeepSeek → Anthropic → OpenAI → Gemini
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import time
from typing import Optional

from .provider import LLMProvider, LLMResult, ProviderError
from .factory import create_llm_provider
from .openai_compat import KIND_REASONING_TRUNCATED  # R-F3627 — cause-aware paging

from ..intel.wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.llm.fallback")

# R-F3477 — how long a total chain exhaustion keeps `resilient` false. Long
# enough that an operator polling /health sees the outage, short enough that the
# chain reports healthy again on its own once calls stop failing. A successful
# call clears it immediately, so this TTL only matters when nothing calls at all.
_CHAIN_EXHAUSTION_TTL_S = float(os.getenv("ARIA_LLM_EXHAUSTION_TTL_S", "120"))

# ── R-F3613 (2026-08-01) — a total outage must PAGE the operator ─────────────
#
# Recording an outage is not the same as reporting it. On 2026-08-01 the chain
# failed on every turn for hours; the gap was filed, the health metric moved,
# and the operator still found out by asking ARIA in WhatsApp and getting a
# degraded reply. CLAUDE.md §19e names that the worst outcome: "a blocker the
# operator has to find himself".
#
# COOLDOWN IS LOAD-BEARING, NOT POLISH. A dead chain exhausts on EVERY call —
# 258 consecutive failures were measured on 2026-07-25 — so an un-throttled
# alert would fire hundreds of WhatsApp messages and become its own incident.
# One page per window; the window is deliberately long because the SECOND alert
# carries almost no information the first did not.
_CHAIN_ALERT_COOLDOWN_S = float(os.getenv("ARIA_LLM_CHAIN_ALERT_COOLDOWN_S", "900"))
_last_chain_alert_at: float = 0.0

# R-F3616 — the PRE-outage page (redundancy lost) has its OWN window, so a
# "no fallback left" warning can never suppress the "everything is down" page
# that may follow it minutes later. Two different claims, two different clocks.
_last_redundancy_alert_at: float = 0.0


# ── R-F2917: context-scoped provider preference ──────────────────────────────
# Operator directive 2026-07-23: DD runs on Claude, EVERYTHING else on DeepSeek.
#
# Doing that per-call would mean editing ~9 LLM call sites across dd_orchestrator
# and deep_researcher, and any site missed (or added later) silently bills the
# wrong provider — the failure mode we can least afford. A contextvar set ONCE at
# the DD entry point covers every LLM call made anywhere inside that run,
# including nested helpers, and cannot drift.
#
# Contextvars propagate across `await` and into tasks created inside the scope,
# which is exactly the shape of a DD run. An explicit prefer_provider= argument
# still wins, so the R-F1366 coder pin is unaffected.
_preferred_provider: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aria_preferred_provider", default="",
)


@contextlib.contextmanager
def provider_scope(name: str):
    """Route every LLM call made inside this block to `name` first.

    The chain still falls back normally, so a cooling or failing preferred
    provider degrades to the rest of the chain rather than failing the run —
    a DD must never die because Anthropic is rate-limited.
    """
    token = _preferred_provider.set((name or "").strip().lower())
    try:
        yield
    finally:
        try:
            _preferred_provider.reset(token)
        except Exception:
            pass


def get_preferred_provider() -> str:
    """The provider preferred by the current context ("" when unscoped)."""
    try:
        return _preferred_provider.get()
    except Exception:
        return ""


def preference_only_providers() -> set[str]:
    """R-F2922 — providers that may serve ONLY when explicitly preferred.

    Operator directive 2026-07-23: DD runs on Claude, everything else on
    DeepSeek. R-F2917 pins DD to Claude, but that alone does NOT deliver the
    guarantee: `ARIA_ANTHROPIC_ENABLED=1` inserts Anthropic at the head of the
    FALLBACK list, so the effective chain is [deepseek, anthropic, ...]. Any
    non-DD call whose primary failed or was cooling would then be served by
    Claude — silently, and exactly during the incidents when call volume spikes.
    `stream()` was worse: it walks self.providers with no preference concept at
    all, so streaming chat could land on Claude outright.

    Naming a provider here removes it from the DEFAULT order entirely. It stays
    fully available to anything that asks for it by name (the DD scope, the
    R-F1366 coder pin).

    R-F3034 (2026-07-25) — a call PINNED to a provider named here no longer
    degrades to the ordinary chain either. The operator restated the directive
    as "DD reports are to be ran fully on Claude no deepseek", and a DD served
    by DeepSeek is a fabrication risk rather than a graceful degradation (see
    the note at the degrade branch in complete()). Set
    ARIA_PREFERRED_MAY_DEGRADE=1 to restore the old behaviour without a deploy.

    Empty string disables the mechanism (every provider serves normally).
    """
    raw = os.getenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "anthropic")
    return {p.strip().lower() for p in (raw or "").split(",") if p.strip()}

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
    #                              per race window per day.
    #
    #                              R-F3513 — the previous note here said the
    #                              operator could "force-reset by setting the
    #                              secret to fresh billing and bouncing the
    #                              machine, which clears in-memory stats AND the
    #                              redis-mirror TTL has already expired". BOTH
    #                              halves are FALSE. Boot REHYDRATES the cooldown
    #                              from the Redis mirror (live log: "Provider
    #                              deepseek_backup HARD cooldown (billing)
    #                              rehydrated from Redis - 56479s remaining"),
    #                              and the mirror's TTL is pinned to the
    #                              cooldown's own end, so it has NOT expired.
    #                              A restart changes nothing and costs a ~10-min
    #                              cold boot. Acting on that advice on 2026-07-30
    #                              would have been a pointless outage.
    #                              To act on a top-up NOW:
    #                                POST /api/aria/admin/llm/cooldown/clear
    #                                     ?provider=deepseek   (operator token)
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
        cooldown_until = self._cooldown_until(stats)
        if cooldown_until <= time.time():
            return False  # not cooling

        # R-F1758: when this provider is the ONLY one in the chain, cap
        # the effective cooldown to 5 seconds. Skipping the only provider
        # means NO call gets made and everyone gets 502. A 5s breather is
        # enough to avoid hammering a rate-limited endpoint while still
        # retrying aggressively enough that a transient blip doesn't cause
        # a 502 cascade. This is ARIA's autonomy guarantee: she never goes
        # silent just because her primary LLM had a transient blip.
        if len(self.providers) <= 1:
            remaining = cooldown_until - time.time()
            return remaining > 5.0  # skip only if cooldown > 5s
        return True

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
        # R-F3477 — a served request is proof the chain works; clear the
        # exhaustion flag immediately rather than waiting for its TTL.
        self._record_chain_success()
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

        # R-F3616 — the PRE-OUTAGE signal (see _check_redundancy_lost).
        try:
            self._check_redundancy_lost()
        except Exception:
            logger.debug("[R-F3616] redundancy check failed", exc_info=True)

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

    @fail_wire(module="fallback", gap_type="engine_failure")
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

    @fail_wire(module="fallback", gap_type="engine_failure")
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 90.0,  # bumped from 60s — DeepSeek needs more for complex queries
        prefer_provider: str = "",
        model: str = "",   # R-F2769 — per-call Claude model override, passed to the chosen provider
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
        called: list[str] = []   # R-F3627 — who was actually DIALLED, not merely listed

        # R-F1366 — per-call provider preference (see docstring).
        # R-F2917 — when the caller did not pass one explicitly, fall back to the
        # CONTEXT preference (provider_scope), which is how a whole DD run is
        # pinned to Claude while everything else stays on the DeepSeek head.
        # An explicit argument always wins, so the coder's DeepSeek pin is
        # untouched.
        if not prefer_provider:
            prefer_provider = get_preferred_provider()
        # R-F2922 — a preference-only provider (Claude) NEVER serves the default
        # order. Without this, ARIA_ANTHROPIC_ENABLED=1 puts it second in the
        # chain and every non-DD call whose primary is failing or cooling lands
        # on it. It stays fully reachable by name below.
        _pref_only = preference_only_providers()
        order = [p for p in self.providers
                 if (p.name or "").lower() not in _pref_only]
        if prefer_provider:
            preferred = [p for p in self.providers if p.name == prefer_provider]
            if preferred:
                # R-F3034 (operator directive 2026-07-25): "DD reports are to
                # be ran fully on Claude no deepseek, and deepseek is for
                # everything else."
                #
                # This previously read `preferred + ordinary providers`, so a
                # rate-limited or cooling Claude silently handed DD work to
                # DeepSeek. That trades the wrong way for this product: the DD
                # line's entire guarantee is never-false-clean, the DD path is
                # deliberately deterministic because DeepSeek fabricates
                # grounding (R-F2779/R-F2780), and the orchestrator ALREADY has
                # an honest failure path — a failed synthesis renders
                # "🟠 INSUFFICIENT EVIDENCE … treat this as 'incomplete', NOT as
                # 'nothing found'" at AMBER-LIGHT, keeping every deterministic
                # layer. An honest incomplete report beats a DeepSeek-authored
                # verdict wearing a Claude-grade badge.
                #
                # Scope: only providers named in ARIA_PREFERENCE_ONLY_PROVIDERS
                # (default: anthropic) are pinned this way. An ordinary
                # prefer_provider (e.g. the R-F1366 coder pin) still degrades
                # through the chain exactly as before.
                _pinned = (prefer_provider or "").lower() in _pref_only
                _allow_degrade = (
                    os.getenv("ARIA_PREFERRED_MAY_DEGRADE", "").lower()
                    in ("1", "true", "yes")
                )
                if _pinned and not _allow_degrade:
                    order = preferred
                    logger.debug(
                        "[R-F3034] %r is preference-only — pinned with NO "
                        "degrade path (set ARIA_PREFERRED_MAY_DEGRADE=1 to "
                        "restore fallback)", prefer_provider,
                    )
                else:
                    order = preferred + [
                        p for p in order if p.name != prefer_provider
                    ]
            else:
                # R-F3087 — a preference-only provider is a hard contract, not
                # a best-effort ordering hint.  Before this guard, a DD scoped
                # to Anthropic silently ran on DeepSeek whenever Anthropic was
                # absent from the constructed chain.  That is worse than an
                # honest incomplete DD because the report still looked
                # Claude-authored.  Ordinary preferences retain the historical
                # fallback behaviour; the explicit operator escape hatch also
                # remains available.
                _missing_pinned = (
                    (prefer_provider or "").lower() in _pref_only
                    and os.getenv("ARIA_PREFERRED_MAY_DEGRADE", "").lower()
                    not in ("1", "true", "yes")
                )
                if _missing_pinned:
                    raise ProviderError(
                        prefer_provider,
                        "preferred provider is not configured in the LLM chain",
                        kind="other",
                        retryable=False,
                    )
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
                called.append(provider.name)
                stats["calls"] = stats.get("calls", 0) + 1
                # R-F2933 — a per-call routed model is provider-SPECIFIC. When a
                # DD prefers Claude and the call degrades to DeepSeek, forwarding
                # model="claude-opus-4-8" made DeepSeek 400 ("supported names are
                # deepseek-v4-*, you passed claude-opus-4-8") — 14 failures + a
                # 60s cooldown that then starved the DD's other DeepSeek layers.
                # OpenAICompatProvider guards its own payload, but the guard is
                # invisible from here and any future provider could forget it, so
                # gate at the ONE forwarding point: only pass `model` to the
                # provider the id belongs to (a claude-* id → anthropic only).
                _fwd_model = model
                if _fwd_model:
                    _pname = (provider.name or "").lower()
                    _is_claude_id = str(_fwd_model).startswith("claude")
                    if _is_claude_id and _pname != "anthropic":
                        _fwd_model = ""   # let this provider use its own model
                result = await provider.complete(
                    system_prompt, user_message,
                    max_tokens=max_tokens, timeout=per_call,
                    **({"model": _fwd_model} if _fwd_model else {}),
                )
                self._record_success(provider, stats)
                result.routed_via = f"fallback:{provider.name}"
                return result

            except Exception as e:
                self._record_failure(provider, stats, e)
                last_error = e

        # R-F3036 — EVERY candidate provider is gone. A single provider failing
        # is routine (the chain usually covers it); having no LLM at all is an
        # outage, and it has to be distinguishable from a blip on the surfaces
        # the operator reads. On 2026-07-25 this condition held for hours while
        # /api/aria/brain/stats showed 106 modules at success_rate 1.0 and the
        # daily spend line read $0.00 — a dead limb that looked like a quiet day.
        # R-F3477 — record the OUTCOME so get_health() stops reporting a healthy
        # chain during a total outage.
        # R-F3613 — one shared handler, mirrored into the stream fork below.
        self._on_chain_exhausted(
            order, attempted, prefer_provider, last_error, path="complete",
            called=called,
        )

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
        model: str = "",   # R-F2769 — per-call Claude model override
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
        called: list[str] = []   # R-F3627 §13 mirror — see complete()

        # R-F2922 — streaming is the CHAT path and has no preference concept, so
        # it walked self.providers directly. With Claude in the chain that meant
        # a streaming chat turn could be served by Claude the moment DeepSeek
        # hiccuped. DD does not stream, so excluding preference-only providers
        # here costs nothing and closes the largest remaining exposure.
        _pref_only = preference_only_providers()
        _stream_order = [p for p in self.providers
                         if (p.name or "").lower() not in _pref_only]

        for provider in _stream_order:
            if attempted >= self._MAX_FALLBACK_ATTEMPTS:
                logger.warning(
                    "Stream fallback chain stopped after %d attempts; %d providers untried",
                    attempted, len(_stream_order) - attempted,
                )
                break

            stats = self._stats.get(provider.name, {})
            if self._should_skip(stats):
                logger.debug("Skipping %s for stream (cooling down)", provider.name)
                continue

            try:
                attempted += 1
                called.append(provider.name)
                stats["calls"] = stats.get("calls", 0) + 1
                # R-F2933 — same provider-specific-model guard as complete().
                _fwd_model = model
                if _fwd_model and str(_fwd_model).startswith("claude") \
                        and (provider.name or "").lower() != "anthropic":
                    _fwd_model = ""
                async for chunk in provider.stream(
                    system_prompt, user_message,
                    max_tokens=max_tokens, timeout=timeout, on_done=on_done,
                    **({"model": _fwd_model} if _fwd_model else {}),
                ):
                    yield chunk
                self._record_success(provider, stats)
                return  # stream completed successfully

            except Exception as e:
                self._record_failure(provider, stats, e)
                last_error = e

        # R-F3613 §13 MIRROR — this fork previously raised WITHOUT recording the
        # exhaustion or wiring it. Consequences, all silent: get_health()
        # .resilient stayed True through a streaming outage, no gap was filed,
        # and R-F3612's self_introspect block would have told the operator the
        # chain was fine. Web chat streams, so this was the likelier fork to be
        # hit — the §13 stream-bypass rule exists for exactly this.
        # No preference concept on the stream path (see _stream_order above).
        self._on_chain_exhausted(
            _stream_order, attempted, "", last_error, path="stream",
            called=called,
        )

        if isinstance(last_error, ProviderError):
            raise last_error
        raise ProviderError(
            "fallback",
            "all LLM providers failed (stream) — try again in a minute",
            kind="other", retryable=True, cause=last_error,
        )

    @fail_wire(module="fallback", gap_type="engine_failure")
    def clear_cooldown(self, provider_name: str = "") -> dict:
        """R-F3513 — make a billing top-up take effect NOW.

        A HARD billing cooldown is 24h (R-F678) and is mirrored to Redis with a
        TTL pinned to its own end, and boot REHYDRATES it. ``_record_success``
        is the only thing that clears it, and a cooling provider is never
        called — so once set, it sustains itself for the full 24h no matter what
        the operator does. Paying for credit could not be acted on at all.

        CLAUDE.md §17 claimed a restart would fix it ("bouncing the machine ...
        the redis-mirror TTL has already expired"). Both halves are false, and
        that guidance is corrected in the same change as this method.

        Clears BOTH sides — the in-process stats entry and the Redis mirror —
        because clearing either alone leaves the other to restore it.

        Reports honestly: ``was_cooling`` says whether anything was actually
        undone, so the caller is never told a cooldown was lifted that was not
        there. Pass "" to clear every provider in the chain.
        """
        names = ([provider_name] if provider_name
                 else [p.name for p in self.providers])
        if provider_name and provider_name not in self._stats:
            return {"cleared": False, "providers": [], "was_cooling": False,
                    "reason": f"unknown provider {provider_name!r}; chain is "
                              f"{[p.name for p in self.providers]}"}

        now = time.time()
        was_cooling = False
        for name in names:
            stats = self._stats.get(name)
            if stats is None:
                continue
            if float(stats.get("cooldown_until") or 0) > now:
                was_cooling = True
            stats["cooldown_until"] = 0
            stats["failures"] = 0
            stats["last_kind"] = ""
            try:
                self._clear_redis_cooldown(name)
            except Exception as exc:
                logger.warning(
                    "[R-F3513] cleared %s in memory but the Redis mirror delete "
                    "failed — a restart may rehydrate the cooldown: %s", name, exc)
        # A cleared chain is serveable again; drop any stale exhaustion flag so
        # /health does not keep reporting an outage that has just been resolved.
        self._record_chain_success()
        logger.info("[R-F3513] cooldown cleared for %s (was_cooling=%s)",
                    names, was_cooling)
        return {"cleared": True, "providers": names, "was_cooling": was_cooling,
                "reason": ""}

    @fail_wire(module="fallback", gap_type="engine_failure")
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

    # ── R-F3477: chain-level outcome memory ─────────────────────────────────
    # One flag, deliberately TTL'd. A latching flag would mark the chain dead
    # forever after a single blip if nothing called it again; expiry means the
    # signal is "recently proven broken", which is what an operator needs.

    def _reset_chain_outcome(self) -> None:
        self._chain_exhausted_at = 0.0

    def _record_chain_exhausted(self) -> None:
        """Every provider failed for one request — the chain is NOT resilient."""
        self._chain_exhausted_at = time.time()

    def _on_chain_exhausted(
        self,
        order: list,
        attempted: int,
        prefer_provider: str,
        last_error: Exception | None,
        *,
        path: str,
        called: list[str] | None = None,
    ) -> None:
        """R-F3613 — THE one place a total chain outage is handled.

        Called from BOTH complete() and stream(). Before this, the whole
        treatment lived inline in complete() only, so the §13 stream fork could
        exhaust every provider while `_chain_exhausted_at` was never set — which
        left `get_health().resilient` reporting True during a streaming outage,
        filed no gap, and (post R-F3612) would have shown the operator a
        self_introspect block saying the chain was fine. Web chat streams, so
        that fork is not a corner case.

        Three things happen, in increasing order of how loudly they shout:
          1. record the outcome  -> get_health().resilient goes False (R-F3477)
          2. wire the failure    -> gap + health metric (R-F3036)
          3. PAGE THE OPERATOR   -> §19e, cooldown-guarded

        Never raises: the caller is already on its failure path and is about to
        raise the provider error. An alerting bug must not replace the real one.
        """
        self._record_chain_exhausted()
        # R-F3627 — "TRIED" used to list every provider in the chain ORDER,
        # including ones skipped as cooling and never dialled. On 2026-08-01 the
        # operator page therefore read "TRIED: deepseek, deepseek_backup" when
        # exactly ONE call had been made — the `attempts=1` beside it was the
        # only hint, and it contradicted the sentence next to it. §22: a status
        # line states what happened, so name who was CALLED and who was SKIPPED.
        if called is None:
            # A caller that did not record who it dialled. Say exactly that
            # rather than inferring — deriving "skipped" from an absent list
            # would fabricate the very claim this change exists to remove.
            tried = (", ".join(p.name for p in order) or "<none>") + " (dialled set not recorded)"
        else:
            _skipped = [p.name for p in order if p.name not in called]
            tried = ", ".join(called) or "<none — every provider was cooling>"
            if _skipped:
                tried += f" (SKIPPED, cooling: {', '.join(_skipped)})"
        detail = (
            f"ALL LLM providers failed — no provider served this call "
            f"(path={path}). tried=[{tried}] attempts={attempted} "
            f"prefer_provider={prefer_provider or '<none>'} "
            f"last_error={str(last_error)[:200]}"
        )
        try:
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(
                module="llm_chain_exhausted",
                detail=detail,
                gap_type="llm_provider_failure",
                source="llm_chain_exhausted",
            )
        except Exception:
            pass
        try:
            self._alert_operator_chain_down(tried, attempted, last_error, path)
        except Exception:
            logger.debug("[R-F3613] operator alert dispatch failed", exc_info=True)

    def _check_redundancy_lost(self) -> None:
        """R-F3616 (2026-08-01) — page BEFORE the outage, not only during it.

        R-F3613 pages when EVERY provider has failed. By then the user has
        already had a degraded reply — which is precisely how 2026-08-01 was
        discovered. The state worth catching is the one just before: the chain
        has been configured with a fallback, and that fallback is gone, so a
        single further failure is a total outage.

        WHY THIS TRIGGER AND NOT A FAILURE RATE. §14 is explicit that a cooling
        provider served by a fallback is "operational, never degraded", and a
        raw failure-rate alarm would contradict it — paging every time the chain
        did exactly what it was built to do. Losing REDUNDANCY is a different
        claim, and an honest one: the chain is still serving, and it now has no
        second chance. The message says exactly that, so it can never be read as
        "ARIA is down" when she is not.

        Silent by construction on a single-provider chain: with nothing to lose,
        there is no redundancy to report losing. That is a real limitation, not
        an oversight — a one-provider chain is a standing single point of
        failure and belongs in configuration review, not in a per-call alert.
        """
        global _last_redundancy_alert_at
        if len(self.providers) < 2:
            return  # no redundancy existed — nothing to lose (see docstring)

        now = time.time()
        active = [p.name for p in self.providers
                  if not self._should_skip(self._stats.get(p.name, {}))]
        if len(active) != 1:
            return  # 0 -> that is the R-F3613 outage path; 2+ -> still redundant

        if now - _last_redundancy_alert_at < _CHAIN_ALERT_COOLDOWN_S:
            return
        _last_redundancy_alert_at = now

        cooling = [p.name for p in self.providers if p.name not in active]
        logger.warning(
            "[R-F3616] LLM chain redundancy LOST — only %s remains; cooling: %s",
            active[0], ", ".join(cooling) or "<none>",
        )
        text = (
            "⚠️ STALLED: LLM chain has no fallback left.\n\n"
            f"STILL SERVING: {active[0]} (answers are NOT degraded right now)\n"
            f"UNAVAILABLE: {', '.join(cooling) or '<none>'}\n\n"
            "WHY THIS MATTERS: one more provider failure is a total outage.\n"
            "ACTION: check credit/cooldown on the unavailable provider(s) — "
            "POST /api/aria/admin/llm/cooldown/clear (operator token) after a "
            "top-up; GET /api/aria/health/perf shows the live chain."
        )
        self._dispatch_operator_page(text, source="llm_chain_redundancy_lost")

    def _alert_operator_chain_down(
        self, tried: str, attempted: int,
        last_error: Exception | None, path: str,
    ) -> None:
        """Send the §19e operator page for a total LLM outage.

        Deliberately does NOT use the LLM — it is the thing that is down. This
        is a plain HTTP POST to the WA app (R-F839 points WANotifier at the live
        ARIA_WA_INTERNAL_URL, not the retired Seenode bridge).

        Message shape follows §19e: what is DONE, what is STUCK, WHY, and the
        exact ACTION needed — the operator should not have to interpret it.
        """
        global _last_chain_alert_at
        now = time.time()
        if now - _last_chain_alert_at < _CHAIN_ALERT_COOLDOWN_S:
            return  # already paged this window — see the cooldown rationale above
        _last_chain_alert_at = now

        # R-F3627 — THE ACTION MUST MATCH THE CAUSE.
        #
        # This line was unconditional: "check provider credit/cooldown … clear a
        # stale cooldown after a top-up". On 2026-08-01 the cause was a request
        # too small for the model's own reasoning, on a healthy, paid, reachable
        # provider — so the page's own WHY line ("reasoning consumed the token
        # budget") contradicted its ACTION line, and the ACTION pointed at a
        # lever that could not have helped. A page that names the wrong remedy
        # costs more operator time than no page: it is acted on.
        _kind = getattr(last_error, "kind", "") or ""
        if _kind == KIND_REASONING_TRUNCATED:
            action = (
                "ACTION: this is NOT a credit or cooldown problem — the provider "
                "is healthy and answering, but the completion budget is too small "
                "to hold the model's reasoning AND its answer. Clearing a cooldown "
                "will NOT help. R-F3627 reserves the answer and escalates once; if "
                "you are seeing this, the escalation ceiling itself was hit — raise "
                "_REASONING_MAX_COMPLETION_TOKENS (llm/openai_compat.py)."
            )
        elif _kind in ("billing", "auth"):
            action = (
                "ACTION: provider credit or key — "
                "POST /api/aria/admin/llm/cooldown/clear (operator token) clears "
                "the cooldown after a top-up; a 24h billing cooldown does NOT "
                "clear on restart (R-F3513)."
            )
        else:
            action = (
                "ACTION: check provider credit/cooldown — "
                "POST /api/aria/admin/llm/cooldown/clear (operator token) clears a "
                "stale cooldown after a top-up."
            )

        text = (
            "🚨 BLOCKED: LLM chain — every provider failed.\n\n"
            f"STUCK: no provider served the last request (path={path}, "
            f"attempts={attempted}).\n"
            f"CALLED: {tried}\n"
            f"WHY: {str(last_error)[:300] or 'unknown'}\n\n"
            "IMPACT: chat, DD and research answers are degraded until a provider "
            "recovers.\n"
            f"{action}\n"
            "GET /api/aria/health/perf shows the live chain.\n"
            f"(further alerts suppressed for {int(_CHAIN_ALERT_COOLDOWN_S)}s)"
        )

        self._dispatch_operator_page(text, source="llm_chain_exhausted")

    def _dispatch_operator_page(self, text: str, *, source: str) -> None:
        """R-F3613/R-F3616 — THE one operator-paging send.

        Both pages (total outage, and redundancy lost) go through here so the
        delivery semantics, the §21a wiring and the unconfigured-channel
        handling cannot drift apart — the drift that produced this session's
        other defects.

        Never uses the LLM: on the outage path it is the thing that is down.
        Never raises: the caller is on a failure path already.
        """
        def _send():
            async def _run():
                try:
                    from ..autonomous.wa_notifier import WANotifier
                    n = WANotifier()
                    if not n.is_configured:
                        # §21a — an unconfigured alert channel is a DARK path.
                        # Say so in the brain rather than failing silently, or
                        # the next outage pages nobody and nothing records why.
                        from ..intel.engine_wiring import wire_failure as _wf
                        _wf(
                            module="llm_chain_alert",
                            detail=(f"{source}: operator alert channel is NOT "
                                    "configured (need ARIA_WA_INTERNAL_URL + "
                                    "ARIA_INTERNAL_TOKEN + ARIA_CODER_WA_GROUP_ID)"
                                    " — nobody was paged"),
                            gap_type="llm_provider_failure",
                            source="llm_chain_alert:unconfigured",
                        )
                        return
                    outcome = await n.notify(text)
                    from ..intel.engine_wiring import wire_success as _ws, wire_failure as _wf
                    if str(outcome).startswith("ok"):
                        _ws(module="llm_chain_alert",
                            summary=f"operator paged: {source}",
                            source_id="llm_chain_alert:sent")
                    else:
                        _wf(module="llm_chain_alert",
                            detail=f"operator page NOT delivered ({source}): {outcome}",
                            gap_type="llm_provider_failure",
                            source="llm_chain_alert:send_failed")
                except Exception as exc:  # pragma: no cover — never propagate
                    logger.warning("[R-F3613] operator alert failed: %s", exc)
            return _run()

        try:
            from ..intel.engine_wiring import _dispatch_fire_and_forget as _d
            _d(_send)
        except Exception:
            logger.debug("[R-F3613] alert dispatcher unavailable", exc_info=True)

    def _record_chain_success(self) -> None:
        """A request was served. Clear immediately: proof beats a stale flag."""
        self._chain_exhausted_at = 0.0

    def _chain_exhaustion_age(self) -> float | None:
        """Seconds since the chain last exhausted every provider, or None."""
        at = getattr(self, "_chain_exhausted_at", 0.0) or 0.0
        if at <= 0:
            return None
        age = time.time() - at
        if age > _CHAIN_EXHAUSTION_TTL_S:
            return None
        return round(age, 1)

    @fail_wire(module="fallback", gap_type="engine_failure")
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
        # R-F3477 — `resilient` must follow OUTCOMES, not chain membership.
        # It used to be `len(active) > 0`, where "active" only means a provider's
        # cooldown timestamp has passed. Live 2026-07-30 that reported
        # resilient=true / status=operational while 14 consecutive real calls in
        # the same five minutes returned "all LLM providers failed".
        # §14 is unchanged: a COOLING provider is the chain working as designed.
        # What is added is that a chain which just exhausted every provider is
        # not healthy, whatever its cooldown timestamps say.
        _exhausted_age = self._chain_exhaustion_age()
        return {
            "active_providers": active,
            "cooling_providers": cooling,
            "resilient": len(active) > 0 and _exhausted_age is None,
            "last_exhaustion_age_s": _exhausted_age,
            "primary_active": bool(active and chain_order and active[0] == chain_order[0]),
            "serving_provider": active[0] if active else None,
            "chain_order": chain_order,
        }


@fail_wire(module="fallback", gap_type="engine_failure")
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
    # R-F1949 — SHADOW/canary mode. With ARIA_LLM_SHADOW=1, ARIA-LLM is slotted
    # BELOW the primary (fallback position, appended after the primary further
    # down) so it serves real traffic ONLY when the primary (DeepSeek) fails — a
    # safe canary — while DeepSeek stays primary. Unset ARIA_LLM_SHADOW to promote
    # ARIA-LLM to primary (the original R-F93 behaviour).
    _aria_llm_shadow = (os.getenv("ARIA_LLM_SHADOW", "").strip().lower() in ("1", "true", "yes"))
    # R-F2410 — TWO-TRACK is now the DEFAULT when ARIA_LLM_URL is set: the sovereign
    # serves GROUNDED SYNTHESIS only (via model_router at the synthesis call sites),
    # and is NOT inserted as the global chain primary, so all coverage/closed-book/
    # general traffic stays on DeepSeek. The legacy R-F93 "sovereign primary for ALL
    # turns" is preserved behind ARIA_LLM_PRIMARY_ALL=1 (escape hatch). SHADOW is
    # unchanged (append below the primary as a canary).
    _aria_llm_primary_all = (os.getenv("ARIA_LLM_PRIMARY_ALL", "").strip().lower() in ("1", "true", "yes"))
    _aria_llm_provider = None
    if _aria_llm_url:
        # ARIA-LLM speaks OpenAI-compatible API; reuse the OpenAICompatProvider.
        # R-F2645: derive the base through the ONE shared normaliser so this
        # (the 4th consumer of ARIA_LLM_URL) cannot drift from the provider and
        # the health probe. OpenAICompatProvider appends /chat/completions
        # (openai_compat.py:81), so the /v1 must already be on the base — which
        # is the documented shape.
        from . import aria_llm_url as _aria_llm_url_mod
        _aria_llm_provider = create_llm_provider(
            "openai",
            os.getenv("ARIA_LLM_KEY", "sovereign"),
            os.getenv("ARIA_LLM_MODEL", "aria-llm-v0.1"),
            base_url=_aria_llm_url_mod.normalise_base(_aria_llm_url),
        )
        if _aria_llm_provider and _aria_llm_provider.is_configured:
            try:
                _aria_llm_provider.name = "aria_llm"
            except Exception:
                pass
            # R-F2686 — gate the sovereign behind the R-F1957 warm-gate at the ONE
            # point it is constructed, so BOTH chain slots below (SHADOW fallback and
            # PRIMARY_ALL head) are covered. Before this, wrap() was never called
            # ANYWHERE in prod, so those slots took the RAW provider gated only on
            # is_configured: a deliberately-stopped pod (§24) ate a live 404 + the
            # call timeout before the chain moved on.
            #
            # HONEST SCOPE (do not over-read this): under the R-F2410 DEFAULT
            # two-track config (neither ARIA_LLM_SHADOW nor ARIA_LLM_PRIMARY_ALL set
            # — the live prod state on 2026-07-17) the sovereign is NOT placed in
            # this chain at all, so this wrap is INERT today. It arms the two slots
            # for the moment either flag is flipped (SHADOW=1 is the documented
            # promotion path). The path that DOES carry sovereign traffic in
            # two-track is model_router → aria_llm_provider, which has NO warm-gate
            # — only R-F2648's schedule signal (_sovereign_pod_serving), which is a
            # POLICY check with no network proof. Gating that path is a separate,
            # still-open item (to-do list #2 #4).
            #
            # Wrap AFTER the rename so the wrapper inherits name="aria_llm"
            # (breaker/_stats/cooldown keys stay stable). Fails CLOSED → skips to
            # DeepSeek. Lazy import: fallback→resilience only (no cycle).
            try:
                from .resilience import LLMHealthChecker as _LLMHealthChecker
                _aria_llm_provider = _LLMHealthChecker.wrap(_aria_llm_provider)
            except Exception as _wrap_e:
                # Never fail chain construction over the gate — an unwrapped
                # sovereign is the pre-R-F2686 behaviour, not an outage.
                logger.warning(
                    "[R-F2686] sovereign warm-gate wrap failed (non-fatal, "
                    "provider stays ungated): %s", _wrap_e,
                )
            if _aria_llm_shadow:
                logger.info(
                    "ARIA-LLM (R-F1949 SHADOW) at %s — will serve as FALLBACK below primary (canary); DeepSeek stays primary",
                    _aria_llm_url,
                )
            elif _aria_llm_primary_all:
                # Legacy R-F93 escape hatch — sovereign primary for ALL turns.
                providers.append(_aria_llm_provider)
                logger.info(
                    "ARIA-LLM (R-F93 PRIMARY_ALL) configured at %s — taking PRIMARY position for ALL turns",
                    _aria_llm_url,
                )
            else:
                # R-F2410 DEFAULT two-track — sovereign NOT global-primary; it serves
                # grounded synthesis via model_router while DeepSeek stays chain primary.
                logger.info(
                    "ARIA-LLM (R-F2410 TWO-TRACK) at %s — grounded synthesis via model_router; DeepSeek stays chain primary for coverage/fallback",
                    _aria_llm_url,
                )

    # Primary (configured via env vars in main.py — typically Anthropic)
    primary = create_llm_provider(
        primary_provider, primary_key, primary_model, primary_base_url,
    )
    if primary and primary.is_configured:
        providers.append(primary)

    # R-F1949: in SHADOW mode, slot ARIA-LLM right after the primary (fallback/
    # canary) — it serves only when the primary fails, so DeepSeek stays primary
    # while ARIA-LLM gets observable real traffic. Promote by unsetting ARIA_LLM_SHADOW.
    if (_aria_llm_provider is not None and _aria_llm_shadow
            and _aria_llm_provider.is_configured and _aria_llm_provider not in providers):
        providers.append(_aria_llm_provider)
        logger.info("ARIA-LLM appended as FALLBACK (R-F1949 shadow/canary; primary unchanged)")

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
    # R-F3032 / R-F3035 (2026-07-25) — the DeepSeek entry was hardcoded to
    # `deepseek-chat`, which DeepSeek RETIRED. Because DeepSeek is the primary
    # and Anthropic is preference-only (R-F2922), and groq/openai/gemini/ollama
    # are all unset in production, the DEFAULT chain had exactly ONE member —
    # so a single upstream model retirement took the entire non-DD ecosystem
    # down with no fallback at all (258/258 calls failed, $0.00 for the day).
    #
    # Two changes, both aimed at that single point of failure:
    #   1. the model id is env-driven, so a future retirement is a secret set
    #      rather than a code deploy;
    #   2. a SECOND DeepSeek entry on a different model id, so retiring one
    #      leaves a working member behind. Same key and account — this buys
    #      resilience against a model retirement (the failure that actually
    #      happened), NOT against an account/key/network failure. R-F3036
    #      covers that case by making the dead chain loud instead of silent.
    from .openai_compat import default_deepseek_model, backup_deepseek_model
    _ds_key = os.getenv("DEEPSEEK_API_KEY", "")
    _ds_primary = default_deepseek_model()
    _ds_backup = backup_deepseek_model()
    fallback_configs = [
        ("deepseek",  _ds_key,                            _ds_primary),
        ("groq",      os.getenv("GROQ_API_KEY", ""),      "llama-3.3-70b-versatile"),
        ("openai",    os.getenv("OPENAI_API_KEY", ""),    "gpt-4o-mini"),
        ("gemini",    os.getenv("GEMINI_API_KEY", ""),    "gemini-2.5-flash"),
    ]
    if _ds_key and _ds_backup and _ds_backup != _ds_primary:
        # Appended last: it only serves once every other provider is gone.
        fallback_configs.append(("deepseek", _ds_key, _ds_backup))
    if _anthropic_enabled:
        # Operator explicitly re-enabled — prepend at the head.
        #
        # R-F2924 — the model was hardcoded "claude-sonnet-4-6". LLM_MODEL only
        # applies to the PRIMARY provider, and after the 2026-07-23 restructure
        # Anthropic is deliberately NOT primary (DeepSeek is; Claude is reachable
        # only by name, R-F2922). So the operator set LLM_MODEL=claude-opus-4-8
        # for DD quality and DD silently ran on Sonnet 4.6 anyway — a USP-level
        # mismatch that no error would ever surface. Now env-driven, falling back
        # to LLM_MODEL when that is itself a Claude id, so the DD runs on the
        # model that was actually chosen.
        _anthropic_model = (os.getenv("ARIA_ANTHROPIC_MODEL") or "").strip()
        if not _anthropic_model:
            _llm_model = (os.getenv("LLM_MODEL") or "").strip()
            _anthropic_model = (
                _llm_model if _llm_model.startswith("claude-") else "claude-sonnet-4-6"
            )
        logger.info("[R-F2924] Anthropic chain entry model: %s", _anthropic_model)
        fallback_configs.insert(
            0,
            ("anthropic", os.getenv("ANTHROPIC_API_KEY", ""), _anthropic_model),
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
    # R-F3035 — FallbackProvider keys its per-provider stats (failure counts
    # and cooldowns) by `p.name`, so two entries sharing a name would share
    # ONE cooldown: the first failure would cool both and the backup would
    # never be tried, silently defeating the redundancy it exists to provide.
    # Give any repeat of a name its own identity so it gets its own stats.
    # R-F3035 — seed from the providers already registered (the primary, and
    # ARIA-LLM when configured). Counting only loop-built entries left the
    # production shape with TWO providers both named "deepseek" — the primary
    # plus the backup — which is the shared-cooldown collision this rename
    # exists to prevent.
    _seen_names: dict[str, int] = {}
    for _p in providers:
        _pn = getattr(_p, "name", "") or ""
        _seen_names[_pn] = _seen_names.get(_pn, 0) + 1
    # R-F3035 — the skip below used to be `name == primary_provider`, which
    # dropped EVERY entry for the primary's provider. In production
    # (LLM_PROVIDER=deepseek) that silently discarded the backup DeepSeek entry
    # this fix exists to add, leaving the default chain at one member again —
    # caught by the §9 lifespan smoke, not by the unit test, because the test
    # built the chain with no primary. Compare the effective MODEL too, so the
    # primary is still de-duplicated while a same-provider/different-model
    # entry is kept.
    _primary_obj = next(
        (p for p in providers if (getattr(p, "name", "") or "") == primary_provider),
        None,
    )
    _primary_model = (
        getattr(_primary_obj, "_model", "") or primary_model or ""
    ).strip()
    for name, key, model in fallback_configs:
        if name == primary_provider and (
            not _primary_model or (model or "").strip() == _primary_model
        ):
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
            # R-F3035 — construct under the real provider name (the factory
            # derives base_url/auth from it), then disambiguate the SECOND
            # and later entries so their stats and cooldowns are independent.
            _n = _seen_names.get(name, 0)
            _seen_names[name] = _n + 1
            if _n:
                fb.name = f"{name}_backup{_n if _n > 1 else ''}"
                logger.info(
                    "[R-F3035] %s registered as %r (model=%r) — independent "
                    "cooldown so a retired model id cannot zero the chain",
                    name, fb.name, model,
                )
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


def get_provider_status() -> dict:
    """R-F2375 (H5): aggregate LLM provider availability for /health/perf.

    Grounded self-state (R-F396): reports, per provider slot, whether it is
    CONFIGURED (env key / url present) and its live circuit-breaker state from
    the breaker registry. Never fabricates a provider being "up" — a slot with
    no key is ``configured: False``; a slot whose breaker is OPEN is
    ``available: False``. Before this existed, /health/perf's ``llm_providers``
    was permanently ``{}`` (the caller looked up a function that did not exist).
    """
    import os as _os
    slots = [
        ("aria_llm",  bool((_os.getenv("ARIA_LLM_URL") or "").strip())),
        ("deepseek",  bool((_os.getenv("DEEPSEEK_API_KEY") or "").strip())),
        ("anthropic", bool((_os.getenv("ANTHROPIC_API_KEY") or "").strip())),
        ("openai",    bool((_os.getenv("OPENAI_API_KEY") or "").strip())),
        ("gemini",    bool((_os.getenv("GEMINI_API_KEY") or _os.getenv("GOOGLE_API_KEY") or "").strip())),
    ]
    breaker_state: dict[str, str] = {}
    try:
        from ..intel import circuit_breaker as _cb
        for b in _cb.get_all_breakers():
            nm = str(b.get("name", "")).lower()
            if nm:
                breaker_state[nm] = str(b.get("state", ""))
    except Exception as e:  # breaker registry unavailable — report config only
        logger.debug("get_provider_status: breaker registry read failed: %s", e)
    out: dict[str, dict] = {}
    for name, configured in slots:
        st = None
        for bn, bstate in breaker_state.items():
            if name in bn:
                st = bstate
                break
        out[name] = {
            "configured": configured,
            "breaker_state": st,               # OPEN / CLOSED / HALF_OPEN, or None
            "available": configured and st != "OPEN",
        }
    return out

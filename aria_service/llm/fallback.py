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
from typing import Any, Optional

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

    Empty string disables the mechanism (every provider serves normally) — and
    R-F3942 makes that state SAY SO rather than pass silently. See
    `rule_one_status()`.
    """
    raw = os.getenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "anthropic")
    return {p.strip().lower() for p in (raw or "").split(",") if p.strip()}


#: R-F3942 — announce a Rule One breach ONCE per process, not per health poll.
_RULE_ONE_BREACH_ANNOUNCED = False


def rule_one_status() -> dict:
    """Is Anthropic confined to DD? (RULE ONE, operator 2026-08-12.)

    "anthropic API calls must be only active on DD reports, when a new DD report
    is been actioned, as well as for brave API, that was the rule number one."

    WHY THIS EXISTS. The rule was broken for days and NOTHING said so. The live
    secret `ARIA_PREFERENCE_ONLY_PROVIDERS` was set to the EMPTY STRING, which
    this module documents as "disables the mechanism" — so Claude re-entered the
    general order and served any call whose primary was cooling, which DeepSeek
    does many times a day. It surfaced only when the credit balance ran out and
    took DD down with it (DD pins Claude non-degradably). Measured at that point:
    anthropic $39.10 of a $73.34 month — 53% of spend from 2.6% of calls, 540 of
    them on `claude-opus-4-8` — against DD's own 8 calls and $0.04.

    An empty override is therefore not a neutral value; it is a policy change
    with a bill attached, and it was invisible. The general-chain composition was
    observable on /health all along, but only as a provider list — nothing named
    the RULE, so nobody reading it knew a list containing "anthropic" was a
    breach. This states the policy itself.

    Reports rather than enforces, deliberately: `preference_only_providers()` is
    on the hot path of every completion and must stay a pure, cheap read. §21a —
    a breach also reaches the brain, once per process, so it is actionable
    without anyone thinking to poll a health field.
    """
    global _RULE_ONE_BREACH_ANNOUNCED
    pref = preference_only_providers()
    key_present = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    confined = "anthropic" in pref
    # A breach only MATTERS when a key exists — without one Claude cannot serve
    # regardless, and crying breach would be a guard that fires on nothing.
    breached = key_present and not confined

    if breached and not _RULE_ONE_BREACH_ANNOUNCED:
        _RULE_ONE_BREACH_ANNOUNCED = True
        logger.warning(
            "[R-F3942] RULE ONE BREACH — anthropic is NOT preference-only "
            "(ARIA_PREFERENCE_ONLY_PROVIDERS=%r). Claude is in the GENERAL chain "
            "and will serve non-DD calls whenever the primary cools. Unset that "
            "variable to restore the code default.",
            os.getenv("ARIA_PREFERENCE_ONLY_PROVIDERS"),
        )
        try:
            # Imported LOCALLY, as every other wire_failure call in this module
            # is — engine_wiring is not safe to import at fallback's module scope.
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(
                module="fallback",
                detail=("RULE ONE breach: anthropic is in the general LLM chain "
                        "(preference_only=%s). Non-DD traffic can spend Claude "
                        "credits; DD pins Claude non-degradably, so exhausting "
                        "them takes DD down." % sorted(pref)),
                gap_type="engine_failure",
            )
        except Exception as e:      # pragma: no cover - signalling is best-effort
            logger.debug("[R-F3942] rule-one signal failed: %s", e)

    # ── R-F3946 — THE SECOND CLAUSE. ────────────────────────────────────────
    # This function's own `rule` string says "anthropic ... as well as for brave
    # API", and it measured only anthropic. So production reported
    # `breached: false` while Brave was being spent by POST /chat, /explore,
    # /explore-deep and /research/spawn — eight @_brave_scope routes with no DD
    # gate. A deep-diligence pass read this field and published "RULE ONE is
    # holding". A half-measure reporting a whole rule is worse than no measure:
    # it gets believed.
    #
    # Read through web_search so there is ONE policy and one place it can be
    # weakened (§1 R-F2639). Unreadable → treat as UNKNOWN, never as compliant:
    # a breach that cannot be measured must not read as its absence.
    brave_confined = None
    brave_non_dd_grants = None
    brave_allowed_purposes = None
    brave_grants_by_purpose = None
    try:
        from ..intel.web_search import brave_policy_status as _bps
        _b = _bps()
        brave_confined = bool(_b.get("confined_to_dd"))
        brave_non_dd_grants = int(_b.get("non_dd_grants") or 0)
        # R-F4217 — the allow-list is now TWO purposes (dd + wa, operator
        # 2026-08-21). Publish it and the per-purpose grants, so this surface
        # states which rule it is measuring instead of leaving a reader to infer
        # "DD only" from a field name. A half-measure reporting a whole rule is
        # what R-F3946 was written to stop; a stale-named measure is the same bug.
        brave_allowed_purposes = list(_b.get("allowed_purposes") or [])
        brave_grants_by_purpose = dict(_b.get("grants_by_purpose") or {})
        # A grant that actually happened is a LIVE breach, not a policy opinion.
        if brave_non_dd_grants > 0:
            brave_confined = False
    except Exception as e:      # pragma: no cover - defensive
        logger.debug("[R-F3946] brave policy unreadable: %s", e)

    breached = breached or (brave_confined is False)

    return {
        # R-F4217 — the rule STRING must track the rule. It said "brave ... for DD
        # reports only" while ARIA WA is now an authorised Brave surface; leaving
        # it would have told the next reader the opposite of the policy, which is
        # precisely how WA's access kept getting reverted (C-197).
        "rule": ("anthropic is for DD reports only (operator 2026-08-12); "
                 "brave is for DD reports and ARIA WA (amended 2026-08-21)"),
        "anthropic_key_present": key_present,
        "anthropic_confined_to_dd": confined,
        # R-F3946 — tri-state: None means COULD NOT MEASURE, never "compliant".
        "brave_confined_to_dd": brave_confined,
        "brave_non_dd_grants": brave_non_dd_grants,
        # R-F4217 — what "confined" actually means here, published rather than implied.
        "brave_allowed_purposes": brave_allowed_purposes,
        "brave_grants_by_purpose": brave_grants_by_purpose,
        "breached": breached,
        "preference_only_providers": sorted(pref),
    }


# R-F3767 — §21a. Returns a SET, so a failure reads as an EMPTY set — i.e. "no
# provider is pinned as non-degrading". That is the §14 rule (a cooling provider
# with a healthy fallback must report operational, not degraded): losing the pin
# silently turns an expected cooldown — anthropic on billing, deliberately
# declined per §18 — back into a DEGRADED health reading.
@fail_wire(module="fallback", gap_type="engine_failure")
def non_degrading_pins() -> set[str]:
    """Providers whose EXPLICIT pin is a contract, not an ordering hint.

    Split out 2026-08-03. `ARIA_PREFERENCE_ONLY_PROVIDERS` was doing two
    unrelated jobs at once:

      A. CHAIN COMPOSITION — keep this provider out of the DEFAULT order, so a
         general chat call never lands on it (cost).
      B. PIN CONTRACT — when a caller explicitly asks for it (DD → Claude), it
         must NOT silently degrade to the rest of the chain (R-F3034/R-F3087:
         an honest incomplete DD beats a DeepSeek-authored verdict wearing a
         Claude-grade badge).

    Those coincided while Claude was DD-only, so one flag served both. They stop
    coinciding the moment Claude is ALSO wanted as a general fallback: clearing
    the flag to buy (A) silently surrenders (B). Measured directly — clearing it
    on aria-intel put anthropic in the general chain AND made a pinned DD
    degradable in the same instant, because `_pinned` is computed from the same
    set. One value, two meanings, and the second one fails silently.

    Defaults to `preference_only_providers()` when unset, so nothing changes for
    any deployment that never sets it — the flag keeps its historical meaning
    until someone deliberately separates the two.
    """
    raw = os.getenv("ARIA_NON_DEGRADING_PINS")
    if raw is None:
        return preference_only_providers()
    return {p.strip().lower() for p in raw.split(",") if p.strip()}

# F68 fix 2026-04-28: HARD cooldowns (auth/billing) are mirrored to Redis
# so they survive restarts. Without this, every fly.io restart re-probed
# the failed backend and burned 5 calls before the in-process cooldown
# re-engaged. Soft cooldowns (rate_limit, server) are left in-memory only
# — those failure modes ARE often transient and re-probing is fine.
_REDIS_KEY_PREFIX = "crucix:aria:llm:cooldown:"

# R-F3680 — how long a SOFT-cooling provider is left alone when it is the only
# thing the chain can still reach. This is the "5s breather" R-F1758 documented
# and never delivered (it compared time REMAINING, so it only ever shortened a
# cooldown by 5s). Short enough that a transient blip does not become an outage,
# long enough that a genuinely sick endpoint is not hammered per request.
_LAST_RESORT_BREATHER_S = float(os.getenv("ARIA_LLM_LAST_RESORT_BREATHER_S", "5"))

# R-F3685 — how often a HARD-cooling provider is re-tested for recovery.
#
# R-F678 set the billing cooldown to 24h because "30-min re-probes wasted 96
# calls/day on a provider with no credit". That reasoning was about USER calls:
# the re-probe happened on the request path, so every probe cost a real request
# its latency and its failure. This probe is NOT on that path — it is a
# background `max_tokens=1` call — so the objection does not transfer, and the
# thing R-F678 was protecting (user requests never wait on a dead provider)
# stays exactly as it is.
_RECOVERY_PROBE_INTERVAL_S = float(
    os.getenv("ARIA_LLM_RECOVERY_PROBE_INTERVAL_S", "900"))
_RECOVERY_PROBE_TIMEOUT_S = float(
    os.getenv("ARIA_LLM_RECOVERY_PROBE_TIMEOUT_S", "20"))

# R-F3693 — the last-resort dial is decided PER REQUEST, so its §21a signal needs
# its own throttle or a sustained outage turns the brain-wiring into the incident
# (the R-F3613 lesson, applied before it happens rather than after).
_LAST_RESORT_WIRE_DEBOUNCE_S = float(
    os.getenv("ARIA_LLM_LAST_RESORT_WIRE_DEBOUNCE_S", "60"))


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

    def _cooldown_is_hard(self, stats: dict) -> bool:
        """Is this cooldown one that a re-dial cannot possibly recover from?

        HARD = auth / billing / explicitly non-retryable. There is no credit or
        no valid key, so every dial is a guaranteed failure that the user waits
        for, plus vendor spam. SOFT = timeout / server / rate_limit: the
        provider may well work on the next call, which is the entire reason the
        last-resort dial in `_should_skip` exists.

        `_record_failure` writes the flag explicitly. When it is absent — a
        rehydrated or hand-built stats dict — derive it from `last_kind` rather
        than guessing, and fall back to SOFT only when nothing says otherwise.
        """
        if "cooldown_hard" in stats:
            return bool(stats["cooldown_hard"])
        return str(stats.get("last_kind") or "") in ("auth", "billing")

    def _cooldown_age(self, stats: dict) -> float:
        """Seconds since this cooldown was armed (see `_should_skip`)."""
        since = stats.get("cooldown_since") or stats.get("last_failure") or 0
        if not since:
            # Origin unknown (legacy/hand-built stats). Treat the breather as
            # already spent: this value is only read when NOTHING else in the
            # chain is reachable, and in that state going silent is the worse
            # of the two errors.
            return float("inf")
        return max(0.0, time.time() - float(since))

    def _should_skip(self, stats: dict, *, alternative_exists: bool = True) -> bool:
        """Should dispatch pass over this provider?

        R-F3680 (2026-08-04) — THE DECISION IS ABOUT REACHABILITY, NOT COUNTING.

        A cooldown is a routing instruction: "go somewhere else for a while".
        It is only meaningful if there IS somewhere else. `alternative_exists`
        is that question, computed by the caller from the providers it will
        actually walk — so this is now the general rule that R-F1758 wrote as a
        hardcoded special case.

        R-F1758 capped the cooldown only when `len(self.providers) <= 1`. That
        is the CONFIGURED list: it counts preference-only entries dispatch never
        walks, and entries that are dead for a day. Measured live 2026-08-04,
        the chain was [deepseek, anthropic, deepseek_backup] with BOTH fallbacks
        hard-cooled on `billing` for ~22h — so `len` was 3, the guard was off,
        and DeepSeek's 60s soft cooldown was honoured in deference to two
        providers that could not answer. For those 60s nothing was dialled at
        all. Adding a backup with no credit did not add redundancy; it removed
        the protection ARIA had when she had one provider, which is the exact
        inversion docs/aria_llm_fallback_readiness_2026_08_01.md warned about.

        AND THE CAP ITSELF WAS INVERTED. The R-F1758 comment promises "cap the
        effective cooldown to 5 seconds"; `return remaining > 5.0` skips until
        only 5s REMAIN, i.e. it shortens a cooldown by 5s rather than capping it
        at 5s. On a 60s cooldown that is 55s of silence; on the 24h billing
        cooldown live right now it opens the gate 86,395 seconds late. The
        guarantee in its docstring — "she never goes silent just because her
        primary LLM had a transient blip" — has never once been delivered.
        Measure the cooldown's AGE, which is what a breather is.

        HARD cooldowns are never last-resorted: see `_cooldown_is_hard`.
        """
        cooldown_until = self._cooldown_until(stats)
        if cooldown_until <= time.time():
            return False  # not cooling

        if alternative_exists:
            return True  # the cooldown keeps its full force — go elsewhere

        # Nothing else is reachable. Skipping here means NO call is made at
        # all, so the only question left is whether a dial could succeed.
        if self._cooldown_is_hard(stats):
            return True  # no credit / bad key — dialling is failing slower
        return self._cooldown_age(stats) < _LAST_RESORT_BREATHER_S

    def can_dispatch_now(self) -> bool:
        """Would dispatch dial ANY provider right now?

        R-F4222 / C-202 — ADMISSION MUST ASK THE QUESTION DISPATCH ANSWERS.

        `_llm_serving_state` (routes/aria.py) refused a chat whenever
        `resilient` was not True, and `resilient` is redundancy: `len(active) > 0`
        where "active" means only that a cooldown TIMESTAMP has passed. So the
        instant a single-provider chain soft-cooled, admission returned a 503 —
        *before dispatch could apply R-F3680's last-resort rule, which exists for
        exactly that case*. Its docstring promises she "never goes silent just
        because her primary LLM had a transient blip"; through the chat endpoint
        that promise was unreachable. Measured live 2026-08-21: four refusals in
        one hour, `reason: timeout`, on `general_vendor_depth: 1`.

        This asks `_should_skip` — the SAME predicate dispatch uses, with the same
        `alternative_exists` computation — so the two layers cannot drift apart
        (R-F2639: one measure). It is NOT a relaxation: a HARD cooldown still
        returns False here, because `_should_skip` refuses it even with no
        alternative ("dialling is failing slower"), and the 5s breather is still
        honoured. R-F2814's guarantee — never enter a pipeline that will hang —
        is preserved; it is simply now measured on servability rather than on
        redundancy.
        """
        order = [
            p for p in self.providers
            if (p.name or "").lower() not in preference_only_providers()
        ]
        for p in order:
            stats = self._stats.get(p.name, {})
            if not self._should_skip(
                stats,
                alternative_exists=self._has_reachable_alternative(order, p.name),
            ):
                return True
        return False

    def _has_reachable_alternative(self, order: list, exclude: str) -> bool:
        """Is any provider in `order` OTHER than `exclude` servable right now?

        Recomputed per candidate rather than once per dispatch: the loop cools
        providers as they fail, so a set computed up front goes stale exactly
        when it matters. `order` is the caller's own walk list, so a
        preference-only provider that dispatch will never reach cannot count as
        an alternative — the R-F3634 rule, applied to the dispatch decision
        itself rather than only to the surfaces that describe it.
        """
        for peer in order:
            if peer.name == exclude:
                continue
            if not self._should_skip(self._stats.get(peer.name, {})):
                return True
        return False

    # ── R-F3685: a hard cooldown must be falsifiable ─────────────────────────

    def _providers_due_for_recovery_probe(self) -> list:
        """HARD-cooling providers that have not been re-tested this interval.

        SOFT cooldowns are excluded deliberately: they expire on their own in
        60s and the R-F3680 last-resort path already dials them when there is
        nothing else. Probing those would be pure waste.
        """
        now = time.time()
        due = []
        for p in self.providers:
            stats = self._stats.get(p.name, {})
            if self._cooldown_until(stats) <= now:
                continue          # not cooling
            if not self._cooldown_is_hard(stats):
                continue          # soft — R-F3680 already covers it
            last = stats.get("last_recovery_probe") or 0
            if now - last < _RECOVERY_PROBE_INTERVAL_S:
                continue
            due.append(p)
        return due

    def _wire_probe_outcome(self, provider_name: str, outcome: str,
                            detail: str = "") -> None:
        """§21a — EVERY recovery-probe outcome reaches the brain.

        R-F3693. The probe shipped observing nothing but `logger`. The recovery
        branch was wired only transitively (via `_record_success`), and the two
        NEGATIVE branches — the ones that mean the self-heal did not work — were
        fully dark. R-F3687 is the cost of that: the probe failed on an unrelated
        HTTP 400 every 15 minutes indefinitely, scored it INCONCLUSIVE, left a
        funded provider locked out, and recorded nothing anywhere. It was found by
        hand. Wired, it reports itself.

        `recovered` is emitted here as well as by `_record_success` on purpose:
        those answer different questions. `_record_success` says the provider
        works; this says THE SELF-HEAL WORKED — the only way to tell that the
        probe mechanism is alive rather than silently no-opping.

        Never raises: an observability bug must not break the thing it observes.
        """
        try:
            from ..intel.engine_wiring import wire_success as _ws, wire_failure as _wf
            if outcome == "recovered":
                _ws(
                    module="llm_recovery_probe",
                    summary=f"Provider recovered: {provider_name}",
                    detail=f"recovery probe released {provider_name}: {detail}"[:600],
                    source_id=f"llm_recovery_probe:recovered:{provider_name}",
                )
            else:
                _wf(
                    module="llm_recovery_probe",
                    detail=f"{provider_name} {outcome}: {detail}"[:600],
                    gap_type="llm_provider_failure",
                    source=f"llm_recovery_probe:{outcome}",
                )
        except Exception:
            logger.debug("[R-F3693] probe-outcome wiring failed", exc_info=True)

    def _wire_last_resort_dial(self, provider_name: str, reason: str) -> None:
        """§21a — the chain's most degraded SERVING state must be observable.

        R-F3680 dials a COOLING provider when nothing else is reachable. §14 is
        right that the USER should be told "operational" — she is still being
        served — but the brain has to know it happened, or the last stop before a
        total outage is invisible on every surface the operator reads.

        Debounced per provider: this decision is taken per REQUEST, and an
        un-throttled signal would become its own incident (the R-F3613 lesson).
        """
        try:
            now = time.time()
            stats = self._stats.setdefault(provider_name, {})
            if now - (stats.get("last_resort_wired_at") or 0) < _LAST_RESORT_WIRE_DEBOUNCE_S:
                return
            stats["last_resort_wired_at"] = now
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(
                module="llm_last_resort_dial",
                detail=(f"last_resort: dialled {provider_name} DESPITE its "
                        f"cooldown — no reachable alternative ({reason}). The "
                        f"chain is one failure from a total outage."),
                gap_type="llm_provider_failure",
                source=f"llm_last_resort_dial:{provider_name}",
            )
        except Exception:
            logger.debug("[R-F3693] last-resort wiring failed", exc_info=True)

    async def _probe_recovery(self, provider) -> bool:
        """Re-test ONE hard-cooling provider. True iff it was released.

        WHY THIS EXISTS. `_record_success` is the only thing that clears a
        cooldown, and a cooling provider is never called — so the cooldown is
        the sole cause of the silence that sustains it, for the full 24h,
        whatever becomes true in the world. R-F3513 found this and added a
        MANUAL operator lever, which makes recovery depend on a human
        remembering an admin endpoint exists.

        Measured live 2026-08-04: the production Anthropic key returned HTTP
        200 with real token usage while the chain held it `billing`-cooled with
        ~20h left. The operator said "anthropic has credit" and was right; ARIA
        had no way to find that out for herself. §25a: every limb must report
        whether it is actually working, and a limb she cannot feel coming back
        is not hers.

        Deliberately minimal and deliberately quiet: `max_tokens=1`, a short
        timeout, called DIRECTLY on the provider rather than through the chain
        so it cannot trigger the exhaustion/alerting machinery or count as
        user traffic.
        """
        stats = self._stats.setdefault(provider.name, {})
        stats["last_recovery_probe"] = time.time()
        kind_before = stats.get("last_kind") or "unknown"
        try:
            # R-F3687 — THE SYSTEM PROMPT MUST NOT BE EMPTY.
            #
            # This was `complete("", "hi", ...)`. Measured live 2026-08-04, the
            # Anthropic provider applies `cache_control` to the system block, and
            # Anthropic rejects that on an empty one:
            #   HTTP 400 {"type":"invalid_request_error","message":
            #             "system.0: cache_control cannot be set for empty text blocks"}
            # That is kind="other" — INCONCLUSIVE by the rules below — so the probe
            # ran, failed for a reason that had nothing to do with credit, and left
            # the cooldown standing. Anthropic could never be released, which is
            # the exact defect R-F3685 exists to fix, reintroduced by its own probe.
            # Proven by the same call succeeding here the moment the prompt is
            # non-empty, while deepseek_backup (openai_compat, no cache_control)
            # was released on the first try.
            await provider.complete(
                "ping", "hi", max_tokens=1, timeout=_RECOVERY_PROBE_TIMEOUT_S,
            )
        except Exception as exc:
            _kind = getattr(exc, "kind", "") or ""
            if _kind == KIND_REASONING_TRUNCATED:
                # It generated tokens and ran out of budget — that is a live,
                # paid, authenticated provider answering. Proof of recovery.
                pass
            elif _kind in ("billing", "auth"):
                logger.info(
                    "[R-F3685] recovery probe: %s still %s — cooldown stands",
                    provider.name, _kind,
                )
                self._wire_probe_outcome(
                    provider.name, "still_locked_out",
                    f"kind={_kind} — operator action needed: {str(exc)[:200]}",
                )
                return False
            else:
                # A timeout or a 5xx says nothing about credit or keys. Do not
                # release on it (we have not proven health) and do not extend
                # (we have not proven sickness) — just try again next interval.
                logger.info(
                    "[R-F3685] recovery probe for %s inconclusive (%s): %s",
                    provider.name, _kind or "other", str(exc)[:150],
                )
                # R-F3693 — THE BRANCH WHOSE SILENCE HID R-F3687. An inconclusive
                # probe is a BROKEN SELF-HEAL until proven otherwise: the lockout
                # stands, the next attempt is 15 minutes away, and if the cause is
                # in the probe itself (it was) this repeats forever with nobody
                # told. Distinguished from still_locked_out because the remedies
                # are opposite — one needs the operator's wallet, the other needs
                # a code fix.
                self._wire_probe_outcome(
                    provider.name, "inconclusive",
                    f"kind={_kind or 'other'} — probe could not determine "
                    f"credit/auth state, lockout stands: {str(exc)[:200]}",
                )
                return False

        logger.warning(
            "[R-F3685] %s RECOVERED (was %s, %ds of cooldown remained) — "
            "returning it to the chain",
            provider.name, kind_before,
            int(max(0, self._cooldown_until(stats) - time.time())),
        )
        self._wire_probe_outcome(
            provider.name, "recovered",
            f"was {kind_before}; released with "
            f"{int(max(0, self._cooldown_until(stats) - time.time()))}s remaining",
        )
        self._record_success(provider, stats)
        return True

    def _schedule_recovery_probes(self) -> None:
        """Fire-and-forget the due probes. NEVER awaited by a user request.

        The slot is claimed BEFORE the task is spawned so two concurrent
        dispatches cannot both probe the same provider.
        """
        due = self._providers_due_for_recovery_probe()
        if not due:
            return  # healthy no-op — nothing due, nothing to report
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # R-F3705 — probes were DUE and cannot be scheduled, so the self-heal
            # is INERT. Returning silently here made that indistinguishable from
            # "nothing needed doing". Report it once per provider per interval
            # (the due-check already throttles) rather than going dark.
            for _p in due:
                self._wire_selfheal_fault(
                    _p.name, "probe_unschedulable",
                    "no running event loop — recovery probes cannot be started, "
                    "so hard cooldowns will stand for their full duration",
                )
            return
        now = time.time()
        for p in due:
            self._stats.setdefault(p.name, {})["last_recovery_probe"] = now
            loop.create_task(self._probe_recovery_quietly(p))

    def _wire_selfheal_fault(self, provider_name: str, outcome: str,
                             detail: str = "") -> None:
        """§21a — the self-heal FAILING TO RUN is a distinct, reportable fault.

        R-F3705. `_probe_recovery` reports its three outcomes (R-F3693), but the
        two functions WRAPPING it were dark: a crash was swallowed at
        `logger.debug` (not emitted at the service's level) and an unschedulable
        probe returned silently. Either way the self-heal stops and the 24h
        lockout it exists to lift simply stands — the R-F3687 failure class over
        again, where the recovery mechanism no-ops and only a human driving it by
        hand notices.

        Deliberately NOT folded into `inconclusive`: that means the probe RAN and
        could not determine credit state. These mean it did not run at all. One
        needs a code fix in the probe, the other in the scheduling — different
        remedies, so different names.
        """
        try:
            from ..intel.engine_wiring import wire_failure as _wf
            _wf(
                module="llm_recovery_probe",
                detail=f"{provider_name} {outcome}: {detail}"[:600],
                gap_type="llm_provider_failure",
                source=f"llm_recovery_probe:{outcome}",
            )
        except Exception:
            logger.debug("[R-F3705] self-heal fault wiring failed", exc_info=True)

    async def _probe_recovery_quietly(self, provider) -> None:
        """Background wrapper — a probing bug must never surface to a CALLER.

        R-F3705: "never surface anywhere" was the bug. Quiet UPWARD (it is a
        fire-and-forget task) must not mean quiet toward the BRAIN.
        """
        try:
            await self._probe_recovery(provider)
        except Exception as exc:
            logger.warning(
                "[R-F3705] recovery probe CRASHED for %s: %s",
                getattr(provider, "name", "?"), str(exc)[:200], exc_info=True,
            )
            self._wire_selfheal_fault(
                getattr(provider, "name", "?"), "probe_crashed",
                f"{type(exc).__name__}: {str(exc)[:200]} — the self-heal is not "
                f"running; any hard cooldown will stand for its full duration",
            )

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
        # R-F3680 — clear the cooldown's provenance with the cooldown itself,
        # or a later soft cooldown inherits a stale `cooldown_hard=True` and is
        # never last-resort dialled.
        stats.pop("cooldown_hard", None)
        stats.pop("cooldown_since", None)
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
                # R-F3680 — record WHAT KIND of cooldown this is and WHEN it
                # was armed. `_should_skip` needs both when it is deciding
                # whether a last-resort dial could possibly succeed; deriving
                # them later from `last_kind` alone cannot distinguish a
                # non-retryable "other" from a soft one.
                stats["cooldown_hard"] = True
                stats["cooldown_since"] = now
                # R-F3685 — THIS FAILURE IS THE MOST RECENT PROBE. Without
                # this, a provider that was proven dead a millisecond ago is
                # immediately "due" for a recovery probe, so the very next
                # request re-dials it — re-creating exactly the wasted re-probe
                # traffic R-F678 removed. The first recovery probe belongs one
                # full interval after the lockout is armed.
                #
                # Deliberately NOT set on the Redis rehydrate path: a cooldown
                # restored at boot is already hours old, so re-testing it once
                # per restart is correct and is what would have released
                # anthropic at the 2026-08-04 10:03 restart.
                stats["last_recovery_probe"] = now
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
                    evidence=str(error)[:400],   # R-F3685 — auditable lockout
                )
        elif kind == "rate_limit":
            stats["cooldown_until"] = now + self._SOFT_COOLDOWN_SECONDS
            stats["cooldown_hard"] = False   # R-F3680 — a 429 clears on its own
            stats["cooldown_since"] = now
            logger.warning(
                "Provider %s rate-limited, soft cooldown %ds",
                provider.name, self._SOFT_COOLDOWN_SECONDS,
            )
        else:
            # Only cool down after 2 consecutive failures for soft errors.
            if stats["failures"] >= 2:
                stats["cooldown_until"] = now + self._SOFT_COOLDOWN_SECONDS
                stats["cooldown_hard"] = False   # R-F3680 — timeout/server
                stats["cooldown_since"] = now
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
        # R-F3680 — name the failer, so the page can never report it as the
        # survivor that is "still serving".
        try:
            self._check_redundancy_lost(failed_provider=provider.name)
        except Exception:
            logger.debug("[R-F3616] redundancy check failed", exc_info=True)

    # ── F68: Redis cooldown mirror (HARD cooldowns only) ────────────────

    @staticmethod
    def _redis_key(provider_name: str) -> str:
        return f"{_REDIS_KEY_PREFIX}{provider_name}"

    def _mirror_cooldown_to_redis(self, provider_name: str, kind: str,
                                  cooldown_until: float,
                                  evidence: str = "") -> None:
        """Fire-and-forget write of a HARD cooldown to Redis. Never blocks
        the LLM hot path — failures are logged at debug only.

        R-F3685 — `evidence` is the provider's own error text, persisted with
        the cooldown. A 24h non-retryable lockout that survives restarts is one
        of the most consequential states in this service, and until now it
        recorded only `{until, kind}`. On 2026-08-04 anthropic was locked out
        while answering HTTP 200 on demand, and the question "what did it
        actually return at 07:43:17?" was UNANSWERABLE — nothing durable had
        kept it. A lockout that cannot be audited cannot be shown to be earned.
        """
        async def _write():
            try:
                from ..intel import redis_store as rs
                payload = json.dumps({
                    "until": cooldown_until, "kind": kind,
                    "armed_at": time.time(),
                    "evidence": (evidence or "")[:400],
                })
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
                # R-F3680 — ONLY hard cooldowns are ever mirrored (see
                # _mirror_cooldown_to_redis), so a rehydrated one is hard by
                # construction. Say so explicitly rather than leaving it to be
                # re-derived from `last_kind`, which cannot see a
                # non-retryable "other".
                #
                # `cooldown_since` is BOOT time, not the moment the cooldown was
                # armed — the mirror records only the end, so the true start is
                # not recoverable. That is safe today because `_cooldown_age` is
                # consulted ONLY for soft cooldowns and this branch is hard by
                # construction. If hard cooldowns ever become last-resort
                # dialable, mirror the start time before relying on this.
                stats["cooldown_hard"] = True
                stats["cooldown_since"] = now
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

        # R-F3685 — re-test any hard-cooling provider in the background. Never
        # awaited: this must not add a millisecond to the user's request.
        self._schedule_recovery_probes()

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
        _non_degrading = non_degrading_pins()
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
                # Scope: only providers named in ARIA_NON_DEGRADING_PINS (which
                # defaults to ARIA_PREFERENCE_ONLY_PROVIDERS, itself defaulting
                # to anthropic) are pinned this way. An ordinary prefer_provider
                # (e.g. the R-F1366 coder pin) still degrades through the chain
                # exactly as before.
                #
                # Read from the PIN set, not the chain-composition set: those are
                # two different questions, and reading the wrong one is how DD
                # loses its no-degrade contract the moment Claude is added to the
                # general chain. See non_degrading_pins().
                _pinned = (prefer_provider or "").lower() in _non_degrading
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
                    (prefer_provider or "").lower() in _non_degrading
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
            # R-F3680 — a cooldown says "go elsewhere"; honour it only when
            # there IS an elsewhere. `order` is the walk list, so entries this
            # dispatch will never reach cannot vouch for a skip.
            _alt = self._has_reachable_alternative(order, provider.name)
            if self._should_skip(stats, alternative_exists=_alt):
                logger.debug("Skipping %s (cooling down, %d recent failures)",
                             provider.name, stats.get("failures", 0))
                continue
            if not _alt and self._cooldown_until(stats) > time.time():
                logger.warning(
                    "[R-F3680] dialling %s DESPITE its cooldown — it is the "
                    "only reachable provider left; going silent is worse",
                    provider.name,
                )
                # R-F3693 §21a — a console line is DARK. The brain must see the
                # chain's most degraded serving state.
                self._wire_last_resort_dial(
                    provider.name, f"path=complete kind={stats.get('last_kind') or 'unknown'}")

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

        # R-F3685 §13 MIRROR — same background recovery probe as complete().
        self._schedule_recovery_probes()

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
            # R-F3680 §13 MIRROR — same reachability rule as complete(). Web
            # chat streams, so this is the fork a user is likeliest to hit.
            _alt = self._has_reachable_alternative(_stream_order, provider.name)
            if self._should_skip(stats, alternative_exists=_alt):
                logger.debug("Skipping %s for stream (cooling down)", provider.name)
                continue
            if not _alt and self._cooldown_until(stats) > time.time():
                logger.warning(
                    "[R-F3680] streaming from %s DESPITE its cooldown — it is "
                    "the only reachable provider left", provider.name,
                )
                # R-F3693 §13 MIRROR — same §21a wiring as complete().
                self._wire_last_resort_dial(
                    provider.name, f"path=stream kind={stats.get('last_kind') or 'unknown'}")

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

    def _check_redundancy_lost(self, *, failed_provider: str = "") -> None:
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
        # Count only the providers DISPATCH WILL ACTUALLY WALK — the same
        # preference_only filter complete()/stream() apply. This is the R-F3634
        # defect in a second place: that fix corrected get_health() but left this
        # alert reading the RAW chain.
        #
        # Measured live 2026-08-03. Chain [deepseek, anthropic, deepseek_backup];
        # both deepseek entries cooling. Unfiltered, `active` == ["anthropic"] ==
        # exactly 1, so this fired at 18:01 saying "STILL SERVING: anthropic
        # (answers are NOT degraded right now)". Anthropic is preference-only
        # (R-F2922/R-F3034: DD-reserved), so general chat had ZERO reachable
        # providers — it was ALREADY a total outage being reported as a warning
        # that explicitly denied degradation. The real outage page did not arrive
        # until 18:32, costing 31 minutes.
        #
        # Filtered, `active` is 0 here and the R-F3613 exhaustion path owns it,
        # which is the honest signal. Limitation kept deliberately: a DD-scoped
        # call CAN still reach a preference-only provider, so this check speaks
        # for the general path only — the path every non-DD user is on.
        _pref_only = preference_only_providers()
        _general = [p for p in self.providers
                    if (p.name or "").lower() not in _pref_only]
        if len(_general) < 2:
            return  # no redundancy existed — nothing to lose (see docstring)

        # R-F3681 (2026-08-04) — THE SURVIVOR CANNOT BE THE PROVIDER THAT JUST
        # FAILED.
        #
        # This check runs from `_record_failure`, so its subject is by
        # construction the provider that just failed. A provider's FIRST soft
        # failure sets no cooldown (the >=2-failures branch), so it still reads
        # as "active" here — and when it is the last one standing the page said
        #     STILL SERVING: deepseek (answers are NOT degraded right now)
        # from inside the failure handler of a request that was, in the same
        # call, about to be paged as "every provider failed". The operator got
        # both at 11:29 on 2026-08-04, one minute apart, saying opposite things.
        #
        # This is R-F3477's doctrine reaching the second implementation:
        # "`resilient` must follow OUTCOMES, not chain membership ... 'active'
        # only means a provider's cooldown timestamp has passed". The honest
        # claim is about someone ELSE: exclude the failer, and then
        #   1 remaining -> redundancy genuinely lost, the survivor really is
        #                  serving, and the operator needs to know.
        #   0 remaining -> the chain just exhausted; R-F3613 owns that page and
        #                  a "not degraded" warning would contradict it.
        now = time.time()
        _failed = (failed_provider or "").strip()
        active = [p.name for p in _general
                  if p.name != _failed
                  and not self._should_skip(self._stats.get(p.name, {}))]
        if len(active) != 1:
            return  # 0 -> that is the R-F3613 outage path; 2+ -> still redundant

        if now - _last_redundancy_alert_at < _CHAIN_ALERT_COOLDOWN_S:
            return
        _last_redundancy_alert_at = now

        cooling = [p.name for p in _general if p.name not in active]
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
        # R-F3634 — report the order DISPATCH WILL ACTUALLY WALK.
        #
        # complete() and stream() both build their order as
        #     [p for p in self.providers if p.name.lower() not in preference_only_providers()]
        # (R-F3034: Anthropic is reserved for DD, so a general chat call must never
        # fall onto it and burn Claude spend). This health summary did NOT apply that
        # filter, so it advertised a chain the request could not use.
        #
        # Live 2026-08-01 that cost real diagnostic time: five operator pages said
        # "every provider failed ... attempts=2, CALLED: deepseek, deepseek_backup"
        # while /health reported chain_order [deepseek, anthropic, deepseek_backup],
        # all three active, none cooling. Anthropic looked silently broken. It was not
        # — it is deliberately unreachable on that path. The dispatcher was right and
        # the surface describing it was wrong, which is the worst way round.
        _pref_only = preference_only_providers()
        _general = [p for p in self.providers if (p.name or "").lower() not in _pref_only]
        active: list[str] = []
        cooling: list[dict] = []
        for p in _general:
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
        chain_order = [p.name for p in _general]
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
            # R-F4222 — a DIFFERENT question from `resilient`, deliberately kept
            # separate. `resilient` is redundancy + recent outcomes (R-F3477);
            # this is "would dispatch dial anything right now?". Collapsing them
            # is what made admission refuse what dispatch would have served.
            "can_dispatch_now": self.can_dispatch_now(),
            "last_exhaustion_age_s": _exhausted_age,
            "primary_active": bool(active and chain_order and active[0] == chain_order[0]),
            "serving_provider": active[0] if active else None,
            "chain_order": chain_order,
            # Surfaced SEPARATELY, not hidden: these exist and are reachable, but
            # only when a caller names them (DD asks for anthropic by preference).
            # Folding them into chain_order overstates general-path redundancy;
            # omitting them entirely would hide a configured provider.
            "preference_only_providers": sorted(
                p.name for p in self.providers
                if (p.name or "").lower() in _pref_only
            ),
            # R-F3942 — the POLICY, not just the provider lists. A reader could
            # already see anthropic sitting in chain_order; nothing told them that
            # was a breach of Rule One rather than intended redundancy.
            "rule_one": rule_one_status(),
            # Published BESIDE preference_only because they are now two separate
            # questions, and the difference is invisible from outside without it.
            # With preference_only empty and this holding anthropic, the chain is
            # saying two things at once: "Claude MAY serve general traffic" and
            # "a pinned DD still cannot fall back to DeepSeek". An operator
            # reading only the first would see Claude in chain_order and have no
            # way to tell whether R-F3034's contract survived the change — which
            # is exactly how it was surrendered on 2026-08-03 without anything
            # erroring. R-F3634's rule applies to this pair too: publish what
            # dispatch actually reads, or the surface describes a different
            # system from the one running.
            "non_degrading_pins": sorted(non_degrading_pins()),
            # R-F3634 — DISTINCT VENDORS on the general path. `deepseek` and
            # `deepseek_backup` are two entries and ONE vendor: a vendor-side
            # timeout takes both, so failing over between them cannot help. Two
            # entries read as redundancy; this says how much there really is.
            "general_vendor_depth": len({
                (p.name or "").lower().split("_")[0] for p in _general
            }),
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
    # R-F3698 (2026-08-04) — CHAIN PLACEMENT IS ITS OWN DECISION.
    #
    # This read `ARIA_LLM_SHADOW` (R-F1949). That variable's OTHER consumer,
    # `model_router.promotion_stage()`, treats it as the CONSERVATIVE control —
    # its docstring says so outright: "the conservative flag wins; that is the
    # only direction that is safe to get wrong." Setting it means HOLD THE
    # SOVEREIGN BACK from serving grounded synthesis.
    #
    # Here the same variable meant the OPPOSITE: insert the sovereign into the
    # GENERAL fallback chain. So the one action an operator takes to be careful
    # also wired a RunPod endpoint into production failover — and per §24 that
    # pod is force-stopped outside its scheduled windows, i.e. offline most of
    # the week. docs/aria_llm_fallback_readiness_2026_08_01.md item 1: "A
    # fallback must be MORE available than what it backs up ... It would read as
    # added redundancy and subtract availability."
    #
    # R-F3636 fixed half of this — it made ARIA_LLM_SHADOW an INPUT to the stage
    # rather than a second switch — but never reached this builder. Measured live
    # 2026-08-04 the two consumers disagreed about the same word at the same
    # instant: model_router._shadow() -> True, _aria_llm_shadow -> False.
    #
    # Placement now has its own flag, defaulting OFF, and is NOT derived from the
    # promotion stage: a stage is about which GROUNDED SYNTHESIS turns route to
    # the sovereign, which is a different question from whether a sometimes-on
    # endpoint belongs in general failover. Deriving one from the other is what
    # created this defect. When always-on inference hosting is funded (readiness
    # item 1), flip this flag deliberately — do not re-couple them.
    #
    # BEHAVIOUR-PRESERVING: live config is ARIA_LLM_SHADOW='0' (sovereign not in
    # chain); the new flag is unset (sovereign not in chain). The chain does not
    # move. It simply can no longer be moved by accident.
    _aria_llm_in_chain = (
        os.getenv("ARIA_LLM_IN_FALLBACK_CHAIN", "").strip().lower()
        in ("1", "true", "yes")
    )
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
            if _aria_llm_in_chain:
                logger.info(
                    "ARIA-LLM (R-F3698 ARIA_LLM_IN_FALLBACK_CHAIN) at %s — will serve as FALLBACK below primary (canary); DeepSeek stays primary",
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

    # R-F1949 capability, re-addressed by R-F3698: slot ARIA-LLM right after the
    # primary so it serves only when the primary fails, DeepSeek staying primary
    # while ARIA-LLM gets observable real traffic. Now requested EXPLICITLY via
    # ARIA_LLM_IN_FALLBACK_CHAIN — the capability is unchanged, only the flag that
    # asks for it, which no longer doubles as "be conservative". See the note at
    # _aria_llm_in_chain.
    if (_aria_llm_provider is not None and _aria_llm_in_chain
            and _aria_llm_provider.is_configured and _aria_llm_provider not in providers):
        providers.append(_aria_llm_provider)
        logger.info(
            "ARIA-LLM appended as FALLBACK (R-F3698 explicit "
            "ARIA_LLM_IN_FALLBACK_CHAIN=1; primary unchanged)")

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


def get_provider_status(chain: Any | None = None) -> dict:
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

    # ── R-F3704 — a COOLING provider is not available ───────────────────────
    #
    # THE DEFECT: `available` was `configured and breaker != OPEN`, read from
    # the circuit-breaker registry ONLY. It never consulted the R-F678 billing
    # cooldown, which is a completely separate mechanism (`_stats[name]
    # ["cooldown_until"]`, set by `_record_failure`).
    #
    # Measured live 2026-08-04, both surfaces at the same moment:
    #   /health          llm_chain.cooling_providers = [anthropic (billing,
    #                    79796s remaining), deepseek_backup (billing, 79795s)]
    #   /health/perf     llm_providers.anthropic = {"available": true}
    #
    # The docstring promises "Never fabricates a provider being 'up'". That is
    # exactly what it did — and this field feeds R-F3612's self-introspection
    # chain-health block (routes/aria.py:9333-9349), the surface that exists
    # BECAUSE ARIA once answered "currently healthy" during a total chat outage.
    #
    # `deepseek_backup` was invisible entirely: it is not one of the slots
    # below, so its cooldown could never be reported. Slots are now unioned
    # with whatever the live chain actually holds.
    cooldowns: dict[str, float] = {}
    cool_kinds: dict[str, str] = {}
    live_names: list[str] = []
    try:
        import time as _t
        _now = _t.time()
        for _p in (getattr(chain, "providers", None) or []):
            _n = (getattr(_p, "name", "") or "")
            if not _n:
                continue
            live_names.append(_n)
            _s = (getattr(chain, "_stats", {}) or {}).get(_n, {}) or {}
            _cd = float(_s.get("cooldown_until", 0) or 0)
            if _cd > _now:
                cooldowns[_n.lower()] = _cd - _now
                cool_kinds[_n.lower()] = str(_s.get("last_kind", "") or "")
    except Exception as e:
        # UNKNOWN, not "fine": if we cannot read cooldowns we must not assert
        # availability, so `available` is reported as None below.
        logger.debug("get_provider_status: cooldown read failed: %s", e)
        cooldowns = {}
        cool_kinds = {}
        live_names = []
        _cooldowns_readable = False
    else:
        _cooldowns_readable = True

    # Union the declared slots with the chain's real membership so a slot the
    # table does not know about (deepseek_backup) still gets reported.
    _slot_names = {n for n, _ in slots}
    for _n in live_names:
        if _n not in _slot_names:
            slots.append((_n, True))  # present in the live chain ⇒ configured

    out: dict[str, dict] = {}
    for name, configured in slots:
        st = None
        for bn, bstate in breaker_state.items():
            if name in bn:
                st = bstate
                break
        _cool_s = cooldowns.get(name.lower())
        _cooling = _cool_s is not None
        if not _cooldowns_readable and configured:
            _available: bool | None = None      # could not measure — never True
        else:
            _available = bool(configured) and st != "OPEN" and not _cooling
        out[name] = {
            "configured": configured,
            "breaker_state": st,               # OPEN / CLOSED / HALF_OPEN, or None
            # R-F3704 — the cooldown is now first-class, and its REASON travels
            # with it: "billing" needs an operator top-up, "rate_limit" clears
            # itself. Reporting them identically pointed at the wrong fix.
            "cooling": _cooling,
            "cooldown_seconds_remaining": int(_cool_s) if _cool_s else 0,
            "cooldown_kind": cool_kinds.get(name.lower(), ""),
            "available": _available,
        }
    return out

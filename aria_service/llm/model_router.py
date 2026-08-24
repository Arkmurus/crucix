"""model_router — R-F2410 two-track sovereign/DeepSeek router (ACTIVATION PREP).

The sovereign 7B (aria_llm_grounded_dpo_v1, base Mistral-7B-Instruct-v0.3) is
PROVEN better than DeepSeek on GROUNDED synthesis (0.82 citation precision vs
0.67, half the fabricated citations — R-F2397 fresh eval) but weaker on coverage
(answers ~81% of answerable questions vs DeepSeek's 93%). So the activation design
is TWO-TRACK, not "sovereign primary for everything":

    grounded synthesis (tool-backed, retrieved context to synthesise)  -> SOVEREIGN
    closed-book / general / coverage / everything else                 -> DeepSeek
    sovereign error / timeout / cooldown                               -> DeepSeek
                                                                          (report
                                                                           "operational",
                                                                           never
                                                                           "degraded" — §14)

DEFAULT-SAFE (§16 activation is operator-gated): with ARIA_LLM_URL UNSET this
module is a pure pass-through — every synthesis call is byte-identical to today's
DeepSeek-only path. With ARIA_LLM_URL SET, the R-F2400 promotion gate defaults to
SHADOW: ARIA's sovereign model generates alongside DeepSeek but does not serve
users until an explicit promotion stage allows canary or serving.

Ramp knobs (see docs/aria_llm_v01_activation.md):
    ARIA_LLM_URL            base URL of the served sovereign endpoint (the flip)
    ARIA_LLM_PROMOTION_STAGE=shadow|canary|serve
                            promotion gate; default shadow when URL is set
    ARIA_LLM_SHADOW=1       SHADOW: generate sovereign ALONGSIDE but ship DeepSeek
                            to users; log the grounded-rate comparison (zero user
                            risk validation)
    ARIA_LLM_CANARY_PCT=N   CANARY: route only N% (0-100) of grounded turns to the
                            sovereign; the rest DeepSeek (stable per canary_key)
    ARIA_LLM_PRIMARY_ALL=1  legacy R-F93: sovereign primary for ALL turns (escape
                            hatch, NOT the two-track default)
    ARIA_LLM_ROUTER_DISABLED=1  hard-off: DeepSeek only even if URL set (incident)
    ARIA_LLM_TIMEOUT        per-call sovereign budget (s, default 40) before fallback

§13 mirrored: complete_synthesis() (chat) + stream_synthesis() (chat_stream).
§21a wired: every route decision + fallback -> brain.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time  # R-F2694 — throttle the unmeasurable-gate warning
from collections import deque
from typing import Any, AsyncGenerator, Callable, Optional

from .provider import LLMResult, LLMProvider
from . import aria_llm_provider

logger = logging.getLogger("aria.llm.model_router")

# §21a brain-wiring — soft-import so the router never hard-fails on wiring.
try:
    from ..intel.engine_wiring import wire_success, wire_failure
except Exception:  # pragma: no cover
    def wire_success(**_kw: Any) -> None: ...
    def wire_failure(**_kw: Any) -> None: ...


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


# ── Mode gates ────────────────────────────────────────────────────────────────

def sovereign_configured() -> bool:
    """The one activation flip — is a sovereign endpoint URL set?"""
    return bool((os.getenv("ARIA_LLM_URL") or "").strip())


def _router_disabled() -> bool:
    return _truthy(os.getenv("ARIA_LLM_ROUTER_DISABLED"))


def _primary_all() -> bool:
    """Legacy R-F93 escape hatch — sovereign primary for ALL turns (chain-level)."""
    return _truthy(os.getenv("ARIA_LLM_PRIMARY_ALL"))


def _shadow() -> bool:
    """R-F3636 — DERIVED from the stage, never read independently.

    This used to be `_truthy(os.getenv("ARIA_LLM_SHADOW"))`, a SECOND switch for a
    state promotion_stage() already owns. route_decision then combined them as
    `if _shadow() or stage == "shadow"`, so two env vars could disagree and the code
    silently privileged whichever was truthy — while get_health() published BOTH with
    no precedence.

    Live 2026-08-01: ARIA_LLM_PROMOTION_STAGE=shadow, ARIA_LLM_SHADOW=0,
    ARIA_LLM_CANARY_PCT=50. Reading `shadow: false` beside `canary_pct: 50` says she is
    serving half of chat. She is not — the OR short-circuits on stage. I misread it
    that way myself before tracing line 312, which is the point: a config that needs a
    code trace to interpret is not a config.
    """
    return promotion_stage() == "shadow"


def _capture_enabled() -> bool:
    """R-F2531 — is the grounded-synthesis flywheel capturing? When on, canary's
    not-selected grounded turns route through shadow so their sovereign side is
    generated + captured (see route_decision)."""
    return _truthy(os.getenv("ARIA_SHADOW_DISTILL_ENABLED"))


def promotion_stage() -> str:
    """Current sovereign promotion stage.

    ``shadow`` is the safe default once ARIA_LLM_URL is configured: the model can
    be measured live without serving users. ``canary`` enables percentage-based
    grounded serving via ARIA_LLM_CANARY_PCT. ``serve`` routes all grounded
    synthesis to sovereign, with DeepSeek fallback on error. ``off`` forces
    DeepSeek while keeping the endpoint configured for probes.
    """
    # R-F3636 — the legacy ARIA_LLM_SHADOW is an INPUT to the stage, never a bypass
    # and never overridable. Deriving the stage the other way round (letting
    # STAGE=canary win over an explicit SHADOW=1) would START SERVING USERS for anyone
    # relying on the older flag to hold the model back. The conservative flag wins;
    # that is the only direction that is safe to get wrong.
    if _truthy(os.getenv("ARIA_LLM_SHADOW")):
        return "shadow"
    raw = (os.getenv("ARIA_LLM_PROMOTION_STAGE") or "shadow").strip().lower()
    aliases = {
        "0": "off",
        "disable": "off",
        "disabled": "off",
        "deepseek": "off",
        "1": "shadow",
        "measure": "shadow",
        "shadow-only": "shadow",
        "2": "canary",
        "pilot": "canary",
        "3": "serve",
        "serving": "serve",
        "sovereign": "serve",
    }
    stage = aliases.get(raw, raw)
    if stage not in {"off", "shadow", "canary", "serve"}:
        return "shadow"
    return stage


def two_track_active() -> bool:
    """True iff the router should two-track (sovereign for grounded synthesis only).

    Active only when the sovereign URL is set AND we're not in the legacy
    primary-for-all mode AND not hard-disabled. In primary_all mode the chain
    (fallback.py) puts sovereign at the head for everything, so the router defers.
    """
    return sovereign_configured() and not _router_disabled() and not _primary_all()


# ── Grounded-synthesis detection ──────────────────────────────────────────────
# A "grounded synthesis" turn is one where tools/RAG gathered evidence and the
# LLM is synthesising an answer FROM that evidence — exactly the skill the
# sovereign beats DeepSeek on. Signalled by the tool/context markers the engine
# already threads through (aria_engine.py:3971 uses the same "[TOOL:" test for
# timeout tuning) plus a non-trivial retrieved intel context.
_TOOL_MARKERS = ("[TOOL:", "[I have already run", "[ATTACHED DOCUMENT")
_MIN_CONTEXT_CHARS = 200


def is_grounded_synthesis(message: str = "", context: str = "") -> bool:
    m = message or ""
    if any(k in m for k in _TOOL_MARKERS):
        return True
    return len((context or "").strip()) >= _MIN_CONTEXT_CHARS


# ── Canary assignment (stable per key) ────────────────────────────────────────

def _canary_pct() -> int:
    try:
        v = int((os.getenv("ARIA_LLM_CANARY_PCT") or "10").strip())
    except (TypeError, ValueError):
        return 100
    return max(0, min(100, v))


def _in_canary(canary_key: str) -> bool:
    pct = _canary_pct()
    if pct >= 100:
        return True
    if pct <= 0:
        return False
    h = int(hashlib.sha256((canary_key or "").encode("utf-8")).hexdigest(), 16)
    return (h % 100) < pct


def _sovereign_timeout(default: float) -> float:
    try:
        v = float((os.getenv("ARIA_LLM_TIMEOUT") or "").strip())
        return v if v >= 5 else default
    except (TypeError, ValueError):
        return default


# ── Route decision ────────────────────────────────────────────────────────────

def _sovereign_pod_serving() -> bool:
    """R-F2648 — is the sovereign pod expected to be serving right now?

    Delegates to runpod_scheduler.expected_serving() (active work-claim OR
    shadow-autostart-in-window) — the SAME no-network signal the health probe
    (resilience._probe) consults, so routing and health never disagree about a
    deliberately-stopped pod. Fails SAFE toward serving so an error never
    silently disables sovereign routing when the pod IS up.
    """
    try:
        from ..intel import runpod_scheduler as _sched
        return _sched.expected_serving()
    except Exception:
        return True


_WARN_EVERY_S = 300.0
_last_unmeasurable_warn = 0.0


def _sovereign_warm() -> Optional[bool]:
    """R-F2694 — has a live probe PROVEN the sovereign endpoint warm RIGHT NOW?

    True / False = MEASURED. None = no health checker is running, so this cannot be
    measured — which is NOT the same as "measured and failed" (the tri-state honesty
    rule CLAUDE.md §1 codifies for the phase gates: `None` renders `unknown`, never
    `open`). Callers must treat the three cases distinctly.

    Complements `_sovereign_pod_serving()` rather than replacing it. That signal is
    POLICY — "the §24 schedule says the pod SHOULD be up" — computed with no network
    call. This one is PROOF: `LLMHealthChecker.is_available()` is True only if a probe
    actually COMPLETED against the endpoint within `ARIA_LLM_WARM_TTL_S` and the
    breaker is closed (R-F1957's warm-gate). A pod that is scheduled-on but cold,
    hung, or still scaling from zero passes policy and fails proof — and that gap is
    exactly the traffic R-F2648's comment claims cannot reach a dead pod.
    """
    try:
        from . import resilience as _res
        hc = _res._health_checker_instance   # bound by LLMHealthChecker.start() (R-F2686)
        if hc is None:
            return None
        # A DISABLED checker (endpoint="") answers is_available()=False forever. That is
        # NOT "measured cold" — it is "never measured", and reporting it as cold would
        # send every grounded turn to DeepSeek SILENTLY (the False branch does not warn).
        # It happens when ARIA_LLM_URL becomes visible only after this module imported:
        # resilience binds _ARIA_LLM_URL at import (resilience.py:55) while
        # two_track_active() reads the env at CALL time, so the two can disagree. R-F2686
        # guards its own copy of this drift with a call-time re-read; do the same here
        # rather than let the gate fail silently.
        if not getattr(hc, "_enabled", True):
            return None
        return bool(hc.is_available())
    except Exception:
        return None


def _warn_sovereign_unmeasurable() -> None:
    """§21a — a gate that cannot measure must never be silent (throttled)."""
    global _last_unmeasurable_warn
    now = time.time()
    if (now - _last_unmeasurable_warn) < _WARN_EVERY_S:
        return
    _last_unmeasurable_warn = now
    logger.warning(
        "[R-F2694] sovereign warm-gate UNMEASURABLE — no usable LLMHealthChecker while "
        "ARIA_LLM_URL is set, so nothing can PROVE the pod is up. Failing closed: "
        "grounded turns route to DeepSeek (coverage is unaffected; sovereign/shadow "
        "capture is paused until a probe succeeds). Check the resilience layer started."
    )
    try:
        # NB: the 2nd param is `detail`, not `error` (engine_wiring.py:171-176) — the
        # wrong kwarg raises TypeError, which this try/except would SWALLOW, leaving a
        # wire that looks present and is dark (§21a). Verified against the signature.
        wire_failure(
            module="model_router",
            detail="sovereign warm-gate unmeasurable (no LLMHealthChecker running)",
            gap_type="llm_provider_failure",
            source="model_router:_sovereign_warm",
        )
    except Exception:
        pass


def route_decision(message: str = "", context: str = "", *, canary_key: str = "") -> str:
    """Return the routing verdict for a synthesis turn:
        "deepseek"  — closed-book / general / coverage / router off (default today)
        "sovereign" — grounded synthesis, sovereign serves (fallback to DeepSeek on error)
        "shadow"    — grounded synthesis, generate both but SHIP DeepSeek (validation)
    """
    if not two_track_active():
        return "deepseek"
    if not is_grounded_synthesis(message, context):
        return "deepseek"
    # R-F2648 — when the sovereign pod is DELIBERATELY off (CLAUDE.md §24
    # stop-only: no work-claim, outside any serving window) do not route
    # grounded turns to it. Without this, shadow/serve verdicts call
    # aria_llm_provider → the dead RunPod endpoint → 404 on every grounded turn
    # (the flywheel-capture path), then fall back to DeepSeek — pure latency +
    # noise. Same single signal the health probe uses, so the two never
    # disagree about when the pod is up. Fails SAFE toward routing on error.
    if not _sovereign_pod_serving():
        return "deepseek"
    # R-F2694 — POLICY said the pod should be up; now require PROOF that it IS.
    # `_sovereign_pod_serving()` is a no-network schedule check, so a pod that is
    # scheduled-on but cold / hung / still scaling from zero passed it and took the
    # grounded turn — eating the full call timeout before falling back to DeepSeek.
    # The probe already knows better (10s cadence, R-F1957 warm-gate). Checked AFTER
    # the schedule gate so a deliberately-stopped pod (§24) still short-circuits with
    # no dependency on the probe.
    #
    # Tri-state, and BOTH non-True cases route to DeepSeek — for different reasons:
    #   False = measured cold  → the probe proved the pod is not up.
    #   None  = NOT measurable → nothing has proven it IS up, so admitting it would be
    #           a claim we cannot back. R-F1957's rule is "unproven = skip", and the
    #           sibling gate on this exact singleton (resilience._admission, R-F2686)
    #           already fails CLOSED here; a Pass-2 review caught these two disagreeing
    #           on identical input. They must not. Note the router does NOT go through
    #           wrap(), so on None there is no second line of defence — _sovereign_complete
    #           calls aria_llm_provider directly. None is reachable in the boot window
    #           before start() binds the singleton, which correlates POSITIVELY with a
    #           cold scale-from-zero pod: precisely when fail-open is most wrong.
    # Failing closed costs sovereign/flywheel traffic while unmeasurable — an acceptable
    # price, loudly warned + brain-wired, never silent (§21a).
    _warm = _sovereign_warm()
    if _warm is None:
        _warn_sovereign_unmeasurable()
        return "deepseek"
    if _warm is False:
        return "deepseek"
    stage = promotion_stage()
    if stage == "off":
        return "deepseek"
    # R-F3636 — ONE source. `_shadow()` now derives from `stage`, so the old OR was
    # `stage == "shadow" or stage == "shadow"`. Behaviour is identical; what changes is
    # that there is no longer a second switch able to disagree with the first.
    if stage == "shadow":
        return "shadow"
    if stage == "serve":
        return "sovereign"
    if not _in_canary(canary_key):
        # R-F2531 — canary's NOT-selected grounded turns: when the flywheel is
        # capturing (ARIA_SHADOW_DISTILL_ENABLED), route them through SHADOW so the
        # sovereign is generated in the background and the DeepSeek-vs-sovereign
        # comparison is captured for the DPO flywheel. The USER still gets DeepSeek
        # (shadow ships DeepSeek) — identical UX, zero added user latency. Without
        # capture enabled, plain DeepSeek (byte-identical to before).
        if _capture_enabled():
            return "shadow"
        return "deepseek"
    return "sovereign"


async def _sovereign_complete(
    system: str, user: str, *, max_tokens: int, timeout: float,
) -> LLMResult | None:
    """One sovereign completion -> LLMResult, or None on any failure (caller
    falls back to DeepSeek). aria_llm_provider already wires its own failures.

    R-F3606 — THE sovereign chat chokepoint: every sovereign completion on the
    chat path (routed turns and the shadow-compare) goes through here, so this
    is where R-F1360's latency ceiling is applied. It is deliberately NOT
    applied inside aria_llm_provider, which the self-coder also calls with
    max_tokens=8192 for whole-file generation (R-F1363).
    """
    try:
        r = await aria_llm_provider.complete(
            user, system=system,
            max_tokens=aria_llm_provider.clamp_for_sovereign(max_tokens),
            timeout=timeout,
        )
    except Exception as e:  # network/timeout/etc.
        logger.warning("[model_router] sovereign complete raised: %s", e)
        return None
    if r.get("ok") and (r.get("text") or "").strip():
        return LLMResult(
            text=r["text"],
            input_tokens=int(r.get("tokens_in", 0) or 0),
            output_tokens=int(r.get("tokens_out", 0) or 0),
            model=r.get("model", "aria-llm"),
            routed_via="sovereign",
        )
    return None


# R-F2521 — in-memory shadow-comparison accumulator. wire_success telemetry
# DROPS the summary string (brain_hook.record_signal stores only module+success),
# so the grounded-rate deltas were un-readable. This keeps a running tally +
# recent samples in process memory (resets on restart — fine, shadow is a live
# measurement window) and is exposed via shadow_stats() / the /llm/shadow route.
_shadow_stats_acc: dict[str, float] = {
    "samples": 0, "deepseek_sum": 0.0, "sovereign_sum": 0.0,
    "sovereign_wins": 0, "sovereign_answered": 0,
}
_shadow_recent: deque = deque(maxlen=50)  # (deepseek_score, sovereign_score|None)


def _record_shadow_stat(deepseek_score: float, sovereign_score: float | None) -> None:
    _shadow_stats_acc["samples"] += 1
    _shadow_stats_acc["deepseek_sum"] += deepseek_score
    if sovereign_score is not None:
        _shadow_stats_acc["sovereign_answered"] += 1
        _shadow_stats_acc["sovereign_sum"] += sovereign_score
        if sovereign_score > deepseek_score:
            _shadow_stats_acc["sovereign_wins"] += 1
    _shadow_recent.append((
        round(deepseek_score, 3),
        round(sovereign_score, 3) if sovereign_score is not None else None,
    ))


def shadow_stats() -> dict[str, Any]:
    """Readable summary of the shadow grounded-rate comparison (R-F2521)."""
    s = _shadow_stats_acc
    n = int(s["samples"]) or 1
    ans = int(s["sovereign_answered"]) or 1
    return {
        "samples": int(s["samples"]),
        "sovereign_answered": int(s["sovereign_answered"]),
        "deepseek_grounded_mean": round(s["deepseek_sum"] / n, 3),
        "sovereign_grounded_mean": round(s["sovereign_sum"] / ans, 3),
        "sovereign_win_rate": round(s["sovereign_wins"] / ans, 3),
        "recent": list(_shadow_recent)[-15:],
    }


def _log_shadow(context: str, base: LLMResult, sov: LLMResult | None,
                message: str = "") -> None:
    """SHADOW stage — compare grounded-rate of DeepSeek vs sovereign on the SAME
    grounded turn (R-F2397 grounding_reward now parses production context)."""
    try:
        from ..intel import grounding_reward as gr
        b = gr.score(base.text or "", context or "")
        s = gr.score((sov.text if sov else "") or "", context or "")
        _record_shadow_stat(b.score, s.score if sov else None)  # R-F2521 readable tally
        # R-F2527 — durably capture the discarded comparison for the DPO flywheel.
        # Flag-gated OFF by default (ARIA_SHADOW_DISTILL_ENABLED); soft-import so the
        # router never hard-fails on it; the module itself never raises.
        try:
            from ..intel import grounded_shadow_distill as _gsd
            _gsd.record_shadow_pair(
                message=message,
                context=context or "",
                deepseek_text=base.text or "",
                sovereign_text=(sov.text if sov else None),
                deepseek_score=b.score,
                sovereign_score=(s.score if sov else None),
                deepseek_breakdown=b,
                sovereign_breakdown=(s if sov else None),
            )
        except Exception:
            pass
        wire_success(
            module="model_router",
            summary=(f"SHADOW grounded-rate deepseek={b.score:.3f} "
                     f"sovereign={(s.score if sov else float('nan')):.3f} "
                     f"(shipped=deepseek)"),
            source_id="model_router:shadow_compare",
        )
    except Exception as e:  # pragma: no cover
        logger.debug("[model_router] shadow compare failed: %s", e)


# ── Async shadow comparison (R-F2520) ─────────────────────────────────────────
# Option (a): in the SHADOW stage, generate the sovereign side FIRE-AND-FORGET
# AFTER the user already has DeepSeek's answer. This lets the STREAM path (which
# ships DeepSeek and returns immediately) sample the sovereign for the
# grounded-rate comparison at ZERO added user latency. Pre-R-F2520 shadow only
# fired on the non-stream path (a sequential await = latency tax), and live
# traffic is streaming, so it collected nothing (R-F2517 monitoring: 0 organic
# samples over ~8h). This closes that gap.
_shadow_bg_tasks: set = set()
_SHADOW_MAX_INFLIGHT = 32  # cap concurrent shadow calls so a burst can't pile up
                           # unbounded; drops are wired, never silent.


def _shadow_max_inflight() -> int:
    try:
        v = int((os.getenv("ARIA_LLM_SHADOW_MAX_INFLIGHT") or "").strip())
        return v if v >= 1 else _SHADOW_MAX_INFLIGHT
    except (TypeError, ValueError):
        return _SHADOW_MAX_INFLIGHT


async def _shadow_compare_bg(
    system_prompt: str, user_message: str, context: str,
    base_text: str, max_tokens: int,
) -> None:
    """Background sovereign generation + grounded-rate log. Never raises."""
    try:
        sov = await _sovereign_complete(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=_sovereign_timeout(40.0),
        )
        _log_shadow(
            context,
            LLMResult(text=base_text, model="deepseek", routed_via="deepseek"),
            sov,
            message=user_message,
        )
    except Exception as e:  # pragma: no cover — fire-and-forget must never surface
        logger.debug("[model_router] shadow bg compare failed: %s", e)


def _spawn_shadow_compare(
    system_prompt: str, user_message: str, context: str,
    base_text: str, max_tokens: int,
) -> "object | None":
    """Fire-and-forget the sovereign shadow comparison on the running loop.
    Zero added user latency — the caller has already shipped DeepSeek. Returns
    the task (for tests) or None when skipped (no loop / backpressure)."""
    import asyncio
    cap = _shadow_max_inflight()
    if len(_shadow_bg_tasks) >= cap:
        wire_failure(
            module="model_router",
            detail=f"shadow compare dropped — {len(_shadow_bg_tasks)} in flight >= cap {cap}",
            gap_type="shadow_backpressure",
            source="model_router:shadow_drop",
        )
        return None
    try:
        task = asyncio.create_task(
            _shadow_compare_bg(system_prompt, user_message, context,
                               base_text, max_tokens)
        )
    except RuntimeError:
        return None  # no running loop (sync caller) — skip
    _shadow_bg_tasks.add(task)
    task.add_done_callback(_shadow_bg_tasks.discard)
    return task


def _verify_grounded(text: str, context: str, message: str) -> str:
    """R-F2542 — THE honesty guarantee: strip any citation that does not resolve to
    the retrieved evidence BEFORE the answer reaches the user, so no fabricated /
    made-up source can ever ship. Grounded turns only (needs context to verify
    against). Deterministic, never raises; on any issue returns the text unchanged."""
    try:
        if not is_grounded_synthesis(message, context):
            return text
        from ..intel import citation_verifier as cv
        v = cv.verify_and_clean(text or "", context or "")
        out = text
        if v.get("fabricated_removed"):
            wire_success(
                module="model_router",
                summary=f"citation-verify: stripped {v['fabricated_removed']} unverified source(s) before shipping",
                source_id="model_router:citation_verify",
            )
            out = v["answer"]
        # R-F2809 (north star P1) — brain-central CLAIM grounding: extend the
        # deterministic source-verify to FIGURES. An asserted figure absent from the
        # evidence AND the user's question, with no citation / hedge / derivation /
        # hypothetical, is an ungrounded numeric claim. Default 'off' (inert on
        # deploy); 'measure' observes the rate WITHOUT altering the answer; 'flag'
        # marks ungrounded figures [unverified]. Both chat + stream inherit this.
        mode = (os.getenv("ARIA_CLAIM_GROUNDING", "off") or "off").strip().lower()
        if mode in ("measure", "flag"):
            from ..intel import claim_grounding as cg
            cgr = cg.ground_claims(out, context or "", message=message or "", mode=mode)
            if cgr.get("ungrounded_sentences"):
                # R-F2811 — log to stdout so the measure rate is watchable in fly logs
                # (wire_success only bumps a generic module counter and drops the detail).
                logger.info(
                    "[R-F2809 claim-grounding %s] %d ungrounded-figure sentence(s): %s",
                    mode, cgr["ungrounded_sentences"], cgr["ungrounded_figures"][:6],
                )
                wire_success(
                    module="model_router",
                    summary=f"claim-grounding [{mode}]: {cgr['ungrounded_sentences']} ungrounded-figure "
                            f"sentence(s): {cgr['ungrounded_figures'][:4]}",
                    source_id="model_router:claim_grounding",
                )
                if mode == "flag":
                    out = cgr["answer"]
        return out
    except Exception:  # pragma: no cover — verification must never break a turn
        return text


async def complete_synthesis(
    base_llm: LLMProvider,
    system_prompt: str,
    user_message: str,
    *,
    message: str = "",
    context: str = "",
    max_tokens: int = 4096,
    timeout: float = 60.0,
    canary_key: str = "",
) -> LLMResult:
    """Two-track synthesis completion (chat path). Pass-through to DeepSeek when
    the router is inactive (URL unset) — byte-identical to today. On sovereign
    error/timeout -> DeepSeek fallback, reported operational (§14). R-F2542: the
    final answer is citation-verified before return — no fabricated source ships."""
    decision = route_decision(message, context, canary_key=canary_key)

    if decision == "deepseek":
        result = await base_llm.complete(
            system_prompt, user_message, max_tokens=max_tokens, timeout=timeout,
        )
    elif decision == "shadow":
        result = await base_llm.complete(
            system_prompt, user_message, max_tokens=max_tokens, timeout=timeout,
        )
        # R-F2520 — fire-and-forget the sovereign compare so there is NO latency
        # tax on the shipped DeepSeek answer (was a sequential await pre-R-F2520).
        _spawn_shadow_compare(system_prompt, user_message, context,
                              result.text or "", max_tokens)
    else:  # decision == "sovereign"
        sov = await _sovereign_complete(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=_sovereign_timeout(timeout),
        )
        if sov is not None:
            wire_success(
                module="model_router",
                summary="grounded synthesis routed to sovereign (operational)",
                source_id="model_router:route_sovereign",
            )
            result = sov
        else:
            # §14 — cooling/error is NOT degraded; fall back to DeepSeek, stay operational.
            wire_failure(
                module="model_router",
                detail="sovereign unavailable on grounded turn — fell back to DeepSeek (operational)",
                gap_type="llm_fallback",
                source="model_router:fallback_deepseek",
            )
            result = await base_llm.complete(
                system_prompt, user_message, max_tokens=max_tokens, timeout=timeout,
            )

    # R-F2542 — citation verification gate: no fabricated source reaches the user.
    try:
        result.text = _verify_grounded(result.text, context, message)
    except Exception:
        pass
    return result


async def stream_synthesis(
    base_llm: LLMProvider,
    system_prompt: str,
    user_message: str,
    *,
    message: str = "",
    context: str = "",
    max_tokens: int = 4096,
    timeout: float = 120.0,
    on_done: Optional[Callable[[LLMResult], None]] = None,
    canary_key: str = "",
) -> AsyncGenerator[str, None]:
    """Two-track synthesis stream (chat_stream path, §13 mirror). Pass-through to
    DeepSeek stream when inactive. Sovereign streaming falls back to DeepSeek only
    BEFORE the first token is emitted (a mid-stream failure surfaces to the caller's
    existing local_brain handler, same as today). SHADOW ships DeepSeek's stream."""
    decision = route_decision(message, context, canary_key=canary_key)
    grounded = is_grounded_synthesis(message, context)

    # Non-grounded turns carry no evidence to cite → nothing to verify → stream
    # token-by-token exactly as before (byte-identical, zero added latency).
    if decision == "deepseek" and not grounded:
        async for chunk in base_llm.stream(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout, on_done=on_done,
        ):
            yield chunk
        return

    # GROUNDED via DeepSeek (deepseek-routed grounded turn, or shadow which ships
    # DeepSeek): R-F2542 — a grounded answer must be citation-verified BEFORE the
    # user sees it, so we generate it fully, strip any unverifiable source, then
    # emit. No fabricated citation can appear mid-stream.
    if decision in ("deepseek", "shadow"):
        _parts: list[str] = []
        async for chunk in base_llm.stream(
            system_prompt, user_message, max_tokens=max_tokens, timeout=timeout,
        ):
            _parts.append(chunk)
        full = "".join(_parts)
        if decision == "shadow":
            _spawn_shadow_compare(system_prompt, user_message, context, full, max_tokens)
        cleaned = _verify_grounded(full, context, message)
        if on_done:
            try:
                on_done(LLMResult(text=cleaned, model="deepseek", routed_via="deepseek"))
            except Exception:  # pragma: no cover
                pass
        yield cleaned
        return

    # decision == "sovereign" — buffer sovereign, verify, emit; any failure → DeepSeek.
    emitted = False
    full = ""
    try:
        # R-F3606 §13 mirror — same chat-only latency ceiling as _sovereign_complete.
        async for piece in aria_llm_provider.stream(
            user_message, system=system_prompt,
            max_tokens=aria_llm_provider.clamp_for_sovereign(max_tokens),
            temperature=0.3,
        ):
            if piece:
                emitted = True
                full += piece
    except Exception as e:
        logger.warning("[model_router] sovereign stream failed (%s) — %s",
                       "mid" if emitted else "pre-token", e)
        emitted = False  # buffered — nothing shipped yet, so fall back cleanly

    if not emitted:
        wire_failure(
            module="model_router",
            detail="sovereign stream unavailable — fell back to DeepSeek (operational)",
            gap_type="llm_fallback",
            source="model_router:stream_fallback",
        )
        _parts = []
        async for chunk in base_llm.stream(
            system_prompt, user_message, max_tokens=max_tokens, timeout=timeout,
        ):
            _parts.append(chunk)
        cleaned = _verify_grounded("".join(_parts), context, message)
        if on_done:
            try:
                on_done(LLMResult(text=cleaned, model="deepseek", routed_via="deepseek"))
            except Exception:  # pragma: no cover
                pass
        yield cleaned
        return

    wire_success(
        module="model_router",
        summary="grounded synthesis STREAM routed to sovereign (operational)",
        source_id="model_router:route_sovereign_stream",
    )
    cleaned = _verify_grounded(full, context, message)
    if on_done:
        try:
            on_done(LLMResult(text=cleaned, model="aria-llm", routed_via="sovereign"))
        except Exception:  # pragma: no cover
            pass
    yield cleaned


def serving_users() -> bool:
    """Is the sovereign model answering ANY real user turn right now?

    R-F4299 (C-253). The 2026-08-01 readiness doc asked exactly this question and
    could not answer it from the surface: "is she shadowing, or serving 50% of
    chat?" Answering it required reading `promotion_stage`, `canary_pct`,
    `primary_all` and `router_disabled` and running the precedence rules by hand —
    and its author recorded getting it wrong. A config that needs a code trace to
    interpret is not a config, so this states the consequence directly.

    Deliberately INCLUDES `primary_all`: R-F93's escape hatch routes every turn
    regardless of stage, and reporting `serving_users: False` beside an active
    escape hatch would be the same class of lie this fix exists to remove.
    """
    if _router_disabled():
        return False
    if _primary_all():
        return True
    return promotion_stage() in ("canary", "serve")


def canary_pct_effective() -> int:
    """The share of grounded turns the sovereign ACTUALLY takes, 0-100.

    R-F4299 (C-253) — `canary_pct` is a raw knob and is INERT outside the canary
    stage. Live 2026-08-24 it read 50 while the stage was `shadow`, so the surface
    said "50" about a model serving nobody. The number was not wrong; it simply
    did not mean what any reader would take it to mean. `canary_pct` is still
    published beside this, because hiding the knob would trade one confusion for
    another — the reader needs to see both the setting and its effect.
    """
    if _router_disabled():
        return 0
    if _primary_all():
        return 100
    stage = promotion_stage()
    if stage == "serve":
        return 100
    if stage == "canary":
        return _canary_pct()
    return 0


def sovereign_model() -> str:
    """Which model id the router would call. R-F4299 — nothing reported this, so
    no surface could show that live points at `aria-llm-v0.1` while the only
    models with recorded 500-Q evals are v0.2 and v0.4."""
    return (os.getenv("ARIA_LLM_MODEL") or "").strip()


def summary() -> dict[str, Any]:
    """Capability-manifest / diagnostics entry (no secrets)."""
    return {
        "module": "model_router",
        "purpose": "R-F2410 two-track: sovereign for grounded synthesis, DeepSeek coverage/fallback",
        "sovereign_configured": sovereign_configured(),
        "two_track_active": two_track_active(),
        # R-F3636 — report the EFFECTIVE mode, plus the raw input, clearly labelled.
        # Publishing two independent-looking switches with no precedence is what made
        # this unreadable: a reader cannot tell which one the router obeyed.
        "promotion_stage": promotion_stage(),
        "shadow": _shadow(),                       # derived: stage == "shadow"
        "shadow_env_override": _truthy(os.getenv("ARIA_LLM_SHADOW")),
        "canary_pct": _canary_pct(),
        # R-F4299 (C-253) — CONSEQUENCES, not just knobs. `canary_pct: 50`
        # beside `promotion_stage: shadow` reads as "half of chat is served"
        # and means nothing of the sort; these say what actually happens.
        "serving_users": serving_users(),
        "canary_pct_effective": canary_pct_effective(),
        "model": sovereign_model(),
        "legacy_shadow_var_present": os.getenv("ARIA_LLM_SHADOW") is not None,
        "primary_all": _primary_all(),
        "router_disabled": _router_disabled(),
        "shadow_inflight": len(_shadow_bg_tasks),  # R-F2520 async compares in flight
        # R-F2694 — the two gates that can send 100% of grounded traffic to DeepSeek
        # while everything above still reads "serve". Without these, diagnostics report
        # an intent ("promotion_stage: serve") and hide the actual routing — the reader
        # cannot tell a live sovereign from a dormant one (§22: state what is MEASURED).
        # R-F2648 opened this gap; R-F2694 added a second reason, so both are named here.
        "sovereign_pod_serving": _sovereign_pod_serving(),   # POLICY (§24 schedule)
        "sovereign_warm": _sovereign_warm(),                 # PROOF (None = unmeasurable)
    }

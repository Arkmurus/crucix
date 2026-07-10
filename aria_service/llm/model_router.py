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
    return _truthy(os.getenv("ARIA_LLM_SHADOW"))


def promotion_stage() -> str:
    """Current sovereign promotion stage.

    ``shadow`` is the safe default once ARIA_LLM_URL is configured: the model can
    be measured live without serving users. ``canary`` enables percentage-based
    grounded serving via ARIA_LLM_CANARY_PCT. ``serve`` routes all grounded
    synthesis to sovereign, with DeepSeek fallback on error. ``off`` forces
    DeepSeek while keeping the endpoint configured for probes.
    """
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
    stage = promotion_stage()
    if stage == "off":
        return "deepseek"
    if _shadow() or stage == "shadow":
        return "shadow"
    if stage == "serve":
        return "sovereign"
    if not _in_canary(canary_key):
        return "deepseek"
    return "sovereign"


async def _sovereign_complete(
    system: str, user: str, *, max_tokens: int, timeout: float,
) -> LLMResult | None:
    """One sovereign completion -> LLMResult, or None on any failure (caller
    falls back to DeepSeek). aria_llm_provider already wires its own failures."""
    try:
        r = await aria_llm_provider.complete(
            user, system=system, max_tokens=max_tokens, timeout=timeout,
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


def _log_shadow(context: str, base: LLMResult, sov: LLMResult | None) -> None:
    """SHADOW stage — compare grounded-rate of DeepSeek vs sovereign on the SAME
    grounded turn (R-F2397 grounding_reward now parses production context)."""
    try:
        from ..intel import grounding_reward as gr
        b = gr.score(base.text or "", context or "")
        s = gr.score((sov.text if sov else "") or "", context or "")
        _record_shadow_stat(b.score, s.score if sov else None)  # R-F2521 readable tally
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
    error/timeout -> DeepSeek fallback, reported operational (§14)."""
    decision = route_decision(message, context, canary_key=canary_key)

    if decision == "deepseek":
        return await base_llm.complete(
            system_prompt, user_message, max_tokens=max_tokens, timeout=timeout,
        )

    if decision == "shadow":
        base_res = await base_llm.complete(
            system_prompt, user_message, max_tokens=max_tokens, timeout=timeout,
        )
        # R-F2520 — fire-and-forget the sovereign compare so there is NO latency
        # tax on the shipped DeepSeek answer (was a sequential await pre-R-F2520).
        _spawn_shadow_compare(system_prompt, user_message, context,
                              base_res.text or "", max_tokens)
        return base_res   # SHIP DeepSeek — zero user risk, zero added latency

    # decision == "sovereign"
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
        return sov
    # §14 — cooling/error is NOT degraded; fall back to DeepSeek, stay operational.
    wire_failure(
        module="model_router",
        detail="sovereign unavailable on grounded turn — fell back to DeepSeek (operational)",
        gap_type="llm_fallback",
        source="model_router:fallback_deepseek",
    )
    return await base_llm.complete(
        system_prompt, user_message, max_tokens=max_tokens, timeout=timeout,
    )


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

    if decision == "deepseek":
        async for chunk in base_llm.stream(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout, on_done=on_done,
        ):
            yield chunk
        return

    if decision == "shadow":
        # R-F2520 — ship DeepSeek's stream to the user AND accumulate it, then
        # fire-and-forget the sovereign compare AFTER the stream completes. This
        # samples STREAMING grounded traffic (the dominant path) at ZERO added
        # user latency — the gap R-F2517 monitoring found (stream shadow was a
        # no-op, so shadow collected nothing live).
        _parts: list[str] = []
        async for chunk in base_llm.stream(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout, on_done=on_done,
        ):
            _parts.append(chunk)
            yield chunk
        _spawn_shadow_compare(system_prompt, user_message, context,
                              "".join(_parts), max_tokens)
        return

    # decision == "sovereign" — stream sovereign, pre-first-token fallback to DeepSeek.
    emitted = False
    full = ""
    try:
        async for piece in aria_llm_provider.stream(
            user_message, system=system_prompt,
            max_tokens=max_tokens, temperature=0.3,
        ):
            if piece:
                emitted = True
                full += piece
                yield piece
    except Exception as e:
        if emitted:
            raise  # mid-stream failure -> caller's existing handler (as today)
        logger.warning("[model_router] sovereign stream failed pre-token: %s", e)

    if not emitted:
        wire_failure(
            module="model_router",
            detail="sovereign stream produced no tokens — fell back to DeepSeek (operational)",
            gap_type="llm_fallback",
            source="model_router:stream_fallback",
        )
        async for chunk in base_llm.stream(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout, on_done=on_done,
        ):
            yield chunk
        return

    wire_success(
        module="model_router",
        summary="grounded synthesis STREAM routed to sovereign (operational)",
        source_id="model_router:route_sovereign_stream",
    )
    if on_done:
        try:
            on_done(LLMResult(text=full, model="aria-llm", routed_via="sovereign"))
        except Exception:  # pragma: no cover
            pass


def summary() -> dict[str, Any]:
    """Capability-manifest / diagnostics entry (no secrets)."""
    return {
        "module": "model_router",
        "purpose": "R-F2410 two-track: sovereign for grounded synthesis, DeepSeek coverage/fallback",
        "sovereign_configured": sovereign_configured(),
        "two_track_active": two_track_active(),
        "promotion_stage": promotion_stage(),
        "shadow": _shadow(),
        "canary_pct": _canary_pct(),
        "primary_all": _primary_all(),
        "router_disabled": _router_disabled(),
        "shadow_inflight": len(_shadow_bg_tasks),  # R-F2520 async compares in flight
    }

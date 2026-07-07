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


def _log_shadow(context: str, base: LLMResult, sov: LLMResult | None) -> None:
    """SHADOW stage — compare grounded-rate of DeepSeek vs sovereign on the SAME
    grounded turn (R-F2397 grounding_reward now parses production context)."""
    try:
        from ..intel import grounding_reward as gr
        b = gr.score(base.text or "", context or "")
        s = gr.score((sov.text if sov else "") or "", context or "")
        wire_success(
            module="model_router",
            summary=(f"SHADOW grounded-rate deepseek={b.score:.3f} "
                     f"sovereign={(s.score if sov else float('nan')):.3f} "
                     f"(shipped=deepseek)"),
            source_id="model_router:shadow_compare",
        )
    except Exception as e:  # pragma: no cover
        logger.debug("[model_router] shadow compare failed: %s", e)


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
        sov = await _sovereign_complete(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=_sovereign_timeout(40.0),
        )
        _log_shadow(context, base_res, sov)
        return base_res   # SHIP DeepSeek — zero user risk

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

    if decision in ("deepseek", "shadow"):
        async for chunk in base_llm.stream(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout, on_done=on_done,
        ):
            yield chunk
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
    }

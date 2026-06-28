"""R-F595 — Auto-fire self_introspect on self-capability questions.

Past incident 2026-05-16: operator asked "Aria, share an overview of your
full system capabilities" — ARIA emitted "34 autonomous tasks",
"48/49 sources OK", "Officeholder Verification 18-month TTL" — all
fabrications because the LLM did not proactively call self_introspect.
Real values: 78 tasks / 431 sources / no TTL (permanent per
aria_infinite_memory.md).

This module pre-detects capability/architecture questions in the user
message and fetches /api/aria/health/perf BEFORE the LLM call, prepending
a [TOOL: self_introspect] block to the chat context. The LLM then has
real numbers available and the R-F401/R-F594 guard catches any deviation.

Pairs with:
  - aria_engine.py Clause 25 (LLM-level instruction)
  - self_claim_guard.py (response post-scan)
  - routes/aria.py:health_perf_ep (the live data source)
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import re
import time  # R-F1611 — uptime for build/deploy proprioception

logger = logging.getLogger("aria.intel.self_introspect_guard")


# Keywords suggesting the user is asking about ARIA's own capabilities,
# architecture, sources, or state. Detection is conservative — false
# negatives are fine (Clause 25 still applies, R-F401 guard still scans);
# false positives are cheap (an extra /health/perf call is in-process).
_CAPABILITY_KEYWORDS = re.compile(
    r"(?:"
        # "your <noun>" with optional adjective(s) between
        r"\byour\s+(?:\w+\s+){0,3}"
        r"(?:capabilit(?:y|ies)|architecture|sources?|tasks?|"
        r"layers?|signals?|memory|brain|knowledge|state|ttl|retention|"
        r"engine|features?|tools?|inventory|coverage|ledger)\b"
    r"|"
        r"\bhow\s+many\s+(?:\w+\s+){0,2}"
        r"(?:tasks?|sources?|signals?|facts?|chunks?|"
        r"layers?|neurons?|countries|jurisdictions|languages|adapters?|"
        r"feeds?|entries|records)\b"
    r"|"
        r"\bwhat\s+(?:can|do)\s+you\s+(?:do|know|have|see|hear|track)\b"
    r"|"
        r"\bwhat\s+are\s+your\s+(?:capabilit\w*|sources?|tools?|layers?|gaps?)\b"
    r"|"
        r"\b(?:full|complete|overall|system)\s+(?:system\s+)?capabilit\w*"
    r"|"
        r"\bself[-\s]assess(?:ment)?\b"
    r"|"
        r"\bgap\s+analys[ie]s\b"
    r"|"
        r"\bsystem\s+overview\b"
    r"|"
        r"\bintrospect\w*\b"
    r"|"
        r"\b(?:current|live)\s+(?:counts?|inventory|state|metrics)\b"
    r"|"
        r"\bhow\s+do\s+you\s+(?:work|operate|function)\b"
    r"|"
        r"\baria.{0,30}(?:capabilit\w*|sources?|signals?|architecture|tasks?\s+scheduled)\b"
    r")",
    re.IGNORECASE,
)


def detect_self_capability_question(message: str) -> bool:
    """True if the message asks about ARIA's own capabilities/architecture.

    Conservative: misses are OK (Clause 25 + R-F401 guard still apply);
    extra hits are cheap (in-process call to health_perf_ep).

    R-F918 — also fires on self-STATE / availability questions ("why are you
    unavailable", "are you down") so the live /health/perf block is injected
    and the LLM answers from real operational state instead of fabricating a
    diagnostic (the 2026-05-26 incident).

    R-F1046 — wired to brain via _wire_introspect_hit() on every detection.
    """
    if not message or not isinstance(message, str):
        return False
    if _CAPABILITY_KEYWORDS.search(message):
        _wire_introspect_hit(message)
        return True
    try:
        from .self_infra_detector import is_self_state_query
        result = is_self_state_query(message)
        if result:
            _wire_introspect_hit(message)
            return True
    except Exception:
        pass
    # R-F1725: also check for self-analysis requests ("system gap analysis",
    # "audit of your ecosystem", "deep dive of your system"). These were
    # previously missed because is_self_state_query only checks availability
    # questions ("are you down"), and _CAPABILITY_KEYWORDS requires "your X"
    # or "how many" patterns. The is_self_analysis_request function exists
    # in self_infra_detector but was never wired into this guard.
    try:
        from .self_infra_detector import is_self_analysis_request
        if is_self_analysis_request(message):
            _wire_introspect_hit(message)
            return True
    except Exception:
        pass
    return False


def _build_deploy_lines() -> list[str]:
    """R-F1611 — BUILD/DEPLOY proprioception lines for the self_introspect block.

    Before this, the introspect block omitted WHAT CODE IS LIVE and WHICH LOOPS
    RUN, so the LLM confabulated to fill the void (the fabricated "LLM 75-81%"
    on 2026-06-16). Surfaces the real live build_rev + uptime + autonomous-loop
    health so ARIA answers "what version am I / what did I deploy / are my loops
    alive" from truth, not invention. Lazy import of main (fully loaded by the
    time this runs → no circular dependency)."""
    try:
        from aria_service import main as _m
        uptime_s = int(time.time() - getattr(_m, "_BOOT_TIME", time.time()))
        br = getattr(_m, "ARIA_BUILD_REV", "UNKNOWN")
        hh, rem = divmod(uptime_s, 3600)
        out = [
            "",
            "BUILD / DEPLOY (what code is LIVE now — cite for 'what version / what did you deploy'):",
            f"  - live build_rev: {br}",
            f"  - uptime: {hh}h {rem // 60}m (booted {uptime_s}s ago)",
        ]
        resp = getattr(_m, "_BG_RESPAWN", {}) or {}
        tasks = getattr(_m, "_BG_TASKS", set()) or set()
        if resp:
            live = {t.get_name() for t in tasks if not t.done()}
            dead = sorted(set(resp) - live)
            live_n = len(set(resp) & live)
            out.append(
                f"  - autonomous loops: {live_n}/{len(resp)} live"
                + (f"; DEAD: {dead}" if dead else " (all healthy)")
            )
            if dead:
                out.append(
                    "  - NOTE: the DEAD loops above are a REAL problem — say so honestly; "
                    "the self-heal supervisor (R-F1610) attempts to re-spawn them."
                )
        return out
    except Exception as e:  # noqa: BLE001
        return [
            "",
            f"BUILD / DEPLOY: UNAVAILABLE (probe failed: {str(e)[:80]}) — "
            "say so honestly; do NOT invent a version or loop count.",
        ]


async def self_introspect_context_block(message: str) -> str:
    """End-to-end: detect → call health_perf_ep → return context block.

    Returns "" when the message isn't a capability question.
    Returns a formatted [TOOL: self_introspect] block otherwise so the
    LLM can quote live values verbatim instead of inventing them.
    """
    if not detect_self_capability_question(message):
        return ""

    # Lazy import to avoid module-level circularity: routes.aria imports
    # from intel.* at module load.
    try:
        from ..routes.aria import health_perf_ep
        perf = await health_perf_ep()
    except Exception as exc:
        logger.warning("R-F595: health_perf_ep call failed: %s", exc)
        return (
            "\n\n[TOOL: self_introspect — auto-fired by R-F595 — health_perf failed]\n"
            f"Reason: {str(exc)[:120]}\n"
            "Answer from operational knowledge but FLAG the missing instrumentation. "
            "Do NOT invent inventory counts, TTLs, eviction policies, or task counts. "
            "If asked 'how many X', say 'I don't have the live count in this turn'."
        )

    inventory = perf.get("inventory", {}) or {}
    retention = perf.get("retention", {}) or {}
    autonomy = perf.get("autonomy", {}) or {}
    advisories = perf.get("advisories", []) or []

    # R-F961 (2026-05-28) — surface the REAL degraded_reasons + operating_mode +
    # live autonomy state. Live incident: a self-gap-analysis claimed "a subsystem
    # needs attention (DEGRADED)" + "(autonomy fields UNAVAILABLE)" and confabulated
    # a mystery failing subsystem — when degraded_reasons was simply
    # ['mode_supervised'] (a posture, NOT a broken subsystem) and the engine was
    # actually enabled + running. Pull the truth so introspection stops guessing.
    _status_info, _auton_live = {}, {}
    try:
        from ..routes.aria import health_check_ep as _hc
        _h = await _hc()
        _status_info = {
            "operating_mode": _h.get("operating_mode"),
            "status": _h.get("status"),
            "degraded_reasons": _h.get("degraded_reasons") or [],
        }
    except Exception as _he:
        logger.debug("R-F961 health_check_ep failed: %s", _he)
    try:
        from ..routes.aria import autonomous_status_ep as _as
        _a = await _as() or {}
        _eng = _a.get("engine", {}) or {}
        if _eng:  # only when the endpoint actually returned engine state
            _auton_live = {
                "enabled": _eng.get("enabled"),
                "running": _eng.get("running"),
                "paused": (_a.get("safety", {}) or {}).get("engine_paused"),
                "autonomy_level": _eng.get("autonomy_label") or _eng.get("autonomy_level"),
                "fire_count": _eng.get("fire_count"),
                "scheduled_tasks": len(_a.get("tasks") or []),
            }
    except Exception as _ae:
        logger.debug("R-F961 autonomous_status_ep failed: %s", _ae)

    # R-F1435: probe coder status via heartbeat + blackout detector.
    # The coder ticks its heartbeat every 30s (coder_entrypoint._heartbeat_ticker).
    # If the heartbeat exists and is fresh (< 2x interval), the coder is running.
    # This fixes the proprioception bug where self_introspect reported
    # ARIA_CODER_ENABLED=0 (dormant) while the coder was actively fixing gaps.
    _coder_status: dict[str, object] = {}
    try:
        from .self_restart import get_blackout_status as _gbs
        _bs = _gbs()
        _agents = _bs.get("agents", {}) or {}
        _coder_agent = _agents.get("aria_coder") or {}
        if _coder_agent:
            _age = _coder_agent.get("heartbeat_age_s", float("inf"))
            _coder_status = {
                "running": _age < 120,  # heartbeat < 2min = actively ticking
                "heartbeat_age_s": _age,
                "blackout_count": _coder_agent.get("blackout_count", 0),
                "recovery_count": _coder_agent.get("recovery_count", 0),
            }
        else:
            _coder_status = {"running": False, "reason": "no heartbeat registered"}
    except Exception as _ce:
        logger.debug("R-F1435 coder status probe failed: %s", _ce)
        _coder_status = {"running": False, "reason": f"probe failed: {_ce}"}

    lines = [
        "\n\n[TOOL: self_introspect — auto-fired by R-F595 capability detector]",
        "Live data from /api/aria/health/perf. Cite these EXACT numbers verbatim.",
        "Do NOT round, estimate, or replace with prior values from training.",
    ]
    # R-F961 — STATUS section, with the degraded-reason guard so introspection
    # never confabulates a mystery failing subsystem.
    if _status_info:
        _dr = _status_info.get("degraded_reasons") or []
        lines += ["", "STATUS:",
                  f"  - operating_mode: {_status_info.get('operating_mode')}",
                  f"  - status: {_status_info.get('status')}",
                  f"  - degraded_reasons: {_dr}"]
        if _dr == ["mode_supervised"]:
            lines.append("  - NOTE: degraded_reasons is ONLY 'mode_supervised' → NO subsystem is "
                         "failing. You are in SUPERVISED posture (deliberate review-gating), NOT "
                         "broken. Do NOT claim a subsystem needs attention or that the cause is unknown.")
        elif _dr:
            lines.append(f"  - NOTE: the failing subsystem(s) are NAMED here ({_dr}) — cite them; "
                         "do not say the degraded cause is unidentifiable.")

    # R-F1611 — BUILD / DEPLOY proprioception (see _build_deploy_lines).
    lines += _build_deploy_lines()

    lines += ["", "INVENTORY:"]

    for key in ("knowledge_facts", "ledger_signals", "rag_chunks",
                "rag_documents", "rag_facts_indexed"):
        v = inventory.get(key)
        if v is None:
            lines.append(f"  - {key}: UNAVAILABLE (probe failed — say so honestly)")
        else:
            try:
                lines.append(f"  - {key}: {int(v):,}")
            except (TypeError, ValueError):
                lines.append(f"  - {key}: {v}")

    # Autonomy block — R-F961 prefers the LIVE engine state from
    # /api/aria/autonomous/status (enabled/running/paused/level/fires/tasks),
    # because /health/perf's `autonomy` dict often lacks these field names and
    # the old fallback printed "(autonomy fields UNAVAILABLE)" — which the LLM
    # then turned into "SUPERVISED means no autonomous execution". The engine is
    # a separate axis from the operating MODE: it can be enabled+running while
    # the mode is SUPERVISED. Both are reported so introspection can't conflate.
    lines.append("")
    lines.append("AUTONOMY (engine state — independent of operating_mode above):")
    if _auton_live:
        lines.append(f"  - enabled: {_auton_live.get('enabled')}")
        lines.append(f"  - running: {_auton_live.get('running')}")
        lines.append(f"  - paused: {_auton_live.get('paused')}")
        lines.append(f"  - autonomy_level: {_auton_live.get('autonomy_level')}")
        lines.append(f"  - fire_count: {_auton_live.get('fire_count')}")
        lines.append(f"  - scheduled_tasks: {_auton_live.get('scheduled_tasks')}")
        lines.append("  - NOTE: 'enabled+running' means the autonomous loop IS executing tasks. "
                     "A SUPERVISED operating_mode does NOT stop the engine — it gates "
                     "review of certain change types. Do NOT say autonomy is off because of SUPERVISED.")
    else:
        # Fall back to whatever /health/perf surfaced; never fabricate.
        for key in ("scheduled_tasks", "tasks_total", "tasks_enabled",
                    "ticks_24h", "tasks_fired_24h"):
            if key in autonomy:
                lines.append(f"  - {key}: {autonomy.get(key)}")
        if not any(k in autonomy for k in ("scheduled_tasks", "tasks_total",
                                            "tasks_enabled", "ticks_24h",
                                            "tasks_fired_24h")):
            lines.append("  - (autonomy fields UNAVAILABLE — do not fabricate task counts)")

    # R-F1435: coder status — fixes the proprioception bug where
    # self_introspect reported ARIA_CODER_ENABLED=0 (dormant) while the
    # coder was actively fixing gaps. Probed via heartbeat freshness.
    lines.append("")
    lines.append("CODER STATUS (ARIA-Coder — autonomous self-coding engine):")
    if _coder_status.get("running"):
        lines.append(f"  - running: True (heartbeat age {_coder_status.get('heartbeat_age_s', '?'):.0f}s)")
        lines.append(f"  - blackout_count: {_coder_status.get('blackout_count', 0)}")
        lines.append(f"  - recovery_count: {_coder_status.get('recovery_count', 0)}")
        lines.append("  - NOTE: the coder IS actively running — it detects gaps, plans fixes, "
                     "writes code, and stages improvements. Do NOT report it as dormant.")
    else:
        _reason = _coder_status.get("reason", "unknown")
        lines.append(f"  - running: False ({_reason})")
        lines.append("  - NOTE: the coder is NOT running. It starts automatically when "
                     "ARIA_INTERNAL_TOKEN is set and the process boots.")

    # Retention — the explicit "no TTL" anchor so the LLM can never
    # reintroduce an 18-month TTL claim.
    lines.append("")
    lines.append("RETENTION POLICY (these contradict any TTL/eviction claim):")
    for layer in ("knowledge", "ledger", "rag", "mem0"):
        kn = retention.get(layer, {})
        if not kn:
            continue
        ttl = kn.get("ttl_days")
        eviction = kn.get("eviction")
        policy = kn.get("policy", "")
        anchor = kn.get("anchor", "")
        bits = [f"ttl_days={ttl}"]
        if eviction is not None:
            bits.append(f"eviction={eviction}")
        if policy:
            bits.append(f"policy={policy}")
        line = f"  - {layer}: " + "  ".join(bits)
        if anchor:
            line += f"  ({anchor})"
        lines.append(line)

    if advisories:
        lines.append("")
        lines.append("ADVISORIES:")
        for adv in advisories[:6]:
            lines.append(f"  - {adv}")

    lines.append("")
    lines.append(
        "ANSWER POLICY (R-F595 + Clause 25): quote ONLY the values above. "
        "Do NOT state any count, TTL, or policy not in this block. If a "
        "capability isn't covered, say 'I don't have live visibility into "
        "<X> in this turn — calling self_introspect did not surface it'."
    )

    return "\n".join(lines)


# ── R-F1046: Brain wiring ──────────────────────────────────────────────────────

def _wire_introspect_hit(message: str) -> None:
    """Fire-and-forget brain signal when a self-introspection question is detected.
    Writes to brain_hook so ARIA learns which self-capability questions are asked.
    Never raises."""
    try:
        from . import brain_hook as _bh
        import asyncio as _aio
        try:
            _loop = _aio.get_running_loop()
        except RuntimeError:
            _loop = None
        if _loop is not None:
            _t = _loop.create_task(_bh.absorb_silent(
                module="self_introspect_guard",
                summary=f"Self-introspection question detected: {message[:120]}",
                success=True,
                source_id="self_introspect_guard:detect_self_capability_question",
            ))
            _t.add_done_callback(lambda t: t.result() if not t.cancelled() and not t.exception() else None)
    except Exception:
        pass

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="self_introspect_guard",
                     summary="self_introspect_guard module active",
                     source_id="self_introspect_guard:init")
    except Exception:
        pass

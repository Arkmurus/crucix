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

import logging
import re

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
    """
    if not message or not isinstance(message, str):
        return False
    if _CAPABILITY_KEYWORDS.search(message):
        return True
    try:
        from .self_infra_detector import is_self_state_query
        return is_self_state_query(message)
    except Exception:
        return False


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

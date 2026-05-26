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

    lines = [
        "\n\n[TOOL: self_introspect — auto-fired by R-F595 capability detector]",
        "Live data from /api/aria/health/perf. Cite these EXACT numbers verbatim.",
        "Do NOT round, estimate, or replace with prior values from training.",
        "",
        "INVENTORY:",
    ]

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

    # Autonomy block — schema varies across engine versions, so try common
    # field names. Missing fields are NOT fabricated; say UNAVAILABLE.
    lines.append("")
    lines.append("AUTONOMY:")
    for key in ("scheduled_tasks", "tasks_total", "tasks_enabled",
                "ticks_24h", "tasks_fired_24h"):
        if key in autonomy:
            v = autonomy.get(key)
            lines.append(f"  - {key}: {v}")
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

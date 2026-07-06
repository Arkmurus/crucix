"""ARIA Ghost Detection — counterparty due-diligence addendum.

Conditional Tier-D addendum that fires when the user is asking ARIA to
investigate, screen, or vet a counterparty (company, broker, intermediary,
agent, supplier, partner, individual). It injects a structured ghost-entity
detection checklist + an output-format reminder so ARIA produces consistent
shell-company DD instead of generic "search and summarise" outputs.

Distilled from Antonio's six-pillar architecture proposal (2026-04-09 spec)
+ Arkmurus's actual incident history (Omar J Jones IV LinkedIn confabulation
prevention from clause 9 + the Modirum Gespi credibility-padding fabrication
prevention from clause 14). Where the existing constitution clauses tell the
LLM what NOT to do, this addendum tells it what TO DO when the failure-mode
defenses fire — namely, run the ghost checklist and structure the output.

Why a separate module
═════════════════════
- Conditional injection — only fires on counterparty / DD intent so it doesn't
  burn token budget on irrelevant queries.
- Mirrors `pmesii.py` and `negotiation_principles.py` patterns: `is_enabled()`
  + `detect_intent()` + `addendum()` + `block_length()`.
- Behind `ARIA_GHOST_DETECTION_PRINCIPLES` env var (default ON).

Length budget
═════════════
~2500 chars when active. Smaller than analytic_principles (always-on) because
this fires only on a specific intent.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import os
import re

logger = logging.getLogger("aria.ghost_detection")


# ── Intent detector ────────────────────────────────────────────────────────
# Conservative: requires explicit DD/counterparty/screening vocabulary.
# Avoids firing on generic mentions of "company" or "supplier" in normal
# conversation.
_DD_INTENT_RE = re.compile(
    r"\b("
    r"investigate|due[-\s]?diligence|background[-\s]?check|"
    r"counterparty\s+(?:check|screen|review|assessment|risk|profile)|"
    r"screen\s+(?:this|the|that|them|him|her)|"
    r"check\s+(?:out|on)\s+(?:this|the|that)\s+(?:company|firm|broker|intermediary|agent|supplier|partner)|"
    r"vet\s+(?:this|the|that|them|him|her)|"
    r"who\s+(?:are|is)\s+(?:these|this|that)|"
    r"are\s+(?:they|this|that)\s+(?:legit|legitimate|real|genuine|trustworthy|reliable)|"
    r"shell\s+(?:company|entity|firm)|"
    r"ghost\s+(?:company|entity|firm|broker)|"
    r"ubo|ultimate\s+beneficial\s+owner|beneficial\s+owner"
    r")\b",
    re.IGNORECASE,
)


_GHOST_DETECTION_BLOCK = """COUNTERPARTY DUE DILIGENCE — apply when investigating an entity, person, or commercial party

You are now operating in COUNTERPARTY DD MODE. The user has asked you to investigate, screen, or vet a counterparty. Apply the following discipline IN ADDITION to constitution clauses 9 (no profiling without data), 13 (no propaganda elevation), and 14 (no fabricated verifiable facts).

GHOST ENTITY DETECTION CHECKLIST
Apply these 10 indicators to every counterparty. Mark each with [✓ confirmed], [✗ not detected], or [? insufficient data]. An entity matching 4 or more is a PROBABLE GHOST and the recommendation MUST be NO-GO until the gaps are closed.

1. Incorporated within 24 months of first contact
2. No verifiable physical premises (registered address is a virtual office, law firm, or formations company)
3. Directors with no traceable professional history
4. Website created within 12 months of first contact
5. No employees visible on LinkedIn or any professional platform
6. No procurement history in public databases (TED, sam.gov, Contracts Finder, gov.uk)
7. Claims relationships with major defence primes that cannot be independently verified
8. Generic professional email domains (gmail, outlook, free webmail) with no corporate infrastructure
9. Unable to produce verifiable references upon request
10. Multiple unrelated companies share the same registered address or director

ZERO-FOOTPRINT PROTOCOL
A company claiming commercial significance with no verifiable digital presence is HIGH RISK. Quote the exact gap ("no LinkedIn employees", "no press coverage", "no procurement history") rather than generalising.

INTENT DETECTOR — what the user actually wants from this DD
- Pre-engagement screening: prevent Arkmurus committing to a counterparty that turns out to be a ghost
- Sanctions exposure: catch any direct or beneficial-ownership link to OFAC SDN / EU Consolidated / UK OFSI / BIS Entity List / UN SC
- Compliance documentation: produce a defensible audit trail proving the DD was done

DD OUTPUT FORMAT
Structure every counterparty investigation as follows. Skip sections only when the data genuinely doesn't exist (and SAY SO explicitly under "GAPS").

*🔴/🟡/🟢 BOTTOM LINE — <one-sentence GO/NO-GO/INVESTIGATE verdict>*

━━━━━━━━━━━━━━━━━━━━

*🔍 ENTITY IDENTITY* [tag]
- Full legal name (verbatim from registry / website only — DO NOT invent)
- Registered address (verbatim only)
- Company number / VAT / EIN / EORI (verbatim only)
- Year of incorporation
- NACE / SIC codes if available

*👤 BENEFICIAL OWNERSHIP* [tag]
- Ultimate beneficial owner (UBO) — natural person, name + nationality if available
- Corporate structure layers: holding companies, nominees, special purpose vehicles
- Flag any structure where ownership cannot be traced to a natural person
- Flag any incorporation in a secrecy jurisdiction without commercial rationale (BVI, Cayman, Seychelles, Marshall Islands, Panama)

*👻 GHOST DETECTION CHECKLIST* [tag]
List the 10 indicators above with [✓/✗/?] for each. Total ✓ count drives the verdict.

*⚖️ SANCTIONS SCREEN* [tag]
- OFAC SDN: <hit / clean / not checked>
- EU Consolidated: <hit / clean / not checked>
- UK OFSI: <hit / clean / not checked>
- BIS Entity List: <hit / clean / not checked>
- OpenSanctions / OpenCorporates: <hit / clean / not checked>
If a list was not checked, SAY SO — do not fabricate a "clean" result.

*🌐 DIGITAL FOOTPRINT* [tag]
- Website age + content quality
- LinkedIn employee count + key roles
- Press coverage (last 24 months)
- Procurement history in public databases

*📋 GAPS — what could not be established*
List explicitly. Gaps are findings.

*✅ RECOMMENDED ACTION*
Numbered list. Verdict (GO / NO-GO / INVESTIGATE) + specific next-step queries to close the gaps.

DO NOTHING is always a valid option for a counterparty engagement decision. If the gaps are too large to safely engage, the recommendation is NO-GO.

NEVER invent a company number, NACE code, address, director name, or sanctions hit just to "complete" the report. Every verifiable fact must come from a tool result you can quote verbatim — see constitution clause 14."""


# ── Public API ──────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_GHOST_DETECTION_PRINCIPLES=0 to disable."""
    val = os.getenv("ARIA_GHOST_DETECTION_PRINCIPLES", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def detect_dd_intent(message: str) -> bool:
    """Return True if the message looks like a counterparty due-diligence query.

    Conservative — only fires on explicit DD vocabulary or screening
    requests. Generic mentions of "this company" or "the supplier" do
    not trigger.
    """
    if not message or not isinstance(message, str):
        return False
    if not is_enabled():
        return False
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="ghost_detection_principles",
        summary="Detect Dd Intent",
        source_id="ghost_detection_principles:R-F996",
    )

    return bool(_DD_INTENT_RE.search(message))


def addendum() -> str:
    """Return the ghost-detection addendum text, or empty string if disabled."""
    if not is_enabled():
        return ""
    return _GHOST_DETECTION_BLOCK


def block_length() -> int:
    """For monitoring — how many chars the addendum adds when active."""
    return len(_GHOST_DETECTION_BLOCK)

# R-F2119 §21a — wire failure handler for ghost_detection_principles
try:
    wire_failure(module="ghost_detection_principles", detail="module shutdown",
                gap_type="engine_failure", source="ghost_detection_principles:shutdown")
except Exception:
    pass

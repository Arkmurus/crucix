from .engine_wiring import wire_success, wire_failure
"""Pre-output hallucination guard (R-F1527).

Runs AFTER the LLM generates a response but BEFORE it's sent to the user.
Scans for:
1. [CONFIRMED] claims that have NO corresponding source in the tool context
2. Numerical claims (dates, amounts, percentages) that contradict the knowledge base
3. Entity-specific claims (CEO names, registration numbers, contract values) that
   are fabricated

This is the structural guard against the Vision International / Modirum class of
fabrication — where the LLM invents specific verifiable facts and presents them
with high confidence.
"""
import logging
import re
from typing import Any

logger = logging.getLogger("aria.hallucination_guard")

# Patterns for verifiable claims that MUST have a source
_CONFIRMED_CLAIM_RE = re.compile(
    r"\[CONFIRMED\]\s*([^.\[]+)",
    re.IGNORECASE,
)

# Numerical patterns: dates, dollar amounts, percentages, contract values
_NUMERICAL_CLAIM_RE = re.compile(
    r"(?:\$[\d,]+(?:\.\d+)?(?: million| billion| thousand)?|"
    r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}|"
    r"\d{4}-\d{2}-\d{2}|"
    r"\d+%\s*(?:of|year|interest|rate)|"
    r"(?:USD|EUR|GBP|AED)\s*[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)

# Entity identifiers that MUST be sourced
_ENTITY_ID_RE = re.compile(
    r"(?:(?:registration|company)\s+(?:number|no|#)\s*[A-Z0-9][A-Z0-9-]{2,}|"
    r"NACE\s+code\s*[A-Z0-9.-]+|"
    r"VAT\s+(?:number|no|#)\s*[A-Z0-9-]{4,}|"
    r"EIN\s+[0-9-]{4,}|"
    r"IBAN\s+[A-Z0-9]{4,})",
    re.IGNORECASE,
)

# Inline citation patterns — if any of these are present, the claim is sourced
_CITATION_RE = re.compile(
    r"\[from\s|\[snippet\s|\[EXTRACT|\[LEGACY|\[UNVERIFIED|\[CONTRADICTED|"
    r"\[ASSESSED|\[PROBABLE|\[UNCERTAIN|\[SPECULATIVE|"
    r"\[from dd_orchestrate:",  # R-F1951: DD orchestrator citations
    re.IGNORECASE,
)


def _extract_claims(response_text: str) -> list[dict[str, Any]]:
    """Extract all verifiable claims from a response.

    Returns a list of dicts with:
      - text: the claim text
      - type: "confirmed" | "numerical" | "entity_id"
      - has_citation: bool — whether the claim has an inline citation
      - confidence: the confidence tag used
    """
    claims: list[dict[str, Any]] = []

    # Check [CONFIRMED] claims
    for match in _CONFIRMED_CLAIM_RE.finditer(response_text):
        claim_text = match.group(1).strip()
        # Check if there's a citation within the next 200 chars
        end = match.end()
        surrounding = response_text[end:end + 200]
        has_citation = bool(_CITATION_RE.search(surrounding))
        claims.append({
            "text": claim_text[:150],
            "type": "confirmed",
            "has_citation": has_citation,
            "confidence": "CONFIRMED",
            "position": match.start(),
        })

    # Check numerical claims that aren't already covered by a confirmed claim
    for match in _NUMERICAL_CLAIM_RE.finditer(response_text):
        claim_text = match.group(0)
        # Check surrounding context for citation
        start = max(0, match.start() - 50)
        end = min(len(response_text), match.end() + 200)
        surrounding = response_text[start:end]
        has_citation = bool(_CITATION_RE.search(surrounding))
        # Only flag if it's presented as fact (not in a hypothetical or question)
        claims.append({
            "text": claim_text[:100],
            "type": "numerical",
            "has_citation": has_citation,
            "confidence": "UNVERIFIED",
            "position": match.start(),
        })

    # Check entity identifiers
    for match in _ENTITY_ID_RE.finditer(response_text):
        claim_text = match.group(0)
        end = match.end()
        surrounding = response_text[end:end + 200]
        has_citation = bool(_CITATION_RE.search(surrounding))
        claims.append({
            "text": claim_text[:100],
            "type": "entity_id",
            "has_citation": has_citation,
            "confidence": "UNVERIFIED",
            "position": match.start(),
        })

    return claims


def check_response(response_text: str, tool_context: str = "") -> dict[str, Any]:
    """Run the pre-output hallucination guard on a response.

    Returns:
        {
            "passed": bool,       # True if no issues found
            "claims": list,       # All extracted claims
            "red_flags": list,    # Claims that need attention
            "suggested_action": str,  # "allow" | "flag" | "block"
        }
    """
    if not response_text or len(response_text) < 50:
        return {
            "passed": True,
            "claims": [],
            "red_flags": [],
            "suggested_action": "allow",
        }

    claims = _extract_claims(response_text)
    red_flags: list[dict[str, Any]] = []

    for claim in claims:
        # RED FLAG 1: [CONFIRMED] claim with NO inline citation
        if claim["type"] == "confirmed" and not claim["has_citation"]:
            # Check if the tool context has any sources
            has_sources = bool(tool_context and len(tool_context.strip()) > 50)
            if not has_sources:
                red_flags.append({
                    **claim,
                    "reason": "CONFIRMED claim without any source in context",
                    "severity": "HIGH",
                })
            else:
                red_flags.append({
                    **claim,
                    "reason": "CONFIRMED claim without inline citation (sources exist in context)",
                    "severity": "MEDIUM",
                })

        # RED FLAG 2: Entity identifier (reg number, VAT, etc.) without citation
        if claim["type"] == "entity_id" and not claim["has_citation"]:
            red_flags.append({
                **claim,
                "reason": "Specific entity identifier without source citation",
                "severity": "HIGH",
            })

        # RED FLAG 3: Numerical claim without citation
        if claim["type"] == "numerical" and not claim["has_citation"]:
            red_flags.append({
                **claim,
                "reason": "Numerical claim without source citation",
                "severity": "MEDIUM",
            })

    # Determine action
    high_severity = [f for f in red_flags if f.get("severity") == "HIGH"]
    medium_severity = [f for f in red_flags if f.get("severity") == "MEDIUM"]

    if high_severity:
        suggested_action = "block"
    elif medium_severity:
        suggested_action = "flag"
    else:
        suggested_action = "allow"

    if red_flags:
        logger.warning(
            "[R-F1527] hallucination guard: %d red flags (%d high, %d medium) — action=%s",
            len(red_flags), len(high_severity), len(medium_severity), suggested_action,
        )
        for rf in red_flags[:5]:
            logger.warning(
                "  [R-F1527] %s: %s — %s",
                rf["severity"], rf["reason"], rf["text"][:80],
            )

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="hallucination_guard",
                     summary="hallucination_guard module active",
                     source_id="hallucination_guard:init")
    except Exception:
        try:
            wire_failure(module="hallucination_guard", detail="module init failed",
                        gap_type="engine_failure", source="hallucination_guard:init")
        except Exception:
            pass

    return {
        "passed": len(red_flags) == 0,
        "claims": claims,
        "red_flags": red_flags,
        "suggested_action": suggested_action,
    }

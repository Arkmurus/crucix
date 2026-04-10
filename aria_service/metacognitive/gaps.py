"""ARIA Gap Detection Engine — Layer 2 of the metacognitive stack.

When ARIA reads any new document — an OSINT paper, a military doctrine
update, a compliance regulation — she compares it against her existing
capability profile and identifies:

  1. What she did not know
  2. What she was wrong about
  3. What techniques she should add to her methodology
  4. What this changes about her confidence calibration

This is the discipline of a serious student: not "what does this say?"
but "what does this reveal about what I did not know?"

Gap records persist to Redis and feed into the weekly consciousness
review and the monthly gap-closure sprint.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..intel import redis_store as rs
from . import domains as dom

if TYPE_CHECKING:
    from ..llm.provider import LLMProvider

logger = logging.getLogger("aria.metacognitive.gaps")

# Redis keys
_GAPS_LIST = "crucix:metacog:gaps:detected"
_CORRECTIONS_LIST = "crucix:metacog:gaps:corrections"
_METHODOLOGY_LIST = "crucix:metacog:gaps:methodology"
_DOCUMENTS_READ_LIST = "crucix:metacog:gaps:documents_read"
_MAX_STORED = 500


_GAP_DETECTION_SYSTEM = (
    "You are ARIA's gap detection subsystem. You read new material and evaluate "
    "yourself against it — identifying what gaps this document reveals in your "
    "existing knowledge and capability. This is the discipline of a serious "
    "student: not 'what does this say?' but 'what does this reveal about what "
    "I did not know?' Respond ONLY with valid JSON. No preamble."
)

_GAP_DETECTION_USER = """NEW DOCUMENT / MATERIAL:
{document_content}

DOCUMENT TYPE: {doc_type}
DOMAIN: {domain}

YOUR EXISTING CAPABILITY PROFILE IN THIS DOMAIN:
{existing_capability}

Perform a rigorous self-evaluation:

1. KNOWLEDGE GAP ANALYSIS — What in this document did you not know or had incorrectly? Be specific.
2. METHODOLOGY GAP ANALYSIS — What techniques or approaches should you add?
3. CORRECTION ANALYSIS — What do you need to update in existing knowledge?
4. CONFIDENCE RECALIBRATION — Where are you now less/more confident?
5. INTEGRATION PLAN — Which skill pillars (Investigator/Researcher/Analyst/Reasoner/Reader/Writer) should be updated?
6. PRIORITY — Rate: TRANSFORMATIVE / HIGH / MEDIUM / LOW / NEGLIGIBLE

JSON format:
{{"knowledge_gaps_identified":[{{"gap":"specific thing","domain":"","sub_domain":"","severity":"HIGH/MEDIUM/LOW","corrective_action":"what to do"}}],"methodology_gaps_identified":[{{"gap":"technique to add","applicable_to_pillar":"","implementation":"how"}}],"corrections_required":[{{"previous_belief":"was","correction":"is now","domain":""}}],"confidence_adjustments":[{{"sub_domain":"","direction":"INCREASE/DECREASE","reason":""}}],"pillar_updates_required":{{"Investigator":"","Researcher":"","Analyst":"","Reasoner":"","Reader":"","Writer":""}},"document_contribution":"TRANSFORMATIVE/HIGH/MEDIUM/LOW/NEGLIGIBLE","priority_learning_items":["most important item"]}}"""


async def detect_gaps_from_document(
    document_content: str,
    doc_type: str,
    domain: str,
    llm: "LLMProvider | None",
    existing_capability: str = "",
) -> dict:
    """ARIA reads a document and identifies capability gaps.

    Returns: Gap analysis dict with knowledge gaps, methodology gaps,
    corrections, confidence adjustments, and priority items.
    """
    t0 = time.time()
    result = {
        "ok": False, "skipped": False, "skipped_reason": "",
        "gaps_count": 0, "corrections_count": 0, "duration_ms": 0,
    }

    if not llm or not getattr(llm, "is_configured", False):
        result["skipped"] = True
        result["skipped_reason"] = "no_llm"
        return result

    if not document_content or len(document_content.strip()) < 100:
        result["skipped"] = True
        result["skipped_reason"] = "document_too_short"
        return result

    # Build capability context from domain registry
    if not existing_capability:
        d = dom.get_domain(domain)
        if d:
            existing_capability = (
                f"Domain: {d['description']}\n"
                f"Sub-domains: {', '.join(d['sub_domains'])}\n"
                f"Reference standards: {', '.join(d['reference_standards'])}"
            )
        else:
            existing_capability = "Standard ARIA v3.0 capability profile"

    prompt = _GAP_DETECTION_USER.format(
        document_content=document_content[:8000],
        doc_type=doc_type,
        domain=domain,
        existing_capability=existing_capability,
    )

    try:
        from ..intel import cost_tracker
        with cost_tracker.feature("metacognitive"):
            llm_result = await llm.complete(
                _GAP_DETECTION_SYSTEM,
                prompt,
                max_tokens=2500,
                timeout=45.0,
            )
        raw = (getattr(llm_result, "text", "") or "").strip()
    except Exception as e:
        logger.debug("Gap detection LLM call failed: %s", e)
        result["skipped"] = True
        result["skipped_reason"] = f"llm_error: {str(e)[:80]}"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    clean = raw.replace("```json", "").replace("```", "").strip()
    try:
        analysis = json.loads(clean)
    except json.JSONDecodeError:
        logger.warning("Gap detection JSON parse failed: %s", raw[:200])
        result["skipped"] = True
        result["skipped_reason"] = "json_parse_failed"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    ts = datetime.now(timezone.utc).isoformat()
    analysis["analysed_at"] = ts
    analysis["doc_type"] = doc_type
    analysis["domain"] = domain

    # Persist gaps, corrections, methodology updates separately for
    # efficient retrieval by the consciousness engine
    knowledge_gaps = analysis.get("knowledge_gaps_identified", [])
    corrections = analysis.get("corrections_required", [])
    methodology_gaps = analysis.get("methodology_gaps_identified", [])

    for gap in knowledge_gaps:
        gap["detected_at"] = ts
        gap["source_doc_type"] = doc_type
        await rs.lpush(_GAPS_LIST, json.dumps(gap))

    for corr in corrections:
        corr["detected_at"] = ts
        await rs.lpush(_CORRECTIONS_LIST, json.dumps(corr))

    for meth in methodology_gaps:
        meth["detected_at"] = ts
        await rs.lpush(_METHODOLOGY_LIST, json.dumps(meth))

    # Trim all lists
    await rs.ltrim(_GAPS_LIST, 0, _MAX_STORED - 1)
    await rs.ltrim(_CORRECTIONS_LIST, 0, _MAX_STORED - 1)
    await rs.ltrim(_METHODOLOGY_LIST, 0, _MAX_STORED - 1)

    # Track documents read for the consciousness report
    doc_record = {
        "doc_type": doc_type,
        "domain": domain,
        "contribution": analysis.get("document_contribution", "UNKNOWN"),
        "gaps_found": len(knowledge_gaps),
        "corrections_found": len(corrections),
        "read_at": ts,
    }
    await rs.lpush(_DOCUMENTS_READ_LIST, json.dumps(doc_record))
    await rs.ltrim(_DOCUMENTS_READ_LIST, 0, 200)

    result["ok"] = True
    result["gaps_count"] = len(knowledge_gaps)
    result["corrections_count"] = len(corrections)
    result["methodology_count"] = len(methodology_gaps)
    result["contribution"] = analysis.get("document_contribution", "UNKNOWN")
    result["analysis"] = analysis
    result["duration_ms"] = int((time.time() - t0) * 1000)
    return result


async def get_recent_gaps(limit: int = 30) -> list[dict]:
    raw = await rs.lrange(_GAPS_LIST, 0, limit - 1)
    return [json.loads(r) for r in raw if r]


async def get_recent_corrections(limit: int = 20) -> list[dict]:
    raw = await rs.lrange(_CORRECTIONS_LIST, 0, limit - 1)
    return [json.loads(r) for r in raw if r]


async def get_recent_methodology_updates(limit: int = 20) -> list[dict]:
    raw = await rs.lrange(_METHODOLOGY_LIST, 0, limit - 1)
    return [json.loads(r) for r in raw if r]


async def get_documents_read(limit: int = 50) -> list[dict]:
    raw = await rs.lrange(_DOCUMENTS_READ_LIST, 0, limit - 1)
    return [json.loads(r) for r in raw if r]


async def get_high_severity_gaps(limit: int = 10) -> list[dict]:
    """Return only HIGH severity gaps — used by the monthly gap-closure sprint."""
    all_gaps = await get_recent_gaps(limit=200)
    high = [g for g in all_gaps if g.get("severity") == "HIGH"]
    return high[:limit]

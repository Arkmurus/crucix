"""ARIA branded-report builder.

from .engine_wiring import wire_success

Generates Arkmurus-formatted compliance / due-diligence / market reports by
combining:

  1. **Investigation** — same flow as /investigate (the existing researcher
     module). Pulls structured intel about the subject entity from web +
     internal sources.
  2. **Tier D template retrieval** — pulls Antonio's existing report
     templates out of the RAG store via tier filter, so the LLM has the
     real Arkmurus skeleton to fill in.
  3. **Template fill** — single LLM call that takes the investigation
     output + the template + the requested report type and produces a
     client-ready draft.
  4. **Confidence + assumption flagging** — leverages the same
     [CONFIRMED]/[PROBABLE]/[ASSESSED] discipline used elsewhere; the
     confidence-footer module then surfaces this to the WhatsApp reply.

If no Tier D template chunks are found in the RAG store, the builder falls
back to a built-in skeleton and tells the caller plainly. This makes the
command usable from day one — Antonio can /report on something before he's
ingested any templates, then re-run after ingesting and get a much better
output.

Independent of any deployed chat path. Building a report is a separate
endpoint (/api/aria/report) and slash command (/report). The chat handler
itself is unmodified.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aria.report_builder")

# ── Report types ────────────────────────────────────────────────────────────
# Each entry holds:
#   description: shown in /report help and as part of the LLM prompt
#   focus:       what the investigation should prioritise
#   skeleton:    fallback template skeleton when no Tier D template exists
REPORT_TYPES: dict[str, dict[str, Any]] = {
    "dd": {
        "description": "Due diligence report on a counterparty (person or company).",
        "focus": (
            "ownership structure, beneficial owners, sanctions / PEP exposure, "
            "litigation history, financial health, regulatory standing, "
            "reputational red flags, operational footprint"
        ),
        "skeleton": """ARKMURUS DUE DILIGENCE — DRAFT

Subject: {subject}
Prepared: {today}
Classification: INTERNAL · CLIENT-EYES-ONLY

1. EXECUTIVE SUMMARY
   - Net assessment (1-2 sentences) with confidence tag
   - Headline risks (3 bullets max)
   - Recommendation: PROCEED / PROCEED WITH CONDITIONS / DO NOT PROCEED

2. SUBJECT IDENTIFICATION
   - Legal name(s) and trading names
   - Registration number / jurisdiction
   - Registered address
   - Date of incorporation / DOB
   - Sources verified: [...]

3. OWNERSHIP & CONTROL
   - Direct shareholders
   - Ultimate beneficial owners (UBOs)
   - Group structure
   - PEP / sanctions screening of controllers

4. REGULATORY & SANCTIONS
   - OFAC / OFSI / EU / UN list checks
   - Adverse media review
   - Litigation history
   - Regulatory enforcement actions

5. COMMERCIAL PROFILE
   - Activities and sectors
   - Key customers / counterparties (where known)
   - Financial indicators
   - Geographic footprint

6. RED FLAGS & GAPS
   - Confirmed concerns (with confidence tags)
   - Information gaps Antonio should resolve before proceeding
   - Recommended verification steps

7. RECOMMENDATION
   - Final risk rating
   - Conditions for proceeding (if any)
   - Monitoring requirements
""",
    },
    "compliance": {
        "description": "Compliance assessment for an entity / transaction (export controls + sanctions + AML).",
        "focus": (
            "export control jurisdiction (UK SITCL / US ITAR-EAR / EU dual-use / "
            "Wassenaar), sanctions exposure (OFAC / OFSI / UN), AML risk, end-use "
            "and end-user concerns"
        ),
        "skeleton": """ARKMURUS COMPLIANCE ASSESSMENT — DRAFT

Subject: {subject}
Prepared: {today}
Classification: INTERNAL

1. SUMMARY
   - Compliance verdict: PERMITTED / PERMITTED WITH CONDITIONS / NOT PERMITTED / INSUFFICIENT INFO
   - Highest-risk dimension
   - Confidence tag

2. JURISDICTION ANALYSIS
   - UK SITCL applicability
   - US ITAR / EAR (re-export risk)
   - EU dual-use Annex I
   - Wassenaar / MTCR / NSG / Australia Group categories
   - Local jurisdiction (subject's country)

3. SANCTIONS EXPOSURE
   - OFAC SDN / sectoral
   - OFSI consolidated list
   - EU restrictive measures
   - UN sanctions
   - Adverse media

4. END-USE / END-USER
   - Stated end-use
   - End-user diligence required
   - Diversion risk indicators

5. AML RISK
   - Source of funds
   - Beneficial ownership transparency
   - High-risk jurisdiction exposure

6. RECOMMENDATION
   - Conditions for proceeding
   - Required licences / approvals
   - Documentation needed from counterparty
""",
    },
    "market": {
        "description": "Market entry / opportunity report for a country or sector.",
        "focus": (
            "addressable market size, key buyers, competitive landscape, regulatory barriers, "
            "relationship requirements, Arkmurus tier (incumbent / established / developing / cold), "
            "win conditions"
        ),
        "skeleton": """ARKMURUS MARKET REPORT — DRAFT

Subject: {subject}
Prepared: {today}
Classification: INTERNAL

1. EXECUTIVE SUMMARY
   - Opportunity verdict + Arkmurus tier
   - Addressable spend (estimate + confidence)
   - Recommended posture

2. MARKET STRUCTURE
   - Key buyers / decision-makers
   - Procurement process
   - Typical deal size
   - Competitive landscape

3. REGULATORY ENVIRONMENT
   - Local procurement law
   - Export control implications
   - Compliance constraints

4. ARKMURUS POSITIONING
   - Relationship tier
   - Existing contacts / capital
   - Required relationship investment
   - Realistic win probability

5. ENTRY STRATEGY
   - Recommended sequence
   - Key meetings to secure
   - Risks to monitor

6. NEXT STEPS
   - 30-day actions
   - 90-day milestones
""",
    },
}


def list_report_types() -> str:
    """Human-readable list of supported report types for help text."""
    lines = ["Supported report types:"]
    for k, v in REPORT_TYPES.items():
        lines.append(f"  /report {k} <subject>  —  {v['description']}")
    return "\n".join(lines)


async def _retrieve_tier_d_template(report_type: str, subject: str) -> tuple[str, int]:
    """Pull Tier D template chunks from the RAG store for this report type.

    Returns (concatenated_template_text, n_chunks). If nothing is found,
    returns ("", 0) and the caller should fall back to the built-in skeleton.
    """
    try:
        from . import rag_store
        # Search the corpus for template-shaped content. We over-fetch and
        # post-filter for tier='D' since the deployed search() doesn't take
        # a tier parameter (yet).
        query = f"Arkmurus {report_type} report template skeleton sections headings"
        results = await rag_store.search(
            query,
            top_k=20,
            source_type="corpus",
            include_facts=False,
        )
    except Exception as e:
        logger.debug("Tier D template retrieval failed: %s", e)
        return "", 0

    tier_d_chunks: list[str] = []
    for r in results or []:
        # The corpus_ingest module stores tier in the chunk metadata. The
        # search() return shape doesn't expose arbitrary metadata, only the
        # whitelisted fields. We work around by checking the source string
        # which we know is shaped 'corpus:<tier>:<class>:<filename>'.
        source = (r.get("source") or "")
        if ":D:" in source or source.startswith("corpus:D:"):
            tier_d_chunks.append(r.get("text", ""))

    if not tier_d_chunks:
        return "", 0
    # Cap to 8000 chars of template context — more than that and we crowd
    # out the investigation findings in the LLM prompt.
    joined = "\n\n---\n\n".join(tier_d_chunks)[:8000]
    return joined, len(tier_d_chunks)


async def build_report(
    llm: Any,
    *,
    report_type: str,
    subject: str,
    extra_context: str = "",
) -> dict:
    """Build a branded report. Returns a dict with the draft + provenance.

    Result shape:
        {
            "report_type":          str,
            "subject":              str,
            "draft":                str,                # the report markdown
            "template_source":      "tier_d" | "fallback_skeleton",
            "template_chunks_used": int,
            "investigation_facts":  int,
            "error":                str | None,
        }
    """
    report_type = (report_type or "").strip().lower()
    subject = (subject or "").strip()
    if report_type not in REPORT_TYPES:
        return {
            "error": f"unknown report_type {report_type!r}",
            "supported": list(REPORT_TYPES.keys()),
        }
    if not subject:
        return {"error": "subject required"}
    if not llm:
        return {"error": "LLM not configured"}

    spec = REPORT_TYPES[report_type]

    # ── 1. Investigation ──────────────────────────────────────────────
    # We use the existing investigate() function from researcher.py — same
    # flow /investigate uses, so the data quality is identical.
    investigation_text = ""
    investigation_facts = 0
    try:
        from .researcher import investigate
        inv = await investigate(llm, subject, depth="thorough")
        if isinstance(inv, dict):
            investigation_text = (
                inv.get("report")
                or inv.get("summary")
                or inv.get("analysis")
                or ""
            )
            investigation_facts = int(inv.get("facts_learned", 0) or 0)
    except Exception as e:
        logger.warning("report_builder: investigation failed for %s: %s", subject, e)
        investigation_text = ""

    # ── 2. Template retrieval ─────────────────────────────────────────
    template_text, n_template_chunks = await _retrieve_tier_d_template(report_type, subject)
    template_source = "tier_d" if template_text else "fallback_skeleton"
    if not template_text:
        from datetime import date
        template_text = spec["skeleton"].format(
            subject=subject,
            today=date.today().isoformat(),
        )

    # ── 3. LLM template fill ──────────────────────────────────────────
    system_prompt = (
        "You are ARIA, the Arkmurus Research Intelligence Agent, drafting a "
        f"{spec['description']} The output must be a complete, client-ready "
        "draft formatted in clean markdown. Discipline:\n"
        "  - Every material claim carries [CONFIRMED] / [PROBABLE] / [ASSESSED] / "
        "[UNCERTAIN] / [SPECULATIVE].\n"
        "  - Where the investigation produced no data for a section, say so plainly "
        "with [UNCERTAIN] and list what the team needs to find.\n"
        "  - Never invent sources, names, or numbers.\n"
        "  - Preserve the section structure of the template exactly.\n"
        "  - Focus areas for this report: " + spec["focus"] + ".\n"
        "  - End the draft with an 'INFORMATION GAPS' bullet list — explicit gaps "
        "the team must close before this draft can become final."
    )

    user_prompt = (
        f"SUBJECT: {subject}\n"
        f"REPORT TYPE: {report_type.upper()}\n\n"
        f"=== TEMPLATE ({template_source}) ===\n"
        f"{template_text}\n\n"
        f"=== INVESTIGATION FINDINGS ===\n"
        f"{investigation_text or '(investigation returned no findings — flag this as a gap)'}\n\n"
    )
    if extra_context:
        user_prompt += f"=== ADDITIONAL CONTEXT ===\n{extra_context}\n\n"
    user_prompt += (
        "=== INSTRUCTIONS ===\n"
        "Fill the template using the investigation findings. Follow the discipline rules. "
        "Output the complete draft in markdown."
    )

    try:
        draft = await llm.complete(system_prompt, user_prompt, max_tokens=4000, timeout=180.0)
    except Exception as e:
        logger.warning("report_builder: LLM completion failed: %s", e)
        return {
            "error": f"LLM completion failed: {e}",
            "report_type": report_type,
            "subject": subject,
            "template_source": template_source,
            "template_chunks_used": n_template_chunks,
            "investigation_facts": investigation_facts,
        }


    # R-F996 — wire to brain
    wire_success(
        module="report_builder",
        summary="Report building",
        source_id="report_builder:R-F996",
    )
    return {
        "report_type": report_type,
        "subject": subject,
        "draft": draft or "",
        "template_source": template_source,
        "template_chunks_used": n_template_chunks,
        "investigation_facts": investigation_facts,
        "error": None,
    }

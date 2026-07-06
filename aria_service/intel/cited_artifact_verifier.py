"""Cited-artifact verifier.

Extracted from the SERBAN letter learning 2026-04-17: when a
counterparty cites a specific document ID (e.g. "NCNDA-KOR-MCT-0126")
to create a false sense of an ongoing structured process, ARIA should
check whether that document actually exists in our records before
accepting the claim. Reinforces deception detection with a verifiable
lookup step.

Patterns detected:
    NCNDA-*, NDA-*, LOI-*, MOU-*, ICPO-*, RWA-*, BCL-*, FCO-*,
    SCO-*, AWB-*, BL-*, PO-*, INV-*, RFQ-*, RFP-*, EUC-*, BCL-*

Lookup targets:
    - counterparty_claim_ledger.get_claims(counterparty)
    - mem0 personal notebook (if available)
    - audit_log (if available)

If none of the lookups succeed, the citation is flagged as UNVERIFIED.
This is different from "false" — it means we have no record of the
artifact. Operators decide whether absence is significant.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("aria.intel.cited_artifact_verifier")


# ═══════════════════════════════════════════════════════════════════════
# Document ID pattern catalogue
# ═══════════════════════════════════════════════════════════════════════
#
# Each pattern: a compiled regex that matches a plausible defence /
# brokering document identifier. Patterns are conservative — they
# require a clear prefix-number shape so they do not false-positive on
# ordinary prose.

_DOC_ID_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # NCNDA-KOR-MCT-0126 — multi-part, underscores or dashes
    ("NCNDA", re.compile(r"\bNCNDA[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    # NDA-1234, NDA-2026-001
    ("NDA",   re.compile(r"\bNDA[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("LOI",   re.compile(r"\bLOI[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("MOU",   re.compile(r"\bMOU[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("ICPO",  re.compile(r"\bICPO[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("RWA",   re.compile(r"\bRWA[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("BCL",   re.compile(r"\bBCL[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("FCO",   re.compile(r"\bFCO[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("SCO",   re.compile(r"\bSCO[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("EUC",   re.compile(r"\bEUC[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("RFQ",   re.compile(r"\bRFQ[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("RFP",   re.compile(r"\bRFP[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("PO",    re.compile(r"\bPO[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("INV",   re.compile(r"\bINV[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("AWB",   re.compile(r"\bAWB[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
    ("BL",    re.compile(r"\bBL[-_][A-Z0-9]+(?:[-_][A-Z0-9]+)*\b", re.IGNORECASE)),
)


@dataclass
class CitedArtifact:
    doc_type:   str   # e.g. "NCNDA", "LOI"
    doc_id:     str   # the full match, normalised upper-case
    verified:   bool
    source:     str   # "claim_ledger", "mem0", "audit_log", or "" when unverified
    note:       str = ""


def extract_cited_ids(text: str) -> list[CitedArtifact]:
    """Extract every plausible document ID from a block of text.

    Returns a de-duplicated list of CitedArtifact (verified=False,
    source="") — callers pass these to verify_against_records() to
    attempt a lookup.
    """
    if not text or not text.strip():
        return []

    seen: set[str] = set()
    artifacts: list[CitedArtifact] = []
    for doc_type, pattern in _DOC_ID_PATTERNS:
        for m in pattern.findall(text):
            norm = m.upper().strip()
            key = f"{doc_type}::{norm}"
            if key in seen:
                continue
            seen.add(key)
            artifacts.append(CitedArtifact(
                doc_type=doc_type,
                doc_id=norm,
                verified=False,
                source="",
            ))
    return artifacts


def verify_against_records(
    artifacts: list[CitedArtifact],
    counterparty: str | None = None,
    deal_id: str | None = None,
) -> list[CitedArtifact]:
    """Attempt to verify each cited artifact against ARIA's records.

    Currently checks:
      - counterparty_claim_ledger: scans stored claim text for the ID.
      - mem0 (if configured): best-effort search for the ID string.

    Each artifact in the returned list carries verified=True if any
    record hit was found, with `source` + `note` filled in.

    This function is synchronous and tolerant of missing backends —
    failures degrade to verified=False, source="" (UNVERIFIED).
    """
    if not artifacts:
        return artifacts

    # ── Pass 1: claim_ledger scan ──
    try:
        from . import counterparty_claim_ledger
        if counterparty:
            ledger = counterparty_claim_ledger.ARIACounterpartyClaimLedger()
            claims = ledger.get_claims(counterparty=counterparty, deal_id=deal_id or "")
            claim_blob = " ".join(
                (c.claim_summary or "") + " " + (getattr(c, "raw_quote", "") or "")
                for c in claims
            ).upper()
            for art in artifacts:
                if art.verified:
                    continue
                if art.doc_id in claim_blob:
                    art.verified = True
                    art.source = "claim_ledger"
                    art.note = f"Cited ID found in prior claim ledger for {counterparty}."
    except Exception as exc:
        logger.debug("Claim-ledger verification skipped: %s", exc)

    # ── Pass 2: mem0 scan (best effort) ──
    try:
        from . import mem0_notebook  # optional module, may not exist
        if hasattr(mem0_notebook, "search"):
            for art in artifacts:
                if art.verified:
                    continue
                hits = mem0_notebook.search(art.doc_id)  # type: ignore[attr-defined]
                if hits:
                    art.verified = True
                    art.source = "mem0"
                    art.note = f"Cited ID found in mem0 notebook ({len(hits)} hit(s))."
    except Exception as exc:
        logger.debug("mem0 verification skipped: %s", exc)

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="cited_artifact_verifier",
        summary="Verify Against Records",
        source_id="cited_artifact_verifier:R-F996",
    )

    return artifacts


def verify_cited_artifacts(
    text: str,
    counterparty: str | None = None,
    deal_id: str | None = None,
) -> dict[str, Any]:
    """End-to-end: extract IDs from `text` and verify each.

    Returns:
        {
          "cited":      [ {doc_type, doc_id, verified, source, note}, ... ],
          "summary":    human-readable line suitable for a finding,
          "unverified": count of IDs for which no record was found,
          "verified":   count of IDs with a record hit,
        }

    Designed to be consumed by deception_detection and by the DD
    orchestrator. When unverified > 0, callers should add an AMBER
    finding saying "claimed reference X was not found in our records".
    """
    artifacts = extract_cited_ids(text)
    artifacts = verify_against_records(artifacts, counterparty=counterparty, deal_id=deal_id)

    verified = [a for a in artifacts if a.verified]
    unverified = [a for a in artifacts if not a.verified]

    if not artifacts:
        summary = "No document IDs cited in text — nothing to verify."
    elif not unverified:
        summary = (
            f"All {len(artifacts)} cited document ID(s) verified against "
            f"ARIA records: {', '.join(a.doc_id for a in verified)}."
        )
    elif not verified:
        summary = (
            f"None of the {len(artifacts)} cited document ID(s) were found "
            f"in ARIA records: {', '.join(a.doc_id for a in unverified)}. "
            f"Counterparty may be fabricating a paper trail — treat as "
            f"deception signal until they produce the referenced documents."
        )
    else:
        summary = (
            f"{len(verified)} of {len(artifacts)} cited document ID(s) "
            f"verified; {len(unverified)} UNVERIFIED "
            f"({', '.join(a.doc_id for a in unverified)}). Request copies "
            f"of the unverified references before acknowledging them."
        )

    result = {
        "cited": [
            {
                "doc_type": a.doc_type,
                "doc_id": a.doc_id,
                "verified": a.verified,
                "source": a.source,
                "note": a.note,
            }
            for a in artifacts
        ],
        "summary": summary,
        "verified": len(verified),
        "unverified": len(unverified),
    }

    # Brain-hook: unverified citations = deception signal; verified = benign.
    if artifacts:
        try:
            import asyncio as _asyncio
            from . import brain_hook as _bh
            has_unverified = len(unverified) > 0
            async def _emit():
                try:
                    await _bh.absorb(
                        module="cited_artifact_verifier",
                        summary=f"Cited-artifact check on {counterparty or 'unknown'}: "
                                f"{len(verified)} verified, {len(unverified)} unverified",
                        entity_name=counterparty,
                        success=not has_unverified,
                        confidence="CONFIRMED" if not has_unverified else "ASSESSED",
                        gap_type="unverified_citation" if has_unverified else None,
                        gap_detail=(
                            ", ".join(a.doc_id for a in unverified[:3])
                            if has_unverified else None
                        ),
                    )
                except Exception as e:
                    logger.debug("cited_artifact brain_hook failed: %s", e)
            try:
                _asyncio.get_running_loop().create_task(_emit())
            except RuntimeError:
                pass
        except Exception as exc:
            logger.debug("cited_artifact brain dispatch failed: %s", exc)
    return result

# R-F2119 §21a — wire failure handler for cited_artifact_verifier
try:
    wire_failure(module="cited_artifact_verifier", detail="module shutdown",
                gap_type="engine_failure", source="cited_artifact_verifier:shutdown")
except Exception:
    pass

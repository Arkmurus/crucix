"""R-F3274 — carrying what a document says onto the career timeline.

R-F3265 taught this module to READ a PDF, an image, a DOCX and an email with
its attachments. Nothing consumed the result. A payslip covering 2022-2023
landed on the file, was classified, carried covers_from/covers_to, and the
timeline still reported "0 verified · 0 declared · 61 uncovered" — because the
only route from a document to a period was the officer passing
`attach_to_entry_id` by hand. Reading a document and then not using what it
says is barely better than not reading it.

This module is the missing consumer, and it is PURE: it takes a case, a
document and its extraction and returns a new career list plus a summary of
what it did. Nothing here writes, and nothing reads a clock.

── What it is allowed to conclude ────────────────────────────────────────
Two things, and the line between them is the design:

  * A document that evidences a period ATTACHES itself to the periods it
    overlaps and lifts them UNVERIFIED -> EVIDENCE_RECEIVED. Never VERIFIED.
    Verification is a referee's or a human's act; a payslip arriving is
    evidence received, and calling that verified is the false clean this
    module exists to prevent.

  * An APPLICATION FORM declares a history, so periods read off one are
    created UNVERIFIED — exactly what coverage_map documents that state to
    mean: "declared by the applicant, not yet verified. Present on the file,
    NOT yet evidence."

── The allow-list, which is the most important control here ──────────────
A PASSPORT has covers_from/covers_to too: its issue and expiry. A naive
overlap rule would let a single identity document "cover" ten years of a
career timeline and turn a blank grid green. Only documents that evidence an
ENGAGEMENT may touch the timeline, and the set is enumerated rather than
derived, so adding a document type is a decision somebody makes on purpose.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from .models import (
    CareerEntry,
    CareerEntryType,
    DocumentType,
    UploadedDocument,
    VerificationState,
    VettingCase,
)

# Documents that evidence an ENGAGEMENT — being somewhere, doing something,
# over a period. Enumerated deliberately: see the module docstring. Identity,
# address and status documents are absent because they evidence who someone is
# or where they live, not what they were doing.
PERIOD_EVIDENCING_TYPES: frozenset[DocumentType] = frozenset({
    DocumentType.PAYSLIP,
    DocumentType.P45,
    DocumentType.P60,
    DocumentType.EMPLOYMENT_CONTRACT,
    DocumentType.REDUNDANCY_LETTER,
    DocumentType.HMRC_DOCUMENT,
    DocumentType.DWP_CONFIRMATION,
    DocumentType.EMPLOYER_REFERENCE,
    DocumentType.EDUCATION_REFERENCE,
    DocumentType.ACCOUNTANT_REFERENCE,
    DocumentType.TRAVEL_EVIDENCE,
})

# A period may only be lifted from these. Everything else is either already
# stronger, or a finding that must not be quietly cleared by an upload.
_LIFTABLE_FROM = {VerificationState.UNVERIFIED}

_SOURCE_EXTRACTED = "EXTRACTED_FROM_DOCUMENT"


def _overlaps(a_start: date, a_end: date | None,
              b_start: date, b_end: date | None) -> bool:
    """Do two periods share any day? An open end means "still running"."""
    if a_end is not None and a_end < b_start:
        return False
    if b_end is not None and b_end < a_start:
        return False
    return True


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _document_is_usable(document: UploadedDocument) -> str:
    """Empty string when the document may act; otherwise the stated reason.

    Mirrors `documents.needs_human_review` rather than re-deciding it: below
    the confidence floor a classification is a suggestion for a human, and any
    authenticity concern means somebody looks before the file moves.
    """
    from .documents import CONFIDENCE_FLOOR

    if document.extraction_confidence < CONFIDENCE_FLOOR:
        return (f"classification confidence {document.extraction_confidence:.2f} "
                f"is below the {CONFIDENCE_FLOOR:.2f} floor — left for a human")
    if document.authenticity_flags:
        return ("the document carries an authenticity concern — left for a "
                "human before it may evidence anything")
    return ""


def _link_to_periods(
    career: list[CareerEntry], document: UploadedDocument,
) -> tuple[list[CareerEntry], list[str]]:
    """Attach the document to every period it overlaps; lift what may lift."""
    linked: list[str] = []
    out: list[CareerEntry] = []
    for entry in career:
        if not _overlaps(document.covers_from, document.covers_to,
                         entry.start, entry.end):
            out.append(entry)
            continue
        linked.append(entry.entry_id)
        updates: dict[str, Any] = {}
        if document.document_id not in entry.supporting_documents:
            updates["supporting_documents"] = [
                *entry.supporting_documents, document.document_id]
        # VERIFIED and COVERED_BY_STAT_DEC are already stronger; a further
        # document does not add to them. VERIFICATION_FAILED is a FINDING —
        # lifting it back to a neutral-looking state on an upload would erase
        # the one thing the file most needs to keep saying.
        if entry.state in _LIFTABLE_FROM:
            updates["state"] = VerificationState.EVIDENCE_RECEIVED
        out.append(entry.model_copy(update=updates) if updates else entry)
    return out, linked


def _declared_periods(
    career: list[CareerEntry], document: UploadedDocument, extraction: dict,
) -> tuple[list[CareerEntry], list[str], int]:
    """Periods read off an application form, as DECLARED (never evidenced)."""
    data = (extraction or {}).get("data") or {}
    raw = data.get("declared_periods")
    if not isinstance(raw, list):
        return [], [], 0

    created: list[CareerEntry] = []
    created_ids: list[str] = []
    rejected = 0
    for item in raw[:60]:
        if not isinstance(item, dict):
            rejected += 1
            continue
        start = _parse_date(item.get("start"))
        end = _parse_date(item.get("end"))
        if start is None or (end is not None and end < start):
            # Not normalised: a period that ends before it starts is a misread,
            # and quietly swapping the dates would file a claim the document
            # does not make.
            rejected += 1
            continue
        try:
            entry_type = CareerEntryType(
                str(item.get("entry_type", "")).strip().upper())
        except ValueError:
            rejected += 1
            continue
        organisation = item.get("organisation")
        organisation = (str(organisation).strip()[:256] or None
                        if organisation else None)

        # Do not duplicate something already on the file. Same kind of period,
        # overlapping dates, same organisation (or none named) is the same
        # period said twice — and an officer-entered entry always wins, because
        # a human typed it and a model only read it.
        duplicate = any(
            e.entry_type is entry_type
            and _overlaps(start, end, e.start, e.end)
            and (not organisation or not e.organisation
                 or organisation.casefold() == (e.organisation or "").casefold())
            for e in [*career, *created]
        )
        if duplicate:
            continue

        entry = CareerEntry(
            entry_id=f"ext-{uuid.uuid4().hex[:10]}",
            entry_type=entry_type, start=start, end=end,
            organisation=organisation,
            state=VerificationState.UNVERIFIED,
            supporting_documents=[document.document_id],
            source=_SOURCE_EXTRACTED,
            source_document_id=document.document_id,
            notes=("Read from the application form on file — declared by the "
                   "applicant, not verified. Confirm against the document."),
        )
        created.append(entry)
        created_ids.append(entry.entry_id)
    return created, created_ids, rejected


def apply_document_to_timeline(
    case: VettingCase, document: UploadedDocument, extraction: dict,
) -> tuple[list[CareerEntry], dict[str, Any]]:
    """Return (new career list, summary). Pure — the caller persists.

    The summary is not decoration: when this declines to act, the reason has to
    reach the officer, or a document that changed nothing looks identical to a
    document that had nothing to say.
    """
    career = list(case.career)
    summary: dict[str, Any] = {
        "linked_entry_ids": [], "created_entry_ids": [],
        "rejected": 0, "reason": "",
    }

    blocked = _document_is_usable(document)
    if blocked:
        summary["reason"] = blocked
        return career, summary

    if document.doc_type is DocumentType.APPLICATION_FORM:
        created, created_ids, rejected = _declared_periods(
            career, document, extraction)
        summary["created_entry_ids"] = created_ids
        summary["rejected"] = rejected
        if rejected and not created_ids:
            summary["reason"] = (
                f"{rejected} declared period(s) could not be read reliably and "
                f"were not added")
        return [*career, *created], summary

    if document.doc_type not in PERIOD_EVIDENCING_TYPES:
        summary["reason"] = (
            f"a {document.doc_type.value} does not evidence an engagement, so "
            f"it was not applied to the timeline")
        return career, summary

    if document.covers_from is None:
        summary["reason"] = "the document does not state a period it covers"
        return career, summary

    career, linked = _link_to_periods(career, document)
    summary["linked_entry_ids"] = linked
    if not linked:
        summary["reason"] = (
            "no declared period overlaps the dates this document covers")
    return career, summary

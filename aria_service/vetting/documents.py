"""R-F3144/R-F3145 — evidence intake and document extraction for vetting.

── Why dd_evidence_store and not a new bucket (R-F3144) ──────────────────
The module README proposed presigned uploads to Tigris. This repo already
has `intel/dd_evidence_store.py` (R-F3083): content-addressed artifact
retention, sha256 verification on append, an explicit tenant boundary, and
idempotent re-append. That is exactly what BS 7858's record-keeping
expectation asks for — a retained copy, who examined it, when — and §6 puts
the burden of proof on any new third party. So evidence lands there.

The evidence vocabulary already models this case honestly: an applicant's
payslip is `SourceAuthority.USER_SUPPLIED`, not a primary-official record.
Recording it as anything stronger would overstate the evidence at the point
it enters the file, which is the hardest place to correct later.

── What extraction may and may not decide (R-F3145) ──────────────────────
The model reads documents. It does not grade people, and it does not get to
mark its own homework. Every extraction lands as a PROPOSAL:

  * A confident classification sets doc_type and the covered dates.
  * Anything below the confidence floor, or any authenticity concern, is
    recorded as an authenticity_flag and the document is left for a human —
    it is never silently accepted.
  * An LLM failure (unreachable, timeout, invalid output) yields NO
    extraction at all. The document is still stored and still counted as
    present; it simply carries `extraction_unavailable`. A failed read must
    never look like a document that was read and found unremarkable.

That last rule is the whole point. Everything the employer relies on — gaps,
clocks, thresholds — is computed deterministically by rules.py from what is
on the file. Extraction only proposes what a document IS.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

from .models import DocumentType, UploadedDocument

_log = logging.getLogger(__name__)

# Below this, a classification is a suggestion for a human, not a fact.
CONFIDENCE_FLOOR = 0.70

MAX_DOCUMENT_BYTES = 16 * 1024 * 1024

_EXTRACTION_SYSTEM_PROMPT = """\
You classify documents submitted for pre-employment screening.

Return ONLY what the document itself shows. Rules:
- If you cannot tell what the document is, use doc_type "OTHER" and a low
  confidence. Guessing is worse than admitting uncertainty here: a
  misclassified document silently changes which evidence a screening file
  appears to hold.
- covers_from / covers_to are the period the document EVIDENCES (e.g. the
  pay period on a payslip, the tax year on a P60), not the date it was
  printed. Use null if the document does not state a period.
- issuer is the organisation that produced the document, verbatim.
- Report anything that would make a careful human look twice in
  authenticity_concerns: inconsistent fonts or alignment, dates that
  contradict each other, evidence of digital editing, a mismatched logo,
  an altered total. Do not speculate about intent; describe what you see.
- You are NOT assessing the person. Never infer suitability, character or
  risk. Classify the document only.
"""

_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string",
                     "enum": [t.value for t in DocumentType]},
        "issuer": {"type": ["string", "null"]},
        "covers_from": {"type": ["string", "null"],
                        "description": "ISO date YYYY-MM-DD or null"},
        "covers_to": {"type": ["string", "null"],
                      "description": "ISO date YYYY-MM-DD or null"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "authenticity_concerns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["doc_type", "confidence"],
}


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_iso_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


# The evidence contract (dd_evidence_standard) requires evidence_id, case_id,
# subject_entity_id and source_attempt_id to be UUIDs. Vetting case ids are
# human references ("VET-2026-001") and subjects are names, so they are mapped
# into a UUID namespace with uuid5 — DETERMINISTIC, so the same case and the
# same bytes always produce the same ids, which is what keeps the store's
# de-duplication working across restarts. A uuid4 here would silently turn one
# re-uploaded payslip into two pieces of evidence.
_VETTING_NS = uuid.UUID("5f7d4a2e-9c11-4b6e-8a3f-2d6c1e0b7a94")


def _stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(_VETTING_NS, "|".join(parts)))


def build_evidence_record(
    *,
    tenant_id: str,
    case_id: str,
    evidence_id: str,
    content_hash: str,
    filename: str,
    subject_entity_id: str,
) -> dict[str, Any]:
    """One canonical evidence record for an applicant-supplied document."""
    return {
        "evidence_id": evidence_id,
        "tenant_id": tenant_id,
        "case_id": _stable_uuid("case", tenant_id, case_id),
        "case_scope_version": 1,
        "subject_entity_id": _stable_uuid(
            "subject", tenant_id, case_id, subject_entity_id),
        # Stable per upload: the same bytes uploaded twice to the same case
        # de-duplicate rather than double-count as two pieces of evidence.
        "source_attempt_id": _stable_uuid(
            "attempt", tenant_id, case_id, content_hash),
        "source_id": "vetting_applicant_upload",
        "source_authority": "user_supplied",
        "retrieval_outcome": "success",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "request_fingerprint": sha256_hex(
            f"{tenant_id}|{case_id}|{filename}|{content_hash}".encode()),
        "licence_policy_id": "vetting_applicant_submission",
        "access_method": "applicant_upload",
        "adapter_version": "vetting-upload-1",
        "parser_version": "vetting-extract-1",
        "content_hash": content_hash,
        # PERMITTED: the standard expects the examined copy to be retained on
        # the screening file, and retention is a documented obligation here
        # rather than an optional convenience. The contract then REQUIRES a
        # raw_artifact_uri; the store is content-addressed, so the hash IS the
        # locator.
        "snapshot_policy": "permitted",
        "raw_artifact_uri": f"vetting-evidence://{content_hash}",
        "source_url": None,
        "structured_payload": {"filename": filename},
    }


async def extract_document(
    *,
    text: str,
    filename: str,
    llm: Any = None,
) -> dict[str, Any]:
    """Classify one document. NEVER raises, and never fabricates on failure.

    Returns a dict with `available` False when no usable extraction could be
    obtained — the caller must treat that as "not yet read", never as "read
    and unremarkable".
    """
    from ..llm.structured import call_structured

    if not text.strip():
        return {"available": False, "reason": "no extractable text in document"}

    result = await call_structured(
        _EXTRACTION_SYSTEM_PROMPT,
        f"Filename: {filename}\n\nDocument text:\n{text[:12000]}",
        schema=_EXTRACTION_SCHEMA,
        caller="vetting.extract_document",
        max_tokens=600,
        timeout=45.0,
        llm=llm,
    )
    if not result.ok or not isinstance(result.data, dict):
        # call_structured already wired the failure to the brain (§21a).
        return {
            "available": False,
            "reason": f"extraction unavailable ({result.outcome})",
        }
    return {"available": True, "data": result.data}


def apply_extraction(
    *,
    document_id: str,
    evidence_id: str,
    fallback_doc_type: DocumentType,
    extraction: dict[str, Any],
) -> UploadedDocument:
    """Turn a raw extraction into an UploadedDocument, conservatively.

    The confidence floor and the authenticity flags both route to the same
    place: a human looks. Nothing here can mark a document accepted.
    """
    flags: list[str] = []
    doc_type = fallback_doc_type
    issuer: str | None = None
    covers_from: date | None = None
    covers_to: date | None = None
    confidence = 0.0

    if not extraction.get("available"):
        # A failed read is a DISCLOSED gap, not a silent pass.
        flags.append("extraction_unavailable")
        flags.append(str(extraction.get("reason", "unknown"))[:200])
        return UploadedDocument(
            document_id=document_id, doc_type=doc_type, evidence_id=evidence_id,
            extraction_confidence=0.0, authenticity_flags=flags,
        )

    data = extraction.get("data") or {}
    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    raw_type = str(data.get("doc_type", "") or "").strip().upper()
    if confidence >= CONFIDENCE_FLOOR:
        try:
            doc_type = DocumentType(raw_type)
        except ValueError:
            flags.append(f"unrecognised_doc_type:{raw_type[:40]}")
    else:
        # Keep the caller's declared type; record the suggestion for a human
        # rather than acting on it.
        flags.append(
            f"low_confidence_classification:{raw_type[:40]}@{confidence:.2f}")

    issuer = (str(data.get("issuer")).strip()
              if data.get("issuer") else None)
    covers_from = _parse_iso_date(data.get("covers_from"))
    covers_to = _parse_iso_date(data.get("covers_to"))
    if covers_from and covers_to and covers_to < covers_from:
        # A document that evidences a period ending before it starts is not a
        # period we may quietly normalise — drop both and flag it.
        flags.append("inconsistent_coverage_dates")
        covers_from = covers_to = None

    for concern in (data.get("authenticity_concerns") or [])[:10]:
        text = str(concern).strip()
        if text:
            flags.append(f"authenticity:{text[:160]}")

    return UploadedDocument(
        document_id=document_id,
        doc_type=doc_type,
        evidence_id=evidence_id,
        covers_from=covers_from,
        covers_to=covers_to,
        issuer=issuer,
        extraction_confidence=confidence,
        authenticity_flags=flags,
    )


def needs_human_review(document: UploadedDocument) -> bool:
    """True when this document must not be counted as accepted evidence."""
    return (
        bool(document.authenticity_flags)
        or document.extraction_confidence < CONFIDENCE_FLOOR
    )


def new_document_id() -> str:
    return f"vdoc_{uuid.uuid4().hex[:16]}"


def new_evidence_id() -> str:
    # Must be a bare UUID — the evidence contract validates the format.
    return str(uuid.uuid4())


def decode_text_best_effort(content: bytes, filename: str) -> str:
    """Extract text we can actually read, without pretending we read more.

    PDFs and images are NOT parsed here: this returns "" for them, which
    routes to `extraction_unavailable` and a human. Shipping a plausible-
    looking extraction from bytes we never decoded would be the fabrication
    this module exists to avoid — a real PDF/OCR path is separate work.
    """
    lowered = filename.lower()
    if lowered.endswith((".txt", ".csv", ".md", ".json")):
        try:
            return content.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — never fail an upload on decode
            return ""
    if lowered.endswith(".json"):
        try:
            return json.dumps(json.loads(content))
        except Exception:  # noqa: BLE001
            return ""
    return ""

"""R-F3180 — the unauthenticated vetting portal.

DELIBERATELY A SEPARATE MODULE FROM routes/vetting.py.

Every route in `routes/vetting.py` carries `Depends(_router_auth_dep)`. These
do not — they are reached by an applicant with a link, or a referee with an
emailed link, neither of whom has an account. Putting them in the same router
and remembering to exempt them would make "is this endpoint authenticated?"
a question you answer by reading a decorator list. Here it is answered by
which file you are in.

── What a token can do ───────────────────────────────────────────────────
Exactly two things: read the minimal context for its own scope, and upload a
document to its own case. It cannot list cases, read findings, read a verdict,
see other documents, or discover anything about the applicant beyond what its
scope allows. The employer's assessment is not reachable from this surface at
all.

── Why every failure is the same 404 ─────────────────────────────────────
An expired link, a revoked link, a mistyped link and a link for a case that
has since been disposed of all return the identical response. Distinguishing
them would turn this endpoint into an oracle: "revoked" confirms the link was
once real, which confirms a named person was screened by a named employer.
That inference is harmful on its own, which is the same reason the authenticated
side 404s instead of 403ing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..intel.engine_wiring import wire_success
from ..intel.wire import fail_wire
from ..vetting.invites import (
    InviteError, InviteKind, applicant_context, referee_context, token_hash,
)
from ..vetting.store import get_case_store

_log = logging.getLogger(__name__)

# No `dependencies=[...]`: unauthenticated BY DESIGN, and visibly so.
router = APIRouter(prefix="/api/vetting-portal", tags=["vetting-portal"])

_MODULE = "vetting_portal"

# One response for every failure mode — see the module docstring.
_GONE = HTTPException(
    status_code=404,
    detail={"code": "link_not_valid",
            "message": "This link is not valid. It may have expired or been "
                       "withdrawn. Please ask your contact for a new one."},
)

MAX_PORTAL_DOCUMENT_BYTES = 16 * 1024 * 1024


async def _resolve(token: str):
    """Return (invite, case) for a usable token, or raise the single 404."""
    import asyncio

    presented = (token or "").strip()
    if not presented:
        raise _GONE
    store = get_case_store()
    invite = await asyncio.to_thread(
        store.get_invite_by_token_hash, token_hash(presented))
    if invite is None or not invite.is_usable(datetime.now(UTC)):
        raise _GONE
    case = await asyncio.to_thread(store.get, invite.tenant_id, invite.case_id)
    if case is None:
        # The case was disposed of (retention, or an erasure request). The link
        # dies with it — and says no more than any other dead link.
        raise _GONE
    return invite, case


@router.get("/{token}")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def portal_context_ep(token: str):
    """The minimal context for this link's scope. Never the case."""
    import asyncio

    invite, case = await _resolve(token)
    try:
        if invite.kind is InviteKind.REFEREE:
            context = referee_context(case, invite.entry_id)
        else:
            context = applicant_context(case)
    except InviteError:
        # The period this referee was asked about is no longer on the case.
        raise _GONE from None

    # ── R-F3223: tell the applicant WHAT is still needed ──────────────────
    #
    # The portal offered a file picker and nothing else, so every upload
    # arrived typed as OTHER — satisfying no requirement, and leaving the
    # applicant to guess what "your documents" meant. They then send four
    # payslips and no proof of address, and nobody finds out until an officer
    # opens the file.
    #
    # APPLICANT links only. A referee confirms one engagement and must never
    # be shown the subject's outstanding document list — that is disclosure
    # about a person to someone with no reason to know it, and it is exactly
    # the scope creep the separate referee context exists to prevent.
    outstanding: list[dict] = []
    if invite.kind is not InviteKind.REFEREE and case.manifest is not None:
        try:
            from ..vetting.packs.base import registry
            from ..vetting.requirements import RequirementState, resolve_requirements

            pack = registry.get_exact(
                pack_id=case.manifest.pack_id,
                version=case.manifest.pack_version,
                content_hash=case.manifest.pack_hash)
            for item in resolve_requirements(case, pack):
                if item.state in (RequirementState.ACCEPTED,
                                  RequirementState.WAIVED):
                    continue
                if not item.requirement.mandatory:
                    continue
                outstanding.append({
                    "key": item.requirement.key,
                    "label": item.requirement.label,
                    # The type the upload should be declared as. The applicant
                    # chooses a plain-English label; this is what goes on the
                    # wire, so the picker cannot offer a value the engine
                    # would reject.
                    "doc_type": item.requirement.accepted[0].value,
                    "still_needed": max(0, item.needed - item.held),
                })
        except Exception as exc:  # noqa: BLE001
            # Advisory only. A pack this process does not carry, or any other
            # failure here, must not turn a working upload link into a dead
            # one — the applicant can still send files, they simply do not get
            # the checklist.
            _log.debug("portal: could not resolve outstanding documents: %s", exc)

    store = get_case_store()
    await asyncio.to_thread(store.record_invite_use, invite.token_hash)
    wire_success(module=_MODULE, summary=f"portal opened ({invite.kind.value})")
    return {
        "kind": invite.kind.value,
        "expires_at": invite.expires_at,
        "context": context,
        "outstanding": outstanding,
        "upload_accepted": True,
    }


class PortalUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=256)
    content_base64: str = Field(min_length=1)
    declared_doc_type: str = "OTHER"


@router.post("/{token}/documents")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def portal_upload_ep(token: str, body: PortalUploadRequest):
    """Upload one document into the invite's case.

    Reuses the SAME intake path as the authenticated route: hash-verified,
    encrypted with the case key before the evidence store sees it, extraction
    that proposes and never accepts. A document arriving through a link is not
    weaker evidence than one an officer uploaded, and must not be handled by a
    second, thinner code path.
    """
    import asyncio
    import base64
    import binascii

    from ..intel.dd_evidence_store import (
        EvidencePersistenceError, get_evidence_store,
    )
    from ..vetting.crypto import encrypt, encryption_enabled
    from ..vetting.documents import (
        apply_extraction, build_evidence_record, decode_text_best_effort,
        extract_document, new_document_id, new_evidence_id, sha256_hex,
    )
    from ..vetting.models import DocumentType

    invite, case = await _resolve(token)

    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_document",
                    "message": "That file could not be read. Please try again."},
        ) from exc
    if not content:
        raise HTTPException(status_code=422,
                            detail={"code": "invalid_document",
                                    "message": "That file appears to be empty."})
    if len(content) > MAX_PORTAL_DOCUMENT_BYTES:
        raise HTTPException(status_code=413,
                            detail={"code": "document_too_large",
                                    "message": "That file is too large (16MB max)."})

    try:
        fallback_type = DocumentType(body.declared_doc_type.strip().upper())
    except ValueError:
        fallback_type = DocumentType.OTHER

    store = get_case_store()
    plaintext_sha256 = sha256_hex(content)
    stored_bytes, encrypted = content, False
    if encryption_enabled():
        case_key = await asyncio.to_thread(
            store.get_or_create_case_key, invite.tenant_id, invite.case_id)
        stored_bytes = encrypt(content, case_key)
        encrypted = True

    record = build_evidence_record(
        tenant_id=invite.tenant_id, case_id=invite.case_id,
        evidence_id=new_evidence_id(), content_hash=sha256_hex(stored_bytes),
        filename=body.filename, subject_entity_id=case.applicant_name,
    )
    try:
        evidence_store = await asyncio.to_thread(get_evidence_store)
        result = await asyncio.to_thread(
            evidence_store.append, record, stored_bytes)
    except EvidencePersistenceError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "not_stored",
                    "message": "That document could not be stored. Please try "
                               "again or contact your screening officer."},
        ) from exc

    extraction = await extract_document(
        text=decode_text_best_effort(content, body.filename),
        filename=body.filename)
    document = apply_extraction(
        document_id=new_document_id(), evidence_id=result.evidence_id,
        fallback_doc_type=fallback_type, extraction=extraction,
    ).model_copy(update={"plaintext_sha256": plaintext_sha256,
                         "encrypted": encrypted,
                         # Provenance: an officer reviewing the file must be
                         # able to see this arrived via a link, not from them.
                         "authenticity_flags": [
                             *extraction.get("flags", []),
                             f"uploaded_via:{invite.kind.value.lower()}_link",
                         ]})

    career = case.career
    if invite.kind is InviteKind.REFEREE and invite.entry_id:
        career = [
            e.model_copy(update={
                "supporting_documents": [*e.supporting_documents,
                                         document.document_id]})
            if e.entry_id == invite.entry_id else e
            for e in case.career
        ]
    await asyncio.to_thread(
        store.save,
        case.model_copy(update={"documents": [*case.documents, document],
                                "career": career}))
    await asyncio.to_thread(store.record_invite_use, invite.token_hash)

    wire_success(module=_MODULE,
                 summary=f"portal upload ({invite.kind.value})")
    # The uploader is told it ARRIVED — nothing more. The classification, the
    # confidence, and whether it was flagged for human review are the
    # employer's to see, not the applicant's and certainly not a referee's.
    # Returning them here would also tell someone probing the link exactly how
    # the extractor scores a forgery, which is a free calibration signal.
    return {
        "received": True,
        "filename": body.filename,
        "message": "Thank you — your document has been received.",
    }

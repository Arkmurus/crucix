"""R-F3138 — HTTP surface for the vetting module.

Mounted at `/api/aria/vetting/*`, which means it inherits the aria-web
catch-all at `server.mjs:6444` for free: `requireAuth` plus the R-F2211 IDOR
guard that PINS `user_id` to the JWT identity on both the query string and
the body before proxying. So the tenant this module trusts is not a value the
client can choose — it is the one Node overwrote.

Kept in its own module rather than appended to the 28k-line `routes/aria.py`:
a new product surface should be readable on its own, and the duplicate-route
audit (R-F2278) is easier to reason about when a feature's paths are in one
place. It reuses aria.py's `_router_auth_dep` object DIRECTLY rather than
re-deriving the auth contract — two independent auth dependencies would be
free to drift apart, and the one that drifts is the one nobody is watching.

── The deliberate divergence from the DD routes ──────────────────────────
DD treats an empty `user_id` as "admin/autonomous — see everything"
(`/dd/reports`, R-F607). This module does the OPPOSITE: an empty tenant
reads NOTHING and writes are refused with 400. A vetting case holds
criminal-conviction and financial data about a named individual, so the
blast radius of a wildcard reached by accident is categorically different
from a company DD report. There is no admin see-all path here on purpose;
if one is ever needed it must be an explicit, separately audited route, not
the default value of a query parameter.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..intel.engine_wiring import wire_success
from ..intel.wire import fail_wire
from ..vetting.models import (
    CareerEntry,
    FinancialFlags,
    ScreeningInputs,
    UploadedDocument,
    VettingCase,
)
from ..vetting.packs.base import PackNotUsable, registry
from ..vetting.service import AssessmentService
from ..vetting.store import CaseNotFound, CasePersistenceError, get_case_store
from .aria import _router_auth_dep

_log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/aria/vetting",
    tags=["vetting"],
    dependencies=[Depends(_router_auth_dep)],
)

_MODULE = "vetting"


def _tenant(user_id: str) -> str:
    """Resolve the tenant, or refuse.

    Node pins `user_id` from the JWT. A blank value here means the request
    did not come through that path (or came through a future path that
    forgot), and the correct response is to refuse — NOT to fall back to a
    default that would read across tenants.
    """
    tenant = (user_id or "").strip()
    if not tenant:
        raise HTTPException(
            status_code=400,
            detail={"code": "tenant_required",
                    "message": "vetting requests must carry an authenticated "
                               "user identity"},
        )
    return tenant


def _service() -> AssessmentService:
    return AssessmentService(get_case_store(), registry)


def _parse_as_of(as_of: str | None) -> date:
    """`as_of` is explicit in the domain; the ROUTE is where 'today' is allowed.

    Keeping the default here rather than in `rules.assess()` is what preserves
    replay: the stored assessment records the date it was run for, so the same
    call reproduces byte-identically later.
    """
    if not as_of:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(as_of)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_as_of",
                    "message": "as_of must be an ISO date (YYYY-MM-DD)"},
        ) from exc


# ── request bodies ────────────────────────────────────────────────────────
class CreateCaseRequest(BaseModel):
    """Note the absence of `tenant_id`: it is NOT a client-supplied field.

    Accepting it here — even 'for admin convenience' — would put the tenant
    boundary back under caller control, which is the exact shape the R-F2211
    pin exists to remove.
    """
    case_id: str = Field(min_length=1, max_length=128)
    applicant_name: str = Field(min_length=1, max_length=256)
    date_of_birth: date
    employment_start: date
    screening_years: int = 5
    pack_id: str = "uk_bs7858"
    conditional_employment_start: date | None = None
    extension_approved: bool = False
    inputs: ScreeningInputs = Field(default_factory=ScreeningInputs)
    career: list[CareerEntry] = Field(default_factory=list)
    financial: FinancialFlags = Field(default_factory=FinancialFlags)


class UpdateCaseRequest(BaseModel):
    inputs: ScreeningInputs | None = None
    career: list[CareerEntry] | None = None
    documents: list[UploadedDocument] | None = None
    financial: FinancialFlags | None = None
    conditional_employment_start: date | None = None
    extension_approved: bool | None = None


# ── packs ─────────────────────────────────────────────────────────────────
@router.get("/packs")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_packs_ep():
    """The jurisdiction packs this process carries, with their real status.

    Exposed so the UI can state plainly which framework a case is governed by
    and whether that pack is decision-eligible — a FRAMEWORK_ONLY pack must
    never be presented as though it certifies anything.
    """
    packs = registry.list_packs()
    wire_success(module=_MODULE, summary=f"listed {len(packs)} screening packs")
    return {"packs": packs, "count": len(packs)}


# ── cases ─────────────────────────────────────────────────────────────────
@router.post("/cases")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_create_case_ep(body: CreateCaseRequest, user_id: str = ""):
    import asyncio

    tenant = _tenant(user_id)
    try:
        case = VettingCase(
            tenant_id=tenant,
            case_id=body.case_id,
            applicant_name=body.applicant_name,
            date_of_birth=body.date_of_birth,
            employment_start=body.employment_start,
            screening_years=body.screening_years,
            conditional_employment_start=body.conditional_employment_start,
            extension_approved=body.extension_approved,
            inputs=body.inputs,
            career=body.career,
            financial=body.financial,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_case", "errors": [str(exc)]},
        ) from exc

    service = _service()
    try:
        created = await asyncio.to_thread(service.create_case, case, body.pack_id)
    except PackNotUsable as exc:
        # A DRAFT pack is not legally reviewed for the jurisdiction it names.
        # Refusing here is the enforced-by-construction half of the pack
        # lifecycle — it is what stops a jurisdiction being sold early.
        raise HTTPException(
            status_code=409,
            detail={"code": "pack_not_usable", "message": str(exc)},
        ) from exc
    except CasePersistenceError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "case_exists", "message": str(exc)},
        ) from exc

    wire_success(module=_MODULE,
                 summary=f"vetting case created under pack {body.pack_id}")
    return {
        "case_id": created.case_id,
        "manifest": created.manifest.model_dump() if created.manifest else None,
    }


@router.get("/cases")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_list_cases_ep(limit: int = 50, user_id: str = ""):
    import asyncio

    tenant = _tenant(user_id)
    store = get_case_store()
    cases = await asyncio.to_thread(store.list_cases, tenant, limit)
    wire_success(module=_MODULE, summary=f"listed {len(cases)} vetting cases")
    return {"cases": cases, "count": len(cases)}


@router.get("/case/{case_id}")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_get_case_ep(case_id: str, user_id: str = ""):
    import asyncio

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        # 404, never 403 — see store.py. Confirming that a case exists would
        # disclose that a named person is under screening.
        raise HTTPException(status_code=404, detail="case not found")
    # §21a — the success branch must reach the brain too. Reading a screening
    # file is exactly the access an audit surface should be able to see; a
    # read path that emits nothing is dark by the rule's own definition.
    wire_success(module=_MODULE, summary="vetting case read")
    return case.model_dump(mode="json")


@router.patch("/case/{case_id}")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_update_case_ep(
    case_id: str, body: UpdateCaseRequest, user_id: str = "",
):
    import asyncio

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items()
               if v is not None}
    if not updates:
        raise HTTPException(
            status_code=422,
            detail={"code": "empty_update", "message": "no fields to update"},
        )
    try:
        updated = case.model_copy(update=updates)
        # Re-validate: model_copy does NOT run validators, so a bad career
        # entry would otherwise be persisted unchecked.
        updated = VettingCase.model_validate(updated.model_dump())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_case", "errors": [str(exc)]},
        ) from exc

    await asyncio.to_thread(store.save, updated)
    wire_success(module=_MODULE, summary="vetting case updated")
    return {"case_id": case_id, "updated": sorted(updates)}


class UploadDocumentRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=256)
    content_base64: str = Field(min_length=1)
    # The uploader's declared type is a FALLBACK, kept when extraction is
    # unavailable or low-confidence. It is never treated as verified.
    declared_doc_type: str = "OTHER"
    attach_to_entry_id: str | None = None


@router.post("/case/{case_id}/documents")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_upload_document_ep(
    case_id: str, body: UploadDocumentRequest, user_id: str = "",
):
    """R-F3144/R-F3145 — take one document into the evidence file.

    The bytes are hash-verified and retained in the SAME store the DD side
    uses (R-F3083), under this tenant. Extraction then PROPOSES what the
    document is; it can never mark it accepted. A document whose extraction
    failed is still recorded as present, carrying `extraction_unavailable` —
    a failed read must never resemble a read that found nothing wrong.
    """
    import asyncio
    import base64
    import binascii

    from ..intel.dd_evidence_store import (
        EvidencePersistenceError,
        get_evidence_store,
    )
    from ..vetting.documents import (
        MAX_DOCUMENT_BYTES,
        apply_extraction,
        build_evidence_record,
        decode_text_best_effort,
        extract_document,
        needs_human_review,
        new_document_id,
        new_evidence_id,
        sha256_hex,
    )
    from ..vetting.models import DocumentType

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_document",
                    "errors": ["content_base64 is not valid base64"]},
        ) from exc
    if not content:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_document", "errors": ["document is empty"]},
        )
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413,
                            detail={"code": "document_too_large"})

    try:
        fallback_type = DocumentType(body.declared_doc_type.strip().upper())
    except ValueError:
        fallback_type = DocumentType.OTHER

    content_hash = sha256_hex(content)
    evidence_id = new_evidence_id()
    record = build_evidence_record(
        tenant_id=tenant, case_id=case_id, evidence_id=evidence_id,
        content_hash=content_hash, filename=body.filename,
        subject_entity_id=case.applicant_name,
    )
    try:
        evidence_store = await asyncio.to_thread(get_evidence_store)
        result = await asyncio.to_thread(evidence_store.append, record, content)
    except EvidencePersistenceError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "evidence_not_persisted",
                    "errors": list(getattr(exc, "errors", (str(exc),)))},
        ) from exc

    extraction = await extract_document(
        text=decode_text_best_effort(content, body.filename),
        filename=body.filename,
    )
    document = apply_extraction(
        document_id=new_document_id(),
        evidence_id=result.evidence_id,
        fallback_doc_type=fallback_type,
        extraction=extraction,
    )

    documents = [*case.documents, document]
    career = case.career
    if body.attach_to_entry_id:
        career = [
            e.model_copy(update={
                "supporting_documents": [*e.supporting_documents,
                                         document.document_id]})
            if e.entry_id == body.attach_to_entry_id else e
            for e in case.career
        ]
    await asyncio.to_thread(
        store.save,
        case.model_copy(update={"documents": documents, "career": career}),
    )

    wire_success(module=_MODULE,
                 summary=f"vetting document stored ({document.doc_type.value})")
    return {
        "document_id": document.document_id,
        "evidence_id": result.evidence_id,
        "evidence_status": result.status,
        "content_hash_verified": result.content_hash_verified,
        "doc_type": document.doc_type.value,
        "extraction_confidence": document.extraction_confidence,
        "authenticity_flags": document.authenticity_flags,
        # The single field the UI should key its "needs a human" badge on.
        "needs_human_review": needs_human_review(document),
    }


@router.post("/case/{case_id}/assess")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_assess_ep(
    case_id: str, as_of: str | None = None, user_id: str = "",
):
    """Run the deterministic assessment.

    The response is the engine's verdict verbatim: status, clause-referenced
    findings, the pinned pack, and the counts. The terminal good state is
    READY_FOR_CONTROLLER_REVIEW — this endpoint never issues a pass or fail
    on a person, and a FRAMEWORK_ONLY pack cannot get past EVIDENCE_COMPLETE.
    """
    import asyncio

    tenant = _tenant(user_id)
    resolved_as_of = _parse_as_of(as_of)
    service = _service()
    try:
        result = await asyncio.to_thread(
            service.assess, tenant, case_id, resolved_as_of)
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case not found") from None
    except PackNotUsable as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "pack_not_usable", "message": str(exc)},
        ) from exc

    wire_success(
        module=_MODULE,
        summary=f"assessed vetting case -> {result.get('status', 'UNKNOWN')}",
    )
    return result


@router.get("/retention")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_retention_ep(as_of: str | None = None, user_id: str = ""):
    """R-F3148 — retention schedule for this tenant's cases.

    Reports a due date only where one can honestly be computed. A file whose
    clock has not started (screening in progress, or employment ongoing) is
    reported with `due_date: null` and the REASON — never with a guessed date.
    """
    import asyncio

    from ..vetting.retention import retention_due_date

    tenant = _tenant(user_id)
    resolved_as_of = _parse_as_of(as_of)
    store = get_case_store()
    summaries = await asyncio.to_thread(store.list_cases, tenant, 500)

    rows = []
    for summary in summaries:
        case = await asyncio.to_thread(store.get, tenant, summary["case_id"])
        if case is None or case.manifest is None:
            continue
        try:
            pack = registry.get_exact(
                pack_id=case.manifest.pack_id,
                version=case.manifest.pack_version,
                content_hash=case.manifest.pack_hash,
            )
        except PackNotUsable:
            # A case pinned to a pack this process does not carry cannot have
            # its retention computed here. Say so; do not omit the case, which
            # would make it silently invisible to a disposal review.
            rows.append({"case_id": case.case_id, "due_date": None,
                         "reason": "pinned pack not available in this process",
                         "overdue": False})
            continue
        verdict = retention_due_date(case, pack, resolved_as_of)
        rows.append({
            "case_id": case.case_id,
            "outcome": case.outcome,
            "due_date": verdict.due_date.isoformat() if verdict.due_date else None,
            "reason": verdict.reason,
            "overdue": verdict.overdue,
        })

    overdue = [r for r in rows if r["overdue"]]
    wire_success(module=_MODULE,
                 summary=f"retention reviewed: {len(overdue)} overdue of {len(rows)}")
    return {"as_of": resolved_as_of.isoformat(), "cases": rows,
            "overdue_count": len(overdue)}


@router.post("/case/{case_id}/dispose")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_dispose_case_ep(case_id: str, user_id: str = ""):
    """R-F3148 — dispose of a case, reporting exactly what survives.

    Returns `erasure_complete: false` whenever retained evidence artifacts
    outlive the case record. Reporting a clean erasure while artifacts remain
    would be the worst outcome available here: an overstated data-protection
    response is the one nobody goes back and fixes.
    """
    import asyncio

    from ..vetting.retention import plan_disposal

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    plan = plan_disposal(case)
    deleted = await asyncio.to_thread(store.delete, tenant, case_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="case not found")

    wire_success(
        module=_MODULE,
        summary=f"vetting case disposed (erasure_complete={plan.complete})",
    )
    return plan.as_dict()


@router.delete("/case/{case_id}")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_delete_case_ep(case_id: str, user_id: str = ""):
    import asyncio

    tenant = _tenant(user_id)
    store = get_case_store()
    deleted = await asyncio.to_thread(store.delete, tenant, case_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="case not found")
    wire_success(module=_MODULE, summary="vetting case deleted")
    return {"case_id": case_id, "deleted": True}

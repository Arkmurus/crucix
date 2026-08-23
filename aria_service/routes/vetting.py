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
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..intel.engine_wiring import wire_failure, wire_success
from ..intel.wire import fail_wire
from ..vetting.models import (
    CareerEntry,
    CaseOutcomeLiteral,
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


async def _require_art10_position(tenant: str, case, as_of: date) -> None:
    """R-F3158 — refuse to HOLD criminal-offence data without an Art. 10 basis.

    Enforced on WRITE, not on read: the point is that the data never enters
    the file without an evidenced condition. Checking at read time would mean
    the unlawful processing had already happened and we merely declined to
    show it.

    Cases that do not engage Art. 10 are untouched — requiring every tenant to
    record a condition before they have any conviction data would be its own
    compliance theatre.
    """
    import asyncio

    from ..vetting.legal_basis import (
        JurisdictionNotReviewed, LegalBasisError, holds_criminal_offence_data,
        regime_for, validate_position,
    )

    if not holds_criminal_offence_data(case):
        return

    # R-F3162 — the Art. 10 regime follows the CASE's jurisdiction, taken from
    # its pinned pack. The UK conditions are UK statute and authorise nothing
    # elsewhere; a case pinned to an EU or INTL pack must not be waved through
    # on a DPA 2018 condition.
    if case.manifest is not None:
        try:
            pack = registry.get_exact(
                pack_id=case.manifest.pack_id,
                version=case.manifest.pack_version,
                content_hash=case.manifest.pack_hash,
            )
        except PackNotUsable as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "pack_unavailable", "message": str(exc)},
            ) from exc
        try:
            regime_for(pack.jurisdiction)
        except JurisdictionNotReviewed as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "jurisdiction_not_reviewed",
                        "jurisdiction": pack.jurisdiction,
                        "message": str(exc)},
            ) from exc

    store = get_case_store()
    position = await asyncio.to_thread(store.get_art10_position, tenant)
    if position is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "art10_basis_required",
                "message": (
                    "This case holds criminal-conviction or offence data. "
                    "UK GDPR Art. 10 permits that only where authorised by "
                    "domestic law (DPA 2018 s.10(5) + Schedule 1). Record the "
                    "Schedule 1 condition and its appropriate policy document "
                    "at POST /api/aria/vetting/legal-basis before continuing."
                ),
            },
        )
    try:
        validate_position(position, as_of)
    except LegalBasisError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "art10_basis_invalid", "message": str(exc)},
        ) from exc


async def _require_art10_position_for_pack(
    tenant: str, case, pack_id: str, as_of: date,
) -> None:
    """R-F3171 — the create-time variant of the Art. 10 gate.

    At create the case has no manifest yet, so the jurisdiction must come from
    the pack being REQUESTED. Everything after that is the same check; sharing
    the tail rather than duplicating it keeps the two paths from drifting,
    which is how create came to be ungated in the first place.
    """
    import asyncio

    from ..vetting.legal_basis import (
        JurisdictionNotReviewed, LegalBasisError, holds_criminal_offence_data,
        regime_for, validate_position,
    )

    if not holds_criminal_offence_data(case):
        return

    try:
        pack = registry.latest_usable(pack_id)
    except PackNotUsable:
        return          # the create itself will refuse, with a better message
    try:
        regime_for(pack.jurisdiction)
    except JurisdictionNotReviewed as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "jurisdiction_not_reviewed",
                    "jurisdiction": pack.jurisdiction, "message": str(exc)},
        ) from exc

    store = get_case_store()
    position = await asyncio.to_thread(store.get_art10_position, tenant)
    if position is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "art10_basis_required",
                    "message": (
                        "This case would hold criminal-conviction or offence "
                        "data. Record the Schedule 1 condition and its "
                        "appropriate policy document at "
                        "POST /api/aria/vetting/legal-basis first — nothing "
                        "has been created.")},
        )
    try:
        validate_position(position, as_of)
    except LegalBasisError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "art10_basis_invalid", "message": str(exc)},
        ) from exc


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
    # Backwards-compatible wire field, constrained to the only standard this
    # product supports. The UI does not expose a selector.
    pack_id: Literal["uk_bs7858"] = "uk_bs7858"
    conditional_employment_start: date | None = None
    offer_date: date | None = None
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
    offer_date: date | None = None
    extension_approved: bool | None = None
    stat_dec_days_used: int | None = Field(default=None, ge=0)
    stat_dec_approved_by: str | None = Field(default=None, max_length=200)
    # R-F3152 — WITHOUT these the retention fields were unreachable: the model
    # carried outcome/outcome_date/employment_end, retention.py computed from
    # them, and no API path could ever set them. So every case stayed PENDING
    # and the storage-limitation duty (Art. 5(1)(e)) could never be discharged
    # — a retention feature that could not retain anything.
    outcome: CaseOutcomeLiteral | None = None
    outcome_date: date | None = None
    employment_end: date | None = None


class LegalBasisRequest(BaseModel):
    """The tenant's Art. 10 position. Every field is a legal determination."""
    condition: str
    apd_reference: str = ""
    apd_review_date: date | None = None
    dpia_reference: str = ""
    determined_by: str = Field(min_length=1, max_length=200)


@router.get("/legal-basis")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_legal_basis_get_ep(user_id: str = ""):
    """R-F3158 — the tenant's recorded Art. 10 position, and the options.

    The conditions are returned with their statutory notes so the choice is
    made against what each actually authorises, rather than by picking the one
    whose name reads best.
    """
    import asyncio

    from ..vetting.legal_basis import (
        CONDITION_NOTES, REVIEWED_JURISDICTIONS, LegalBasisError, Sch1Condition,
        recommendation_for, requires_apd, validate_position,
    )

    tenant = _tenant(user_id)
    store = get_case_store()
    position = await asyncio.to_thread(store.get_art10_position, tenant)

    valid, problem = False, "no position recorded"
    if position is not None:
        try:
            validate_position(position, datetime.now(UTC).date())
            valid, problem = True, ""
        except LegalBasisError as exc:
            problem = str(exc)

    wire_success(module=_MODULE, summary="art10 position read")
    return {
        "recorded": position is not None,
        "valid": valid,
        "problem": problem,
        "position": None if position is None else {
            "condition": position.condition.value,
            "apd_reference": position.apd_reference,
            "apd_review_date": (position.apd_review_date.isoformat()
                                if position.apd_review_date else None),
            "dpia_reference": position.dpia_reference,
            "determined_by": position.determined_by,
        },
        "available_conditions": [
            {"code": c.value, "note": CONDITION_NOTES[c],
             "apd_required": requires_apd(c)}
            for c in Sch1Condition
        ],
        # R-F3164 — a pre-filled position for the CONTROLLER to confirm. We do
        # not select it for them: under Art. 28 the controller determines the
        # means, and choosing on their behalf would make us a joint controller.
        # Removing the research is help; making the choice is liability.
        "recommended": recommendation_for("GB"),
        # R-F3162 — which jurisdictions this build can lawfully hold
        # criminal-offence data for at all.
        "reviewed_jurisdictions": REVIEWED_JURISDICTIONS,
    }


@router.post("/legal-basis")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_legal_basis_set_ep(body: LegalBasisRequest, user_id: str = ""):
    """R-F3158 — record the Art. 10 condition and its appropriate policy document.

    The position is VALIDATED before it is stored. Storing an invalid position
    and reporting the problem later would leave a tenant believing they had a
    basis; refusing here means the only positions on file are ones that hold.
    """
    import asyncio

    from ..vetting.legal_basis import (
        Art10Position, LegalBasisError, Sch1Condition, validate_position,
    )

    tenant = _tenant(user_id)
    try:
        condition = Sch1Condition(body.condition.strip().upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_condition",
                    "accepted": [c.value for c in Sch1Condition]},
        ) from exc

    position = Art10Position(
        tenant_id=tenant, condition=condition,
        apd_reference=body.apd_reference.strip(),
        apd_review_date=body.apd_review_date,
        dpia_reference=body.dpia_reference.strip(),
        determined_by=body.determined_by.strip(),
    )
    try:
        validate_position(position, datetime.now(UTC).date())
    except LegalBasisError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_legal_basis", "message": str(exc)},
        ) from exc

    store = get_case_store()
    await asyncio.to_thread(store.set_art10_position, position)
    wire_success(module=_MODULE,
                 summary=f"art10 position recorded: {condition.value}")
    return {"recorded": True, "condition": condition.value,
            "apd_required": position.apd_required}


# ── packs ─────────────────────────────────────────────────────────────────
@router.get("/packs")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_packs_ep():
    """Compatibility surface exposing the one active BS 7858 standard."""
    pack = registry.latest_usable("uk_bs7858")
    active = {
        "pack_id": pack.pack_id,
        "version": pack.version,
        "standard": "BS 7858:2019",
        "status": pack.status.value,
        "legal_coverage": pack.legal_coverage.value,
        "decision_eligible": pack.employment_decision_eligible,
    }
    wire_success(module=_MODULE, summary="served the active BS 7858 standard")
    return {"packs": [active], "count": 1, "single_standard": True}


@router.get("/standard")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_standard_coverage_ep():
    """R-F3214 — which clauses of BS 7858 this module actually implements.

    Answers "does ARIA understand the standard?" with a number that can be
    wrong, rather than a claim that cannot. Every ENCODED clause is
    cross-checked against the live rule pack at read time; one that cannot be
    corroborated is reported as CLAIMED_NOT_CORROBORATED rather than counted.

    The response carries `not_modelled` alongside the counts on purpose. A
    coverage figure read without it reads as completeness, and this module's
    whole discipline is that absence is never a finding.

    No BSI text is stored or served — that is copyright. Clause numbers and
    ARIA's own statement of each obligation only; a licensed copy is required
    to audit against it.
    """
    from ..vetting.standard_map import coverage_report

    try:
        pack = registry.latest_usable("uk_bs7858")
    except PackNotUsable as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "pack_not_usable", "message": str(exc)}) from exc
    report = coverage_report(pack)
    wire_success(
        module=_MODULE,
        summary=(f"served BS 7858 clause coverage: "
                 f"{report['counts']['corroborated']}/"
                 f"{report['counts']['encoded']} corroborated"))
    return report


@router.get("/document-types")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_document_types_ep():
    """R-F3210 — the document vocabulary, served rather than hardcoded.

    The upload control sent `declared_doc_type: "OTHER"` for every file, which
    meant every real document landed as OTHER — and OTHER satisfies no
    requirement and evidences no period. That is not a cosmetic gap: PDFs and
    images are not text-extractable (`decode_text_best_effort` returns "" for
    them by design), so the declared type is the ONLY signal for the majority
    of real uploads. Nothing was ever going to classify them.

    Served from the enum so the picker cannot drift from what the engine
    accepts — a client-side copy of this list is a list that will one day
    offer a type the server rejects.
    """
    from ..vetting.models import DocumentType

    # Grouped the way an officer thinks about them, not the way the enum is
    # declared. The residue group exists so adding a member to the enum can
    # never silently drop it out of the picker.
    groups = {
        "Identity & entitlement": ["PASSPORT", "DRIVING_LICENCE",
                                   "BIRTH_CERTIFICATE", "RESIDENCE_PERMIT",
                                   "RIGHT_TO_WORK_CHECK"],
        "Address": ["PROOF_OF_ADDRESS", "BANK_STATEMENT", "HMRC_DOCUMENT"],
        "Application & interview": ["APPLICATION_FORM", "CV",
                                    "SIGNED_AUTHORISATION", "INTERVIEW_RECORD"],
        "Employment history": ["EMPLOYER_REFERENCE", "PAYSLIP", "P45", "P60",
                               "EMPLOYMENT_CONTRACT", "REDUNDANCY_LETTER",
                               "DWP_CONFIRMATION", "EDUCATION_REFERENCE",
                               "ACCOUNTANT_REFERENCE", "TRAVEL_EVIDENCE",
                               "STATUTORY_DECLARATION"],
        "Criminality & licensing": ["DISCLOSURE_CERTIFICATE",
                                    "NPCC_POLICE_LETTER", "SIA_LICENCE"],
    }
    placed = {value for values in groups.values() for value in values}
    residue = [t.value for t in DocumentType if t.value not in placed]
    if residue:
        groups["Other"] = residue
    return {"groups": [{"label": label, "types": types}
                       for label, types in groups.items()],
            "count": len(list(DocumentType))}


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
            offer_date=body.offer_date,
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

    # R-F3171 — gate CREATE too. PATCH and upload were gated but create was not,
    # so conviction data entered ungated AND the case then became permanently
    # un-editable: every subsequent write hit the gate the create had skipped.
    # The pack is not pinned yet, so check the jurisdiction of the pack being
    # REQUESTED rather than of a manifest that does not exist.
    await _require_art10_position_for_pack(
        tenant, case, "uk_bs7858", datetime.now(UTC).date())

    service = _service()
    try:
        created = await asyncio.to_thread(
            service.create_case, case, "uk_bs7858")
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
                 summary="vetting case created under BS 7858:2019")
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
        # R-F3158 — merge as PLAIN DATA and validate once, rather than
        # model_copy(update=<dumped dict>) followed by model_dump(). That older
        # form left `inputs` as a raw dict on an intermediate object, which
        # pydantic warned about and which any dict-unaware reader (the Art. 10
        # detector among them) would silently mis-read. One validated object,
        # no half-typed intermediate.
        merged = case.model_dump()
        merged.update(updates)
        updated = VettingCase.model_validate(merged)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_case", "errors": [str(exc)]},
        ) from exc

    # R-F3158 — if this update makes the case hold criminal-offence data, the
    # tenant's Art. 10 position must already be evidenced. Checked BEFORE the
    # write so the data never lands without a basis.
    await _require_art10_position(tenant, updated, datetime.now(UTC).date())

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
    from ..vetting.crypto import encrypt, encryption_enabled
    from ..vetting.models import DocumentType
    from ..vetting.timeline import apply_document_to_timeline

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

    # R-F3155 — encrypt BEFORE the evidence store sees the bytes. Only
    # ciphertext is ever persisted there, so destroying the case key at
    # disposal makes the retained artifact irrecoverable (Art. 17), while the
    # append-only spine keeps its tamper-evidence intact.
    #
    # The plaintext digest stays on the CASE record (integrity proof of what
    # was examined); the evidence store hashes what it actually holds, which
    # is the ciphertext. Hashing plaintext there would both mismatch the stored
    # bytes and leave a durable oracle for the very content we are erasing.
    plaintext_sha256 = sha256_hex(content)
    stored_bytes = content
    encrypted = False
    if encryption_enabled():
        case_key = await asyncio.to_thread(
            store.get_or_create_case_key, tenant, case_id)
        stored_bytes = encrypt(content, case_key)
        encrypted = True

    content_hash = sha256_hex(stored_bytes)
    evidence_id = new_evidence_id()
    record = build_evidence_record(
        tenant_id=tenant, case_id=case_id, evidence_id=evidence_id,
        content_hash=content_hash, filename=body.filename,
        subject_entity_id=case.applicant_name,
    )
    try:
        evidence_store = await asyncio.to_thread(get_evidence_store)
        result = await asyncio.to_thread(evidence_store.append, record, stored_bytes)
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
    ).model_copy(update={"plaintext_sha256": plaintext_sha256,
                         "encrypted": encrypted})

    documents = [*case.documents, document]
    # R-F3158 — a disclosure certificate or police letter engages Art. 10; the
    # tenant's condition + APD must be evidenced before it joins the file.
    await _require_art10_position(
        tenant, case.model_copy(update={"documents": documents}),
        datetime.now(UTC).date())
    # R-F3274 — carry what the document says onto the career timeline. Until
    # now the ONLY route from a document to a period was the officer passing
    # attach_to_entry_id by hand, so a payslip that plainly covered a declared
    # period left the timeline reading "0 declared · 61 uncovered".
    #
    # timeline.py is pure and decides conservatively: an evidencing document
    # lifts a period to EVIDENCE_RECEIVED (never VERIFIED — that is a human's
    # or a referee's act), an application form contributes DECLARED periods,
    # and an identity document cannot touch the timeline at all, because a
    # passport's issue-to-expiry span would otherwise "cover" ten years of a
    # career. Its summary is returned to the caller, because a document that
    # changed nothing must not look like a document that had nothing to say.
    career, timeline_summary = apply_document_to_timeline(
        case, document, extraction)

    # The officer's explicit attachment is applied ON TOP and always wins: a
    # human naming the period a document belongs to is a stronger statement
    # than any overlap rule.
    if body.attach_to_entry_id:
        career = [
            e.model_copy(update={
                "supporting_documents": [*e.supporting_documents,
                                         document.document_id]})
            if (e.entry_id == body.attach_to_entry_id
                and document.document_id not in e.supporting_documents) else e
            for e in career
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
        # R-F3274 — what this document did to the timeline, including when the
        # answer is "nothing, because …". Silence would read as success.
        "timeline": timeline_summary,
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
    store = get_case_store()
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

    # R-F3168 — cache the verdict on the case so the list view can show status
    # without re-assessing every row. Written AFTER the authoritative result is
    # computed, and stamped with the date it was computed for, so the UI can
    # say "as of" rather than implying it is current.
    try:
        case = await asyncio.to_thread(store.get, tenant, case_id)
        if case is not None:
            fresh = case.model_copy(update={
                "last_status": result.get("status", ""),
                "last_assessed_at": resolved_as_of.isoformat(),
                "last_blockers": int(result.get("counts", {}).get("blockers", 0)),
                "assessment_stale": False,
            })
            await asyncio.to_thread(store.save, fresh, mark_stale=False)
    except Exception as exc:  # noqa: BLE001 — a cache write must never fail the assessment
        _log.debug("vetting: could not cache assessment status: %s", exc)

    wire_success(
        module=_MODULE,
        summary=f"assessed vetting case -> {result.get('status', 'UNKNOWN')}",
    )
    return result


class PackMigrationRequest(BaseModel):
    """R-F3266 — move a case onto a newer version of its own rule pack."""
    migrated_by: str = Field(min_length=1, max_length=200)
    to_version: str | None = None
    reason: str = ""


@router.post("/case/{case_id}/pack/migrate")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_migrate_pack_ep(
    case_id: str, body: PackMigrationRequest, user_id: str = "",
):
    """Re-pin a case to a newer PRODUCTION version of its pack.

    Deliberately a POST an officer has to make, never something that happens
    on read: a case's governing rules changing by itself, on a page load, is
    the opposite of the replayability the pin exists to give. Every refusal is
    a 409 with the engine's own words, because each one describes a decision
    the caller has to make, not a server fault.
    """
    import asyncio

    from ..vetting.service import PackMigrationRefused

    tenant = _tenant(user_id)
    service = _service()
    try:
        record = await asyncio.to_thread(
            service.migrate_pack,
            tenant, case_id,
            to_version=body.to_version,
            migrated_by=body.migrated_by,
            reason=body.reason,
            at=datetime.now(UTC).date(),
        )
    except CaseNotFound:
        raise HTTPException(status_code=404, detail="case not found") from None
    except PackMigrationRefused as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "pack_migration_refused", "message": str(exc)},
        ) from exc
    except PackNotUsable as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "pack_not_usable", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_migration", "message": str(exc)},
        ) from exc

    wire_success(
        module=_MODULE,
        summary=(f"vetting case re-pinned {record['from_version']} -> "
                 f"{record['to_version']}"),
    )
    # The verdict is now stale by construction (the rules changed), which the
    # caller has to be told plainly rather than left to infer from a re-render.
    return {**record, "assessment_stale": True}


class DecisionRequest(BaseModel):
    decision: str
    decided_by: str = Field(min_length=1, max_length=200)
    reason: str = ""
    assessed_by: str = ""
    blocker_override_reason: str = ""
    conditions: list[str] = Field(default_factory=list)


class DisputeRequest(BaseModel):
    """Art. 22(3) — the applicant's right to contest, and Art. 16 rectification."""
    raised_by: str = Field(min_length=1, max_length=200)
    disputed_finding: str = ""
    statement: str = Field(min_length=1, max_length=4000)


@router.post("/case/{case_id}/decision")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_decision_ep(
    case_id: str, body: DecisionRequest, user_id: str = "",
):
    """R-F3153 — record the employment decision a NAMED HUMAN made.

    The engine's status at the moment of decision is captured alongside it, so
    departures from the recommendation are visible rather than inferred — and
    so it is answerable whether any human ever departs from it at all. That is
    the difference between meaningful human involvement and a rubber stamp,
    which is the question Art. 22 actually asks.

    This endpoint records; it never derives. There is no path here that turns
    an assessment into a decision.
    """
    import asyncio

    from ..vetting.decisions import (
        DecisionError, DecisionOutcome, record_decision,
    )

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    try:
        outcome = DecisionOutcome(body.decision.strip().upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_decision",
                    "accepted": [d.value for d in DecisionOutcome]},
        ) from exc

    # The engine state AT DECISION TIME — recomputed here rather than trusted
    # from the client, so the record cannot claim a cleaner file than existed.
    assessment = await asyncio.to_thread(
        _service().assess, tenant, case_id, datetime.now(UTC).date())

    try:
        record = record_decision(
            case_id=case_id, tenant_id=tenant, decision=outcome,
            decided_by=body.decided_by, reason=body.reason,
            assessed_by=body.assessed_by,
            blocker_override_reason=body.blocker_override_reason,
            conditions=tuple(body.conditions),
            engine_status=assessment.get("status", "UNKNOWN"),
            engine_blockers=int(assessment.get("counts", {}).get("blockers", 0)),
        )
    except DecisionError as exc:
        # 422, not 403: the decision is refused because it is not RECORDABLE as
        # offered, and the message says exactly what is missing.
        raise HTTPException(
            status_code=422,
            detail={"code": "decision_refused", "message": str(exc)},
        ) from exc

    await asyncio.to_thread(
        store.save,
        case.model_copy(update={"decisions": [*case.decisions, record.as_dict()]}),
    )
    wire_success(module=_MODULE,
                 summary=f"vetting decision recorded: {outcome.value}")
    return record.as_dict()


@router.post("/case/{case_id}/dispute")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_dispute_ep(
    case_id: str, body: DisputeRequest, user_id: str = "",
):
    """R-F3154 — record the applicant's challenge (Art. 16, Art. 22(3)).

    A dispute is APPENDED, never applied: the applicant's account and the
    employer's evidence both stay on the file. Silently overwriting a finding
    with the applicant's version would destroy the evidence trail; ignoring the
    challenge would defeat the right. Both accounts, side by side, is the only
    honest resolution.
    """
    import asyncio

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    entry = {
        "dispute_id": f"vdis_{len(case.disputes) + 1}",
        "raised_by": body.raised_by.strip(),
        "raised_at": datetime.now(UTC).isoformat(),
        "disputed_finding": body.disputed_finding.strip(),
        "statement": body.statement.strip(),
        "status": "OPEN",
    }
    await asyncio.to_thread(
        store.save,
        case.model_copy(update={"disputes": [*case.disputes, entry]}),
    )
    wire_success(module=_MODULE, summary="vetting dispute recorded")
    return entry


@router.get("/case/{case_id}/subject-access")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_subject_access_ep(case_id: str, user_id: str = ""):
    """R-F3154 — the Art. 15 subject-access export for one case.

    Everything held about the applicant, in one response, plus the Art. 15(1)
    context a bare data dump omits: the lawful basis, the retention period and
    its reasoning, who decided what, and an explicit statement that no solely
    automated decision was made.

    Document CONTENT is referenced by digest rather than inlined: this endpoint
    is reached with the employer's credentials, and the applicant's own copy of
    their documents is not the employer's to re-issue casually. The digests
    prove which documents are held without redistributing them.
    """
    import asyncio

    from ..vetting.retention import retention_due_date

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    retention_note = {"due_date": None, "reason": "pack unavailable"}
    if case.manifest is not None:
        try:
            pack = registry.get_exact(
                pack_id=case.manifest.pack_id,
                version=case.manifest.pack_version,
                content_hash=case.manifest.pack_hash,
            )
            verdict = retention_due_date(
                case, pack, datetime.now(UTC).date())
            retention_note = {
                "due_date": verdict.due_date.isoformat() if verdict.due_date else None,
                "reason": verdict.reason,
            }
        except PackNotUsable:
            pass

    wire_success(module=_MODULE, summary="subject access export produced")
    return {
        "case_id": case.case_id,
        "applicant_name": case.applicant_name,
        "date_of_birth": case.date_of_birth.isoformat(),
        "personal_data_held": {
            "career_history": [e.model_dump(mode="json") for e in case.career],
            "declared_information": case.inputs.model_dump(mode="json"),
            "financial_flags": case.financial.model_dump(mode="json"),
            "documents": [
                {"document_id": d.document_id, "doc_type": d.doc_type.value,
                 "issuer": d.issuer,
                 "covers_from": d.covers_from.isoformat() if d.covers_from else None,
                 "covers_to": d.covers_to.isoformat() if d.covers_to else None,
                 "plaintext_sha256": d.plaintext_sha256,
                 "flags": d.authenticity_flags}
                for d in case.documents
            ],
        },
        "processing": {
            "purpose": "pre-employment screening",
            "lawful_basis": case.lawful_basis,
            "criminal_data_condition": case.criminal_data_condition,
            "controller": tenant,
            "processor": "ARIA (Arkmurus)",
            "rules_applied": case.manifest.model_dump() if case.manifest else None,
        },
        "decisions": case.decisions,
        "disputes": case.disputes,
        "retention": retention_note,
        # Art. 22 / Art. 15(1)(h): stated as a fact of the design, not a claim
        # about intent. Nothing in this module can produce a decision.
        "automated_decision_making": False,
        "automated_decision_note": (
            "Screening findings are produced by a deterministic rule engine. "
            "Every employment decision on this file was recorded against a "
            "named human; see `decisions`."
        ),
        "your_rights": [
            "rectification (Art. 16) — raise a dispute on this case",
            "erasure (Art. 17) — subject to the retention period shown above",
            "restriction (Art. 18)", "objection (Art. 21)",
            "complaint to the ICO or your local supervisory authority",
        ],
    }


class InviteRequest(BaseModel):
    kind: str = "APPLICANT"
    entry_id: str = ""
    referee_name: str = ""
    referee_email: str = ""
    ttl_days: int = 14


@router.post("/case/{case_id}/invites")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_create_invite_ep(
    case_id: str, body: InviteRequest, user_id: str = "",
):
    """R-F3178 — mint a scoped upload link for an applicant or a referee.

    The plaintext token is returned ONCE, here, and is never stored or
    re-displayable. That is what makes a database leak not also a link leak —
    so the caller must send it now; there is no "show me that link again".
    """
    import asyncio

    from ..vetting.invites import InviteError, InviteKind, mint

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    try:
        kind = InviteKind(body.kind.strip().upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_kind",
                    "accepted": [k.value for k in InviteKind]},
        ) from exc

    if kind is InviteKind.REFEREE and not any(
        e.entry_id == body.entry_id for e in case.career
    ):
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_entry",
                    "message": "A referee link must name a career entry that "
                               "exists on this case."},
        )

    try:
        invite, token = mint(
            tenant_id=tenant, case_id=case_id, kind=kind,
            now=datetime.now(UTC), ttl_days=body.ttl_days,
            entry_id=body.entry_id, referee_name=body.referee_name,
            referee_email=body.referee_email,
        )
    except InviteError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_invite", "message": str(exc)},
        ) from exc

    await asyncio.to_thread(store.save_invite, invite)

    # R-F3203 — the invite IS the request. Recording it here rather than asking
    # the officer to log it separately is the whole point: a ledger you must
    # remember to update is what the paper progress sheet already was.
    from ..vetting.requests import code_for_invite, record as record_request

    entry_type = ""
    if body.entry_id:
        entry = next((e for e in case.career if e.entry_id == body.entry_id), None)
        if entry is not None:
            entry_type = entry.entry_type.value
    try:
        request = record_request(
            case_id=case_id,
            code=code_for_invite(kind.value, entry_type),
            sent_to=(body.referee_email or body.referee_name
                     or case.applicant_name or "recipient"),
            sent_at=datetime.now(UTC).date(),
            entry_id=body.entry_id, channel="link", invite_id=invite.invite_id,
        )
        await asyncio.to_thread(store.save_request, tenant, request)
    except Exception as exc:  # noqa: BLE001 — never fail the invite over its ledger row
        _log.warning("vetting: invite minted but request not logged: %s", exc)

    wire_success(module=_MODULE, summary=f"vetting invite minted ({kind.value})")
    return {**invite.as_dict(), "token": token}


@router.get("/case/{case_id}/invites")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_list_invites_ep(case_id: str, user_id: str = ""):
    """Existing links for this case. Never includes a token."""
    import asyncio

    tenant = _tenant(user_id)
    store = get_case_store()
    invites = await asyncio.to_thread(store.list_invites, tenant, case_id)
    wire_success(module=_MODULE, summary=f"listed {len(invites)} vetting invites")
    return {"invites": [i.as_dict() for i in invites], "count": len(invites)}


@router.delete("/case/{case_id}/invites/{invite_id}")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_revoke_invite_ep(
    case_id: str, invite_id: str, user_id: str = "",
):
    import asyncio

    tenant = _tenant(user_id)
    store = get_case_store()
    revoked = await asyncio.to_thread(
        store.revoke_invite, tenant, invite_id, datetime.now(UTC).isoformat())
    if not revoked:
        raise HTTPException(status_code=404, detail="invite not found")
    wire_success(module=_MODULE, summary="vetting invite revoked")
    return {"invite_id": invite_id, "revoked": True}


class RecordRequestRequest(BaseModel):
    code: str
    sent_to: str = Field(min_length=1, max_length=200)
    entry_id: str = ""
    channel: str = ""
    chases: str = ""
    note: str = ""


class RequestStatusUpdate(BaseModel):
    status: str


@router.get("/case/{case_id}/requests")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_list_requests_ep(
    case_id: str, as_of: str | None = None, user_id: str = "",
):
    """R-F3203 — the verification request ledger for one case.

    `overdue` and `days_outstanding` are DERIVED here, never stored: they are a
    function of (sent_at, as_of, policy), and a persisted flag would go stale
    behind a changing file exactly as the cached assessment verdict did.
    """
    import asyncio

    from ..vetting.requests import CODE_LABELS, RequestCode, summarise

    tenant = _tenant(user_id)
    resolved = _parse_as_of(as_of)
    store = get_case_store()
    requests = await asyncio.to_thread(store.list_requests, tenant, case_id)
    summary = summarise(requests, resolved)
    wire_success(module=_MODULE,
                 summary=f"listed {len(requests)} verification requests")
    return {
        "as_of": resolved.isoformat(),
        "requests": [r.as_dict(resolved) for r in requests],
        "summary": summary.as_dict(),
        "codes": [{"code": c.value, "label": CODE_LABELS[c]} for c in RequestCode],
    }


@router.post("/case/{case_id}/requests")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_record_request_ep(
    case_id: str, body: RecordRequestRequest, user_id: str = "",
):
    """Record a request sent outside the link flow — post, phone, or a chaser."""
    import asyncio

    from ..vetting.requests import RequestCode, RequestError, record as record_request

    tenant = _tenant(user_id)
    store = get_case_store()
    if await asyncio.to_thread(store.get, tenant, case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")

    try:
        code = RequestCode(body.code.strip().upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_request_code",
                    "accepted": [c.value for c in RequestCode]},
        ) from exc
    try:
        request = record_request(
            case_id=case_id, code=code, sent_to=body.sent_to,
            sent_at=datetime.now(UTC).date(), entry_id=body.entry_id,
            channel=body.channel, chases=body.chases, note=body.note,
        )
    except RequestError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_request", "message": str(exc)},
        ) from exc

    await asyncio.to_thread(store.save_request, tenant, request)
    wire_success(module=_MODULE, summary=f"verification request recorded ({code.value})")
    return request.as_dict(datetime.now(UTC).date())


@router.patch("/case/{case_id}/requests/{request_id}")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_update_request_ep(
    case_id: str, request_id: str, body: RequestStatusUpdate, user_id: str = "",
):
    """Close a request — a reply arrived, was refused, or bounced."""
    import asyncio

    from ..vetting.requests import RequestStatus

    tenant = _tenant(user_id)
    try:
        status = RequestStatus(body.status.strip().upper())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_status",
                    "accepted": [s.value for s in RequestStatus]},
        ) from exc

    store = get_case_store()
    replied = (datetime.now(UTC).date().isoformat()
               if status is RequestStatus.REPLY_RECEIVED else "")
    ok = await asyncio.to_thread(
        store.update_request_status, tenant, request_id, status.value, replied)
    if not ok:
        raise HTTPException(status_code=404, detail="request not found")
    wire_success(module=_MODULE, summary=f"verification request -> {status.value}")
    return {"request_id": request_id, "status": status.value}


class DocumentSightingUpdate(BaseModel):
    """R-F3204 — BS 7858 7.4 c): was the ORIGINAL seen, and by whom."""
    sighting: str
    examined_by: str = ""


@router.patch("/case/{case_id}/documents/{document_id}/sighting")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_document_sighting_ep(
    case_id: str, document_id: str, body: DocumentSightingUpdate,
    user_id: str = "",
):
    """Record COPY / ORIGINAL / N-A against one document.

    ORIGINAL_SEEN requires a named examiner: the standard asks for a record of
    WHO inspected and copied the original, and an unattributed sighting cannot
    be evidenced to an auditor. Refusing here is better than accepting a
    sighting that will not stand up.
    """
    import asyncio

    tenant = _tenant(user_id)
    accepted = {"ORIGINAL_SEEN", "COPY_ONLY", "NOT_APPLICABLE", "NOT_RECORDED"}
    sighting = body.sighting.strip().upper()
    if sighting not in accepted:
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_sighting", "accepted": sorted(accepted)},
        )
    if sighting == "ORIGINAL_SEEN" and not body.examined_by.strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "examiner_required",
                    "message": "Recording that an original was sighted requires "
                               "the name of the person who examined it "
                               "(BS 7858 7.4 c))."},
        )

    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if not any(d.document_id == document_id for d in case.documents):
        raise HTTPException(status_code=404, detail="document not found")

    documents = [
        d.model_copy(update={
            "sighting": sighting,
            "examined_by": body.examined_by.strip(),
            "examined_at": datetime.now(UTC).date() if sighting == "ORIGINAL_SEEN" else d.examined_at,
        }) if d.document_id == document_id else d
        for d in case.documents
    ]
    await asyncio.to_thread(store.save, case.model_copy(update={"documents": documents}))
    wire_success(module=_MODULE, summary=f"document sighting recorded ({sighting})")
    return {"document_id": document_id, "sighting": sighting}


# ── R-F3209: the officer can finally SEE what was uploaded ────────────────
#
# The module could take a document, encrypt it, hash it, prove its integrity
# and count it — and then had no way to show it to anyone. A vetting officer's
# core task is to LOOK at the passport, and the only path to a stored document
# was a subject-access export handed to the applicant. The evidence side was
# built; the reading side was simply missing.

# What may be rendered INLINE in the browser. Anything else is forced to
# download as an opaque octet-stream.
#
# This is not a nicety. These files are uploaded by applicants and referees
# through an unauthenticated portal link, and they are served back from the
# platform's own origin. An HTML or SVG file rendered inline is script running
# as us, against an authenticated officer's session — stored XSS, delivered by
# the product's own evidence store. The safe set is documents and raster
# images only, with nosniff so the browser cannot be talked out of it.
_INLINE_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "txt": "text/plain; charset=utf-8",
    "csv": "text/plain; charset=utf-8",
}


def _disposition_for(filename: str) -> tuple[str, str]:
    """(content type, disposition) for a stored evidence file."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    media = _INLINE_TYPES.get(extension)
    if media is None:
        return "application/octet-stream", "attachment"
    return media, "inline"


@router.get("/case/{case_id}/documents")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_list_documents_ep(case_id: str, user_id: str = ""):
    """Every document on the file, with what it satisfies and what it needs.

    Enriched with the evidence store's integrity verdict, because "we hold
    this" and "we can still prove this is what we were given" are different
    claims and an officer relying on a document should see both.
    """
    import asyncio

    from ..intel.dd_evidence_store import get_evidence_store

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    def _collect() -> list[dict]:
        evidence_store = get_evidence_store()
        rows: list[dict] = []
        for document in case.documents:
            filename = ""
            integrity: dict = {}
            if document.evidence_id:
                stored = evidence_store.get(tenant, document.evidence_id)
                if stored:
                    payload = stored["record"].get("structured_payload") or {}
                    filename = str(payload.get("filename", ""))
                    integrity = stored["integrity"]
            media, disposition = _disposition_for(filename)
            rows.append({
                "document_id": document.document_id,
                "doc_type": document.doc_type.value,
                "filename": filename,
                "issuer": document.issuer,
                "covers_from": document.covers_from.isoformat() if document.covers_from else None,
                "covers_to": document.covers_to.isoformat() if document.covers_to else None,
                "extraction_confidence": document.extraction_confidence,
                "authenticity_flags": list(document.authenticity_flags),
                "sighting": document.sighting,
                "examined_by": document.examined_by,
                "examined_at": document.examined_at.isoformat() if document.examined_at else None,
                "encrypted": document.encrypted,
                "plaintext_sha256": document.plaintext_sha256,
                # Viewable at all? A record whose artifact was never retained,
                # or whose evidence_id is missing, cannot be opened — say so
                # here rather than letting the officer click into a 404.
                "viewable": bool(document.evidence_id
                                 and integrity.get("artifact_retained")),
                "renders_inline": disposition == "inline",
                "integrity": integrity,
            })
        return rows

    documents = await asyncio.to_thread(_collect)
    wire_success(module=_MODULE,
                 summary=f"listed {len(documents)} vetting documents")
    return {"case_id": case_id, "documents": documents,
            "count": len(documents)}


@router.get("/case/{case_id}/documents/{document_id}/content")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_document_content_ep(
    case_id: str, document_id: str, user_id: str = "",
):
    """Hand back one stored document, decrypted, for the officer to read.

    Every access reaches the brain. That is not observability for its own
    sake: Art. 5(2) accountability means being able to say who looked at an
    applicant's criminal-record certificate, and a read path that emits
    nothing cannot answer it. §21a would call this dark either way.
    """
    import asyncio

    from fastapi.responses import Response

    from ..intel.dd_evidence_store import get_evidence_store
    from ..vetting.crypto import decrypt

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    document = next(
        (d for d in case.documents if d.document_id == document_id), None)
    if document is None or not document.evidence_id:
        raise HTTPException(status_code=404, detail="document not found")

    def _load() -> tuple[bytes, str] | None:
        evidence_store = get_evidence_store()
        found = evidence_store.read_artifact(tenant, document.evidence_id)
        if found is None:
            return None
        raw, record = found
        payload = record.get("structured_payload") or {}
        return raw, str(payload.get("filename", "document"))

    loaded = await asyncio.to_thread(_load)
    if loaded is None:
        # Includes the integrity-failure case, which read_artifact has already
        # wired. Refusing to serve bytes that failed verification is the point
        # — serving them while reporting success would destroy the property
        # the append-only store exists to provide.
        raise HTTPException(
            status_code=404,
            detail={"code": "artifact_unavailable",
                    "message": "The stored copy of this document could not be "
                               "produced. It is recorded on the file, but the "
                               "retained bytes are missing or failed integrity "
                               "verification."})
    raw, filename = loaded

    if document.encrypted:
        case_key = await asyncio.to_thread(
            store.get_or_create_case_key, tenant, case_id)
        try:
            raw = await asyncio.to_thread(decrypt, raw, case_key)
        except Exception as exc:  # noqa: BLE001 — never leak crypto detail
            # A case whose key was destroyed at disposal lands here. That is
            # erasure working exactly as designed (R-F3155), so it is a plain
            # 404, not a server error.
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_unavailable",
                        "message": "The stored copy could not be decrypted. If "
                                   "this case was disposed of, its key was "
                                   "destroyed and the document is "
                                   "irrecoverable by design."}) from exc

    media, disposition = _disposition_for(filename)
    safe_name = "".join(
        c for c in filename if c.isalnum() or c in "._- ").strip() or "document"
    wire_success(
        module=_MODULE,
        summary=(f"vetting document opened ({document.doc_type.value}) "
                 f"on case {case_id}"))
    return Response(
        content=raw,
        media_type=media,
        headers={
            "Content-Disposition": f'{disposition}; filename="{safe_name}"',
            # Never let this sit in a shared cache or a browser's disk cache
            # any longer than the tab that asked for it.
            "Cache-Control": "no-store, private, max-age=0",
            "X-Content-Type-Options": "nosniff",
            # Belt and braces with _INLINE_TYPES: even if a document somehow
            # renders, it may not fetch, script or frame anything.
            "Content-Security-Policy":
                "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox",
        },
    )


# ── R-F3211: requirements the officer adds or waives ──────────────────────
class AddRequirementRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=160)
    accepted: list[str] = Field(min_length=1)
    min_count: int = Field(default=1, ge=1, le=20)
    reference: str = ""
    basis: str = "CLIENT_CONTRACT"
    stage: str = "APPLICATION"
    mandatory: bool = True
    note: str = ""


class WaiveRequirementRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    waived_by: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/case/{case_id}/requirements")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_add_requirement_ep(
    case_id: str, body: AddRequirementRequest, user_id: str = "",
):
    """Add a document this particular screening needs.

    Defaults to CLIENT_CONTRACT rather than STANDARD: a requirement added by
    hand is, by definition, not one the framework demanded, and letting the UI
    label it as the standard's would put a clause reference behind a house
    decision. An officer can still say otherwise explicitly.
    """
    import asyncio

    from ..vetting.models import DocumentRequirement, DocumentType

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    try:
        accepted = [DocumentType(t.strip().upper()) for t in body.accepted]
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "unknown_document_type",
                    "accepted": [t.value for t in DocumentType]}) from exc
    try:
        requirement = DocumentRequirement(
            key=body.key.strip().lower().replace(" ", "_"),
            label=body.label.strip(), accepted=accepted,
            min_count=body.min_count, reference=body.reference.strip(),
            basis=body.basis, stage=body.stage.strip().upper() or "APPLICATION",
            mandatory=body.mandatory, note=body.note.strip(), origin="MANUAL",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_requirement", "errors": [str(exc)]}) from exc

    others = [r for r in case.extra_requirements if r.key != requirement.key]
    await asyncio.to_thread(
        store.save,
        case.model_copy(update={"extra_requirements": [*others, requirement]}))
    wire_success(module=_MODULE,
                 summary=f"vetting requirement added ({requirement.key})")
    return {"case_id": case_id, "requirement": requirement.model_dump(mode="json")}


@router.delete("/case/{case_id}/requirements/{key}")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_remove_requirement_ep(
    case_id: str, key: str, user_id: str = "",
):
    """Remove a MANUAL requirement. A pack requirement is waived, not deleted.

    The distinction is the audit trail: deleting something the framework asked
    for would leave no trace that it was ever asked, which is precisely the
    silence a waiver exists to prevent.
    """
    import asyncio

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    remaining = [r for r in case.extra_requirements if r.key != key]
    if len(remaining) == len(case.extra_requirements):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_a_manual_requirement",
                    "message": "No manually added requirement with that key. A "
                               "requirement that comes from the rule pack is "
                               "waived with a recorded reason, not deleted."})
    await asyncio.to_thread(
        store.save, case.model_copy(update={"extra_requirements": remaining}))
    wire_success(module=_MODULE, summary=f"vetting requirement removed ({key})")
    return {"case_id": case_id, "removed": key}


@router.post("/case/{case_id}/requirements/waive")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_waive_requirement_ep(
    case_id: str, body: WaiveRequirementRequest, user_id: str = "",
):
    """Stop pursuing a requirement, on the record.

    Name and reason are mandatory at the type level, so a waiver cannot be
    anonymous. The waived requirement renders as WAIVED with that name beside
    it and is reported as an INFO finding on every assessment — it never reads
    as satisfied. A file that looks complete because someone quietly stopped
    asking is the failure this shape exists to prevent.
    """
    import asyncio

    from ..vetting.models import RequirementWaiver

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")

    waiver = RequirementWaiver(
        key=body.key.strip(), waived_by=body.waived_by.strip(),
        reason=body.reason.strip(), waived_at=datetime.now(UTC).date())
    others = [w for w in case.requirement_waivers if w.key != waiver.key]
    await asyncio.to_thread(
        store.save,
        case.model_copy(update={"requirement_waivers": [*others, waiver]}))
    wire_success(module=_MODULE,
                 summary=f"vetting requirement waived ({waiver.key})")
    return {"case_id": case_id, "waiver": waiver.model_dump(mode="json")}


@router.delete("/case/{case_id}/requirements/waive/{key}")
@fail_wire(module=_MODULE, gap_type="engine_failure")
async def vetting_unwaive_requirement_ep(
    case_id: str, key: str, user_id: str = "",
):
    """Undo a waiver — the requirement returns to whatever state the file
    actually supports, which is usually OUTSTANDING."""
    import asyncio

    tenant = _tenant(user_id)
    store = get_case_store()
    case = await asyncio.to_thread(store.get, tenant, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    remaining = [w for w in case.requirement_waivers if w.key != key]
    if len(remaining) == len(case.requirement_waivers):
        raise HTTPException(status_code=404, detail="no waiver for that key")
    await asyncio.to_thread(
        store.save, case.model_copy(update={"requirement_waivers": remaining}))
    wire_success(module=_MODULE, summary=f"vetting waiver withdrawn ({key})")
    return {"case_id": case_id, "unwaived": key}


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
    # R-F4258 (C-223) — AN OVERDUE FILE IS A BREACH, NOT A SUCCESS.
    #
    # This reported every review through `wire_success`, including reviews that
    # found files past their lawful disposal date. The count was in the summary
    # STRING, but the signal TYPE was positive — so `capability_gaps`, the
    # self-heal loop and every operator surface that reads failures saw nothing.
    # A number published where no verdict consumes it is the C-96 defect, here
    # sitting on a UK GDPR / BS7858 retention obligation.
    #
    # The review SUCCEEDING and the review FINDING something are different
    # facts. A clean review is still a success — silence is the correct answer
    # when nothing is overdue, and paging on every look is how an alert becomes
    # background noise. Flood control is `record_gap`'s existing dedupe, not a
    # new latch here: this endpoint is view-triggered and could be polled.
    if overdue:
        wire_failure(
            module=_MODULE,
            detail=(
                f"{len(overdue)} vetting case(s) are PAST their retention due "
                f"date and have not been disposed: "
                f"{', '.join(r['case_id'] for r in overdue[:10])}"
                f"{' …' if len(overdue) > 10 else ''}. Personal data held beyond "
                f"its lawful retention period is a breach, not a backlog. "
                f"OPERATOR ACTION: POST /api/aria/vetting/case/{{case_id}}/dispose "
                f"for each. Disposal is MANUAL — nothing schedules it."
            )[:600],
            gap_type="data_protection_violation",
            source="vetting_retention:overdue",
        )
    else:
        wire_success(module=_MODULE,
                     summary=f"retention reviewed: 0 overdue of {len(rows)}")
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

"""R-F3136/R-F3137/R-F3138 — vetting engine, tenant store, HTTP surface.

Part 1 ports all 13 checks from the module's own `tests_phase0.py` so the
vendored engine's guarantees are enforced by OUR suite rather than by a
loose script that nothing runs. Part 2 covers what vendoring added:
import-time double-registration safety (R-F3136) and the tenant boundary
(R-F3137). Part 3 is the capability test for the HTTP surface (R-F3138) —
it drives the real routes, not the service layer underneath them.
"""

from __future__ import annotations

import importlib
from datetime import date

import pytest

from aria_service.vetting import packs as _packs_pkg  # noqa: F401
from aria_service.vetting.models import (
    CareerEntry,
    CareerEntryType,
    DocumentType,
    FinancialFlags,
    Money,
    ScreeningInputs,
    VerificationState,
    VettingCase,
)
from aria_service.vetting.packs import builtin as B
from aria_service.vetting.packs.base import (
    DuplicatePackVersion,
    PackIntegrityError,
    PackNotUsable,
    registry,
)
from aria_service.vetting.service import AssessmentService
from aria_service.vetting.store import CaseNotFound, VettingCaseStore

AS_OF = date(2026, 7, 26)
TENANT = "tenant-a"
OTHER_TENANT = "tenant-b"

_ALL_INPUT_FIELDS = [
    "full_name", "previous_names_declared", "address_history_5y",
    "date_of_birth", "ni_number", "right_to_work_evidenced",
    "convictions_declared", "financial_history_declared",
    "misrepresentation_ack_signed", "verification_authorisation_signed",
    "screening_consent_signed", "interview_done", "identity_verified",
    "address_confirmed", "watchlist_check_done", "public_record_search_done",
]
INPUTS = ScreeningInputs(**{f: True for f in _ALL_INPUT_FIELDS})


@pytest.fixture()
def store(tmp_path):
    """A per-test sqlite file — never the shared /data database."""
    return VettingCaseStore(db_path=tmp_path / "vetting_test.db")


@pytest.fixture()
def svc(store):
    return AssessmentService(store, registry)


def _messy_case(case_id: str = "VET-P0", tenant: str = TENANT) -> VettingCase:
    """The Phase 0 fixture: leap-day DOB, overlap, duplicate, future entry."""
    return VettingCase(
        tenant_id=tenant, case_id=case_id, applicant_name="T",
        date_of_birth=date(1996, 2, 29),           # leap-day DOB
        employment_start=date(2026, 6, 1), screening_years=5,
        conditional_employment_start=date(2026, 6, 1), inputs=INPUTS,
        career=[
            CareerEntry(entry_id="e1", entry_type=CareerEntryType.EMPLOYMENT,
                        start=date(2021, 6, 1), end=date(2023, 12, 31),
                        organisation="Alpha",
                        state=VerificationState.VERIFIED),
            CareerEntry(entry_id="e2", entry_type=CareerEntryType.EMPLOYMENT,
                        start=date(2024, 3, 16), end=None,
                        organisation="Beta"),
            CareerEntry(entry_id="e3", entry_type=CareerEntryType.EMPLOYMENT,
                        start=date(2023, 6, 1), end=date(2023, 12, 31),
                        organisation="Gamma"),          # overlaps Alpha
            CareerEntry(entry_id="e3b", entry_type=CareerEntryType.EMPLOYMENT,
                        start=date(2023, 6, 1), end=date(2023, 12, 31),
                        organisation="Gamma"),          # duplicate
            CareerEntry(entry_id="e4", entry_type=CareerEntryType.EMPLOYMENT,
                        start=date(2027, 1, 1),
                        organisation="Future Co"),      # future-dated
        ],
        financial=FinancialFlags(
            ccj_total=Money(amount_minor=1_250_000, currency="GBP")),
    )


# ── Part 1: the ported Phase 0 guarantees ────────────────────────────────

def test_manifest_pinned_with_hash(store, svc):
    case = svc.create_case(_messy_case(), "uk_bs7858")
    assert case.manifest is not None
    assert len(case.manifest.pack_hash) == 64


def test_engine_findings_and_period(svc):
    svc.create_case(_messy_case(), "uk_bs7858")
    result = svc.assess(TENANT, "VET-P0", as_of=AS_OF)
    codes = {f["code"] for f in result["findings"]}
    # Leap-day DOB: 1996-02-29 + 16y must not crash; period floors at
    # employment_start - 5y because that is later than the 16th birthday.
    assert result["screening_period"][0] == "2021-06-01"
    assert "FUTURE_DATED_HISTORY" in codes
    assert "OVERLAPPING_DECLARATIONS" in codes
    assert "DUPLICATE_ENTRY" in codes
    assert "SIGNOFF_CCJ" in codes


def test_replay_is_deterministic(svc):
    svc.create_case(_messy_case(), "uk_bs7858")
    first = svc.assess(TENANT, "VET-P0", as_of=AS_OF)
    second = svc.assess(TENANT, "VET-P0", as_of=AS_OF)
    assert first == second


def test_currency_mismatch_never_silently_converts(svc):
    svc.create_case(VettingCase(
        tenant_id=TENANT, case_id="VET-EUR", applicant_name="T",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 6, 1),
        inputs=INPUTS,
        financial=FinancialFlags(
            ccj_total=Money(amount_minor=2_000_000, currency="EUR")),
    ), "uk_bs7858")
    result = svc.assess(TENANT, "VET-EUR", as_of=AS_OF)
    assert "FINANCIAL_CURRENCY_REVIEW" in {f["code"] for f in result["findings"]}


def test_duplicate_pack_version_rejected():
    with pytest.raises(DuplicatePackVersion):
        registry.register(B.UK_BS7858)


def test_pack_hash_tamper_refused():
    with pytest.raises(PackIntegrityError):
        registry.get_exact("uk_bs7858", "1.1.0", "0" * 64)


def test_draft_pack_refused_for_new_cases(svc):
    with pytest.raises(PackNotUsable):
        svc.create_case(VettingCase(
            tenant_id=TENANT, case_id="X", applicant_name="T",
            date_of_birth=date(1990, 1, 1),
            employment_start=date(2026, 6, 1),
        ), "pt_generic")


def test_framework_pack_never_issues_decision_readiness(svc):
    clean = VettingCase(
        tenant_id=TENANT, case_id="VET-CLEAN", applicant_name="T",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 6, 1),
        inputs=INPUTS.model_copy(
            update={"criminality_route": B.D.DISCLOSURE_CERTIFICATE}),
        career=[CareerEntry(
            entry_id="c1", entry_type=CareerEntryType.EMPLOYMENT,
            start=date(2021, 6, 1), end=None, organisation="Solo",
            state=VerificationState.VERIFIED)],
    )
    svc.create_case(clean, "intl_baseline")
    result = svc.assess(TENANT, "VET-CLEAN", as_of=AS_OF)
    # A FRAMEWORK_ONLY pack organises evidence; it must never claim a
    # jurisdiction's decision-readiness on a person.
    assert result["status"] == "EVIDENCE_COMPLETE"
    assert result["pack"]["employment_decision_eligible"] is False


def test_impossible_date_range_rejected_at_construction():
    with pytest.raises(ValueError):
        CareerEntry(entry_id="bad", entry_type=CareerEntryType.EMPLOYMENT,
                    start=date(2024, 1, 1), end=date(2023, 1, 1))


# ── Part 2: what vendoring added ─────────────────────────────────────────

def test_rf3136_builtin_packs_survive_a_double_import():
    """The bootstrap loop runs at import time; a second import must not raise.

    Before R-F3136 this used DuplicatePackVersion-raising `register()`, so a
    module reachable under two sys.modules keys would raise DURING IMPORT and
    take the boot down (the §9 failure class).
    """
    importlib.reload(B)
    assert any(p["pack_id"] == "uk_bs7858" for p in registry.list_packs())


def test_rf3136_unknown_pack_is_a_clean_error_not_a_keyerror():
    """An unregistered (pack_id, version) must not escape as a bare KeyError,
    which the HTTP layer can only render as an opaque 500."""
    with pytest.raises(PackNotUsable):
        registry.get_exact("no_such_pack", "9.9.9", "0" * 64)


def test_rf3137_tenant_id_is_required():
    with pytest.raises(ValueError):
        VettingCase(
            tenant_id="", case_id="VET-NO-TENANT", applicant_name="T",
            date_of_birth=date(1990, 1, 1),
            employment_start=date(2026, 6, 1),
        )


def test_rf3137_other_tenant_cannot_read_the_case(store, svc):
    svc.create_case(_messy_case(tenant=TENANT), "uk_bs7858")
    assert store.get(TENANT, "VET-P0") is not None
    # Fail-closed: absent, not forbidden. A 403 would confirm that this named
    # person is under screening by this employer — itself a disclosure.
    assert store.get(OTHER_TENANT, "VET-P0") is None


def test_rf3137_other_tenant_cannot_assess_the_case(svc):
    svc.create_case(_messy_case(tenant=TENANT), "uk_bs7858")
    with pytest.raises(CaseNotFound):
        svc.assess(OTHER_TENANT, "VET-P0", as_of=AS_OF)


def test_rf3137_other_tenant_cannot_overwrite_the_case(store, svc):
    svc.create_case(_messy_case(tenant=TENANT), "uk_bs7858")
    forged = _messy_case(tenant=OTHER_TENANT)
    forged = forged.model_copy(update={"applicant_name": "OVERWRITTEN"})
    with pytest.raises(CaseNotFound):
        store.save(forged)
    assert store.get(TENANT, "VET-P0").applicant_name == "T"


def test_rf3137_listing_is_scoped_to_the_tenant(store, svc):
    svc.create_case(_messy_case(case_id="A1", tenant=TENANT), "uk_bs7858")
    svc.create_case(_messy_case(case_id="B1", tenant=OTHER_TENANT), "uk_bs7858")
    assert [c["case_id"] for c in store.list_cases(TENANT)] == ["A1"]
    assert [c["case_id"] for c in store.list_cases(OTHER_TENANT)] == ["B1"]


def test_rf3137_blank_tenant_reads_nothing(store, svc):
    """An empty tenant must never act as a wildcard."""
    svc.create_case(_messy_case(tenant=TENANT), "uk_bs7858")
    assert store.get("", "VET-P0") is None
    assert store.list_cases("") == []


def test_rf3137_same_case_id_is_independent_per_tenant(store, svc):
    svc.create_case(_messy_case(tenant=TENANT), "uk_bs7858")
    svc.create_case(_messy_case(tenant=OTHER_TENANT), "uk_bs7858")
    assert store.get(TENANT, "VET-P0") is not None
    assert store.get(OTHER_TENANT, "VET-P0") is not None


# ── Part 3: R-F3138 capability tests — the REAL routes ───────────────────
#
# These drive the actual router object that main.py mounts, over HTTP, with
# the real auth dependency — not the service layer underneath it. §3c: a test
# that exercises a helper proves nothing about the surface a caller reaches.

TOKEN = "vetting-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from aria_service.routes.vetting import router as vetting_router
    from aria_service.vetting import store as store_module

    monkeypatch.setenv("ARIA_API_TOKEN", TOKEN)
    monkeypatch.setenv("ARIA_VETTING_DB", str(tmp_path / "routes_test.db"))
    # The store is a process singleton; reset it so this test gets the temp DB
    # and never touches /data.
    monkeypatch.setattr(store_module, "_STORE", None, raising=False)

    app = FastAPI()
    app.include_router(vetting_router)
    return TestClient(app)


def _create_payload(case_id: str = "API-1") -> dict:
    return {
        "case_id": case_id,
        "applicant_name": "Test Applicant",
        "date_of_birth": "1990-01-01",
        "employment_start": "2026-06-01",
        "pack_id": "uk_bs7858",
    }


def test_rf3138_create_then_assess_over_http(client):
    created = client.post("/api/aria/vetting/cases",
                          json=_create_payload(), params={"user_id": TENANT},
                          headers=AUTH)
    assert created.status_code == 200, created.text
    assert len(created.json()["manifest"]["pack_hash"]) == 64

    assessed = client.post("/api/aria/vetting/case/API-1/assess",
                           params={"user_id": TENANT, "as_of": "2026-07-26"},
                           headers=AUTH)
    assert assessed.status_code == 200, assessed.text
    body = assessed.json()
    # An empty case is NOT_READY / IN_PROGRESS — never a clean readiness claim.
    assert body["status"] in {"NOT_READY", "IN_PROGRESS", "AWAITING_SIGNOFF"}
    assert body["pack"]["pack_id"] == "uk_bs7858"
    assert body["as_of"] == "2026-07-26"


def test_rf3138_assess_is_replayable_over_http(client):
    client.post("/api/aria/vetting/cases", json=_create_payload("API-R"),
                params={"user_id": TENANT}, headers=AUTH)
    first = client.post("/api/aria/vetting/case/API-R/assess",
                        params={"user_id": TENANT, "as_of": "2026-07-26"},
                        headers=AUTH).json()
    second = client.post("/api/aria/vetting/case/API-R/assess",
                         params={"user_id": TENANT, "as_of": "2026-07-26"},
                         headers=AUTH).json()
    assert first == second


def test_rf3138_cross_tenant_read_is_404_over_http(client):
    """THE capability test for this module.

    Tenant B asks for tenant A's case by its exact id and must be told it does
    not exist — not 403, which would confirm that a named person is under
    screening by a named employer.
    """
    client.post("/api/aria/vetting/cases", json=_create_payload("API-SECRET"),
                params={"user_id": TENANT}, headers=AUTH)

    for path, method in (
        ("/api/aria/vetting/case/API-SECRET", "get"),
        ("/api/aria/vetting/case/API-SECRET/assess", "post"),
        ("/api/aria/vetting/case/API-SECRET", "delete"),
    ):
        response = getattr(client, method)(
            path, params={"user_id": OTHER_TENANT}, headers=AUTH)
        assert response.status_code == 404, (
            f"{method.upper()} {path} leaked to another tenant: "
            f"{response.status_code} {response.text}"
        )

    listed = client.get("/api/aria/vetting/cases",
                        params={"user_id": OTHER_TENANT}, headers=AUTH)
    assert listed.json()["cases"] == []


def test_rf3138_blank_tenant_is_refused_not_treated_as_wildcard(client):
    """Unlike /dd/reports, an empty user_id here must never mean 'see all'."""
    client.post("/api/aria/vetting/cases", json=_create_payload("API-X"),
                params={"user_id": TENANT}, headers=AUTH)
    for path in ("/api/aria/vetting/cases", "/api/aria/vetting/case/API-X"):
        response = client.get(path, headers=AUTH)
        assert response.status_code == 400, (
            f"{path} with no tenant returned {response.status_code}; a blank "
            f"tenant must be refused, never treated as a wildcard"
        )


def test_rf3138_client_cannot_choose_its_own_tenant(client):
    """A forged tenant_id in the BODY must not override the pinned user_id."""
    payload = _create_payload("API-FORGE") | {"tenant_id": OTHER_TENANT}
    created = client.post("/api/aria/vetting/cases", json=payload,
                          params={"user_id": TENANT}, headers=AUTH)
    assert created.status_code == 200, created.text
    # The case must belong to the PINNED tenant, not the one in the body.
    assert client.get("/api/aria/vetting/case/API-FORGE",
                      params={"user_id": TENANT}, headers=AUTH).status_code == 200
    assert client.get("/api/aria/vetting/case/API-FORGE",
                      params={"user_id": OTHER_TENANT},
                      headers=AUTH).status_code == 404


def test_rf3138_draft_pack_refused_over_http(client):
    payload = _create_payload("API-DRAFT") | {"pack_id": "pt_generic"}
    response = client.post("/api/aria/vetting/cases", json=payload,
                           params={"user_id": TENANT}, headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "pack_not_usable"


def test_rf3138_packs_surface_declares_decision_eligibility(client):
    response = client.get("/api/aria/vetting/packs", headers=AUTH)
    assert response.status_code == 200
    by_id = {p["pack_id"]: p for p in response.json()["packs"]}
    # A FRAMEWORK_ONLY pack must advertise that it cannot issue a decision.
    assert by_id["intl_baseline"]["decision_eligible"] is False
    assert by_id["uk_bs7858"]["decision_eligible"] is True


def test_rf3138_bad_as_of_is_422_not_500(client):
    client.post("/api/aria/vetting/cases", json=_create_payload("API-D"),
                params={"user_id": TENANT}, headers=AUTH)
    response = client.post("/api/aria/vetting/case/API-D/assess",
                           params={"user_id": TENANT, "as_of": "not-a-date"},
                           headers=AUTH)
    assert response.status_code == 422


def test_rf3138_duplicate_case_id_is_409(client):
    client.post("/api/aria/vetting/cases", json=_create_payload("API-DUP"),
                params={"user_id": TENANT}, headers=AUTH)
    second = client.post("/api/aria/vetting/cases",
                         json=_create_payload("API-DUP"),
                         params={"user_id": TENANT}, headers=AUTH)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "case_exists"


# ── R-F3174/R-F3175 — verified against the licensed BS 7858:2019 ─────────

def test_rf3174_uk_pack_v120_is_the_default_for_new_cases():
    latest = registry.latest_usable("uk_bs7858")
    assert latest.version == "1.2.0"


def test_rf3175_versions_order_numerically_not_lexically():
    """max() on the raw string picks "1.9.0" over "1.10.0", so the tenth
    revision of a pack would silently never reach new cases while still being
    reported PRODUCTION. Nothing fails; the wrong rules quietly stay in force."""
    from aria_service.vetting.packs.base import PackRegistry
    key = PackRegistry._version_key
    assert key("1.10.0") > key("1.9.0")
    assert key("1.2.0") > key("1.1.0")
    assert max(["1.9.0", "1.10.0"], key=key) == "1.10.0"


def test_rf3174_v110_still_resolves_so_old_cases_replay():
    """A pack's hash is pinned in every manifest, so 1.2.0 had to be a NEW
    version rather than an edit — editing 1.1.0 in place would break get_exact
    for every case already opened under it."""
    old = registry._packs[("uk_bs7858", "1.1.0")]
    resolved = registry.get_exact("uk_bs7858", "1.1.0", old.content_hash())
    assert resolved.version == "1.1.0"
    assert resolved.max_unverified_gap_days == 31


def test_rf3174_the_31_day_limit_is_unchanged():
    """BS 7858 7.7: "no unverified periods greater than 31 days". The engine
    flags at 32+, which is exactly that. Recorded as a test so a future reading
    of "30 days" cannot quietly tighten the standard itself — a stricter house
    limit belongs in a per-contract setting."""
    for pack in (registry._packs[("uk_bs7858", "1.1.0")],
                 registry._packs[("uk_bs7858", "1.2.0")]):
        assert pack.max_unverified_gap_days == 31


def test_rf3174_v120_captures_sia_expiry_and_register_check():
    """7.3.2 a)8) requires the licence EXPIRY, and 7.4 c)1) requires
    verification against the SIA public register — not merely sight of the
    card. The security industry is this module's first sector."""
    pack = registry._packs[("uk_bs7858", "1.2.0")]
    fields = {c.field for c in pack.checklist}
    assert "sia_licence_expiry" in fields
    assert "sia_register_verified" in fields


def test_rf3174_v120_splits_the_seven_public_record_elements():
    """7.4 f) lists seven required elements. One tick let a partially performed
    search read as complete."""
    pack = registry._packs[("uk_bs7858", "1.2.0")]
    fields = {c.field for c in pack.checklist}
    for f in ("electoral_roll_confirmed", "linked_addresses_5y_searched",
              "ccj_iva_searched", "bankruptcy_orders_searched", "aliases_searched"):
        assert f in fields, f


def test_rf3174_two_documentary_items_required_without_a_reference():
    """7.7 b) — without a direct employer reference the fallback is "two or
    more different items". The engine accepted one."""
    from aria_service.vetting.models import UploadedDocument
    from aria_service.vetting.rules import evidence_findings

    pack = registry._packs[("uk_bs7858", "1.2.0")]
    entry = CareerEntry(entry_id="e1", entry_type=CareerEntryType.EMPLOYMENT,
                        start=date(2022, 1, 1), end=date(2023, 1, 1),
                        organisation="Alpha",
                        supporting_documents=["d1"])
    case = VettingCase(
        tenant_id=TENANT, case_id="EV", applicant_name="T",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 6, 1),
        career=[entry],
        documents=[UploadedDocument(document_id="d1", doc_type=DocumentType.PAYSLIP)])
    codes = {f.code for f in evidence_findings(case, pack, AS_OF)}
    assert "EVIDENCE_INSUFFICIENT" in codes, (
        "one payslip and no reference must not satisfy a period")

    # A second, DIFFERENT item satisfies it.
    entry2 = entry.model_copy(update={"supporting_documents": ["d1", "d2"]})
    case2 = case.model_copy(update={
        "career": [entry2],
        "documents": [*case.documents,
                      UploadedDocument(document_id="d2", doc_type=DocumentType.P60)]})
    assert "EVIDENCE_INSUFFICIENT" not in {
        f.code for f in evidence_findings(case2, pack, AS_OF)}


def test_rf3174_a_direct_reference_stands_alone():
    """A reference from the employer is the primary route and needs no second
    item — the two-item rule is the FALLBACK when a reference cannot be got."""
    from aria_service.vetting.models import UploadedDocument
    from aria_service.vetting.rules import evidence_findings

    pack = registry._packs[("uk_bs7858", "1.2.0")]
    case = VettingCase(
        tenant_id=TENANT, case_id="EV2", applicant_name="T",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 6, 1),
        career=[CareerEntry(entry_id="e1", entry_type=CareerEntryType.EMPLOYMENT,
                            start=date(2022, 1, 1), end=date(2023, 1, 1),
                            organisation="Alpha", supporting_documents=["r1"])],
        documents=[UploadedDocument(document_id="r1",
                                    doc_type=DocumentType.EMPLOYER_REFERENCE)])
    assert "EVIDENCE_INSUFFICIENT" not in {
        f.code for f in evidence_findings(case, pack, AS_OF)}

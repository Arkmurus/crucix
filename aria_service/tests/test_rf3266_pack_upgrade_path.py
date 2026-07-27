"""R-F3266 — a case had no way off the pack version it was created on.

The live symptom, on case MG01: "Required documents: none defined". That
message is HONEST — MG01 is pinned to uk_bs7858 v1.1.0, which genuinely
defines no required documents. v1.3.0 defines eight. The pin is deliberate and
correct (an assessment must be replayable under the rules it was made under),
but nothing could ever move a case forward, so every case created before a pack
revision was frozen on the old rules for its whole life with no way to say so.

The fix is NOT to unpin. It is an explicit, recorded migration:

  * forward only, and only within the same pack_id — silently weakening the
    rules a person is screened under is the failure this whole module exists
    to prevent, and a "migration" to an older pack is exactly that;
  * PRODUCTION only, the same bar `create` holds new cases to, so a migration
    cannot route a live case onto a DRAFT pack that no lawyer has read;
  * refused once a decision has been recorded — changing the rules under a
    closed case rewrites the basis of a decision already communicated to a
    person, which is not a data migration, it is a rewrite of history;
  * written to the case as an audit trail, because "which rules governed this
    file, and when did that change?" must be answerable from the file itself;
  * and it invalidates the cached verdict, because that verdict was computed
    under rules that no longer apply.

One more thing this file pins, found while writing it: `store.save()` updates
`case_json` but NOT the `pack_id`/`pack_version` COLUMNS, and `list_cases()`
builds the queue card from those COLUMNS. Migrating through save() alone would
move the rules while every card kept reporting the old version — the
producer/consumer split that has bitten this repo repeatedly.
"""

from __future__ import annotations

from datetime import date

import pytest

from aria_service.vetting.models import VettingCase
from aria_service.vetting.packs import builtin as B  # noqa: F401  (registers)
from aria_service.vetting.packs.base import PackNotUsable, registry
from aria_service.vetting.service import AssessmentService, PackMigrationRefused
from aria_service.vetting.store import VettingCaseStore

TENANT = "tenant-a"
AT = date(2026, 7, 27)


@pytest.fixture()
def store(tmp_path):
    return VettingCaseStore(db_path=tmp_path / "vetting_migrate.db")


@pytest.fixture()
def service(store):
    return AssessmentService(store, registry)


def _case(case_id: str = "MG01") -> VettingCase:
    return VettingCase(
        tenant_id=TENANT, case_id=case_id, applicant_name="Maria Gomes",
        date_of_birth=date(1990, 1, 1), employment_start=date(2026, 1, 1),
    )


def _pin_to(store, case: VettingCase, version: str) -> VettingCase:
    """Put a case on an OLD pack version, the way the live estate got there:
    created when that version was the latest."""
    from aria_service.vetting.models import CaseManifest

    pack = registry._packs[("uk_bs7858", version)]
    pinned = case.model_copy(update={"manifest": CaseManifest(
        pack_id="uk_bs7858", pack_version=version,
        pack_hash=pack.content_hash())})
    # Go through the raw row so create()'s latest_usable pin is bypassed.
    import json

    with store._connect() as conn:
        conn.execute(
            "INSERT INTO vetting_cases (tenant_id, case_id, case_json, pack_id, "
            "pack_version, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (TENANT, pinned.case_id, pinned.model_dump_json(), "uk_bs7858",
             version, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        conn.commit()
    json  # noqa: B018  (imported for symmetry with store internals)
    return pinned


# ── the capability: the live symptom clears ──────────────────────────────

def test_a_case_stuck_on_an_old_pack_can_be_moved_forward(store, service):
    """THE regression, in the live shape: MG01 on v1.1.0 (0 required
    documents) reaching v1.3.0 (8)."""
    _pin_to(store, _case(), "1.1.0")

    old = registry._packs[("uk_bs7858", "1.1.0")]
    new = registry._packs[("uk_bs7858", "1.3.0")]
    assert not (old.required_documents or []), "premise moved: v1.1.0 defined docs"
    assert new.required_documents, "premise moved: v1.3.0 defines no docs"

    result = service.migrate_pack(
        TENANT, "MG01", to_version="1.3.0", migrated_by="officer@acme", at=AT)

    assert result["from_version"] == "1.1.0"
    assert result["to_version"] == "1.3.0"

    moved = store.get(TENANT, "MG01")
    assert moved.manifest.pack_version == "1.3.0"
    assert moved.manifest.pack_hash == new.content_hash()

    # And the thing the officer actually complained about is gone: the case
    # now resolves to a pack that defines requirements.
    pack = registry.get_exact("uk_bs7858", moved.manifest.pack_version,
                              moved.manifest.pack_hash)
    assert pack.required_documents


def test_the_queue_card_reports_the_new_version_not_the_old_one(store, service):
    """list_cases() builds the card from the pack_version COLUMN, not from the
    manifest inside case_json. A migration that updates only the JSON would
    move the rules while every card kept saying v1.1.0."""
    _pin_to(store, _case(), "1.1.0")
    service.migrate_pack(TENANT, "MG01", to_version="1.3.0",
                         migrated_by="officer@acme", at=AT)

    card = next(c for c in store.list_cases(TENANT) if c["case_id"] == "MG01")
    assert card["pack_version"] == "1.3.0", (
        "the queue card still shows the old pack version — the columns and the "
        "manifest have diverged")


def test_the_default_target_is_the_latest_production_pack(store, service):
    _pin_to(store, _case(), "1.1.0")
    result = service.migrate_pack(TENANT, "MG01", migrated_by="o", at=AT)
    assert result["to_version"] == registry.latest_usable("uk_bs7858").version


# ── refusals: what a migration must never do ─────────────────────────────

def test_it_refuses_to_move_a_case_backwards(store, service):
    """Silently weakening the rules a person is screened under is the failure
    this module exists to prevent."""
    _pin_to(store, _case(), "1.3.0")
    with pytest.raises(PackMigrationRefused, match="forward"):
        service.migrate_pack(TENANT, "MG01", to_version="1.1.0",
                             migrated_by="o", at=AT)
    assert store.get(TENANT, "MG01").manifest.pack_version == "1.3.0"


def test_it_refuses_a_no_op_migration_rather_than_reporting_success(store, service):
    """Reporting a successful migration that changed nothing would put a
    misleading entry in the audit trail."""
    _pin_to(store, _case(), "1.3.0")
    with pytest.raises(PackMigrationRefused):
        service.migrate_pack(TENANT, "MG01", to_version="1.3.0",
                             migrated_by="o", at=AT)


def test_it_refuses_a_draft_pack(store, service):
    """`create` holds new cases to PRODUCTION because a DRAFT pack is by
    definition not legally reviewed. A migration must hold the same bar."""
    _pin_to(store, _case(), "1.1.0")
    with pytest.raises((PackMigrationRefused, PackNotUsable)):
        service.migrate_pack(TENANT, "MG01", to_version="0.2.0",
                             migrated_by="o", at=AT, pack_id="pt_generic")


def test_it_refuses_to_cross_between_different_frameworks(store, service):
    """uk_bs7858 -> intl_baseline is not an upgrade; it is a different
    standard, and a case screened under one cannot be retro-labelled as
    screened under the other."""
    _pin_to(store, _case(), "1.1.0")
    with pytest.raises(PackMigrationRefused, match="same"):
        service.migrate_pack(TENANT, "MG01", to_version="1.2.0",
                             migrated_by="o", at=AT, pack_id="intl_baseline")


def test_it_refuses_once_a_decision_has_been_recorded(store, service):
    """Changing the rules under a closed case rewrites the basis of a decision
    already communicated to a person."""
    case = _pin_to(store, _case(), "1.1.0")
    store.save(case.model_copy(update={
        "decisions": [{"decision": "CLEARED", "at": "2026-06-01"}]}))
    with pytest.raises(PackMigrationRefused, match="decision"):
        service.migrate_pack(TENANT, "MG01", to_version="1.3.0",
                             migrated_by="o", at=AT)


def test_it_refuses_across_the_tenant_boundary(store, service):
    from aria_service.vetting.store import CaseNotFound

    _pin_to(store, _case(), "1.1.0")
    with pytest.raises(CaseNotFound):
        service.migrate_pack("tenant-b", "MG01", to_version="1.3.0",
                             migrated_by="o", at=AT)
    assert store.get(TENANT, "MG01").manifest.pack_version == "1.1.0"


def test_it_refuses_without_a_named_migrator(store, service):
    """An unattributed change to the governing rules is not an audit trail."""
    _pin_to(store, _case(), "1.1.0")
    with pytest.raises(PackMigrationRefused):
        service.migrate_pack(TENANT, "MG01", to_version="1.3.0",
                             migrated_by="  ", at=AT)


# ── the record it leaves behind ──────────────────────────────────────────

def test_the_migration_is_written_to_the_case_as_an_audit_trail(store, service):
    """"Which rules governed this file, and when did that change?" has to be
    answerable from the file itself, months later."""
    _pin_to(store, _case(), "1.1.0")
    service.migrate_pack(TENANT, "MG01", to_version="1.3.0",
                         migrated_by="officer@acme", reason="pack revision",
                         at=AT)

    entries = store.get(TENANT, "MG01").pack_migrations
    assert len(entries) == 1
    e = entries[0]
    assert e["from_version"] == "1.1.0" and e["to_version"] == "1.3.0"
    assert e["from_hash"] and e["to_hash"] and e["from_hash"] != e["to_hash"]
    assert e["migrated_by"] == "officer@acme"
    assert e["at"] == AT.isoformat()
    assert e["reason"] == "pack revision"


def test_the_trail_is_append_only_across_successive_migrations(store, service):
    _pin_to(store, _case(), "1.1.0")
    service.migrate_pack(TENANT, "MG01", to_version="1.2.0",
                         migrated_by="o", at=AT)
    service.migrate_pack(TENANT, "MG01", to_version="1.3.0",
                         migrated_by="o", at=AT)
    entries = store.get(TENANT, "MG01").pack_migrations
    assert [(e["from_version"], e["to_version"]) for e in entries] == [
        ("1.1.0", "1.2.0"), ("1.2.0", "1.3.0")]


def test_migrating_invalidates_the_cached_verdict(store, service):
    """The cached status was computed under rules that no longer apply. It must
    not survive the change that invalidated it."""
    case = _pin_to(store, _case(), "1.1.0")
    store.save(case.model_copy(update={
        "last_status": "EVIDENCE_COMPLETE", "last_assessed_at": "2026-06-01",
        "assessment_stale": False}), mark_stale=False)
    assert store.get(TENANT, "MG01").assessment_stale is False

    service.migrate_pack(TENANT, "MG01", to_version="1.3.0",
                         migrated_by="o", at=AT)
    assert store.get(TENANT, "MG01").assessment_stale is True, (
        "a verdict computed under the OLD pack survived the migration")


def test_the_evidence_on_the_file_is_untouched(store, service):
    """A rules migration changes which rules apply. It must not touch what the
    applicant supplied — documents, career, inputs are evidence, not rules."""
    from aria_service.vetting.models import CareerEntry, CareerEntryType

    case = _pin_to(store, _case(), "1.1.0")
    store.save(case.model_copy(update={"career": [CareerEntry(
        entry_id="e1", entry_type=CareerEntryType.EMPLOYMENT,
        start=date(2022, 1, 1), end=date(2024, 1, 1), organisation="Acme")]}))

    service.migrate_pack(TENANT, "MG01", to_version="1.3.0",
                         migrated_by="o", at=AT)
    moved = store.get(TENANT, "MG01")
    assert len(moved.career) == 1
    assert moved.career[0].organisation == "Acme"
    assert moved.applicant_name == "Maria Gomes"


def test_the_case_still_assesses_after_the_move(store, service):
    """The migration is worthless if the case cannot then be assessed — that
    is the exact failure mode R-F3207 shipped (a pinned hash that no longer
    resolved surfaced as a 500)."""
    _pin_to(store, _case(), "1.1.0")
    service.migrate_pack(TENANT, "MG01", to_version="1.3.0",
                         migrated_by="o", at=AT)
    result = service.assess(TENANT, "MG01", AT)
    assert result["status"]
    assert result["pack"]["version"] == "1.3.0"


# ── the HTTP surface (§3c: drive the route the officer's button hits) ────

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from aria_service.routes.vetting import router as vetting_router
    from aria_service.vetting import store as store_module

    monkeypatch.setenv("ARIA_API_TOKEN", TOKEN)
    monkeypatch.setenv("ARIA_VETTING_DB", str(tmp_path / "routes_migrate.db"))
    monkeypatch.setattr(store_module, "_STORE", None, raising=False)

    app = FastAPI()
    app.include_router(vetting_router)
    return TestClient(app)


def _stuck_case(monkeypatch_db_client) -> None:
    """Put a case on v1.1.0 inside the ROUTE's store, the way MG01 got there."""
    from aria_service.vetting.store import get_case_store

    store = get_case_store()
    _pin_to(store, _case("HTTP-1"), "1.1.0")


def test_the_route_moves_a_stuck_case_forward(client):
    _stuck_case(client)

    r = client.post("/api/aria/vetting/case/HTTP-1/pack/migrate",
                    json={"migrated_by": "officer@acme", "to_version": "1.3.0"},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_version"] == "1.1.0" and body["to_version"] == "1.3.0"
    # The caller must be TOLD the verdict is now stale, not left to infer it.
    assert body["assessment_stale"] is True

    listed = client.get("/api/aria/vetting/cases",
                        params={"user_id": TENANT}, headers=AUTH).json()
    card = next(c for c in listed["cases"] if c["case_id"] == "HTTP-1")
    assert card["pack_version"] == "1.3.0"
    assert card["assessment_stale"] is True


def test_a_refused_migration_is_a_409_with_the_reason(client):
    """Each refusal describes a decision the caller has to make. A 500 would
    say "server fault" about a case that is behaving exactly as designed."""
    _stuck_case(client)
    r = client.post("/api/aria/vetting/case/HTTP-1/pack/migrate",
                    json={"migrated_by": "o", "to_version": "1.1.0"},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "pack_migration_refused"
    assert "forward" in r.json()["detail"]["message"]


def test_the_route_is_tenant_scoped(client):
    _stuck_case(client)
    r = client.post("/api/aria/vetting/case/HTTP-1/pack/migrate",
                    json={"migrated_by": "o", "to_version": "1.3.0"},
                    params={"user_id": "someone-else"}, headers=AUTH)
    assert r.status_code == 404


def test_an_unknown_version_is_409_not_500(client):
    _stuck_case(client)
    r = client.post("/api/aria/vetting/case/HTTP-1/pack/migrate",
                    json={"migrated_by": "o", "to_version": "9.9.9"},
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "pack_not_usable"


def test_the_case_assesses_over_http_after_migrating(client):
    """End to end: the officer's actual complaint was that the file reported
    no required documents. After the move the assessment resolves the new pack."""
    _stuck_case(client)
    client.post("/api/aria/vetting/case/HTTP-1/pack/migrate",
                json={"migrated_by": "o"},
                params={"user_id": TENANT}, headers=AUTH)
    r = client.post("/api/aria/vetting/case/HTTP-1/assess",
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["pack"]["version"] == registry.latest_usable("uk_bs7858").version

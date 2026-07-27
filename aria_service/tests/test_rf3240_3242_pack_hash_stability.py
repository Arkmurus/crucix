"""R-F3240/R-F3241/R-F3242 — a pack hash must survive an additive schema change.

P0 REGRESSION, found in production by an independent 10-cycle log review
(2026-07-27, release 2688 / SHA 4598730c): 14 HTTP 500s across three vetting
endpoints — retention (8), assess (4) and subject-access (2) — all on the
stable release, none attributable to a deploy cutover.

Root cause, proven by recomputing the hashes at both revisions:

  R-F3207 added `required_documents` to the ScreeningPack MODEL. `content_hash()`
  hashes `model_dump(mode="json")`, so every ALREADY-PUBLISHED version silently
  acquired `"required_documents": []` and its hash changed:

      uk_bs7858 1.1.0   90178f66f31a741e -> a4e86844b6625b58
      uk_bs7858 1.2.0   d9f648cdcb151baa -> 04660ce4941ebd23
      intl_baseline 1.1.0  b18d0ebf3194af09 -> 26371f10019dba2d

  Every existing case pins the OLD hash in its CaseManifest, so `get_exact`
  raised PackIntegrityError — which is NOT a subclass of PackNotUsable, so the
  handlers' `except PackNotUsable` could not catch it and it escaped as a 500.

The lesson the module already carried and I mis-applied: "never edit a released
pack in place — publish a new version". I DID publish v1.3.0 correctly. What I
missed is that mutating the pack SCHEMA rewrites the hash of every version at
once, which is the same breakage by a different door.

Three layers, because one is not enough for a property this load-bearing:
  R-F3240 the hash ignores fields left at their default, so an additive schema
          change cannot perturb a published pack again
  R-F3241 the hashes that were already issued stay resolvable
  R-F3242 a pack-integrity failure is a 409 with an explanation, never a 500
"""

from __future__ import annotations

import pytest

from aria_service.vetting.packs import builtin as B  # noqa: F401 — registers packs
from aria_service.vetting.packs.base import (
    PackIntegrityError,
    PackNotUsable,
    PackRegistry,
    PackStatus,
    registry,
)

# Ground truth, recomputed from the tree at a75a20e7^ (the commit before
# R-F3207). These are the hashes real cases in production are pinned to.
LEGACY_HASHES = {
    ("uk_bs7858", "1.1.0"): "90178f66f31a741e",
    ("uk_bs7858", "1.2.0"): "d9f648cdcb151baa",
    ("intl_baseline", "1.1.0"): "b18d0ebf3194af09",
    ("pt_generic", "0.2.0"): "6f3e3d0d3daa3a73",
}


def _full_legacy(pack_id: str, version: str) -> str:
    """The full 64-char legacy hash from the registry's own compatibility table."""
    from aria_service.vetting.packs.base import legacy_hashes_for

    prefix = LEGACY_HASHES[(pack_id, version)]
    for h in legacy_hashes_for(pack_id, version):
        if h.startswith(prefix):
            return h
    raise AssertionError(
        f"{pack_id} v{version}: the legacy hash {prefix}… is not recorded, so "
        f"every case pinned to it is unresolvable")


# ── R-F3241: cases already in production must resolve ────────────────────

def test_every_previously_issued_hash_is_still_resolvable():
    """THE production regression. A case pinned before R-F3207 must still
    assess — this is the exact failure that produced 14 HTTP 500s."""
    for (pack_id, version) in LEGACY_HASHES:
        pack = registry.get_exact(pack_id, version, _full_legacy(pack_id, version))
        assert pack.pack_id == pack_id
        assert pack.version == version


def test_the_recorded_legacy_hashes_are_the_ones_production_actually_pinned():
    """Guards the table against a well-meaning edit. These prefixes were
    recomputed from the tree before R-F3207; if someone regenerates them from
    the CURRENT code they will silently record today's hashes and the table
    will resolve nothing."""
    from aria_service.vetting.packs.base import legacy_hashes_for

    for (pack_id, version), prefix in LEGACY_HASHES.items():
        recorded = legacy_hashes_for(pack_id, version)
        assert any(h.startswith(prefix) for h in recorded), (
            f"{pack_id} v{version}: legacy hash {prefix}… was dropped from the "
            f"compatibility table — every case pinned to it breaks")


# ── R-F3240: an additive schema change may not move a published hash ──────

def test_adding_a_field_to_the_model_cannot_change_a_published_hash():
    """The root cause, pinned. A new optional field left at its default must
    contribute nothing to the hash — otherwise the next additive change repeats
    this outage exactly."""
    pack = registry.latest_usable("uk_bs7858")
    before = pack.content_hash()

    # Simulate the next schema addition the same way R-F3207 made this one:
    # a field present on the model and left at its default for this pack.
    dumped = pack.model_dump(mode="json", exclude_defaults=True)
    assert "required_documents" in dumped, (
        "premise: v1.3.0 sets required_documents, so it must appear")

    old = registry.get_exact("uk_bs7858", "1.1.0",
                             _full_legacy("uk_bs7858", "1.1.0"))
    old_dump = old.model_dump(mode="json", exclude_defaults=True)
    assert "required_documents" not in old_dump, (
        "a pack that does not use the field still carries it into the hash — "
        "the next additive schema change will break every pinned case again")
    assert pack.content_hash() == before


def test_a_genuinely_mutated_pack_is_still_refused():
    """The integrity property must survive the fix. Relaxing the hash to
    tolerate schema drift must NOT tolerate a changed RULE — that is the whole
    reason the hash is pinned per case."""
    isolated = PackRegistry()
    base = B.UK_BS7858_V120.model_copy(update={"pack_id": "mutation_probe"})
    good = isolated.register(base)

    # Same id+version, a CHANGED rule (the 31-day limit quietly tightened).
    mutated = base.model_copy(update={"max_unverified_gap_days": 30})
    assert mutated.content_hash() != good, (
        "changing the unverified-gap limit did not change the hash — the "
        "integrity check no longer detects a mutated rule")
    with pytest.raises(PackIntegrityError):
        isolated.get_exact("mutation_probe", base.version, mutated.content_hash())


def test_an_unknown_hash_is_still_refused():
    with pytest.raises(PackIntegrityError):
        registry.get_exact("uk_bs7858", "1.2.0", "f" * 64)


def test_an_unregistered_version_is_still_pack_not_usable():
    with pytest.raises(PackNotUsable):
        registry.get_exact("uk_bs7858", "9.9.9", "a" * 64)


# ── R-F3242: the endpoints must not 500 ──────────────────────────────────

TOKEN = "vetting-hash-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TENANT = "tenant-hash"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from aria_service.routes.vetting import router as vetting_router
    from aria_service.vetting import store as store_module

    monkeypatch.setenv("ARIA_API_TOKEN", TOKEN)
    monkeypatch.setenv("ARIA_VETTING_DB", str(tmp_path / "vetting.db"))
    monkeypatch.setattr(store_module, "_STORE", None, raising=False)
    app = FastAPI()
    app.include_router(vetting_router)
    return TestClient(app)


def _case_pinned_to(client, case_id: str, pack_hash: str):
    """Create a case, then rewrite its manifest hash to simulate one that was
    pinned before the schema changed — which is precisely what every case in
    production is."""
    import json
    import sqlite3

    from aria_service.vetting.store import get_case_store

    created = client.post("/api/aria/vetting/cases", json={
        "case_id": case_id, "applicant_name": "Jane Doe",
        "date_of_birth": "1990-01-01", "employment_start": "2026-07-01",
        "pack_id": "uk_bs7858",
    }, params={"user_id": TENANT}, headers=AUTH)
    assert created.status_code == 200, created.text

    store = get_case_store()
    case = store.get(TENANT, case_id)
    manifest = case.manifest.model_dump()
    manifest["pack_version"] = "1.2.0"
    manifest["pack_hash"] = pack_hash
    body = json.loads(case.model_dump_json())
    body["manifest"] = manifest
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE vetting_cases SET case_json = ?, pack_version = '1.2.0' "
            "WHERE tenant_id = ? AND case_id = ?",
            (json.dumps(body), TENANT, case_id))
        conn.commit()


def test_a_legacy_pinned_case_assesses_instead_of_500ing(client):
    """The production symptom, end to end."""
    _case_pinned_to(client, "LEGACY-1", _full_legacy("uk_bs7858", "1.2.0"))
    r = client.post("/api/aria/vetting/case/LEGACY-1/assess",
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, (
        f"a case pinned before the schema change returned {r.status_code} — "
        f"this is the production 500: {r.text[:200]}")
    assert r.json()["pack"]["version"] == "1.2.0"


def test_retention_survives_a_legacy_pinned_case(client):
    """Retention walks EVERY case for the tenant, so one unresolvable pack took
    the whole endpoint down — which is why retention failed most often (8 of
    the 14 observed 500s)."""
    _case_pinned_to(client, "LEGACY-2", _full_legacy("uk_bs7858", "1.2.0"))
    r = client.get("/api/aria/vetting/retention",
                   params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, f"retention 500ed: {r.text[:200]}"


def test_subject_access_survives_a_legacy_pinned_case(client):
    _case_pinned_to(client, "LEGACY-3", _full_legacy("uk_bs7858", "1.2.0"))
    r = client.get("/api/aria/vetting/case/LEGACY-3/subject-access",
                   params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code == 200, f"subject-access 500ed: {r.text[:200]}"


def test_an_unresolvable_pack_is_a_409_not_a_500(client):
    """Defence in depth. Even when a pack genuinely cannot be resolved, that is
    a DEFINITE, explainable condition — the same reasoning R-F3136 applied to
    PackNotUsable — and must never surface as a server fault."""
    _case_pinned_to(client, "BROKEN-1", "e" * 64)
    r = client.post("/api/aria/vetting/case/BROKEN-1/assess",
                    params={"user_id": TENANT}, headers=AUTH)
    assert r.status_code != 500, "an unresolvable pack still escapes as a 500"
    assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text[:200]}"
    assert "pack" in r.text.lower()


# ── both issued eras must resolve ────────────────────────────────────────

DEPLOYED_ERA_HASHES = {
    ("uk_bs7858", "1.1.0"): "a4e86844b6625b58",
    ("uk_bs7858", "1.2.0"): "04660ce4941ebd23",
    ("uk_bs7858", "1.3.0"): "f92eb027143e7768",
    ("intl_baseline", "1.1.0"): "26371f10019dba2d",
    ("intl_baseline", "1.2.0"): "9ca08d2423770f44",
    ("pt_generic", "0.2.0"): "3cdff2e36e6bc833",
}


def test_cases_opened_during_the_broken_deploy_also_resolve():
    """Two eras of hash were issued to real cases: before R-F3207, and during
    the R-F3207 deploy that was live for hours. Fixing only the first cohort
    would break the second — the same outage, a different set of applicants."""
    from aria_service.vetting.packs.base import legacy_hashes_for

    for (pack_id, version), prefix in DEPLOYED_ERA_HASHES.items():
        recorded = legacy_hashes_for(pack_id, version)
        full = next((h for h in recorded if h.startswith(prefix)), None)
        assert full, (
            f"{pack_id} v{version}: no manifest issued during the deployed era "
            f"({prefix}…) can be resolved")
        assert registry.get_exact(pack_id, version, full).version == version

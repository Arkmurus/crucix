"""R-F2697 — tenant-scope the DD relationship graph BEFORE its writer is wired.

DD Grade-A Phase 1, gap #2, step 1 of 2.

THE LANDMINE (found by a read-only map, verified at the code before touching it):
`get_related_cases` (dd_vault.py) called `self.get_case(rid)` for EVERY entity linked
by a cross-reference, with NO ownership check, and the route returned those FULL case
dicts — risk_level, risk_score, and since R-F2683 evidence_grade. `get_cross_references`
likewise returned every tenant's rows, each carrying the other party's
`canonical_entity_id` + `finding_summary` (i.e. which entities another tenant
investigated, and what they concluded). That is the R-F2401/2402/2456/2458 cross-tenant
class, dormant ONLY because nothing writes the table in prod.

WHY THIS SHIPS FIRST: gap #2's job is to wire the writer. Doing that first would have
filled a leaky table — the graph would be briefly, genuinely leaky in prod. The ACL
lands first, and `user_id` is added now precisely BECAUSE the table is empty: there is
nothing to backfill, and attribution ("whose run found this?") cannot be reconstructed
after the fact.

FAIL-CLOSED CONTRACT (mirrors `_dd_owned_entity_ids`):
  user_id / visible_entity_ids = None  -> internal/service caller, unrestricted
  a real tenant                        -> only rows their own runs discovered
  an EMPTY owned-set                   -> [] (a real answer, not "unrestricted")
  NULL user_id row (legacy/unattributed) -> internal only, never a tenant
"""
import time

import pytest

from aria_service.intel import dd_vault as _dv


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """A real DDVault on a temp DB — drives the actual SQL, not a mock."""
    db = tmp_path / "dd_vault_test.db"
    monkeypatch.setattr(_dv, "_vault_instance", None, raising=False)
    v = _dv.DDVault(db_path=str(db))
    yield v
    v.close()


def _seed(v):
    """Tenant A investigated acme→beta. Tenant B investigated gamma→delta."""
    v.add_cross_reference("company:UK:acme", "company:UK:beta", "shared_director",
                          "Jane Roe sits on both boards", user_id="tenant_a")
    v.add_cross_reference("company:UK:gamma", "company:UK:delta", "parent_of",
                          "gamma owns 100% of delta", user_id="tenant_b")


# ── 1. The schema migration (R-F2683's pattern) ──────────────────────────────

def test_user_id_column_exists_after_migration(vault):
    cols = {r["name"] for r in vault._get_conn().execute(
        "PRAGMA table_info(dd_cross_references)")}
    assert "user_id" in cols, "the ACL column must exist on fresh AND migrated DBs"


def test_migration_is_idempotent_on_an_existing_db(tmp_path):
    """A re-open must not re-ALTER (R-F2683's PRAGMA guard)."""
    db = str(tmp_path / "v.db")
    v1 = _dv.DDVault(db_path=db); v1.close()
    v2 = _dv.DDVault(db_path=db)          # would raise "duplicate column" if not guarded
    cols = {r["name"] for r in v2._get_conn().execute(
        "PRAGMA table_info(dd_cross_references)")}
    v2.close()
    assert "user_id" in cols


def _legacy_db(path) -> None:
    """A PROD-SHAPED vault: pre-R-F2697 table — no user_id, 3-column UNIQUE key."""
    import sqlite3
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE dd_cross_references (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity   TEXT NOT NULL,
            target_entity   TEXT NOT NULL,
            relationship    TEXT NOT NULL,
            finding_summary TEXT DEFAULT '',
            discovered_at   REAL NOT NULL,
            UNIQUE(source_entity, target_entity, relationship)
        );
        INSERT INTO dd_cross_references
            (source_entity, target_entity, relationship, finding_summary, discovered_at)
        VALUES ('company:UK:legacy','company:UK:old','mentions','pre-existing edge', 1.0);
    """)
    c.commit(); c.close()


def test_migration_runs_the_REAL_alter_path_on_a_legacy_db(tmp_path):
    """The path PROD actually takes — Pass 2 caught that my other two tests never hit it.

    They built the DB from `_CREATE_SQL`, which ALREADY has user_id, so `_migrate()`
    found the column present and skipped the ALTER entirely. Only a table WITHOUT the
    column exercises what /data/dd_vault.db will do on boot.
    """
    db = str(tmp_path / "legacy.db")
    _legacy_db(db)
    v = _dv.DDVault(db_path=db)          # must ALTER + rebuild, not raise
    conn = v._get_conn()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(dd_cross_references)")}
    assert "user_id" in cols, "the ALTER must reach an existing prod table"
    # §7 — the pre-existing row must SURVIVE the rebuild, unattributed.
    rows = conn.execute("SELECT * FROM dd_cross_references").fetchall()
    v.close()
    assert len(rows) == 1 and rows[0]["source_entity"] == "company:UK:legacy"
    assert rows[0]["user_id"] is None, "a legacy edge has no discoverer — NULL, not a guess"


def test_unique_key_includes_user_id_so_tenants_cannot_clobber(tmp_path):
    """R-F2697 Pass-2 finding D: the DATA-LOSS bug, pinned.

    `add_cross_reference` uses INSERT OR REPLACE. With the old 3-column UNIQUE key,
    tenant B writing the same (source,target,relationship) DELETED tenant A's row and
    its finding_summary — silent cross-tenant data loss (§7), and the EXPECTED case the
    moment two tenants DD the same company pair.
    """
    db = str(tmp_path / "legacy.db")
    _legacy_db(db)                        # start from the OLD 3-column key
    v = _dv.DDVault(db_path=db)           # the rebuild must widen the key

    v.add_cross_reference("company:UK:acme", "company:UK:beta", "shared_director",
                          "A's finding: Jane Roe on both boards", user_id="tenant_a")
    v.add_cross_reference("company:UK:acme", "company:UK:beta", "shared_director",
                          "B's finding", user_id="tenant_b")

    a = v.get_cross_references("company:UK:acme", user_id="tenant_a")
    b = v.get_cross_references("company:UK:acme", user_id="tenant_b")
    v.close()
    assert len(a) == 1 and a[0]["finding_summary"] == "A's finding: Jane Roe on both boards", (
        "tenant B's write must NOT destroy tenant A's edge"
    )
    assert len(b) == 1 and b[0]["finding_summary"] == "B's finding"


def test_same_tenant_rewrite_still_dedups(tmp_path):
    """The key must widen, not disappear: one tenant re-running DD refreshes its edge."""
    db = str(tmp_path / "v.db")
    v = _dv.DDVault(db_path=db)
    v.add_cross_reference("company:UK:acme", "company:UK:beta", "shared_director",
                          "first pass", user_id="tenant_a")
    v.add_cross_reference("company:UK:acme", "company:UK:beta", "shared_director",
                          "second pass", user_id="tenant_a")
    rows = v.get_cross_references("company:UK:acme", user_id="tenant_a")
    v.close()
    assert len(rows) == 1 and rows[0]["finding_summary"] == "second pass"


# ── 2. THE capability test — the cross-tenant leak ───────────────────────────

def test_tenant_cannot_see_another_tenants_cross_references(vault):
    """PRE-FIX: tenant B's row came back for anyone. POST-FIX: scoped to the owner."""
    _seed(vault)

    a_refs = vault.get_cross_references("company:UK:acme", user_id="tenant_a")
    assert len(a_refs) == 1 and a_refs[0]["target_entity"] == "company:UK:beta"

    # Tenant A asks about an entity they never investigated: B's finding must not leak.
    leaked = vault.get_cross_references("company:UK:gamma", user_id="tenant_a")
    assert leaked == [], (
        "a cross-reference carries the other tenant's entity id + finding_summary — "
        "returning it is the R-F2401/2402 leak class"
    )


def test_related_cases_never_returns_another_tenants_case(vault):
    """The sharp one: related_cases returns FULL case dicts (risk + evidence_grade)."""
    _seed(vault)
    # Both tenants' cases exist in the shared vault.
    for eid, name in (("company:UK:beta", "Beta Ltd"), ("company:UK:delta", "Delta Ltd")):
        vault.record_case(canonical_entity_id=eid, entity_name=name,
                          risk_score=90.0, risk_level="HIGH")

    # Tenant A owns only acme+beta. gamma/delta are tenant B's.
    a_owned = {"company:UK:acme", "company:UK:beta"}

    related = vault.get_related_cases(
        "company:UK:acme", user_id="tenant_a", visible_entity_ids=a_owned)
    assert [c["canonical_entity_id"] for c in related] == ["company:UK:beta"]

    # Even if a ref pointed at an unowned entity, the case must not come back.
    vault.add_cross_reference("company:UK:acme", "company:UK:delta", "supplier_of",
                              "acme buys from delta", user_id="tenant_a")
    related = vault.get_related_cases(
        "company:UK:acme", user_id="tenant_a", visible_entity_ids=a_owned)
    ids = [c["canonical_entity_id"] for c in related]
    assert "company:UK:delta" not in ids, (
        "delta is tenant B's case — its risk_level/evidence_grade must never be "
        "returned to tenant A, even via a relationship tenant A discovered"
    )


def test_empty_owned_set_is_a_real_answer_not_unrestricted(vault):
    """§1 tri-state: an empty set (owns nothing) != None (internal caller)."""
    _seed(vault)
    vault.record_case(canonical_entity_id="company:UK:beta", entity_name="Beta Ltd",
                      risk_score=1.0, risk_level="LOW")
    related = vault.get_related_cases(
        "company:UK:acme", user_id="tenant_a", visible_entity_ids=set())
    assert related == [], "an external caller who owns nothing must see nothing"


# ── 3. Internal callers keep working (the gate must not become a wall) ───────

def test_internal_caller_is_unrestricted(vault):
    """None = internal/service caller — mirrors _dd_owned_entity_ids' contract.

    Pass 2 flagged the first version of this as VACUOUS (it asserted a single row that
    the pre-fix code returned too, so it passed with the fix reverted). Seed BOTH
    tenants and assert the internal caller sees ACROSS them — that is only true of the
    unrestricted branch, and is what over-tightening would break.
    """
    _seed(vault)
    vault.add_cross_reference("company:UK:acme", "company:UK:zeta", "supplier_of",
                              "B also touches acme", user_id="tenant_b")
    all_refs = vault.get_cross_references("company:UK:acme")     # no user_id → internal
    assert {r["user_id"] for r in all_refs} == {"tenant_a", "tenant_b"}, (
        "an internal/service caller must see the whole graph across tenants"
    )
    scoped = vault.get_cross_references("company:UK:acme", user_id="tenant_a")
    assert {r["user_id"] for r in scoped} == {"tenant_a"}


def test_unattributed_row_is_invisible_to_a_tenant(vault):
    """A NULL user_id row (legacy/unattributed) must never reach a tenant."""
    vault.add_cross_reference("company:UK:acme", "company:UK:zeta", "mentions")  # no user_id
    assert vault.get_cross_references("company:UK:acme", user_id="tenant_a") == []
    assert len(vault.get_cross_references("company:UK:acme")) == 1  # internal sees it


def test_writer_attributes_the_row(vault):
    _seed(vault)
    row = vault.get_cross_references("company:UK:acme")[0]
    assert row["user_id"] == "tenant_a"
    assert row["discovered_at"] <= time.time()

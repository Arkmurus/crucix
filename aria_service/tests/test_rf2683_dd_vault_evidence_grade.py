"""R-F2683 — PERSIST the DD evidence grade to the vault (`dd_cases`).

Phase-0(a) of the DD Grade-A backlog ("to-do list #1"). R-F2681 shipped the
EXPOSE half (render_markdown surfaces the live grade); this is the PERSIST half.

Before this fix `dd_cases` stored risk_score/risk_level but NOT the evidence
grade, so "what grade was this case when we ran it?" was unanswerable from the
vault — the grade existed only inside the report render.

Two capability paths are covered, both of which FAIL before the fix:
  1. record_case() → get_case() round-trips evidence_grade/score/blockers.
  2. An EXISTING prod-shaped DB (created WITHOUT the new columns) is migrated
     in place on open — `CREATE TABLE IF NOT EXISTS` cannot add columns, so
     without an explicit ALTER TABLE the live /data/dd_vault.db would throw
     "no such column" on every write.

Honesty (§1): the persisted grade is a per-run SNAPSHOT stamped with graded_at.
Live display MUST recompute (R-F2681 does) — a stored grade is history, never
the authority.
"""
from __future__ import annotations

import sqlite3

import pytest

from aria_service.intel.dd_vault import DDVault


# The EXACT pre-R-F2683 dd_cases table — this is the shape the live
# /data/dd_vault.db has on disk today, and the shape the migration must handle.
_LEGACY_DD_CASES_SQL = """
CREATE TABLE dd_cases (
    canonical_entity_id TEXT PRIMARY KEY,
    entity_name         TEXT NOT NULL,
    entity_type         TEXT NOT NULL DEFAULT 'company',
    jurisdiction        TEXT DEFAULT '',
    registration_number TEXT DEFAULT '',
    last_run_at         REAL NOT NULL,
    run_count           INTEGER NOT NULL DEFAULT 1,
    latest_report_id    TEXT DEFAULT '',
    previous_report_ids TEXT DEFAULT '[]',
    findings_summary    TEXT DEFAULT '',
    risk_score          REAL DEFAULT 0.0,
    risk_level          TEXT DEFAULT 'unknown',
    status              TEXT NOT NULL DEFAULT 'active',
    tags                TEXT DEFAULT '[]',
    cross_references    TEXT DEFAULT '[]',
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);
"""


def _make_legacy_db(path):
    """Build a pre-R-F2683 vault DB (no grade columns) at `path`."""
    conn = sqlite3.connect(str(path))
    conn.executescript(_LEGACY_DD_CASES_SQL)
    conn.commit()
    conn.close()


@pytest.fixture()
def vault(tmp_path):
    v = DDVault(db_path=tmp_path / "dd_vault.db")
    yield v
    v.close()


def test_record_case_persists_evidence_grade(vault):
    """CAPABILITY: the grade survives a write→read round-trip through SQLite."""
    vault.record_case(
        canonical_entity_id="acme-ltd",
        entity_name="Acme Ltd",
        risk_score=0.4,
        risk_level="AMBER",
        evidence_grade="B",
        evidence_score=72,
        grade_blockers=["only 4 reputable sources (need 5)"],
    )

    case = vault.get_case("acme-ltd")
    assert case is not None
    assert case["evidence_grade"] == "B"
    assert case["evidence_score"] == 72
    assert case["grade_blockers"] == ["only 4 reputable sources (need 5)"]
    # Snapshot must be stamped so a stale grade is detectable, never silent.
    assert case["graded_at"] > 0


def test_grade_is_optional_and_defaults_are_honest(vault):
    """A caller that supplies no grade must NOT fabricate one (e.g. 'A')."""
    vault.record_case(canonical_entity_id="nograde-ltd", entity_name="NoGrade Ltd")

    case = vault.get_case("nograde-ltd")
    assert case["evidence_grade"] == ""      # unknown, not invented
    assert case["evidence_score"] is None    # absent, not 0 (0 == "worst grade")
    assert case["grade_blockers"] == []
    assert case["graded_at"] is None


def test_rerun_updates_the_grade_snapshot(vault):
    """A re-run must overwrite the snapshot — an improved case can't stay Grade D."""
    vault.record_case(
        canonical_entity_id="acme-ltd", entity_name="Acme Ltd",
        evidence_grade="D", evidence_score=35, grade_blockers=["thin evidence"],
    )
    vault.record_case(
        canonical_entity_id="acme-ltd", entity_name="Acme Ltd",
        evidence_grade="A", evidence_score=91, grade_blockers=[],
    )

    case = vault.get_case("acme-ltd")
    assert case["evidence_grade"] == "A"
    assert case["evidence_score"] == 91
    assert case["grade_blockers"] == []
    assert case["run_count"] == 2


def test_existing_db_without_grade_columns_is_migrated_in_place(tmp_path):
    """CAPABILITY: the live /data/dd_vault.db already exists WITHOUT these columns.

    This reproduces prod: build the pre-R-F2683 table, then open it with the new
    code. Without an ALTER TABLE migration record_case() raises "no such column"
    and every DD persist breaks.
    """
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO dd_cases
               (canonical_entity_id, entity_name, last_run_at, run_count,
                created_at, updated_at)
           VALUES ('legacy-ltd', 'Legacy Ltd', 100.0, 3, 100.0, 100.0)"""
    )
    conn.commit()
    conn.close()

    v = DDVault(db_path=db)
    try:
        # The pre-existing row survives the migration (no data loss, §7).
        legacy = v.get_case("legacy-ltd")
        assert legacy is not None
        assert legacy["entity_name"] == "Legacy Ltd"
        assert legacy["run_count"] == 3
        # ...and reads as UNGRADED rather than fabricating a grade.
        assert legacy["evidence_grade"] == ""
        assert legacy["evidence_score"] is None

        # Writes work against the migrated table.
        v.record_case(
            canonical_entity_id="legacy-ltd", entity_name="Legacy Ltd",
            evidence_grade="C", evidence_score=55, grade_blockers=["no identity authority"],
        )
        assert v.get_case("legacy-ltd")["evidence_grade"] == "C"
    finally:
        v.close()


def test_migration_is_idempotent(tmp_path):
    """Re-opening an already-migrated DB must not raise 'duplicate column'."""
    db = tmp_path / "twice.db"
    v1 = DDVault(db_path=db)
    v1.record_case(canonical_entity_id="x-ltd", entity_name="X Ltd", evidence_grade="B")
    v1.close()

    v2 = DDVault(db_path=db)
    try:
        assert v2.get_case("x-ltd")["evidence_grade"] == "B"
    finally:
        v2.close()


def test_failed_migration_does_not_poison_the_connection(tmp_path, monkeypatch):
    """CAPABILITY: a transient lock during the migration must not wedge the vault.

    R-F2683 made _init_db FALLIBLE on the live DB (it now runs ALTER TABLE, which
    needs a write lock while the DD engine is writing). _get_conn used to assign
    self._conn BEFORE _init_db(), so one SQLITE_BUSY left a non-None UN-MIGRATED
    connection that the `is None` guard handed out forever — every later write
    raising "no such column", with no retry, until process restart.

    Prod shape only: a fresh DB gets the columns from CREATE TABLE and self-heals,
    so this uses the legacy table that /data/dd_vault.db actually has.
    """
    db = tmp_path / "locked.db"
    _make_legacy_db(db)

    v = DDVault(db_path=db)
    calls = {"n": 0}
    real_migrate = DDVault._migrate

    def flaky_migrate(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_migrate(self)

    monkeypatch.setattr(DDVault, "_migrate", flaky_migrate)
    try:
        # First touch: the migration loses the race and the write fails LOUDLY.
        with pytest.raises(sqlite3.OperationalError):
            v.record_case(canonical_entity_id="a-ltd", entity_name="A Ltd")
        assert v._conn is None, "failed init must not publish a half-built connection"

        # Second touch RETRIES the migration and succeeds — no restart needed.
        v.record_case(canonical_entity_id="a-ltd", entity_name="A Ltd", evidence_grade="B")
        assert calls["n"] == 2
        assert v.get_case("a-ltd")["evidence_grade"] == "B"
    finally:
        v.close()


def test_list_all_exposes_the_grade(vault):
    """The vault list surface must carry the grade, not just the risk level."""
    vault.record_case(
        canonical_entity_id="listed-ltd", entity_name="Listed Ltd",
        evidence_grade="B", evidence_score=70, grade_blockers=["x"],
    )

    rows = [c for c in vault.list_all(limit=50) if c["canonical_entity_id"] == "listed-ltd"]
    assert rows and rows[0]["evidence_grade"] == "B"
    assert rows[0]["grade_blockers"] == ["x"]

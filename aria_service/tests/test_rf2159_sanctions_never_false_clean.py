"""R-F2159 — sanctions must NEVER assert "clean" when the screen did not
provably run against loaded data.

ROOT-CAUSE GUARD. For a compliance product, a false "not sanctioned" is the
single worst output. Two live false-clean paths were verified (file:line) and
fixed here:

PATH B — sanctions_canonical/lookup.py::check_sanctions
  Before: `verdict = "CLEAR" if exact_rows or q_entity_tokens else
  "INSUFFICIENT_DATA"`. `q_entity_tokens` is non-empty for essentially every
  real name, so an EMPTY/un-refreshed store returned an authoritative CLEAR for
  any company. Fix: gate CLEAR on the store actually holding entries for the
  in-scope sources; empty store → INSUFFICIENT_DATA + source_unavailable.

PATH A — company_investigator.py::_phase_sanctions
  (1) `await asyncio.wait_for(_sc(...))` passed a *sync* function's dict to
  wait_for → TypeError every call → the whole sanctions phase silently produced
  nothing. (2) The empty-matches branch emitted a "clean" finding regardless of
  verdict. (3) Timeout/error were swallowed to logger.debug. Fix: offload the
  sync call to a thread; emit "clean" ONLY on verdict==CLEAR; surface an
  UNVERIFIED finding + risk_indicator on insufficient-data / timeout / error.

These capability tests drive the ACTUAL functions (check_sanctions and
_phase_sanctions), not helpers.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel.sanctions_canonical.normalise import normalise_name


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """Fresh empty canonical-sanctions SQLite per test (mirrors R-F526)."""
    db_path = tmp_path / "sanctions_canonical_rf2159.db"
    monkeypatch.setenv("ARIA_SANCTIONS_CANONICAL_DB", str(db_path))
    yield
    if db_path.exists():
        db_path.unlink()


def _seed_one_unrelated_entry():
    """Put ONE entry in the store so it is provably 'loaded' — but it does not
    match the company we screen, so a correct screen returns CLEAR."""
    from aria_service.intel.sanctions_canonical import store
    rows = [{
        "source_uid": "ofac:test:Acme:1",
        "formatted_name": "ACME SANCTIONED HOLDINGS",
        "normalised_name": normalise_name("ACME SANCTIONED HOLDINGS"),
        "entity_type": "Entity",
        "countries": ["Iran"],
        "addresses": ["Tehran, Iran"],
        "aliases": [{"formatted": "ACME SANCTIONED HOLDINGS",
                     "normalised": normalise_name("ACME SANCTIONED HOLDINGS"),
                     "alias_type": "primary"}],
        "programs": ["IRAN"],
        "designation_at": None,
        "raw_excerpt": "test-seed unrelated entry",
    }]
    store.replace_source("ofac_sdn", rows)


# ───────────────────────── PATH B — canonical store ─────────────────────────

def test_rf2159_empty_store_returns_insufficient_not_clear():
    """THE regression: an EMPTY store must NEVER return CLEAR for a real name.
    Before the fix this returned CLEAR (q_entity_tokens truthy)."""
    from aria_service.intel.sanctions_canonical import check_sanctions, store
    assert store.count_entries() == 0, "fixture should start with an empty store"
    r = check_sanctions("Globex International Trading Ltd", jurisdiction="Cyprus")
    assert r["verdict"] == "INSUFFICIENT_DATA", (
        f"empty store must be INSUFFICIENT_DATA, got {r['verdict']} — "
        "an unloaded store reading as CLEAR is an authoritative false-negative"
    )
    assert r["matches"] == []
    assert r.get("source_unavailable") is True, "must flag the screen could not run"


def test_rf2159_loaded_store_no_match_returns_clear():
    """Legitimate clean is preserved: a LOADED store with no match → CLEAR."""
    from aria_service.intel.sanctions_canonical import check_sanctions, store
    _seed_one_unrelated_entry()
    assert store.count_entries() == 1
    r = check_sanctions("Globex International Trading Ltd", jurisdiction="Cyprus")
    assert r["verdict"] == "CLEAR", (
        f"loaded store + no match must be CLEAR, got {r['verdict']}"
    )
    assert r.get("source_unavailable") is False


def test_rf2159_loaded_store_real_match_still_hits():
    """The fix must not suppress real hits."""
    from aria_service.intel.sanctions_canonical import check_sanctions
    _seed_one_unrelated_entry()
    r = check_sanctions("ACME SANCTIONED HOLDINGS", jurisdiction="Iran")
    assert r["verdict"] in ("HARD_STOP", "REVIEW"), (
        f"a seeded matching entity must hit, got {r['verdict']}"
    )
    assert r["matches"], "expected at least one match"


# ──────────────────── PATH A — company_investigator phase ───────────────────

def _run_phase(company_name: str, jurisdiction: str = ""):
    from aria_service.intel.company_investigator import (
        InvestigationReport, _phase_sanctions,
    )
    report = InvestigationReport(entity_name=company_name)
    asyncio.run(_phase_sanctions(report, company_name, jurisdiction))
    return report


def test_rf2159_phase_empty_store_surfaces_unverified_not_clean():
    """Empty store → the phase must surface an UNVERIFIED finding + a risk
    indicator, and must NOT emit a 'clean'-tagged finding."""
    report = _run_phase("Globex International Trading Ltd", "Cyprus")
    sanctions = [f for f in report.findings if f.category == "sanctions"]
    assert sanctions, (
        "phase produced NO sanctions finding — the sync/async no-op bug is back "
        "(screen silently vanished)"
    )
    tags = {t for f in sanctions for t in f.tags}
    assert "unverified" in tags or "could_not_verify" in tags, (
        f"empty-store screen must be UNVERIFIED, got tags {tags}"
    )
    assert "clean" not in tags, "empty store must NOT read as clean"
    assert any("UNVERIFIED" in ri or "unverified" in ri
               for ri in report.risk_indicators), (
        "an unverified sanctions screen must raise a risk indicator"
    )


def test_rf2159_phase_actually_runs_on_loaded_store():
    """Proves the sync/async no-op is fixed: a loaded store + clean name must
    yield a real CLEAN finding (previously the phase raised TypeError and
    produced nothing at all)."""
    _seed_one_unrelated_entry()
    report = _run_phase("Globex International Trading Ltd", "Cyprus")
    sanctions = [f for f in report.findings if f.category == "sanctions"]
    assert sanctions, "phase produced no finding — sync/async call still broken"
    tags = {t for f in sanctions for t in f.tags}
    assert "clean" in tags, f"loaded-store clean screen should be clean, tags={tags}"


def test_rf2159_phase_timeout_surfaces_unverified(monkeypatch):
    """A timed-out screen must surface as UNVERIFIED, never silently vanish."""
    import aria_service.intel.company_investigator as ci

    # Force the phase's wait_for to time out deterministically.
    real_wait_for = asyncio.wait_for

    async def _boom(coro=None, *a, **k):
        # Close the to_thread coroutine we're intercepting so it isn't leaked.
        if asyncio.iscoroutine(coro):
            coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(ci.asyncio, "wait_for", _boom)
    report = ci.InvestigationReport(entity_name="Slowco Ltd")
    asyncio.run(ci._phase_sanctions(report, "Slowco Ltd", ""))
    sanctions = [f for f in report.findings if f.category == "sanctions"]
    assert sanctions, "timeout produced no finding — screen silently vanished"
    tags = {t for f in sanctions for t in f.tags}
    assert "unverified" in tags and "clean" not in tags
    assert any("TIMED OUT" in ri or "unverified" in ri.lower()
               for ri in report.risk_indicators)

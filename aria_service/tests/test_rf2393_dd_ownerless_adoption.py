"""R-F2393 (2026-07-04) — owner-less DD report adoption.

Symptom (operator-reported): a DD'd entity shows in the watchlist but its
report is MISSING from /dd/reports. Root cause: after a report-index reset,
``list_reports`` rebuilds the index from the dd_vault, which has NO user_id
column (R-F2382), so rebuilt entries come back OWNER-LESS (user_id=None). The
R-F2382/R-F2388 fallback only stamped an owner inside the ``if not index:``
rebuild branch, and only for that one rebuild — so persisted owner-less entries
became STICKY and the operator's scoped list returned []. The watchlist (a
separate key that is never rebuilt) kept the real owner, hence the mismatch.

R-F2393 adopts owner-less entries on EVERY read: reclaim the true owner from
the watchlist by ``last_dd_run_id == run_id`` when available (precise), else
fall back to the configured legacy operator (ARIA_DD_LEGACY_OWNER_UID). These
are capability tests — they drive the ACTUAL broken path (list_reports with a
scoped user_id over an owner-less index) and assert the user-visible outcome
(the operator's scoped list is non-empty).
"""
from __future__ import annotations

import asyncio

import pytest


class _FakeRS:
    def __init__(self, initial: dict | None = None):
        self.store: dict = dict(initial or {})

    async def get_json(self, key):
        return self.store.get(key)

    async def set_json(self, key, value, ex=None):
        self.store[key] = value


@pytest.fixture
def fake_rs(monkeypatch):
    from aria_service.intel import redis_store as real_rs
    fake = _FakeRS()
    monkeypatch.setattr(real_rs, "get_json", fake.get_json)
    monkeypatch.setattr(real_rs, "set_json", fake.set_json)
    return fake


@pytest.fixture(autouse=True)
def _clean_operator_env(monkeypatch):
    """Default: no legacy operator configured, so the anti-leak default holds
    unless a test explicitly sets one."""
    monkeypatch.delenv("ARIA_DD_LEGACY_OWNER_UID", raising=False)
    monkeypatch.delenv("ARIA_CODER_OPERATOR_USER_ID", raising=False)
    monkeypatch.delenv("ARIA_OPERATOR_EMAIL", raising=False)


def _seed(fake, *, index, watchlist=None):
    from aria_service.intel import dd_orchestrator
    fake.store[dd_orchestrator.REPORT_INDEX_KEY] = list(index)
    if watchlist is not None:
        fake.store[dd_orchestrator.WATCHLIST_KEY] = list(watchlist)


# ── The live scenario: watchlist has the owner, the report does not ─────────

def test_ownerless_report_reclaimed_from_watchlist(fake_rs):
    """EXACT live repro (Assan Group): report index entry is owner-less but the
    watchlist entry for the same run_id owns 5834252728d3. The operator's scoped
    /dd/reports must SHOW the report — reclaimed from the watchlist."""
    from aria_service.intel import dd_orchestrator

    _seed(
        fake_rs,
        index=[
            {"run_id": "dd_assan", "entity_name": "Assan Group",
             "user_id": None, "user_email_domain": None},
        ],
        watchlist=[
            {"name": "Assan Group", "last_dd_run_id": "dd_assan",
             "user_id": "5834252728d3", "user_email_domain": "arkmurus.com",
             "share_to_company": True},
        ],
    )

    out = asyncio.run(dd_orchestrator.list_reports(
        limit=50, user_id="5834252728d3", user_email_domain="arkmurus.com"))

    ids = [r["run_id"] for r in out]
    assert ids == ["dd_assan"], f"operator must see their reclaimed report, got {ids}"
    assert out[0]["user_id"] == "5834252728d3"
    assert out[0].get("_owner_reclaimed_by") == "R-F2393_watchlist"


def test_reclaim_persists_to_index(fake_rs):
    """Durability: the reclaimed owner is written back so the repair heals once
    and stays healed (subsequent reads don't re-reclaim from scratch)."""
    from aria_service.intel import dd_orchestrator

    _seed(
        fake_rs,
        index=[{"run_id": "dd_x", "entity_name": "X", "user_id": None}],
        watchlist=[{"name": "X", "last_dd_run_id": "dd_x",
                    "user_id": "op", "user_email_domain": "ark.com"}],
    )

    asyncio.run(dd_orchestrator.list_reports(
        limit=50, user_id="op", user_email_domain="ark.com"))

    persisted = fake_rs.store[dd_orchestrator.REPORT_INDEX_KEY]
    assert persisted[0]["user_id"] == "op", "reclaim must persist to the index"


# ── Legacy-operator fallback when the watchlist can't help ──────────────────

def test_ownerless_report_adopted_by_legacy_operator(fake_rs, monkeypatch):
    """No watchlist match, but ARIA_DD_LEGACY_OWNER_UID is set (single-operator
    deployment). The owner-less report is adopted by the operator so it stops
    vanishing from their scoped list."""
    from aria_service.intel import dd_orchestrator

    monkeypatch.setenv("ARIA_DD_LEGACY_OWNER_UID", "operator123")
    monkeypatch.setenv("ARIA_OPERATOR_EMAIL", "op@arkmurus.com")
    _seed(fake_rs, index=[
        {"run_id": "dd_orphan", "entity_name": "Orphan Co", "user_id": None},
    ], watchlist=[])

    out = asyncio.run(dd_orchestrator.list_reports(limit=50, user_id="operator123"))

    ids = [r["run_id"] for r in out]
    assert ids == ["dd_orphan"], f"legacy operator must see adopted report, got {ids}"
    assert out[0].get("_owner_reclaimed_by") == "R-F2393_legacy_operator"


# ── Anti-leak regression guard (R-F607 must still hold) ─────────────────────

def test_no_leak_when_no_operator_configured(fake_rs):
    """Regression guard: with NO legacy operator configured and NO watchlist
    owner, an owner-less entry must NOT leak into an arbitrary user's scoped
    list — the R-F607 isolation guarantee is preserved."""
    from aria_service.intel import dd_orchestrator

    _seed(fake_rs, index=[
        {"run_id": "dd_leg", "entity_name": "Legacy", "user_id": None},
    ], watchlist=[])

    out = asyncio.run(dd_orchestrator.list_reports(limit=50, user_id="stranger"))
    assert out == [], "owner-less entry must not leak when no operator is configured"


def test_adopted_entry_not_visible_to_other_user(fake_rs, monkeypatch):
    """Adoption targets the CONFIGURED operator, never the arbitrary requester.
    A different user still must not see the adopted report."""
    from aria_service.intel import dd_orchestrator

    monkeypatch.setenv("ARIA_DD_LEGACY_OWNER_UID", "operator123")
    _seed(fake_rs, index=[
        {"run_id": "dd_orphan", "entity_name": "Orphan Co", "user_id": None},
    ], watchlist=[])

    out = asyncio.run(dd_orchestrator.list_reports(limit=50, user_id="someone_else"))
    assert out == [], "adopted-to-operator entry must not leak to a different user"


def test_admin_view_still_returns_everything(fake_rs, monkeypatch):
    """No-filter (admin/autonomous) path is unchanged — it still sees all
    entries, adopted or not."""
    from aria_service.intel import dd_orchestrator

    monkeypatch.setenv("ARIA_DD_LEGACY_OWNER_UID", "operator123")
    _seed(fake_rs, index=[
        {"run_id": "a", "entity_name": "A", "user_id": None},
        {"run_id": "b", "entity_name": "B", "user_id": "someone"},
    ], watchlist=[])

    out = asyncio.run(dd_orchestrator.list_reports(limit=50))
    assert {r["run_id"] for r in out} == {"a", "b"}

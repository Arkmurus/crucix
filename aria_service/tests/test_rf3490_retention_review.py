"""R-F3490 — retention you can review, without a timer that deletes.

THE GAP. R-F3488 made records carry a `retention_class`. Nothing acted on it: there was no
schedule, no review, and no way to answer "what personal data are we holding past its
period?" UK GDPR Art. 5(1)(e) requires personal data be kept no longer than necessary.

TWO CONSTRAINTS THAT LOOK CONTRADICTORY AND ARE NOT.
  * Art. 5(1)(e) needs a retention schedule with PERIODIC REVIEW and erasure *or
    anonymisation* — the ICO's route. It does not require an automatic timer.
  * CLAUDE.md §7 forbids TTL, oldest-first prune and eviction outright.

Both hold at once, because what the law actually needs is a controller who can SEE what is
overdue and decide. So this reports and takes no destructive action of any kind.

THE TWO ANSWERS THAT MATTER MOST are not `due`. They are `no_period_set` (a class was
declared but never given a period) and `unclassified` (personal data with no retention
class at all). An automatic timer would have silently ignored both populations — they are
invisible to a deletion rule and they are exactly what a controller must be told about.

AND IT INVENTS NOTHING. `_retention_periods()` is EMPTY unless configured. Writing "7
years" into code and letting a report present it as policy would be a fabricated finding
wearing compliance clothing; a class with no configured period is reported as undecided.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import rag_store
from aria_service.intel.rag_store import RETENTION_CLASS_KEY


class _Coll:
    def __init__(self, metas: list[dict]):
        self.metas = metas
        self.deleted: list[str] = []

    def get(self, include=None, limit=None, offset=0):
        sl = self.metas[offset: (offset + limit) if limit else None]
        return {"ids": [f"i{offset+n}" for n in range(len(sl))], "metadatas": sl}

    def delete(self, ids):
        self.deleted.extend(ids)


@pytest.fixture
def wired(monkeypatch):
    docs = _Coll([
        # personal, classed, OLD -> due once a period is configured
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         "ingested_at": "2020-01-01T00:00:00+00:00"},
        # personal, classed, RECENT -> within period
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         "ingested_at": "2026-07-01T00:00:00+00:00"},
        # personal, class declared but NO period configured
        {"personal_data": True, RETENTION_CLASS_KEY: "dd_evidence",
         "ingested_at": "2019-01-01T00:00:00+00:00"},
        # personal, NO retention class at all
        {"personal_data": True, "ingested_at": "2019-01-01T00:00:00+00:00"},
        # not personal data — must be ignored entirely
        {"source": "companies_house", "ingested_at": "2019-01-01T00:00:00+00:00"},
    ])
    monkeypatch.setattr(rag_store, "_documents_collection", docs)
    monkeypatch.setattr(rag_store, "_facts_collection", _Coll([]))
    monkeypatch.setattr(rag_store, "_documents_cold_collection", _Coll([]))

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    monkeypatch.setenv("ARIA_RETENTION_PERIODS_DAYS", "chat_notebook=365")
    return docs


def _review(**kw):
    return asyncio.run(rag_store.retention_review(**kw))


def test_capability_overdue_personal_data_is_reported(wired):
    res = _review(now_iso="2026-07-30T00:00:00+00:00")
    assert res["due"] == 1, res
    assert res["within_period"] == 1, res


def test_a_class_with_no_configured_period_is_undecided_not_compliant(wired):
    """The honest half: we cannot call something overdue against a period nobody set,
    and we must not call it fine either."""
    res = _review(now_iso="2026-07-30T00:00:00+00:00")
    assert res["no_period_set"] == 1, res
    assert "dd_evidence" not in res["configured_classes"]


def test_personal_data_with_no_retention_class_is_surfaced(wired):
    """The population an automatic timer would silently ignore — and the one a controller
    most needs to see."""
    res = _review(now_iso="2026-07-30T00:00:00+00:00")
    assert res["unclassified"] == 1, res


def test_non_personal_records_are_not_counted(wired):
    """Public filings and press are not subject to this schedule; counting them would
    inflate the number a controller has to act on until they stop reading it."""
    res = _review(now_iso="2026-07-30T00:00:00+00:00")
    assert res["personal_records"] == 4, res


def test_nothing_is_deleted_and_nothing_is_scheduled(wired):
    """§7: no TTL, no prune, no eviction. This function must never become a timer."""
    docs = wired
    _review(now_iso="2026-07-30T00:00:00+00:00")
    assert docs.deleted == [], "retention_review deleted data"
    res = _review(now_iso="2026-07-30T00:00:00+00:00")
    assert res["action_taken"] == "none"
    assert "nothing is deleted" in res["note"]


def test_no_periods_are_invented(monkeypatch):
    """A fabricated retention period presented as policy is a fabricated finding in
    compliance clothing."""
    monkeypatch.delenv("ARIA_RETENTION_PERIODS_DAYS", raising=False)
    assert rag_store._retention_periods() == {}


def test_a_malformed_configuration_is_ignored_loudly_not_guessed(monkeypatch):
    monkeypatch.setenv("ARIA_RETENTION_PERIODS_DAYS", "good=30,broken,alsobad=xyz")
    assert rag_store._retention_periods() == {"good": 30}


def test_a_read_failure_is_not_reported_as_nothing_due(monkeypatch):
    """'I could not look' must never render as 'nothing is overdue'."""
    class _Broken:
        def get(self, **kw):
            raise RuntimeError("chromadb down")

    monkeypatch.setattr(rag_store, "_documents_collection", _Broken())
    monkeypatch.setattr(rag_store, "_facts_collection", _Coll([]))
    monkeypatch.setattr(rag_store, "_documents_cold_collection", _Coll([]))

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    res = _review()
    assert res["scan_errors"], "an unreadable collection was reported silently"


def test_the_review_covers_every_searchable_collection(wired):
    """Same invariant as R-F3478/R-F3484: a collection a search can read from must be in
    scope for retention too, or overdue data hides in the one nobody reviews."""
    res = _review(now_iso="2026-07-30T00:00:00+00:00")
    assert set(res["per_collection"]) == set(rag_store._SEARCHABLE_COLLECTION_LABELS)

"""R-F3492 — retention is per JURISDICTION, and residency is reported, not assumed.

Operator: "retention should be country specific — UK should match UK retention period, and
operators or super-admin users must be reminded. At the moment it is UK-LHR, but to push
other modules to serve other countries we may run servers in those regions."

THREE THINGS THE FLAT MODEL GOT WRONG.

1. A retention period is a function of (data category, JURISDICTION), not of category
   alone. R-F3490's flat `class=days` silently applied one country's answer to every
   country's data — correct while only the UK was served, wrong the moment a second
   jurisdiction appears, and wrong SILENTLY, which is the dangerous part.

2. Where data SITS and whose law GOVERNS it are different questions. Inferring
   jurisdiction from the storage region is what makes an unlawful transfer invisible: UK
   personal data moved to a non-UK/EEA region still answers to UK law, and the region
   change is precisely the event that needs a Chapter V basis. So the record carries its
   OWN jurisdiction and residency is CHECKED against it.

3. A review nobody sees is not a review. Art. 5(2) puts accountability on the controller,
   so an overdue population, an undecided period, or a residency mismatch is pushed to
   the operator's gap surface rather than waiting to be queried.

STILL INVENTS NOTHING: no period is assumed for any jurisdiction. An unconfigured
(jurisdiction, class) pair is reported as UNDECIDED and named in the reminder, so the
operator knows exactly which key to set.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import rag_store
from aria_service.intel.rag_store import (
    DATA_JURISDICTION_KEY,
    RETENTION_CLASS_KEY,
)


class _Coll:
    def __init__(self, metas):
        self.metas = metas
        self.deleted: list[str] = []

    def get(self, include=None, limit=None, offset=0):
        sl = self.metas[offset: (offset + limit) if limit else None]
        return {"ids": [f"i{offset+n}" for n in range(len(sl))], "metadatas": sl}

    def delete(self, ids):
        self.deleted.extend(ids)


def _wire(monkeypatch, metas, *, region="lhr", periods=""):
    monkeypatch.setattr(rag_store, "_documents_collection", _Coll(metas))
    monkeypatch.setattr(rag_store, "_facts_collection", _Coll([]))
    monkeypatch.setattr(rag_store, "_documents_cold_collection", _Coll([]))

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    monkeypatch.setenv("FLY_REGION", region)
    monkeypatch.setenv("ARIA_RETENTION_PERIODS_DAYS", periods)
    return asyncio.run(rag_store.retention_review(now_iso="2026-07-30T00:00:00+00:00"))


_OLD = "2020-01-01T00:00:00+00:00"


def test_uk_data_uses_the_uk_period(monkeypatch):
    """THE REQUIREMENT: UK data is judged against the UK period."""
    res = _wire(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         DATA_JURISDICTION_KEY: "uk", "ingested_at": _OLD},
    ], periods="uk:chat_notebook=365")
    assert res["due"] == 1, res


def test_another_jurisdiction_is_not_judged_by_the_uk_period(monkeypatch):
    """The silent-wrong case. German data must NOT inherit the UK answer just because
    the UK is the only configured jurisdiction."""
    res = _wire(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         DATA_JURISDICTION_KEY: "de", "ingested_at": _OLD},
    ], periods="uk:chat_notebook=365")
    assert res["due"] == 0, "German data was judged against the UK period"
    assert res["no_period_set"] == 1
    assert "de:chat_notebook" in res["undecided_period_keys"], res


def test_jurisdictions_can_carry_different_periods(monkeypatch):
    res = _wire(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         DATA_JURISDICTION_KEY: "uk", "ingested_at": "2026-01-01T00:00:00+00:00"},
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         DATA_JURISDICTION_KEY: "de", "ingested_at": "2026-01-01T00:00:00+00:00"},
    ], periods="uk:chat_notebook=30,de:chat_notebook=3650")
    assert res["due"] == 1, res           # UK overdue
    assert res["within_period"] == 1, res  # DE still inside its longer period
    assert res["by_jurisdiction"] == {"uk": 1, "de": 1}


def test_an_any_jurisdiction_fallback_must_be_declared(monkeypatch):
    """A bare `class=days` means the operator SAID it applies anywhere. That is a
    decision, not an accident, so it is honoured — but only when written."""
    res = _wire(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         DATA_JURISDICTION_KEY: "de", "ingested_at": _OLD},
    ], periods="chat_notebook=365")
    assert res["due"] == 1, res


def test_uk_data_outside_the_uk_eea_perimeter_is_flagged(monkeypatch):
    """Chapter V. Where data SITS and whose law GOVERNS it are different questions."""
    res = _wire(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         DATA_JURISDICTION_KEY: "uk", "ingested_at": _OLD},
    ], region="iad", periods="uk:chat_notebook=365")
    assert res["storage_region"] == "iad"
    assert res["storage_perimeter"] == "OTHER"
    assert res["residency_mismatch"] == 1, res
    assert any("Chapter V" in r for r in res["reminders"]), res["reminders"]


def test_uk_data_in_lhr_is_not_flagged(monkeypatch):
    """The current deployment must be clean, or the signal is noise from day one."""
    res = _wire(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         DATA_JURISDICTION_KEY: "uk", "ingested_at": _OLD},
    ], region="lhr", periods="uk:chat_notebook=365")
    assert res["storage_perimeter"] == "UK_EEA"
    assert res["residency_mismatch"] == 0


def test_an_unknown_region_counts_as_outside(monkeypatch):
    """Fail closed on geography: an unrecognised region is treated as OUTSIDE the
    perimeter, because guessing permissively is how an unlawful transfer goes unseen."""
    assert rag_store.jurisdiction_of_region("zzz") == "OTHER"
    assert rag_store.jurisdiction_of_region("lhr") == "UK_EEA"


def test_records_without_a_jurisdiction_are_counted_not_assumed(monkeypatch):
    """A record with no jurisdiction must not be assumed to be local."""
    res = _wire(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         "ingested_at": _OLD},
    ], periods="uk:chat_notebook=365")
    assert res["no_jurisdiction"] == 1
    assert res["residency_mismatch"] == 0, "an unknown jurisdiction was assumed non-UK"
    assert res["due"] == 0, "an unknown jurisdiction was judged against the UK period"


def test_the_operator_is_reminded_and_told_which_key_to_set(monkeypatch):
    """The reminder must be actionable: which population, and what to configure."""
    res = _wire(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "dd_evidence",
         DATA_JURISDICTION_KEY: "uk", "ingested_at": _OLD},
        {"personal_data": True, "ingested_at": _OLD},
    ], periods="")
    assert res["needs_operator"] is True
    joined = " ".join(res["reminders"])
    assert "uk:dd_evidence" in joined, joined
    assert "ARIA_RETENTION_PERIODS_DAYS" in joined
    assert "NO retention class" in joined


def test_a_clean_estate_does_not_nag(monkeypatch):
    """A reminder that always fires gets ignored, and then the real one is missed too."""
    res = _wire(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
         DATA_JURISDICTION_KEY: "uk", "ingested_at": "2026-07-29T00:00:00+00:00"},
    ], periods="uk:chat_notebook=365")
    assert res["needs_operator"] is False, res["reminders"]
    assert res["reminders"] == []


def test_review_still_deletes_nothing(monkeypatch):
    """§7 holds regardless of jurisdiction."""
    metas = [{"personal_data": True, RETENTION_CLASS_KEY: "chat_notebook",
              DATA_JURISDICTION_KEY: "uk", "ingested_at": _OLD}]
    docs = _Coll(metas)
    monkeypatch.setattr(rag_store, "_documents_collection", docs)
    monkeypatch.setattr(rag_store, "_facts_collection", _Coll([]))
    monkeypatch.setattr(rag_store, "_documents_cold_collection", _Coll([]))

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    monkeypatch.setenv("ARIA_RETENTION_PERIODS_DAYS", "uk:chat_notebook=1")
    out = asyncio.run(rag_store.retention_review())
    assert docs.deleted == []
    assert out["action_taken"] == "none"


def test_the_endpoint_can_declare_jurisdiction():
    from aria_service.routes.aria import RagIngestRequest
    assert "data_jurisdiction" in RagIngestRequest.model_fields
    assert RagIngestRequest.model_fields["data_jurisdiction"].default == ""

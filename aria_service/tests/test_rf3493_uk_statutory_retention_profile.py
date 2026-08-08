"""R-F3493 — UK retention periods that are RESEARCHED AND CITED, not invented.

R-F3490 deliberately refused to invent periods. This supplies real ones, each traced to a
named instrument, and keeps the refusal intact: the profile is OPT-IN, an explicit env
entry always overrides it, and every number carries its legal basis so a controller can
defend it.

THE PERIODS AND THEIR SOURCES
  * MLR 2017 reg 40 — five years from completion of the transaction or the end of the
    business relationship, with a ten-year cap for transactions within a relationship.
    Verified against legislation.gov.uk, not a summary.
  * BS 7858:2019 — 12 months for unsuccessful applicants; retained during employment;
    specified records 7 years after employment ends. Encoded as PERIODS ONLY; no standard
    text is reproduced, per the R-F3466 assurance review.

TWO THINGS THAT MATTER MORE THAN THE NUMBERS

1. MLR reg 40 imposes a DELETION DUTY, not merely a ceiling: at the end of the period the
   controller MUST delete the personal data unless one of three exceptions applies. That
   is a genuine tension with CLAUDE.md §7 ("never delete"), and it is surfaced to the
   operator rather than resolved silently in either direction. ARIA still deletes nothing
   on its own — whether an exception applies is a controller decision about facts this
   code cannot see.

2. THE TRIGGER IS WRONG BY CONSTRUCTION, and saying so is the honest part. Reg 40 runs
   from the END OF THE RELATIONSHIP; `retention_review` measures from `ingested_at`,
   which is the earliest that clock could possibly start. So a `due` count is "review
   these", never "these are unlawful to hold". Encoding the number while hiding that the
   trigger differs would produce confident, wrong compliance reporting.

NOT LEGAL ADVICE. These are researched defaults to be confirmed by the controller.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import rag_store
from aria_service.intel.rag_store import DATA_JURISDICTION_KEY, RETENTION_CLASS_KEY

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


class _Coll:
    def __init__(self, metas):
        self.metas = metas
        self.deleted: list[str] = []

    def get(self, include=None, limit=None, offset=0):
        sl = self.metas[offset: (offset + limit) if limit else None]
        return {"ids": [f"i{offset+n}" for n in range(len(sl))], "metadatas": sl}

    def delete(self, ids):
        self.deleted.extend(ids)


def _review(monkeypatch, metas, *, profile="", overrides="", region="lhr"):
    monkeypatch.setattr(rag_store, "_documents_collection", _Coll(metas))
    monkeypatch.setattr(rag_store, "_facts_collection", _Coll([]))
    monkeypatch.setattr(rag_store, "_documents_cold_collection", _Coll([]))

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    monkeypatch.setenv("FLY_REGION", region)
    monkeypatch.setenv("ARIA_RETENTION_PROFILE", profile)
    monkeypatch.setenv("ARIA_RETENTION_PERIODS_DAYS", overrides)
    return asyncio.run(rag_store.retention_review(now_iso="2026-07-30T00:00:00+00:00"))


_OLD = "2015-01-01T00:00:00+00:00"     # > 10 years ago
_RECENT = "2026-06-01T00:00:00+00:00"


def test_the_profile_is_off_until_switched_on(monkeypatch):
    """R-F3490's refusal survives: no schedule applies until the controller enables one."""
    monkeypatch.delenv("ARIA_RETENTION_PROFILE", raising=False)
    monkeypatch.delenv("ARIA_RETENTION_PERIODS_DAYS", raising=False)
    assert rag_store._retention_periods() == {}
    assert rag_store.retention_bases() == {}


def test_the_uk_profile_supplies_the_mlr_period(monkeypatch):
    res = _review(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "cdd_evidence",
         DATA_JURISDICTION_KEY: "uk", "ingested_at": _OLD},
    ], profile="uk_statutory_v1")
    assert res["due"] == 1, res
    assert res["retention_profile"] == "uk_statutory_v1"


def test_every_period_carries_its_legal_basis(monkeypatch):
    """A number a controller cannot trace to an instrument is one they cannot defend."""
    monkeypatch.setenv("ARIA_RETENTION_PROFILE", "uk_statutory_v1")
    bases = rag_store.retention_bases()
    assert bases, "the profile supplies no citations"
    assert "MLR 2017 reg 40" in bases["uk:cdd_evidence"]
    assert "BS 7858:2019" in bases["uk:vetting_leaver"]
    for key, basis in bases.items():
        assert basis.strip(), f"{key} has a period with no legal basis"


def test_the_mlr_period_is_five_years(monkeypatch):
    """Pinned so a later edit cannot quietly drift the statutory number."""
    monkeypatch.setenv("ARIA_RETENTION_PROFILE", "uk_statutory_v1")
    assert rag_store._retention_periods()["uk:cdd_evidence"] == 1825


def test_bs7858_leaver_is_seven_years_and_unsuccessful_is_twelve_months(monkeypatch):
    monkeypatch.setenv("ARIA_RETENTION_PROFILE", "uk_statutory_v1")
    p = rag_store._retention_periods()
    assert p["uk:vetting_leaver"] == 2555
    assert p["uk:vetting_unsuccessful"] == 365


def test_an_explicit_override_beats_the_researched_default(monkeypatch):
    """The controller's own decision always wins over a default I researched."""
    monkeypatch.setenv("ARIA_RETENTION_PROFILE", "uk_statutory_v1")
    monkeypatch.setenv("ARIA_RETENTION_PERIODS_DAYS", "uk:cdd_evidence=99999")
    assert rag_store._retention_periods()["uk:cdd_evidence"] == 99999


def test_the_deletion_duty_is_surfaced_not_resolved(monkeypatch):
    """MLR reg 40 is a DUTY, not a ceiling — and §7 says never delete. The conflict is
    put in front of the operator; ARIA resolves it in neither direction."""
    res = _review(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "cdd_evidence",
         DATA_JURISDICTION_KEY: "uk", "ingested_at": _OLD},
    ], profile="uk_statutory_v1")
    joined = " ".join(res["reminders"])
    assert "DELETION DUTY" in joined, joined
    assert "three exceptions" in joined
    assert "deletes nothing on its own" in joined
    assert res["action_taken"] == "none"


def test_an_unknown_profile_is_ignored_loudly_not_guessed(monkeypatch):
    monkeypatch.setenv("ARIA_RETENTION_PROFILE", "utopia_v9")
    monkeypatch.delenv("ARIA_RETENTION_PERIODS_DAYS", raising=False)
    assert rag_store._retention_periods() == {}


def test_a_non_uk_record_does_not_inherit_the_uk_profile(monkeypatch):
    """The R-F3492 property must survive the profile: German data is still undecided."""
    res = _review(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "cdd_evidence",
         DATA_JURISDICTION_KEY: "de", "ingested_at": _OLD},
    ], profile="uk_statutory_v1")
    assert res["due"] == 0, "German data was judged by a UK statutory period"
    assert res["no_period_set"] == 1


def test_recent_records_are_not_due(monkeypatch):
    res = _review(monkeypatch, [
        {"personal_data": True, RETENTION_CLASS_KEY: "cdd_evidence",
         DATA_JURISDICTION_KEY: "uk", "ingested_at": _RECENT},
    ], profile="uk_statutory_v1")
    assert res["due"] == 0 and res["within_period"] == 1


def test_the_trigger_caveat_is_documented_in_the_module():
    """The most important honesty in this change: reg 40 runs from the END OF THE
    RELATIONSHIP, and the review measures from ingest. If that caveat is ever deleted,
    the numbers become confident and wrong."""
    import inspect
    src = module_source(rag_store)
    assert "END OF THE BUSINESS RELATIONSHIP" in src
    assert "EARLIEST possible due date" in src


def test_still_deletes_nothing(monkeypatch):
    docs = _Coll([{"personal_data": True, RETENTION_CLASS_KEY: "cdd_evidence",
                   DATA_JURISDICTION_KEY: "uk", "ingested_at": _OLD}])
    monkeypatch.setattr(rag_store, "_documents_collection", docs)
    monkeypatch.setattr(rag_store, "_facts_collection", _Coll([]))
    monkeypatch.setattr(rag_store, "_documents_cold_collection", _Coll([]))

    async def _ok():
        return True

    monkeypatch.setattr(rag_store, "_ensure_async", _ok)
    monkeypatch.setenv("ARIA_RETENTION_PROFILE", "uk_statutory_v1")
    asyncio.run(rag_store.retention_review())
    assert docs.deleted == [], "a statutory profile must not become a deletion timer"

"""R-F4294 / C-249 — OC-7 gets a parent hierarchy, because GLEIF publishes one.

C-248 recorded that OC-7 ("Parent, subsidiaries, affiliates and ultimate parent")
must NOT be bound the way the previous five were: GLEIF *is* called on every run,
but neither gleif module fetched relationship data, so binding it would have
certified a lookup that never happened. It was a missing CAPABILITY, not a
missing reader.

This builds the capability. GLEIF's relationship endpoints are free, key-less and
already reachable, and — measured live 2026-08-24 — they answer OC-7's pass
condition ("returns the direct and ultimate parent, OR STATES THAT NONE IS")
almost word for word:

    /lei-records/{lei}/direct-parent                      200 -> the parent
    /lei-records/{lei}/direct-parent-reporting-exception  200 -> category + reason
                                                                 e.g. NO_KNOWN_PERSON

THE THREE-WAY DISTINCTION IS THE WHOLE POINT, and it is what makes this safe:

  1. a parent is returned                  -> answered, and named
  2. a reporting EXCEPTION is returned     -> the authority STATES none, with a
                                              reason -> answered
  3. neither (404 on both)                 -> the authority said NOTHING -> NOT
                                              answered

Collapsing 3 into 2 would report "no parent" for an entity GLEIF simply has no
statement about — the false clean this whole series exists to prevent.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.intel import dd_standard as ds  # noqa: E402
from aria_service.intel import gleif  # noqa: E402

ANSWERED = {ds.EvidenceState.CORROBORATED.value, ds.EvidenceState.SINGLE_SOURCE.value}


# ── the adapter ────────────────────────────────────────────────────────────

def test_the_adapter_exposes_a_parent_fetch() -> None:
    assert hasattr(gleif, "fetch_parents"), "gleif.fetch_parents is missing"


def test_an_empty_lei_never_calls_out() -> None:
    """A blank LEI must short-circuit, not produce a request for '/lei-records//'."""
    import asyncio
    out = asyncio.run(gleif.fetch_parents(""))
    assert out["checked"] is False
    assert out.get("direct") is None


def _shape(**kw) -> dict:
    base = {"checked": True, "lei": "X" * 20, "direct": None, "ultimate": None,
            "direct_exception": None, "ultimate_exception": None,
            "source_url": "https://api.gleif.org/api/v1/lei-records/" + "X" * 20}
    base.update(kw)
    return base


# ── the reader: three-way, never collapsed ─────────────────────────────────

def _report(hierarchy=None, entity_type="company"):
    network = {}
    if hierarchy is not None:
        network["lei_hierarchy"] = hierarchy
    return {"subject": {"name": "PROBE LTD", "jurisdiction": "GB"},
            "identity": {"entity_name": "PROBE LTD", "entity_type": entity_type},
            "network": network}


def _oc7(hierarchy=None, **kw) -> dict:
    rows = ds.assess(_report(hierarchy, **kw), tier="ENHANCED")["resolutions"]
    return {r["question_id"]: r for r in rows}["OC-7"]


def test_a_named_ultimate_parent_answers() -> None:
    """THE CAPABILITY TEST — the question OC-7 actually asks."""
    row = _oc7(_shape(
        direct={"lei": "213800TB53ELEUKM7Q61", "name": "VODAFONE GROUP PUBLIC LIMITED COMPANY"},
        ultimate={"lei": "213800TB53ELEUKM7Q61", "name": "VODAFONE GROUP PUBLIC LIMITED COMPANY"}))
    assert row["state"] in ANSWERED
    assert "vodafone group" in str(row["reason"]).lower()


def test_a_reporting_exception_is_an_ANSWER_not_a_gap() -> None:
    """GLEIF STATING that no parent is reported is the second half of the pass
    condition, and is evidence — an entity at the top of its own tree."""
    row = _oc7(_shape(
        direct_exception={"category": "DIRECT_ACCOUNTING_CONSOLIDATION_PARENT",
                          "reason": "NO_KNOWN_PERSON"},
        ultimate_exception={"category": "ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT",
                            "reason": "NO_KNOWN_PERSON"}))
    assert row["state"] in ANSWERED
    reason = str(row["reason"]).lower()
    assert "no" in reason and "parent" in reason
    assert "no_known_person" in reason.replace(" ", "_") or "no known person" in reason


def test_silence_from_the_authority_is_NOT_an_answer() -> None:
    """THE FALSE CLEAN THIS PREVENTS.

    No parent AND no reporting exception means GLEIF has no statement either
    way. Reporting that as "no parent" would invent a fact about the group
    structure of an entity nobody asked about.
    """
    row = _oc7(_shape())          # checked, but every field empty
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert row["state"] not in ANSWERED


def test_an_unreachable_authority_is_attempted_not_clean() -> None:
    row = _oc7(_shape(checked=False, error="timeout"))
    assert row["state"] == ds.EvidenceState.ATTEMPTED_INCONCLUSIVE.value
    assert row["state"] not in ANSWERED


def test_no_hierarchy_on_the_report_is_not_run() -> None:
    row = _oc7()
    assert row["state"] == ds.EvidenceState.NOT_RUN.value


def test_no_lei_means_the_authority_was_never_asked() -> None:
    """An entity with no LEI cannot be looked up — that is NOT_RUN, not clean."""
    row = _oc7(_shape(checked=False, lei="", error="no LEI"))
    assert row["state"] not in ANSWERED


def test_a_malformed_block_is_never_an_answer() -> None:
    for junk in ("parent", 0, [], {"direct": "yes"}, {"checked": True}):
        assert _oc7(junk)["state"] not in ANSWERED, junk


def test_a_direct_parent_alone_still_answers_but_says_so() -> None:
    """OC-7 names direct AND ultimate; a direct-only answer must not silently
    imply the ultimate parent was established."""
    row = _oc7(_shape(direct={"lei": "A" * 20, "name": "MIDCO LIMITED"}))
    assert row["state"] in ANSWERED
    assert "ultimate" in str(row["reason"]).lower()


# ── it must be wired, or the capability is dark ────────────────────────────

def test_the_orchestrator_actually_calls_the_fetch() -> None:
    """A capability nothing invokes is the R-F3099 shape: built, tested, never run."""
    src = (ROOT / "aria_service/intel/dd_orchestrator.py").read_text(encoding="utf-8")
    assert "fetch_parents" in src, "dd_orchestrator never calls gleif.fetch_parents"
    assert "lei_hierarchy" in src, "the result is never stored on the report"


def test_the_schema_carries_it() -> None:
    """A producer and a consumer with no carrier between them is the R-F3231 shape."""
    import dataclasses
    from aria_service.intel.dd_schema import NetworkSection
    names = {f.name for f in dataclasses.fields(NetworkSection)}
    assert "lei_hierarchy" in names, "NetworkSection has nowhere to hold the hierarchy"


def test_the_reader_is_actually_bound() -> None:
    assert ds.QUESTIONS_BY_ID["OC-7"].reader is not None

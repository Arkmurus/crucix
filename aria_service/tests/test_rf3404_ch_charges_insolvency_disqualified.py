"""R-F3404 — three free Companies House endpoints, and the 404 trap they sit on.

WHAT THIS ADDS. Charges (#12), insolvency (#11) and disqualified directors (#16) were
answerable only from two booleans on the company profile — `has_charges` and
`has_insolvency_history` — and, for disqualification, not at all: `disqualified-directors`
appeared exactly once in the tree, as a domain fragment in an adverse-media allowlist.
All three endpoints are free on the key already deployed.

THE ROOT DEFECT THIS FIXES FIRST. `_get` collapsed FIVE outcomes into `None`: a genuine
404, an exhausted rate-limit, a timeout, a non-200 and any exception. For
`/company/{n}/insolvency` that is fatal, because **Companies House returns 404 for a
company with no insolvency history** (probed 2026-07-29 against solvent 04300718). An
adapter written on `_get` alone would report "no insolvency" identically for a clean
company and for a request that never arrived — a false clean manufactured at the
transport layer, before any DD logic runs.

So `_get_outcome` returns the reason, `_get` delegates to it (one HTTP path, one retry
policy — forking it would recreate the two-aggregators-disagreeing shape), and only
OUTCOME_NOT_FOUND may mean "clean".

The tests below are ordered: transport honesty first, then each endpoint, then the
name-match discipline that stops a disqualification hit becoming an accusation.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import companies_house as ch


def _run(coro):
    return asyncio.run(coro)


# ── transport: the outcome must survive the call ─────────────────────────────

def test_answered_outcomes_are_exactly_ok_and_not_found():
    """A 404 is an ANSWER (the register says no). Everything else is a failure to look.
    Mirrors dd_evidence_standard.RetrievalOutcome, where ZERO_RESULTS/NO_MATCH are
    answers and TIMEOUT/ACCESS_DENIED are not."""
    assert ch.ANSWERED_OUTCOMES == frozenset({ch.OUTCOME_OK, ch.OUTCOME_NOT_FOUND})
    for bad in (ch.OUTCOME_RATE_LIMITED, ch.OUTCOME_TIMEOUT,
                ch.OUTCOME_HTTP_ERROR, ch.OUTCOME_ERROR, ch.OUTCOME_DISABLED):
        assert bad not in ch.ANSWERED_OUTCOMES


def test_get_still_returns_none_on_every_non_ok_outcome():
    """Regression guard for the refactor: ~20 existing callers rely on `_get` returning
    None. Its contract must be byte-for-byte unchanged."""
    for outcome in (ch.OUTCOME_NOT_FOUND, ch.OUTCOME_RATE_LIMITED, ch.OUTCOME_TIMEOUT,
                    ch.OUTCOME_HTTP_ERROR, ch.OUTCOME_ERROR, ch.OUTCOME_DISABLED):
        with patch.object(ch, "_get_outcome", AsyncMock(return_value=(None, outcome))):
            assert _run(ch._get("/anything")) is None


def test_get_passes_payload_through_on_ok():
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=({"a": 1}, ch.OUTCOME_OK))):
        assert _run(ch._get("/anything")) == {"a": 1}


# ── #11 insolvency — the 404 trap ────────────────────────────────────────────

def test_insolvency_404_is_a_clean_answer_not_a_failure():
    """The whole reason _get_outcome exists."""
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=(None, ch.OUTCOME_NOT_FOUND))):
        r = _run(ch.get_insolvency("04300718"))
    assert r["checked"] is True
    assert r["case_count"] == 0
    assert r["cases"] == []


@pytest.mark.parametrize("outcome", [ch.OUTCOME_RATE_LIMITED, ch.OUTCOME_TIMEOUT,
                                     ch.OUTCOME_HTTP_ERROR, ch.OUTCOME_ERROR,
                                     ch.OUTCOME_DISABLED])
def test_insolvency_unreachable_is_never_a_clean_answer(outcome):
    """THE false clean this module exists to prevent: a company we could not check
    must not read the same as a company with no insolvency."""
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=(None, outcome))):
        r = _run(ch.get_insolvency("04300718"))
    assert r["checked"] is False, f"{outcome} produced a clean insolvency result"
    assert "not a clear result" in r["detail"]
    assert r.get("case_count") is None


def test_insolvency_returns_cases_when_present():
    payload = {"cases": [{"type": "creditors-voluntary-liquidation", "number": "1",
                          "practitioners": [{"name": "A. Practitioner"}]}]}
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=(payload, ch.OUTCOME_OK))):
        r = _run(ch.get_insolvency("04300718"))
    assert r["checked"] is True and r["case_count"] == 1
    assert r["cases"][0]["type"] == "creditors-voluntary-liquidation"
    assert r["cases"][0]["practitioners"] == ["A. Practitioner"]


# ── #12 charges ──────────────────────────────────────────────────────────────

def test_charges_counts_outstanding_separately_from_total():
    """A satisfied charge is history; an OUTSTANDING one is a claim over the assets the
    buyer is about to pay for. Collapsing them loses the only number that matters."""
    payload = {"total_count": 3, "unfiltered_count": 3, "items": [
        {"charge_code": "a", "status": "outstanding",
         "persons_entitled": [{"name": "Bank plc"}]},
        {"charge_code": "b", "status": "part-satisfied", "persons_entitled": []},
        {"charge_code": "c", "status": "fully-satisfied", "persons_entitled": []},
    ]}
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=(payload, ch.OUTCOME_OK))):
        r = _run(ch.get_charges("04300718"))
    assert r["checked"] is True
    assert r["total_count"] == 3
    assert r["outstanding_count"] == 2      # outstanding + part-satisfied
    assert r["items"][0]["persons_entitled"] == ["Bank plc"]


@pytest.mark.parametrize("outcome", [ch.OUTCOME_RATE_LIMITED, ch.OUTCOME_TIMEOUT,
                                     ch.OUTCOME_DISABLED])
def test_charges_unreachable_is_never_zero_charges(outcome):
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=(None, outcome))):
        r = _run(ch.get_charges("04300718"))
    assert r["checked"] is False
    assert r.get("total_count") is None


def test_charges_404_is_zero_charges():
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=(None, ch.OUTCOME_NOT_FOUND))):
        r = _run(ch.get_charges("04300718"))
    assert r["checked"] is True and r["total_count"] == 0


# ── #16 disqualified directors — a name is not an identity ───────────────────

def test_disqualified_hit_is_a_candidate_not_a_determination():
    """R-F3089 class, about a named human being: this endpoint matches on NAME ALONE,
    so the result must never present itself as an identification."""
    payload = {"total_results": 2, "items": [
        {"title": "Gary HOWARD", "address_snippet": "Liverpool", "date_of_birth": {"year": 1970}},
        {"title": "John Robert HOWARD", "address_snippet": "Houghton Le Spring"},
    ]}
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=(payload, ch.OUTCOME_OK))):
        r = _run(ch.search_disqualified_officers("Howard"))
    assert r["checked"] is True
    assert r["match_basis"] == "name_only"
    assert "candidates" in r and "matches" not in r
    assert "date of birth" in r["corroboration_required"].lower()


def test_disqualified_no_hits_is_an_answer():
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=(None, ch.OUTCOME_NOT_FOUND))):
        r = _run(ch.search_disqualified_officers("Zzzzz Nobody"))
    assert r["checked"] is True and r["total_results"] == 0


@pytest.mark.parametrize("outcome", [ch.OUTCOME_RATE_LIMITED, ch.OUTCOME_TIMEOUT])
def test_disqualified_unreachable_never_reads_as_clear(outcome):
    with patch.object(ch, "_get_outcome", AsyncMock(return_value=(None, outcome))):
        r = _run(ch.search_disqualified_officers("Howard"))
    assert r["checked"] is False
    assert r.get("total_results") is None


def test_disqualified_rejects_a_too_short_query():
    """Two characters against a national register is a fishing expedition, and every
    hit would be a name coincidence."""
    r = _run(ch.search_disqualified_officers("Ho"))
    assert r["checked"] is False


# ── all three refuse an empty company number rather than guessing ────────────

@pytest.mark.parametrize("fn", [ch.get_charges, ch.get_insolvency])
def test_empty_company_number_is_unchecked_not_clean(fn):
    r = _run(fn(""))
    assert r["checked"] is False

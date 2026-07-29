"""R-F3424 — employment tribunals, and the false positive they would otherwise create.

WHY THE SOURCE EARNS ITS PLACE. Fundamental #17 was answered by CourtListener (US
federal) and a BAILII proxy (senior UK courts). Neither carries employment tribunals —
which is where a UK services employer (facilities management, security, cleaning:
exactly ARIA's market) is actually sued. A counterparty can hold a clean High Court
record and a long tribunal history, and only the second says how it treats the workforce
that will deliver the contract.

THE FALSE POSITIVE THIS SUITE EXISTS TO PREVENT. gov.uk OR-MATCHES the query words.
MEASURED live 2026-07-29:

    q="Silverbrook Capital Management"  ->  total 31,098, first page 20 decisions
        (Al-Khair Foundation, an NHS trust, Bakkavor Foods, United Utilities...)

because "Capital" and "Management" match independently. Reporting `hit_count` or the
index total would have put "31,098 employment tribunal results" against a small asset
manager. The only honest signal is `respondent_count` — decisions whose TITLE places the
subject on the respondent side of the "v" — and on that same query it is ZERO.

THE SIDE OF THE "v" IS THE WHOLE DISCRIMINATION. Titles read
"<claimant> v <respondent>: <case number>":
  respondent -> a claim was brought AGAINST the subject. DD-relevant.
  claimant   -> the subject brought it. A different fact; calling it "litigation
                against them" is simply wrong.
  neither    -> named in someone else's case (a TUPE transferor, a related company).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport
from aria_service.intel.sources import employment_tribunal as et


def _run(coro):
    return asyncio.run(coro)


# ── title parsing: the corroboration handle ──────────────────────────────────

def test_a_simple_title_splits_into_parties_and_case_number():
    p = et.parse_title("Mrs M Nyiramugisha v Mitie Ltd: 6027659/2025")
    assert p["claimant"] == "Mrs M Nyiramugisha"
    assert p["respondent"] == "Mitie Ltd"
    assert p["case_number"] == "6027659/2025"
    assert p["parsed"] is True


def test_a_joined_decision_keeps_all_its_case_numbers():
    """Live shape. Before the `and` was allowed in the pattern the group failed, the
    trailer collapsed, and the RESPONDENT field absorbed the numbers — so the one field
    that turns a name match into a verifiable record came back empty on exactly the
    multi-claim decisions that matter most."""
    p = et.parse_title("Mr A Depala v Mitie Ltd: 3313506/2023 and 3314330/2023")
    assert p["respondent"] == "Mitie Ltd"
    assert "3313506/2023" in p["case_number"] and "3314330/2023" in p["case_number"]


def test_a_title_with_no_v_is_not_treated_as_a_claimant_match():
    p = et.parse_title("Some Administrative Notice 2024")
    assert p["parsed"] is False
    assert p["claimant"] == ""


def test_the_v_separator_cannot_match_a_letter_inside_a_word():
    p = et.parse_title("Mr Vernon Vickers v Vivid Services Ltd: 1234/2024")
    assert p["respondent"] == "Vivid Services Ltd"


# ── side detection ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("Mr R Furey v Mitie Ltd and Mitie Group plc: 2402320/2020", "respondent"),
    ("Mitie Ltd v Mr R Furey: 2402320/2020", "claimant"),
    ("Ms S Thaker v Mr A Rodrigo and others: 3310655/2021", "neither"),
])
def test_side_of_the_v(title, expected):
    assert et._side_of("Mitie", et.parse_title(title)) == expected


# ── the adapter's honesty fields ─────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _client(resp):
    c = MagicMock()
    c.get = AsyncMock(return_value=resp)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


def _with(resp):
    import httpx
    return patch.object(httpx, "AsyncClient", return_value=_client(resp))


def _payload(total, titles):
    return {"total": total,
            "results": [{"title": t, "link": "/employment-tribunal-decisions/x",
                         "public_timestamp": "2024-01-01"} for t in titles]}


def test_the_index_total_is_named_so_it_cannot_be_read_as_relevance():
    """31,098 unrelated matches must never render as a count about the subject."""
    with _with(_Resp(payload=_payload(31098, ["Mrs A P Birzoi v Al-Khair Foundation: 1/2023"]))):
        r = _run(et.search_decisions("Silverbrook Capital Management"))
    assert r["total_index_matches"] == 31098
    assert "total_available" not in r, "a neutral-sounding name invites misreading"
    assert r["total_is_or_matched"] is True
    assert r["respondent_count"] == 0


def test_respondent_count_is_the_signal():
    with _with(_Resp(payload=_payload(503, [
        "Mr R Furey v Mitie Ltd: 1/2020",
        "Ms S Thaker v Mr A Rodrigo: 2/2021",       # body-only
        "Mitie Ltd v Mr X: 3/2022",                  # claimant side
    ]))):
        r = _run(et.search_decisions("Mitie"))
    assert r["hit_count"] == 3
    assert r["respondent_count"] == 1, "only the respondent-side decision counts"


def test_a_failure_is_not_an_empty_result():
    with _with(_Resp(status=500)):
        r = _run(et.search_decisions("Testco"))
    assert r["ok"] is False and r["outcome"] == et.OUTCOME_TIMEOUT
    assert r["hits"] == []


def test_an_answered_empty_index_is_distinguishable_from_a_failure():
    with _with(_Resp(payload={"total": 0, "results": []})):
        r = _run(et.search_decisions("Testco"))
    assert r["ok"] is True and r["outcome"] == et.OUTCOME_EMPTY


def test_a_short_query_is_skipped():
    r = _run(et.search_decisions("Ab"))
    assert r["outcome"] == "skipped"


# ── the DD wiring ────────────────────────────────────────────────────────────

def test_the_identity_layer_calls_it():
    import inspect
    assert "_run_employment_tribunal(report)" in inspect.getsource(ddo._run_identity)


def _report(name="Testco Ltd") -> ARKDDReport:
    r = ARKDDReport()
    r.identity.entity_name = name
    return r


def _drive(report, res):
    with patch.object(et, "search_decisions", AsyncMock(return_value=res)):
        _run(ddo._run_employment_tribunal(report))
    return report


def _res(hits, *, ok=True, outcome="ok", error=None, total=0):
    return {"source": "employment_tribunal", "ok": ok, "outcome": outcome, "error": error,
            "hits": hits, "hit_count": len(hits),
            "respondent_count": sum(1 for h in hits if h.get("side") == "respondent"),
            "total_index_matches": total, "total_is_or_matched": True,
            "citation_url": "https://www.gov.uk/employment-tribunal-decisions",
            "corroboration_required": "Confirm the respondent's registered name."}


def _hit(title, side, case=""):
    return {"title": title, "side": side, "case_number": case,
            "decided": "2024-01-01", "url": "https://x/1"}


def test_respondent_decisions_produce_an_amber_finding_with_case_numbers():
    r = _drive(_report("Mitie"), _res([
        _hit("Mr R Furey v Mitie Ltd: 2402320/2020", "respondent", "2402320/2020"),
        _hit("Ms X v Mitie Ltd: 3313506/2023", "respondent", "3313506/2023"),
    ], total=503))
    f = next(f for f in r.identity.findings if "RESPONDENT" in f.title)
    assert f.severity == "amber"
    assert "2402320/2020" in f.detail


def test_or_match_noise_never_becomes_a_finding():
    """The Silverbrook shape: a big index total, a full page of hits, zero of them
    naming the subject as respondent."""
    r = _drive(_report("Silverbrook Capital Management"), _res(
        [_hit("Mrs A P Birzoi v Al-Khair Foundation: 1/2023", "neither"),
         _hit("Ms T Campbell v NHS Trust: 2/2022", "neither")], total=31098))
    assert not any("RESPONDENT" in f.title for f in r.identity.findings)
    f = next(f for f in r.identity.findings if "No employment tribunal" in f.title)
    assert f.severity == "info"
    assert "matches query words independently" in f.detail


def test_a_claimant_side_appearance_is_not_litigation_against_them():
    r = _drive(_report("Mitie"), _res([_hit("Mitie Ltd v Mr X: 1/2022", "claimant")]))
    assert not any("RESPONDENT" in f.title for f in r.identity.findings)


def test_severity_is_capped_at_amber_however_many_claims():
    """Employment claims are ordinary for a sizeable employer and most settle. A red
    would equate 'has staff disputes' with 'is a compliance risk'."""
    many = [_hit(f"Claimant {i} v Bigco Ltd: {i}/2024", "respondent", f"{i}/2024")
            for i in range(40)]
    r = _drive(_report("Bigco"), _res(many, total=9000))
    f = next(f for f in r.identity.findings if "RESPONDENT" in f.title)
    assert f.severity == "amber"
    assert "settled or dismissed" in f.detail


def test_a_failed_search_is_a_gap_not_a_clean_line():
    r = _drive(_report(), _res([], ok=False, outcome="timeout", error="HTTP 500"))
    assert not any("No employment tribunal" in f.title for f in r.identity.findings)
    gaps = " ".join(r.identity.data_gaps)
    assert "did not complete" in gaps and "not a clear result" in gaps


def test_a_raising_search_is_a_gap():
    r = _report()
    with patch.object(et, "search_decisions", AsyncMock(side_effect=RuntimeError("boom"))):
        _run(ddo._run_employment_tribunal(r))
    assert "NOT searched" in " ".join(r.identity.data_gaps)


def test_a_short_entity_name_is_not_searched():
    with patch.object(et, "search_decisions", AsyncMock()) as m:
        _run(ddo._run_employment_tribunal(_report("AB")))
    assert not m.called

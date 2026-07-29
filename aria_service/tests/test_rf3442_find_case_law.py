"""R-F3442 — Find Case Law (The National Archives), wired and licence-gated.

IS-17a was answered only by CourtListener (US federal) and a BAILII proxy. Find Case Law
is the official UK judgment service and supports a real PARTY search, which is the query
a DD actually needs — a keyword search returns cases that merely contain the subject's
words, which is the name-coincidence class this engine exists to avoid.

The gate here is LEGAL, not financial, and that distinction is the point: the endpoint is
free and needs no key, but the Open Justice Licence forbids computational analysis
without a separate application. Costing nothing is not the same as being permitted, so
ARIA stays silent until the operator confirms the position via
FIND_CASE_LAW_LICENCE_GRANTED. That is a declaration, not a credential — there is nothing
to authenticate.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel.dd_orchestrator import _run_find_case_law
from aria_service.intel.dd_schema import ARKDDReport
from aria_service.intel.sources import find_case_law as fcl


_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Find Case Law</title>
  <entry>
    <title>Acme Widgets Ltd v Beta Supplies Ltd [2025] EWHC 991 (Comm)</title>
    <link href="https://caselaw.nationalarchives.gov.uk/ewhc/comm/2025/991"/>
    <updated>2025-04-18T00:00:00Z</updated>
    <author><name>England and Wales High Court (Commercial Court)</name></author>
  </entry>
  <entry>
    <title>Gamma Ltd v Acme Widgets Ltd [2024] EWCA Civ 77</title>
    <link href="https://caselaw.nationalarchives.gov.uk/ewca/civ/2024/77"/>
    <updated>2024-02-02T00:00:00Z</updated>
    <author><name>Court of Appeal (Civil Division)</name></author>
  </entry>
</feed>"""


@pytest.fixture
def licensed(monkeypatch):
    monkeypatch.setenv("FIND_CASE_LAW_LICENCE_GRANTED", "1")


@pytest.fixture
def unlicensed(monkeypatch):
    monkeypatch.delenv("FIND_CASE_LAW_LICENCE_GRANTED", raising=False)


def _stub_http(monkeypatch, *, status=200, body=_ATOM):
    import httpx

    class _Resp:
        status_code = status
        text = body

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def _report(name="Acme Widgets Ltd"):
    r = ARKDDReport()
    r.identity.entity_name = name
    r.identity.entity_type = "company"
    return r


# ── the licence gate ───────────────────────────────────────────────────────

def test_nothing_runs_until_the_licence_is_confirmed(unlicensed, monkeypatch):
    """The whole point: free to call is not the same as permitted to call."""
    called = {"n": 0}

    async def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("Find Case Law was queried without a confirmed licence")

    monkeypatch.setattr(fcl, "search_by_party", _boom)
    r = _report()
    asyncio.run(_run_find_case_law(r))
    assert called["n"] == 0
    assert not [f for f in r.identity.findings if f.source.startswith("find_case_law")]


def test_unlicensed_is_silent_not_a_failure(unlicensed):
    """An undecided legal question is not a data gap on every DD — the DD form states it
    instead. Reporting it as a failure on every run would train readers to ignore gaps."""
    r = _report()
    asyncio.run(_run_find_case_law(r))
    assert not [g for g in r.identity.data_gaps if "Find Case Law" in g]


def test_the_hint_names_the_licence_and_the_exact_flag(unlicensed):
    hint = fcl.configuration_hint()
    assert "Open Justice Licence" in hint
    assert "FIND_CASE_LAW_LICENCE_GRANTED" in hint
    assert "no code change" in hint.lower(), "activation must be a decision, not a build"


# ── the search itself ──────────────────────────────────────────────────────

def test_licensed_search_returns_the_judgments(licensed, monkeypatch):
    _stub_http(monkeypatch)
    r = _report()
    asyncio.run(_run_find_case_law(r))
    hits = [f for f in r.identity.findings if f.source == "find_case_law.judgments"]
    assert hits, f"a licensed search must produce a finding; gaps={r.identity.data_gaps}"
    assert "2 UK judgment(s)" in hits[0].title
    assert "EWHC 991" in hits[0].detail
    assert hits[0].severity == "amber", "being a party to litigation is not itself adverse"


def test_a_party_appearance_is_not_graded_as_wrongdoing(licensed, monkeypatch):
    """R-F3412 applied: the cases are the finding, the judgement stays with the reader.
    Grading 'is a litigant' as red would equate having disputes with being a risk."""
    _stub_http(monkeypatch)
    r = _report()
    asyncio.run(_run_find_case_law(r))
    f = [x for x in r.identity.findings if x.source == "find_case_law.judgments"][0]
    assert f.severity != "red"
    assert "does not indicate which side" in f.detail


def test_a_searched_and_empty_result_is_reported(licensed, monkeypatch):
    _stub_http(monkeypatch, body='<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"/>')
    r = _report()
    asyncio.run(_run_find_case_law(r))
    hits = [f for f in r.identity.findings if f.source == "find_case_law.judgments"]
    assert hits and "No UK judgment naming this subject" in hits[0].title
    assert "party search, not a keyword match" in hits[0].detail


def test_an_http_failure_is_a_gap_never_a_clean_line(licensed, monkeypatch):
    _stub_http(monkeypatch, status=503)
    r = _report()
    asyncio.run(_run_find_case_law(r))
    assert not [f for f in r.identity.findings if f.source == "find_case_law.judgments"]
    assert any("NOT searched" in g for g in r.identity.data_gaps), r.identity.data_gaps


def test_an_exception_is_a_gap_never_a_clean_line(licensed, monkeypatch):
    async def _raise(*a, **k):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(fcl, "search_by_party", _raise)
    r = _report()
    asyncio.run(_run_find_case_law(r))
    assert any("NOT searched" in g for g in r.identity.data_gaps)


# ── the parser, on its own ─────────────────────────────────────────────────

def test_parse_atom_extracts_title_court_and_url():
    rows = fcl.parse_atom(_ATOM)
    assert len(rows) == 2
    assert rows[0]["title"].startswith("Acme Widgets Ltd v Beta Supplies")
    assert rows[0]["url"].endswith("/ewhc/comm/2025/991")
    assert "Commercial Court" in rows[0]["court"]


def test_parse_atom_respects_the_limit():
    assert len(fcl.parse_atom(_ATOM, limit=1)) == 1


# ── catalogue + checklist wiring ───────────────────────────────────────────

def test_the_catalogue_reports_it_built_and_states_the_blocker(unlicensed):
    from aria_service.intel.dd_standard import RESOLVERS
    spec = RESOLVERS["find_case_law"]
    assert spec.is_built() is True, "the adapter exists — that is a separate fact from the licence"
    ok, why = spec.availability()
    assert ok is False and "Open Justice Licence" in why, why


def test_the_catalogue_reports_it_usable_once_licensed(licensed):
    from aria_service.intel.dd_standard import RESOLVERS
    assert RESOLVERS["find_case_law"].availability()[0] is True


def test_the_checklist_reader_can_see_find_case_law(licensed, monkeypatch):
    """Producer/consumer: IS-17a declares find_case_law as a resolver, so the reader must
    recognise its findings or the question stays NOT_RUN after a successful search."""
    from aria_service.intel.dd_standard import assess

    _stub_http(monkeypatch)
    r = _report()
    asyncio.run(_run_find_case_law(r))
    payload = {"identity": {
        "entity_name": r.identity.entity_name, "entity_type": "company",
        "findings": [{"title": f.title, "detail": f.detail, "source": f.source,
                      "severity": f.severity, "confidence": f.confidence}
                     for f in r.identity.findings],
        "data_gaps": list(r.identity.data_gaps)}}
    row = next(x for x in assess(payload, tier="STANDARD")["resolutions"]
               if x["question_id"] == "IS-17a")
    assert row["state"] != "NOT_RUN", f"IS-17a must be answered once searched: {row}"

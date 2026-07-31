"""R-F3569 — ASK the registers, instead of hoping a different search returns one.

R-F3553/R-F3566 promote a register credential only when some OTHER search happened to
return that page. The searches that run are ADVERSE ones — they query wrongdoing terms —
so an SIA Approved Contractor listing or an Armed Forces Covenant signature was surfaced
by luck. A DD that can only find fault by construction is not neutral.

These tests drive the real sweep with a stubbed backend and assert the user-visible
outcome: a listing becomes a finding, a non-listing does not, and an unanswered register
is reported as UNAVAILABLE rather than silently read as absent.
"""
from __future__ import annotations

import types

import pytest

from aria_service.intel import dd_orchestrator as dd
from aria_service.intel import web_search as ws
from aria_service.intel.dd_schema import Evidence

_SIA_HOST = "services.sia.homeoffice.gov.uk"
_SIA_URL = f"https://{_SIA_HOST}/Pages/acs.aspx?id=1"


class _R:
    """Minimal SearchResult stand-in — the sweep reads url/title/snippet."""

    def __init__(self, url, title, snippet=""):
        self.url, self.title, self.snippet = url, title, snippet


@pytest.fixture
def backend(monkeypatch):
    """Record every query issued, and serve a scripted reply per host."""
    calls: list[str] = []
    replies: dict = {}

    async def _search(query, max_results=5, **kw):
        calls.append(query)
        for host, res in replies.items():
            if f"site:{host}" in query:
                if isinstance(res, Exception):
                    raise res
                return res
        return []

    monkeypatch.setattr(ws, "search", _search, raising=True)
    return {"calls": calls, "replies": replies}


@pytest.mark.asyncio
async def test_every_curated_register_is_actually_queried(backend):
    """CAPABILITY: the gap was that these were NEVER asked."""
    rows, meta = await dd._register_credential_sweep("Acme Security Group", {})
    expected = {h for h in dd._POSITIVE_REGISTERS if h not in dd._NOT_A_CREDENTIAL}
    assert set(meta["queried"]) == expected, "a curated register was never asked"
    for host in expected:
        assert any(f"site:{host}" in q for q in backend["calls"]), f"{host} not queried"
    assert all('"Acme Security Group"' in q for q in backend["calls"]), (
        "the subject name must be quoted, or the query matches any page on the host"
    )


@pytest.mark.asyncio
async def test_a_real_listing_is_captured_as_official_evidence(backend):
    backend["replies"][_SIA_HOST] = [
        _R(_SIA_URL, "Acme Security Group — Approved Contractor", "ACS approved")]
    rows, meta = await dd._register_credential_sweep("Acme Security Group", {})
    assert len(rows) == 1, f"a real listing was not captured: {rows}"
    assert isinstance(rows[0], Evidence)
    assert rows[0].source_tier == "OFFICIAL"
    assert meta["hits"] == 1


@pytest.mark.asyncio
async def test_a_result_not_on_the_register_host_is_rejected(backend):
    """A backend can return anything. Only what is genuinely ON the register counts —
    otherwise a blog post about SIA approval becomes a credential."""
    backend["replies"][_SIA_HOST] = [
        _R("https://blog.example.com/acme-is-sia-approved", "Acme is SIA approved!")]
    rows, meta = await dd._register_credential_sweep("Acme Security Group", {})
    assert rows == [], "an off-register page was captured as a register listing"
    assert meta["hits"] == 0


@pytest.mark.asyncio
async def test_we_asked_and_it_answered_are_recorded_separately(backend):
    """R-F3516's rule. Without this, an empty result cannot be told apart from a
    sweep that never ran — an unverified absence."""
    backend["replies"][_SIA_HOST] = RuntimeError("backend down")
    rows, meta = await dd._register_credential_sweep("Acme Security Group", {})
    assert _SIA_HOST in meta["queried"], "the attempt must be recorded"
    assert _SIA_HOST in meta["failed"], "a failed register must not read as answered"
    assert _SIA_HOST not in meta["answered"]
    assert set(meta["answered"]) | set(meta["failed"]) == set(meta["queried"])


@pytest.mark.asyncio
async def test_one_dead_register_does_not_kill_the_others(backend):
    backend["replies"][_SIA_HOST] = RuntimeError("down")
    backend["replies"]["armedforcescovenant.gov.uk"] = [
        _R("https://armedforcescovenant.gov.uk/x", "Acme Security Group signatory")]
    rows, meta = await dd._register_credential_sweep("Acme Security Group", {})
    assert len(rows) == 1 and meta["failed"] == [_SIA_HOST]


@pytest.mark.asyncio
async def test_an_empty_subject_name_does_not_query_anything(backend):
    rows, meta = await dd._register_credential_sweep("  ", {})
    assert rows == [] and backend["calls"] == []
    assert meta.get("skipped_reason"), "a skipped sweep must say why"


# ── the carrier reaches the promotion ────────────────────────────────────────

def test_register_checks_are_read_by_the_promotion():
    """CAPABILITY end-to-end: a deliberately-found listing becomes a finding."""
    rep = types.SimpleNamespace(
        digital=types.SimpleNamespace(
            register_checks=[Evidence(
                source="Acme Security Group Approved Contractor Scheme",
                source_tier="OFFICIAL", url=_SIA_URL, snippet="ACS approved")],
            press_coverage=[]),
        adverse_media={})
    rows = dd._positive_source_rows(rep)
    assert len(rows) == 1, "the deliberate register check never reached the scan"
    out = dd.positive_register_findings(rows, {"acme", "security"}, as_of="2026-07-31")
    assert len(out) == 1, f"a deliberate register listing was not promoted: {out}"
    assert "SIA Approved Contractor Scheme" in out[0]["title"]


def test_the_carrier_survives_serialisation():
    """A field the report cannot serialise is a producer with no carrier — the
    defect class that has bitten this repo repeatedly."""
    from dataclasses import asdict

    from aria_service.intel.dd_schema import DigitalSection

    d = DigitalSection()
    d.register_checks = [Evidence(source="x", url=_SIA_URL)]
    d.register_checks_meta = {"queried": [_SIA_HOST], "hits": 1}
    blob = asdict(d)
    assert blob["register_checks"][0]["url"] == _SIA_URL
    assert blob["register_checks_meta"]["hits"] == 1


def test_register_listings_are_not_folded_into_press_coverage():
    """A statutory register listing is not press. Folding it in would mislabel it
    and inflate the press count the report prints."""
    from aria_service.intel.dd_schema import DigitalSection

    d = DigitalSection()
    assert hasattr(d, "register_checks")
    assert d.register_checks is not d.press_coverage

"""R-F2929 — the coverage surface must say WHY, and must not vouch for bad data.

Two distinct honesty problems on the same page:

1. Seventeen jurisdictions rendered as a flat "unproven". But "Angola has no public
   registry API at all", "Germany's source has disappeared", "India blocks automated
   access" and "Hungary responds but the scrape no longer matches" are different
   facts. Flattening them implies "we just haven't got round to it" for cases where no
   amount of work by us would change the answer.

2. CZ and SK are genuinely LIVE — a real registry answered — but return the "IČO"
   LABEL where the company name belongs. A green tick with no qualifier would have the
   page vouching for data that is wrong: a false clean on the product surface.
   Downgrading them would be its own inaccuracy, so the row stays live and carries a
   caveat.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aria_service.intel import registry_coverage as rc

VAULT = Path(__file__).resolve().parents[2] / "public" / "vault.html"


def test_rf2929_every_note_class_has_human_text():
    """A class with no summary would render as a bare slug to a customer."""
    for iso2, note in rc._ADAPTER_NOTES.items():
        cls = note.get("class")
        assert cls in rc._NOTE_CLASSES, f"{iso2} uses unknown class {cls!r}"
        assert rc._NOTE_CLASSES[cls].strip(), f"{cls} has no human summary"


def test_rf2929_every_note_is_dated():
    """A verdict with no date is an opinion — these go stale as registries open up."""
    for iso2, note in rc._ADAPTER_NOTES.items():
        assert note.get("probed_at"), f"{iso2} note has no probe date"


def test_rf2929_reason_returns_class_summary_and_date():
    r = rc._reason_for("DE")
    assert r and r["class"] == "source_gone"
    assert "disappeared" in r["summary"].lower()
    assert "offeneregister" in r["detail"].lower()
    assert r["probed_at"] == "2026-07-23"


def test_rf2929_unknown_jurisdiction_has_no_invented_reason():
    """Absence of a note must stay absent, not become a plausible-sounding one."""
    assert rc._reason_for("ZZ") is None
    assert rc._reason_for("") is None


@pytest.mark.parametrize("iso2,cls", [
    ("AO", "stub_no_registry_api"), ("US", "stub_no_registry_api"),
    ("DE", "source_gone"), ("AE", "source_gone"), ("RO", "source_gone"),
    ("IN", "source_blocks_automation"), ("NG", "source_blocks_automation"),
    ("HU", "reachable_unparsed"), ("TR", "reachable_unparsed"), ("GI", "reachable_unparsed"),
])
def test_rf2929_triaged_jurisdictions_carry_their_verdict(iso2, cls):
    assert rc._ADAPTER_NOTES[iso2]["class"] == cls


def test_rf2929_live_rows_carry_no_reason_but_may_carry_a_caveat(monkeypatch):
    """A live row needs no excuse; a stale one would invite discounting real evidence.
    A caveat, however, must survive onto a live row."""
    async def _fake_load():
        return {
            "CZ": {"adapter": "czech_or_justice", "observations": 1,
                   "last_success_at": "2026-07-23T16:59:13+00:00", "consecutive_failures": 0},
            "DE": {"adapter": "germany_offeneregister", "observations": 1,
                   "consecutive_failures": 0},
        }

    monkeypatch.setattr(rc, "_load", _fake_load)
    cov = asyncio.run(rc.coverage())
    j = cov["jurisdictions"]

    assert j["CZ"]["status"] == "live"
    assert "reason" not in j["CZ"], "a live row carried a not-live reason"
    assert "caveat" in j["CZ"], "the CZ data-quality caveat was dropped on a live row"
    assert "IČO" in j["CZ"]["caveat"]

    assert j["DE"]["status"] == "unproven"
    assert j["DE"]["reason"]["class"] == "source_gone"


def test_rf2929_caveat_never_downgrades_liveness(monkeypatch):
    """CZ answered. Marking it not-live would be a different inaccuracy."""
    async def _fake_load():
        return {"CZ": {"adapter": "czech_or_justice", "observations": 1,
                       "last_success_at": "2026-07-23T16:59:13+00:00",
                       "consecutive_failures": 0}}

    monkeypatch.setattr(rc, "_load", _fake_load)
    j = asyncio.run(rc.coverage())["jurisdictions"]
    assert j["CZ"]["live"] is True


# ── the page must actually render it ───────────────────────────────────────

def test_rf2929_page_renders_reason_and_caveat():
    html = VAULT.read_text(encoding="utf-8", errors="ignore")
    assert "covWhyHtml" in html, "the page has no renderer for the reason column"
    assert "Why / caveat" in html, "the table has no reason column header"
    assert "live === true && caveat" in html, (
        "a live row with a caveat would render as an unqualified green tick"
    )
    for cls in ("stub_no_registry_api", "source_gone",
                "source_blocks_automation", "reachable_unparsed"):
        assert cls in html, f"the page cannot render the {cls} reason"


def test_rf2929_page_legend_explains_the_new_states():
    """A symbol a reader cannot decode is decoration, not disclosure."""
    html = VAULT.read_text(encoding="utf-8", errors="ignore")
    assert "Live *" in html
    assert "No registry API" in html and "Source gone" in html
    assert "Blocked (403)" in html and "Needs re-parse" in html


def test_rf2929_table_column_count_is_consistent():
    """A colspan that disagrees with the header count breaks the empty/error states."""
    html = VAULT.read_text(encoding="utf-8", errors="ignore")
    start = html.find('id="coverage-table"')
    head = html[start:start + 400]
    assert head.count("<th>") == 6, f"expected 6 columns, found {head.count('<th>')}"
    body = html[start:start + 6000]
    assert 'colspan="5"' not in body, "a stale colspan=5 remains after adding a column"

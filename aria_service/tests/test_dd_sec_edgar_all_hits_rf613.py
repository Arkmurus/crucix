"""R-F613 — SEC EDGAR surfaces ALL material hits, not just the first.

Past gap (live evidence pattern): SEC EDGAR layer in dd_orchestrator
identity-loop classified hits into red/amber/info buckets but only
the FIRST hit per bucket became a Finding. A company with 5 recent
RED 8-K events (cybersecurity incident + executive departure +
government investigation + financial restatement + going-concern
auditor opinion) only showed ONE in the DD report. Credibility loss
for financial-health DD.

R-F613 changes the orchestrator block to iterate ALL hits per bucket
up to a cap of 5. INFO summary still fires only when no red/amber
present (no clutter when more substantive findings exist).

This is a source-level pin — verifying the orchestrator code shape
without needing to run the full identity layer end-to-end.
"""
from __future__ import annotations

import pathlib

from ._source_probe import repo_path


def _src() -> str:
    return pathlib.Path(
        repo_path("aria_service/intel/dd_orchestrator.py")
    ).read_text(encoding="utf-8", errors="ignore")


def _sec_edgar_block() -> str:
    """Extract the SEC EDGAR branch of the identity-loop."""
    src = _src()
    start = src.find('elif _lbl == "sec_edgar":')
    assert start > 0, "SEC EDGAR branch not found"
    # Block ends at next elif _lbl == "...":
    end = src.find('elif _lbl ==', start + 30)
    return src[start:end] if end > 0 else src[start:start + 6000]


def test_rf613_red_hits_iterate_with_cap_of_five():
    """Block must iterate _red_hits[:5], not pick _red_hits[0]."""
    block = _sec_edgar_block()
    # Old pattern (single-pick) must be GONE
    assert "_b = _red_hits[0]" not in block, (
        "R-F613 regression: SEC EDGAR red branch still picks just _red_hits[0]"
    )
    # New pattern: loop over the slice
    assert "for _b in _red_hits[:5]" in block, (
        "R-F613: SEC EDGAR red branch must iterate _red_hits[:5]"
    )


def test_rf613_amber_hits_iterate_with_cap_of_five():
    block = _sec_edgar_block()
    assert "_b = _amber_hits[0]" not in block, (
        "R-F613 regression: SEC EDGAR amber branch still picks just _amber_hits[0]"
    )
    assert "for _b in _amber_hits[:5]" in block


def test_rf613_info_summary_unchanged_only_when_no_red_or_amber():
    """The INFO summary intentionally only fires when no red/amber are
    present — don't clutter when more substantive findings exist."""
    block = _sec_edgar_block()
    assert "if _info_hits and not (_red_hits or _amber_hits):" in block, (
        "R-F613: INFO summary gating regressed"
    )


def test_rf613_amber_detail_now_includes_items():
    """Amber detail was previously bare; R-F613 also adds the Items:
    line for parity with red branch (operator wants to see WHICH 8-K
    items triggered the amber tag)."""
    block = _sec_edgar_block()
    # Find the amber loop body
    amber_idx = block.find("for _b in _amber_hits[:5]")
    assert amber_idx > 0
    body = block[amber_idx:amber_idx + 800]
    assert "Items:" in body, (
        "R-F613: amber detail must include 'Items: ...' (parity with red)"
    )


def test_rf613_red_branch_still_carries_confirmed_and_full_detail():
    """The red branch must still emit confidence=CONFIRMED + filing_date
    + items, just for each hit instead of one."""
    block = _sec_edgar_block()
    red_idx = block.find("for _b in _red_hits[:5]")
    assert red_idx > 0
    body = block[red_idx:red_idx + 800]
    assert 'confidence="CONFIRMED"' in body
    assert "filing_date" in body
    assert "items" in body.lower()
    assert "severity=\"red\"" in body


def test_rf613_amber_branch_still_carries_probable():
    block = _sec_edgar_block()
    amber_idx = block.find("for _b in _amber_hits[:5]")
    assert amber_idx > 0
    body = block[amber_idx:amber_idx + 800]
    assert 'confidence="PROBABLE"' in body
    assert "severity=\"amber\"" in body

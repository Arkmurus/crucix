"""R-F2782 phase 1 — Companies House accounts metadata must reach the caller.

WHY THIS EXISTS
───────────────
`get_company_profile` fetched the CH `accounts` object and then kept only
`next_due`, discarding everything else. Consequences, both real:

  1. Every non-US entity arrived at financial_health with no filing evidence and
     fell through to "not a US-listed filer (no SEC EDGAR match)" — a live deep
     DD on BAE Systems plc (FTSE-100, fully public UK filings) returned financial
     capacity UNKNOWN for exactly this reason.
  2. `format_for_prompt` reads `profile["accounts"]["next_due"]`, a key the
     profile never contained, so that line always rendered empty.

The tests below drive the real `get_company_profile` (§3c: exercise the broken
path, not a helper) with `_get` stubbed at the HTTP boundary.

THE HONESTY CONSTRAINT IS ALSO TESTED. Filing metadata carries no revenue or
solvency figures, so it must never be presented as a financial verdict —
`has_figures` stays False and nothing here may answer the DD financial_capacity
question. Closing that gate on metadata alone would manufacture a false clean,
which is the one thing a DD report may not do. Phase 2 (CH Document API iXBRL)
supplies the figures.
"""
from __future__ import annotations

import pytest

from aria_service.intel import companies_house as ch

# Shaped like a real CH /company/{number} payload for a large active filer.
_BAE_LIKE = {
    "company_number": "01470151",
    "company_name": "BAE SYSTEMS PLC",
    "company_status": "active",
    "type": "plc",
    "accounts": {
        "next_due": "2026-09-30",
        "next_made_up_to": "2026-12-31",
        "overdue": False,
        "accounting_reference_date": {"day": "31", "month": "12"},
        "last_accounts": {
            "made_up_to": "2025-12-31",
            "type": "full",
            "period_start_on": "2025-01-01",
            "period_end_on": "2025-12-31",
        },
    },
}


@pytest.fixture
def _stub_get(monkeypatch):
    """Stub the HTTP boundary so no live CH call happens."""
    def _install(payload):
        async def _fake_get(path: str, _attempt: int = 0):
            return payload
        monkeypatch.setattr(ch, "_get", _fake_get)
    return _install


async def test_profile_surfaces_accounts_block(_stub_get):
    """The regression: the accounts block must survive the mapping."""
    _stub_get(_BAE_LIKE)
    profile = await ch.get_company_profile("01470151")

    assert profile is not None
    acc = profile.get("accounts")
    assert acc, "accounts block was discarded — this is the R-F2782 defect"

    assert acc["filed"] is True
    assert acc["last_made_up_to"] == "2025-12-31"
    assert acc["last_type"] == "full"
    assert acc["period_end_on"] == "2025-12-31"
    assert acc["overdue"] is False
    assert acc["distress_flags"] == []


async def test_accounts_next_due_still_present(_stub_get):
    """Back-compat: existing consumers of the flat key keep working."""
    _stub_get(_BAE_LIKE)
    profile = await ch.get_company_profile("01470151")
    assert profile["accounts_next_due"] == "2026-09-30"
    # and the nested copy agrees rather than drifting
    assert profile["accounts"]["next_due"] == "2026-09-30"


async def test_metadata_is_never_a_financial_verdict(_stub_get):
    """The honesty constraint — metadata must not look like figures.

    If this ever fails, someone is about to answer financial_capacity from
    filing dates. That is a false clean; send them to phase 2.
    """
    _stub_get(_BAE_LIKE)
    acc = (await ch.get_company_profile("01470151"))["accounts"]

    assert acc["has_figures"] is False
    for banned in ("verdict", "health_verdict", "revenue", "turnover",
                   "total_assets", "z_score", "solvency"):
        assert banned not in acc, (
            f"accounts metadata must not carry '{banned}' — filing dates are "
            "evidence, not a financial health verdict (R-F2782 phase 2)"
        )


async def test_overdue_accounts_raise_a_distress_flag(_stub_get):
    _stub_get({**_BAE_LIKE, "accounts": {
        "overdue": True,
        "last_accounts": {"made_up_to": "2023-12-31", "type": "small"},
    }})
    acc = (await ch.get_company_profile("01470151"))["accounts"]
    assert acc["overdue"] is True
    assert "accounts_overdue" in acc["distress_flags"]


async def test_dormant_accounts_raise_a_distress_flag(_stub_get):
    _stub_get({**_BAE_LIKE, "accounts": {
        "last_accounts": {"made_up_to": "2025-12-31", "type": "dormant"},
    }})
    acc = (await ch.get_company_profile("01470151"))["accounts"]
    assert acc["last_type"] == "dormant"
    assert "dormant_accounts" in acc["distress_flags"]


async def test_never_filed_does_not_read_as_filed(_stub_get):
    """A company with only a next_due has NOT filed — it must not look clean."""
    _stub_get({**_BAE_LIKE, "accounts": {"next_due": "2026-09-30"}})
    acc = (await ch.get_company_profile("01470151"))["accounts"]
    assert acc["filed"] is False
    assert acc["last_made_up_to"] == ""
    assert "no_accounts_filed" in acc["distress_flags"]


async def test_missing_or_malformed_accounts_is_safe(_stub_get):
    """Absent/garbage accounts must degrade honestly, not raise."""
    for payload in ({k: v for k, v in _BAE_LIKE.items() if k != "accounts"},
                    {**_BAE_LIKE, "accounts": None},
                    {**_BAE_LIKE, "accounts": "nonsense"}):
        _stub_get(payload)
        acc = (await ch.get_company_profile("01470151"))["accounts"]
        assert acc["filed"] is False
        assert acc["has_figures"] is False
        assert "no_accounts_filed" in acc["distress_flags"]

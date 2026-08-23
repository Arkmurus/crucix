"""R-F4267 / C-228 - the sanctions table listed OFSI twice and called it twelve lists.

THE LIVE SYMPTOM, from ``ARIA_DD_Vigilo_Solutions_Limited_dd_9fe0e61e4a0c.pdf``.
Its compliance section prints "Sanctions & watchlists screened" as twelve rows,
of which the seventh and the twelfth are::

    HM Treasury Office of Financial Sanctions Implementation - CLEAN
    UK OFSI - HM Treasury Consolidated List                 - CLEAN

OFSI is HM Treasury's sanctions unit and the UK consolidated list is the list it
publishes. One source, two rows, counted as two independent clearances.

THE MECHANISM. `_CANONICAL_SANCTIONS_SOURCES` defines eleven sources and
`derive_verified_sources` returns exactly those eleven, keyed by canonical name
("UK OFSI / HMT"). R-F740's primary-source OFSI adapter then appended its own row
under the key `"uk_ofsi"`, guarded by ``if "uk_ofsi" not in _vs`` - a key that is
not in the canonical registry and therefore is never present, so the guard could
not fire. The module already holds the correct mapping,
``_PRIMARY_ADAPTER_TO_SOURCE["uk_ofsi"] = "UK OFSI / HMT"``; the orchestrator used
a literal instead.

WHY IT IS NOT COSMETIC. "How many independent lists cleared this counterparty" is
a decision input, and this inflates it. It is the same shape as C-39, where a
screen that reached two lists was rendered as ten CLEAN rows: coverage asserted
past what was actually consulted.

THE FIX MUST NOT WEAKEN THE ROW. The direct adapter and the OpenSanctions
aggregate are genuinely different checks that can disagree (see
`_PRIMARY_ADAPTER_TO_SOURCE`'s own comment), so merging is one-directional: an
OFSI hit may RAISE the canonical row to HIT, and nothing may lower a HIT to CLEAN.
"""
from __future__ import annotations

import asyncio

import pytest

import aria_service.intel.registry_adapters as _radp
import aria_service.intel.sanctions as _sanc
import aria_service.intel.sources.fcdo_sanctions as _fcdo
from aria_service.intel import dd_orchestrator as dor
from aria_service.intel._sanctions_classify import (
    _CANONICAL_SANCTIONS_SOURCES,
    _PRIMARY_ADAPTER_TO_SOURCE,
)
from aria_service.intel.dd_schema import ARKDDReport


_OFSI_KEY = _PRIMARY_ADAPTER_TO_SOURCE["uk_ofsi"]


async def _drive(ofsi_ret: dict) -> dict:
    """Drives the REAL _run_identity, as R-F2460's harness does."""
    report = ARKDDReport(target={"name": "Vigilo Solutions Limited", "type": "company"},
                         orchestrator_mode="company", trace_id="t-rf4267")
    report.identity.entity_name = "Vigilo Solutions Limited"

    async def clean_screen(nm, *a, **k):
        return {"matches": [], "screened": True, "source_unavailable": False}

    async def fcdo_lookup(nm, *a, **k):
        return ofsi_ret

    async def noop_primary(*a, **k):
        return False

    async def noop_vault(*a, **k):
        return 0

    async def noop_reg(*a, **k):
        return {}

    from unittest.mock import patch
    with patch.object(_sanc, "screen_with_aliases", clean_screen), \
         patch.object(_sanc, "fuzzy_screen", clean_screen), \
         patch.object(_fcdo, "lookup", fcdo_lookup), \
         patch.object(dor, "_identity_primary_source_screen", noop_primary), \
         patch.object(dor, "_consult_vault_sources", noop_vault), \
         patch.object(_radp, "lookup_entity", noop_reg):
        try:
            await asyncio.wait_for(
                dor._run_identity({"name": "Vigilo Solutions Limited", "type": "company"},
                                  report),
                timeout=60,
            )
        except Exception:
            pass    # downstream of the OFSI block is out of scope, as in R-F2460
    return (report.identity.sanctions_screen or {}).get("verified_sources") or {}


def test_a_clean_ofsi_lookup_does_not_add_a_second_ofsi_row():
    """THE CAPABILITY TEST - reproduces the delivered report's twelve rows."""
    vs = asyncio.run(_drive({"hits": [], "stale": False, "source_unavailable": False}))
    assert isinstance(vs, dict) and vs, "identity layer produced no per-source table"
    assert len(vs) == len(_CANONICAL_SANCTIONS_SOURCES), (
        f"{len(vs)} source rows for {len(_CANONICAL_SANCTIONS_SOURCES)} canonical "
        f"sources — extra keys: {sorted(set(vs) - set(_CANONICAL_SANCTIONS_SOURCES))}. "
        "The report renders one row per entry, so an extra key is an extra list the "
        "customer is told was screened."
    )
    assert set(vs) <= set(_CANONICAL_SANCTIONS_SOURCES), (
        "a primary-source adapter must write into its CANONICAL row "
        f"({_OFSI_KEY!r}), not alongside it"
    )


def test_the_fresh_primary_lookup_is_still_recorded_on_the_canonical_row():
    """R-F740's reason to exist survives: a UK client can see OFSI was checked directly.

    Deduplicating by DROPPING the primary-source evidence would trade a
    double-count for a loss of provenance, which is the worse of the two.
    """
    vs = asyncio.run(_drive({"hits": [], "stale": False, "source_unavailable": False}))
    assert vs[_OFSI_KEY].get("primary_adapter") == "uk_ofsi", (
        f"the fresh direct OFSI lookup left no trace on {_OFSI_KEY!r}: {vs[_OFSI_KEY]!r}"
    )


def test_a_stale_lookup_is_not_recorded_as_a_primary_check():
    """R-F2460's intent, carried onto the canonical row.

    fcdo_sanctions serves its old cache when the feed is down (stale=True,
    source_unavailable=True, error=None). Claiming a primary-source check on that
    is the never-false-clean breach R-F2460 closed.
    """
    vs = asyncio.run(_drive({"hits": [], "stale": True, "source_unavailable": True}))
    assert "primary_adapter" not in vs[_OFSI_KEY], (
        "a STALE OFSI snapshot was recorded as a primary-source check: "
        f"{vs[_OFSI_KEY]!r}"
    )


def test_an_ofsi_hit_raises_the_canonical_row_and_never_clears_it():
    """One-directional merge. A hit on the direct list must not be lost in the merge."""
    vs = asyncio.run(_drive({
        "hits": [{"name": "VIGILO SOLUTIONS LIMITED", "score": 1.0,
                  "regime": "Russia", "citation_url": "https://ofsi.example/list"}],
        "stale": False, "source_unavailable": False,
    }))
    row = vs[_OFSI_KEY]
    assert row.get("status") == "HIT", (
        f"the direct OFSI lookup found a designation and the canonical row still "
        f"reads {row.get('status')!r} — merging must never lower a hit to clean"
    )

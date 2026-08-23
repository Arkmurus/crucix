"""R-F2460 — a STALE / unavailable UK OFSI snapshot must NOT be labelled a
verified source in the DD per-source table.

fcdo_sanctions.lookup() serves its old cache when the feed is down and
_common.mark_stale_if_expired sets stale=True + source_unavailable=True but
leaves error=None. The OFSI augmentation in _run_identity added 'uk_ofsi' to
verified_sources on `not error`, so a stale outage read as verified-clean.
Post-fix it is added only when the snapshot is fresh (not error/stale/unavailable).

Drives the REAL _run_identity company path; the post-screen helpers are no-op'd
(the OFSI block runs before them) and any residual downstream is tolerated — the
assertion is on sanctions_screen['verified_sources'], set right after the block.

R-F4267 (C-228) — WHERE THE CLAIM IS RECORDED MOVED; THE RULE DID NOT.
These tests used to assert on a separate ``"uk_ofsi"`` KEY in verified_sources.
That key WAS the C-228 defect: the table is keyed by canonical source name, so
appending ``"uk_ofsi"`` gave OFSI two rows and the delivered Vigilo Solutions
report (dd_9fe0e61e4a0c) told the customer twelve lists had been screened when
eleven exist. The primary-source check is now recorded as ``primary_adapter`` ON
the canonical ``"UK OFSI / HMT"`` row.

DO NOT re-green a failure here by restoring the ``"uk_ofsi"`` key — that
reintroduces the double-count. What R-F2460 protects is unchanged and is asserted
below: a STALE cached OFSI snapshot must leave no primary-source claim at all.
"""
import asyncio
from unittest.mock import patch

import aria_service.intel.sanctions as _sanc
import aria_service.intel.registry_adapters as _radp
import aria_service.intel.sources.fcdo_sanctions as _fcdo
from aria_service.intel import dd_orchestrator as dor
from aria_service.intel.dd_schema import ARKDDReport


async def _drive(ofsi_ret):
    report = ARKDDReport(target={"name": "Acme Trading Ltd", "type": "company"},
                         orchestrator_mode="company", trace_id="t-rf2460")
    report.identity.entity_name = "Acme Trading Ltd"

    async def clean_screen(nm, *a, **k):
        return {"matches": [], "verified_sources": [], "screened": True, "source_unavailable": False}

    async def fcdo_lookup(nm, *a, **k):
        return ofsi_ret

    async def noop_primary(*a, **k):
        return False

    async def noop_vault(*a, **k):
        return 0

    async def noop_reg(*a, **k):
        return {}

    with patch.object(_sanc, "screen_with_aliases", clean_screen), \
         patch.object(_sanc, "fuzzy_screen", clean_screen), \
         patch.object(_fcdo, "lookup", fcdo_lookup), \
         patch.object(dor, "_identity_primary_source_screen", noop_primary), \
         patch.object(dor, "_consult_vault_sources", noop_vault), \
         patch.object(_radp, "lookup_entity", noop_reg):
        try:
            await asyncio.wait_for(
                dor._run_identity({"name": "Acme Trading Ltd", "type": "company"}, report),
                timeout=30,
            )
        except Exception:
            # Post-screen downstream steps are out of scope; the OFSI block (and
            # thus report.identity.sanctions_screen) has already been set.
            pass
    return report.identity.sanctions_screen or {}


def _ofsi_row(ss: dict) -> dict:
    """The canonical OFSI row, resolved through the registry rather than a literal."""
    from aria_service.intel._sanctions_classify import _PRIMARY_ADAPTER_TO_SOURCE
    vs = ss.get("verified_sources") or {}
    return (vs.get(_PRIMARY_ADAPTER_TO_SOURCE["uk_ofsi"]) or {})


def test_stale_ofsi_not_marked_verified():
    ss = asyncio.run(_drive({"hits": [], "stale": True, "source_unavailable": True}))
    row = _ofsi_row(ss)
    assert "primary_adapter" not in row, (
        f"stale OFSI must NOT be claimed as a primary-source check, got {row}")


def test_fresh_ofsi_still_marked_verified():
    ss = asyncio.run(_drive({"hits": [], "stale": False, "source_unavailable": False}))
    row = _ofsi_row(ss)
    assert row.get("primary_adapter") == "uk_ofsi", (
        f"fresh OFSI lookup should be recorded on the canonical row, got {row}")


if __name__ == "__main__":
    test_stale_ofsi_not_marked_verified()
    print("PASS test_stale_ofsi_not_marked_verified")
    test_fresh_ofsi_still_marked_verified()
    print("PASS test_fresh_ofsi_still_marked_verified")
    print("ALL PASS")

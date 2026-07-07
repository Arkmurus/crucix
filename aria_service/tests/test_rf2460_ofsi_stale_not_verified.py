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


def test_stale_ofsi_not_marked_verified():
    ss = asyncio.run(_drive({"hits": [], "stale": True, "source_unavailable": True}))
    assert "uk_ofsi" not in (ss.get("verified_sources") or []), \
        f"stale OFSI must NOT be verified-clean, got {ss.get('verified_sources')}"


def test_fresh_ofsi_still_marked_verified():
    ss = asyncio.run(_drive({"hits": [], "stale": False, "source_unavailable": False}))
    assert "uk_ofsi" in (ss.get("verified_sources") or []), \
        f"fresh OFSI screen should be verified, got {ss.get('verified_sources')}"


if __name__ == "__main__":
    test_stale_ofsi_not_marked_verified()
    print("PASS test_stale_ofsi_not_marked_verified")
    test_fresh_ofsi_still_marked_verified()
    print("PASS test_fresh_ofsi_still_marked_verified")
    print("ALL PASS")

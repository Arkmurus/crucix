"""R-F2590 — DD re-run crash: OFSI augmentation corrupted verified_sources from a
dict[str,dict] into a list-of-keys, then _run_verification did `.items()` on the
list → "'list' object has no attribute 'items'" → the whole DD failed. Reproduced
LIVE on the operator's "Modirum Gespi" (BR) report, where the OFSI lookup returned
clean and triggered the corruption branch.

Drives the ACTUAL crashing function (_run_verification) — a unit test on a helper
would not count (§3c). The list-shaped case MUST have crashed before the fix.
"""
import asyncio
from aria_service.intel.dd_orchestrator import ARKDDReport, _run_verification


def test_rf2590_list_shaped_verified_sources_does_not_crash():
    # The exact corrupted state the OFSI augmentation produced (dict -> list of keys).
    r = ARKDDReport()
    r.identity.sanctions_screen = {"verified_sources": ["ofac_sdn", "uk_ofsi"], "matches": []}
    # Pre-fix this raised AttributeError at the `.items()` call and failed the DD.
    asyncio.run(_run_verification({}, r))
    assert r.verification is not None  # ran to completion instead of crashing


def test_rf2590_dict_shaped_verified_sources_counts_checked_sources():
    # The correct shape Fix 1 preserves — uk_ofsi CLEAN must be counted, not crash.
    r = ARKDDReport()
    r.identity.sanctions_screen = {
        "verified_sources": {
            "uk_ofsi": {"label": "UK OFSI", "status": "CLEAN", "match_count": 0, "matched_entities": []},
            "ofac_sdn": {"label": "OFAC SDN", "status": "CLEAN", "match_count": 0, "matched_entities": []},
        },
        "matches": [],
    }
    asyncio.run(_run_verification({}, r))
    assert r.verification is not None


def test_rf2590_empty_and_missing_verified_sources_safe():
    # Defensive: no verified_sources key, and a None screen, both must be safe.
    r1 = ARKDDReport()
    r1.identity.sanctions_screen = {"matches": []}
    asyncio.run(_run_verification({}, r1))
    r2 = ARKDDReport()
    r2.identity.sanctions_screen = None
    asyncio.run(_run_verification({}, r2))
    assert r1.verification is not None and r2.verification is not None

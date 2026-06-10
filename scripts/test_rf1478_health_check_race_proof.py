#!/usr/bin/env python
"""R-F1478 capability test — deploy health-check is race-proof and honest.

Drives the REAL fixed comparison (live_health_check.APPS[...]['health_check']),
which now takes (data, expected_sha) instead of closing over a module global read
from a shared .last_deploy_sha file. The bug: ARIA's autonomous ci_deploy commits
as the operator and overwrites .last_deploy_sha mid-deploy, so the check compared
the (correctly deployed) app against the wrong sha and FALSE-FAILED every manual
deploy — cry-wolf that would mask a real outage.

Asserts the two properties that matter:
  1. PASS when the expected sha IS in the app's build_rev (no false fail).
  2. FAIL on a stale/wrong sha (still catches a REAL build mismatch — not always-pass).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_health_check import APPS  # noqa: E402

_INTEL = APPS["intel"]["health_check"]
# A realistic /health/live body — the r-tag list + "· sha <short>" suffix.
_LIVE = {"status": "alive", "build_rev": "R-F1473+R-F1474+R-F1475 · sha b2beb5f5"}


def test_pass_when_expected_in_build_rev():
    assert _INTEL(_LIVE, "b2beb5f5") is True, "should PASS — deployed sha is in build_rev"


def test_fail_on_stale_sha():
    # The race bug: a concurrent ci_deploy wrote 9cc42d8e to .last_deploy_sha while
    # the app actually serves b2beb5f5. A stale sha MUST fail (so real mismatches
    # are still caught) — but the fix makes the deploy pass its OWN sha explicitly.
    assert _INTEL(_LIVE, "9cc42d8e") is False, "stale/wrong sha must FAIL"


def test_fail_when_app_not_alive():
    dead = {"status": "crashed", "build_rev": "R-F1475 · sha b2beb5f5"}
    assert _INTEL(dead, "b2beb5f5") is False, "a non-alive app must FAIL even if sha matches"


def test_fail_when_no_expected_sha():
    # Empty expected -> cannot verify -> FAIL (never silently pass with no anchor).
    assert _INTEL(_LIVE, "") is False, "no expected sha -> cannot verify -> FAIL"


def test_lambdas_accept_two_args():
    # All three app checks must share the (data, exp) signature so check_app_health
    # can call them uniformly. web/wa ignore exp but must accept it.
    assert APPS["web"]["health_check"]("ok", "anything") is True
    assert APPS["wa"]["health_check"]({"status": "connected"}, "anything") is True


def main() -> int:
    tests = [
        test_pass_when_expected_in_build_rev,
        test_fail_on_stale_sha,
        test_fail_when_app_not_alive,
        test_fail_when_no_expected_sha,
        test_lambdas_accept_two_args,
    ]
    failed = 0
    print("=" * 60)
    print("R-F1478: deploy health-check race-proofing capability test")
    print("=" * 60)
    for t in tests:
        try:
            t()
            print(f"✅ {t.__name__} PASSED")
        except AssertionError as e:
            failed += 1
            print(f"❌ {t.__name__} FAILED — {e}")
    print("=" * 60)
    if failed:
        print(f"{failed} FAILED")
        return 1
    print("ALL TESTS PASSED ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())

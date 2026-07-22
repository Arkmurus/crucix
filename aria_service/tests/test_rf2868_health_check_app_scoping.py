"""R-F2868 — the deploy health suite must only check the tiers it deployed.

THE CRY-WOLF (LIST 7 item ⑩, root cause found 2026-07-22)
─────────────────────────────────────────────────────────
`deploy.ps1` hardcoded::

    live_health_check.py --app all --expected-sha $GIT_SHORT

so a `-Web`-only deploy still asserted the NEW sha against aria-intel and aria-wa,
which were never given it. Observed live: R-F2867 deployed aria-web perfectly
(326→327, verified serving) and the suite still printed
``[FAIL] Live health regression suite FAILED``, because intel was legitimately
still on the previous commit and healthy.

That is worse than noise. A gate that fails on a correct deploy teaches everyone
to ignore it — the same failure mode as the permanently-red quota gate R-F2858
fixed. And once ignored, it cannot report the REAL failure it exists to catch.

ROOT CAUSE, NOT SYMPTOM (CLAUDE.md §1): the answer is not to drop
`--expected-sha` (that would gut the R-F1478 protection against a concurrent
ci_deploy overwriting .last_deploy_sha). It is to stop asserting a sha against
apps this deploy never touched. `--app` could only express ONE tier or `all`,
so a two-tier deploy had no way to say what it meant.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "live_health_check.py"

sys.path.insert(0, str(_REPO / "scripts"))
from live_health_check import APPS, select_apps  # noqa: E402


# ── the selector ─────────────────────────────────────────────────────────────

def test_multiple_tiers_can_be_expressed():
    """THE FIX: a two-tier deploy must be able to say so."""
    assert select_apps(["--app", "intel,web"]) == ["intel", "web"]


def test_single_tier_still_works():
    assert select_apps(["--app", "web"]) == ["web"]


def test_all_still_means_all():
    assert select_apps(["--app", "all"]) == list(APPS.keys())


def test_default_is_unchanged():
    """No --app still means intel — existing callers must not shift behaviour."""
    assert select_apps([]) == ["intel"]


def test_whitespace_and_duplicates_are_tolerated():
    """`--app "intel, web, intel"` must not check intel twice or crash."""
    assert select_apps(["--app", "intel, web, intel"]) == ["intel", "web"]


def test_unknown_tier_is_rejected_not_silently_dropped():
    """NEGATIVE CONTROL — silently ignoring a typo would skip a real check.

    `--app intel,wbe` must FAIL loudly. Dropping the unknown token would run a
    green suite that never checked the web tier at all — a false clean created
    by a typo.
    """
    assert select_apps(["--app", "intel,wbe"]) is None


def test_empty_selection_is_rejected():
    assert select_apps(["--app", ""]) is None
    assert select_apps(["--app", " , "]) is None


def test_missing_value_is_rejected():
    assert select_apps(["--app"]) is None


# ── the callers ──────────────────────────────────────────────────────────────

def test_deploy_ps1_scopes_to_the_deployed_tiers():
    """CAPABILITY: the script that cried wolf must no longer say `--app all`."""
    src = (_REPO / "scripts" / "deploy.ps1").read_text(encoding="utf-8", errors="ignore")
    assert "--app all --expected-sha" not in src, (
        "deploy.ps1 must not assert the new sha against tiers it did not deploy"
    )
    assert "$healthApps" in src, "the app list must be built from the deploy switches"
    # The R-F1478 protection must survive this change.
    assert "--expected-sha" in src, (
        "dropping --expected-sha would gut R-F1478 (concurrent ci_deploy overwrite)"
    )


def test_deploy_sh_stays_in_sync():
    """deploy.sh documents itself as mirroring deploy.ps1 — keep it true."""
    src = (_REPO / "scripts" / "deploy.sh").read_text(encoding="utf-8", errors="ignore")
    # Check the INVOCATION line, not any occurrence — a comment explaining what
    # this used to be is not a regression, and matching it would make the guard
    # fire on its own documentation.
    invocations = [
        line for line in src.splitlines()
        if "live_health_check.py" in line and not line.strip().startswith("#")
    ]
    assert invocations, "deploy.sh must still run the health suite"
    for line in invocations:
        assert "--app all" not in line, f"deploy.sh must scope its health check: {line.strip()}"
    assert "HEALTH_APPS" in src, "the bash mirror needs the same app list"


def test_script_still_runs_and_reports_its_scope():
    """END-TO-END: the real CLI accepts the new form and says what it checked.

    Uses a deliberately unknown tier so nothing is probed over the network — the
    suite must never depend on live apps being up.
    """
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--app", "intel,nope"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1, "an unknown tier must exit non-zero"
    assert "nope" in (proc.stdout + proc.stderr), "the bad tier must be named"

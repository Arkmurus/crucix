"""R-F3728 — CAPABILITY: the wiring ledger must identify a MODULE, not a name.

R-F3727 widened the §21a audit to all of aria_service and baselined the existing
debt. Its key was parsed out of the issue TEXT — but check_wiring_present emits
only the module's BASENAME ("git_utils.py: NO brain wiring found"). Two
consequences, both measured on the live tree:

  COLLISION  `aria_service/guardian/interpret.py` and
             `aria_service/intent/interpret.py` are two distinct dark modules
             that shared ONE entry. The baseline recorded 66 where the truth is
             67; whichever was seen second was silently accepted as "known"
             forever — a hole in the exact gate meant to close a hole.
  BRITTLE    keying on message text means rewording the advice invalidates every
             entry at once, so the gate reports 67 spurious NEW dark modules and
             teaches people to re-baseline instead of read it.

Fixed by scanning per file, so the path is known rather than parsed, and by
recording a stable verdict CATEGORY alongside it.

Run: python -m pytest aria_service/tests/test_rf3728_wiring_baseline_key.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from ._source_probe import repo_path

AUDIT = repo_path("scripts/ci/wiring_audit.py")
BASELINE = repo_path("docs/wiring_audit_baseline.json")


def _baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))["known_dark"]


def test_same_named_modules_get_distinct_entries():
    """THE HEADLINE: a basename key merged these two and hid one."""
    kd = _baseline()
    assert isinstance(kd, dict), "the ledger must map PATH -> verdict, not be a bare list"
    both = [p for p in kd if p.endswith("/interpret.py")]
    assert len(both) == 2, (
        f"guardian/interpret.py and intent/interpret.py are distinct modules and "
        f"must hold distinct entries; got {both}"
    )
    assert "aria_service/guardian/interpret.py" in kd
    assert "aria_service/intent/interpret.py" in kd


def test_every_key_is_a_real_path_that_exists():
    """A key that is not a path cannot be checked off, so the debt never shrinks."""
    for p in _baseline():
        assert "/" in p, f"{p!r} is a basename, not a module path"
        assert repo_path(p).exists(), f"ledger names a module that does not exist: {p}"


def test_verdicts_are_from_the_known_vocabulary():
    allowed = {"no-wiring", "missing-failure", "missing-success", "other"}
    bad = {p: v for p, v in _baseline().items() if v not in allowed}
    assert not bad, f"unrecognised verdicts (a reword would slip through): {bad}"


def test_the_repo_passes_its_own_wiring_gate():
    r = subprocess.run([sys.executable, str(AUDIT)], cwd=str(repo_path(".")),
                       capture_output=True, text=True,
                       # R-F3805 — 90s, not 600: must stay BELOW the 120s per-test
                       # budget so this bound trips FIRST and names the failure,
                       # rather than pytest-timeout killing the process silently
                       # (R-F3459). Measured 2026-08-09: all 6 tests here run in ~22s.
                       timeout=90)
    assert r.returncode == 0, (
        f"the tree does not pass the wiring audit, so CI is red on arrival:\n{r.stdout}"
    )


def test_a_new_dark_module_fails_the_gate(tmp_path):
    """The gate must still bite — a ledger that accepts everything is not a gate."""
    probe = repo_path("aria_service/utils/_rf3728_probe.py")
    probe.write_text(
        "import os\n\n\n"
        "def doit(p):\n"
        '    """An engine path that swallows failure and reaches no brain sink."""\n'
        "    try:\n"
        "        return open(p).read()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        return os.stat(p)\n"
        "    except Exception:\n"
        "        return None\n" + "# pad\n" * 40,
        encoding="utf-8",
    )
    try:
        r = subprocess.run([sys.executable, str(AUDIT)], cwd=str(repo_path(".")),
                           capture_output=True, text=True, timeout=90)
        assert r.returncode == 1, f"a NEW dark module must FAIL the gate\n{r.stdout}"
        assert "_rf3728_probe.py" in r.stdout
        # and it must be named by PATH, so it can actually be found and fixed
        assert "aria_service/utils/_rf3728_probe.py" in r.stdout.replace("\\", "/")
    finally:
        probe.unlink(missing_ok=True)


def test_an_unreadable_ledger_fails_closed(tmp_path, monkeypatch):
    """Absence of evidence is not evidence of wiring (the R-F3717 lesson)."""
    import shutil
    backup = tmp_path / "baseline.json"
    shutil.copy(BASELINE, backup)
    BASELINE.write_text("{ not json", encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, str(AUDIT)], cwd=str(repo_path(".")),
                           capture_output=True, text=True, timeout=90)
        assert r.returncode == 2, (
            f"a corrupt ledger must exit 2 (cannot run), never 0 (clean)\n"
            f"{r.stdout}{r.stderr}"
        )
    finally:
        shutil.copy(backup, BASELINE)

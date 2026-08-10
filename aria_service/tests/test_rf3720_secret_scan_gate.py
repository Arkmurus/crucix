# allowlist-secret-file — R-F3827: this suite is the SECRET SCANNER'S OWN test
# fixtures, so it must contain credential-SHAPED strings to be worth anything.
# Every value here is SYNTHETIC; never paste a live credential into a file that
# has opted out. Same declaration as its siblings test_rf1563_chat_pii_redaction
# and test_rf1832_sast_scan_ast_aware (R-F3683).
#
# WHY THIS WAS NEEDED. scripts/admin/secret_scan.py accepted these three values
# via its hash-keyed baseline (docs/secret_scan_baseline.json, 27 fixtures) and
# reported CLEAN, while scripts/pre-commit --check-all uses an at-the-site PRAGMA
# and failed on the same three. Two deliberate mechanisms answering one question,
# and this file was declared to only one of them — so CI's pre-commit step failed
# on EVERY commit, blocking the pipeline for everybody.
"""R-F3720 — CAPABILITY: the gate that keeps a credential out of a PUBLIC repo.

`Arkmurus/crucix` is public and had NO secret scanning of any kind — no gitleaks,
no trufflehog, no pre-commit hook, nothing in seven workflows.

These tests drive the real scanner as a subprocess (the way CI invokes it), not a
helper, and assert the three properties that decide whether it is worth having:

  CATCHES   a real credential value, including one planted in a file whose other
            findings are already baselined — that is the property a path-based
            "skip tests/" exemption would destroy.
  IGNORES   a fly DIGEST and an env-var REFERENCE. Both cost real time on
            2026-08-05: the digest read as a leak and nearly triggered a
            three-app token rotation (R-F3721); the references produced 19 false
            positives in one run. A gate that cries wolf gets switched off.
  FAILS     loudly when it cannot run. An unreadable baseline exits 2, never 0 —
            absence of evidence is not evidence of cleanliness.

Run: python -m pytest aria_service/tests/test_rf3720_secret_scan_gate.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ._source_probe import repo_path

SCANNER = repo_path("scripts/admin/secret_scan.py")


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCANNER), *args],
        # R-F3805 — 90s, not 300: it must stay BELOW the 120s per-test budget so
        # this bound trips FIRST and yields one named failure, instead of
        # pytest-timeout killing the process with no summary (R-F3459).
        # Measured 2026-08-09: all 8 tests in this file run in ~28s total.
        cwd=str(cwd), capture_output=True, text=True, timeout=90,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo — the scanner reads `git ls-files`."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def _commit(repo: Path, name: str, body: str) -> None:
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=repo, check=True)


def test_it_catches_a_real_credential(repo: Path):
    _commit(repo, "app.py", 'ARIA_API_TOKEN = "Xk92mQp4Lv7WzR3nT8yB6cF1sD5gH0jA"\n')
    r = _run(repo)
    assert r.returncode == 1, f"a committed credential must FAIL the gate\n{r.stdout}"
    assert "ARIA_API_TOKEN" in r.stdout


def test_it_catches_a_vendor_key(repo: Path):
    _commit(repo, "cfg.js", 'const k = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8";\n')
    r = _run(repo)
    assert r.returncode == 1
    assert "GitHub token" in r.stdout


def test_a_fly_digest_is_not_a_leak(repo: Path):
    """R-F3721 — this exact confusion nearly caused a 3-app token rotation."""
    _commit(repo, "NOTES.md",
            "| var | digest |\n| `ARIA_API_TOKEN` | `913fcdca1cf8d901` |\n")
    r = _run(repo)
    assert r.returncode == 0, f"a DIGEST is not a credential value\n{r.stdout}"


def test_an_env_reference_is_not_a_leak(repo: Path):
    """Reading a secret from the environment is the CORRECT pattern."""
    _commit(repo, "a.mjs",
            "const INT_TOKEN = process.env.ARIA_INTERNAL_TOKEN;\n"
            "JWT_SECRET = os.getenv('JWT_SECRET')\n")
    r = _run(repo)
    assert r.returncode == 0, f"env lookups must not fire\n{r.stdout}"


def test_a_placeholder_is_not_a_leak(repo: Path):
    _commit(repo, ".env.sample",
            'API_TOKEN = "your-token-here-replace-me-000000"\n')
    r = _run(repo)
    assert r.returncode == 0, r.stdout


def test_a_baselined_file_still_fails_on_a_DIFFERENT_secret(repo: Path):
    """THE HEADLINE: the baseline accepts a VALUE, not a FILE.

    A path exemption ("skip tests/") would let a real key pasted into an already-
    accepted file through — and tests are exactly where a careless paste lands.
    """
    fixture = 'FAKE_TOKEN = "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk"\n'
    _commit(repo, "tests/test_redaction.py", fixture)
    assert _run(repo, "--update-baseline").returncode == 0
    assert _run(repo).returncode == 0, "the accepted fixture should pass"

    # same file, same rule — a DIFFERENT value
    _commit(repo, "tests/test_redaction.py",
            fixture + 'REAL_TOKEN = "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2Rr1Qq0P"\n')
    r = _run(repo)
    assert r.returncode == 1, (
        "a NEW credential in a baselined file must still fail — otherwise the "
        f"baseline is a hole, not a gate\n{r.stdout}"
    )
    assert "REAL_TOKEN" in r.stdout


def test_an_unreadable_baseline_fails_closed(repo: Path):
    """Absence of evidence must not be reported as cleanliness."""
    _commit(repo, "app.py", "x = 1\n")
    b = repo / "docs" / "secret_scan_baseline.json"
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text("{ this is not json", encoding="utf-8")
    r = _run(repo)
    assert r.returncode == 2, (
        f"a corrupt baseline must exit 2 (cannot run), never 0 (clean)\n{r.stdout}{r.stderr}"
    )


def test_the_repo_itself_is_clean():
    """The live gate: this repo must pass, or CI is broken on arrival."""
    r = _run(repo_path("."))
    assert r.returncode == 0, (
        f"the repo does not pass its own secret gate:\n{r.stdout}"
    )
    baseline = json.loads(repo_path("docs/secret_scan_baseline.json")
                          .read_text(encoding="utf-8"))
    assert baseline.get("accepted"), "baseline must record what it accepts"

"""R-F4117 (C-159) — the secret-scan baseline must stay a reviewable artefact,
never a hole.

The gate was RED on two synthetic fixtures in
`test/upload-path-end-to-end-rf4017.test.mjs`, and a red secret-scan gate
certifies nothing — it is the one check between a pasted credential and a public
repo. The fixtures were accepted through the designed path
(`secret_scan.py --update-baseline`), which keys acceptance on
`sha256(path|rule|VALUE)` rather than on a path exemption.

`secret_scan.py`'s own comment states the property this file exists to PROVE
rather than assert:

    "The known fixtures pass; swap one for a live credential in the SAME file on
     the SAME line and the hash changes, so it is a new finding and CI fails.
     The baseline is a reviewable artefact, not a hole."

That was a comment. Now it is a test — because the whole point of the mechanism
is the part nobody re-checks after the first review, and an acceptance list that
has quietly become a path exemption looks identical from the outside.

`--update-baseline` accepts EVERYTHING currently found, so the danger is not the
mechanism but the habit: run it without reading the diff and it silently blesses
a real credential. The counts test below is the cheap tripwire for that.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from aria_service.tests._source_probe import repo_path

_SCANNER = "scripts/admin/secret_scan.py"
_BASELINE = "docs/secret_scan_baseline.json"

# The two fixtures accepted by R-F4117, keyed by their VALUE hash.
_RF4017 = {
    "beb755e4828fbaa44898",   # the rf4017 internal-token fixture
    "93decdda682fa9934a0b",   # the rf4017 jwt-secret fixture
}

# Assembled at runtime, never written as a literal assignment.
#
# The first version of this file pasted the fixture values into its own source
# and the scanner immediately reported FIVE new findings — the test that proves
# the gate works, tripping the gate. It was right to: "never commit the value"
# applies to the person documenting the incident too, and a fixture is only
# harmless until someone copies the shape and fills in a real key. So nothing
# here forms a scannable `NAME = 'value'` pair on one line.
_VAR = "INTERNAL" + "_TOKEN"
_FIXTURE = "rf4017-" + "internal-" + "token"
_LIVE_SHAPED = "sk-" + "ant-" + "Xk92LmQp7vRt3Yw8Nb5Zc1Ae6Df4Gh0Jk2Ln"


def _scan(cwd=None):
    return subprocess.run(
        [sys.executable, str(repo_path(_SCANNER))],
        cwd=cwd or repo_path("."), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _accepted() -> dict:
    return json.loads(repo_path(_BASELINE).read_text(encoding="utf-8"))["accepted"]


def test_the_repo_passes_its_own_secret_gate():
    """The user-visible symptom: CI was red on this."""
    r = _scan()
    assert r.returncode == 0, (
        "the repo does not pass its own secret gate:\n" + r.stdout + r.stderr)
    assert "CLEAN" in r.stdout, r.stdout


def test_the_rf4017_fixtures_are_accepted_by_value_hash():
    acc = _accepted()
    missing = _RF4017 - set(acc)
    assert not missing, (
        f"the R-F4117 acceptances are gone from {_BASELINE}: {sorted(missing)}")
    for h in _RF4017:
        assert "rf4017" in acc[h], (
            f"{h} no longer describes the rf4017 fixture: {acc[h]!r}")


def test_the_baseline_is_keyed_on_the_value_not_the_path():
    """A path- or line-keyed acceptance WOULD be a hole. Pin the mechanism, so
    a future 'simplification' to `skip tests/` fails here."""
    src = repo_path(_SCANNER).read_text(encoding="utf-8")
    assert 'f"{path.as_posix()}|{rule}|{value}"' in src, (
        "the fingerprint no longer includes the VALUE — the baseline has "
        "become a path exemption, which is exactly what secret_scan.py's own "
        "comment argues against")


def test_a_real_credential_on_an_accepted_line_is_still_caught(tmp_path):
    """THE capability test: the accepted hash must protect the FIXTURE, not the
    line it sits on.

    Runs against a throwaway copy of the repo layout rather than mutating the
    real file — a test that edits tracked source and restores it can leave the
    tree dirty if it fails midway, and this suite runs alongside a §16 baseline
    that reads the working tree.
    """
    import shutil

    root = repo_path(".")
    work = tmp_path / "repo"
    (work / "scripts" / "admin").mkdir(parents=True)
    (work / "docs").mkdir()
    (work / "test").mkdir()
    shutil.copy(root / _SCANNER, work / _SCANNER)
    shutil.copy(root / _BASELINE, work / _BASELINE)

    victim = work / "test" / "upload-path-end-to-end-rf4017.test.mjs"
    # The accepted fixture, verbatim: the scanner must stay quiet about it.
    victim.write_text(f"const {_VAR} = '{_FIXTURE}';\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)

    quiet = _scan(cwd=work)
    assert quiet.returncode == 0, (
        "the accepted fixture must NOT be reported:\n" + quiet.stdout)

    # Same file, same line, real-shaped credential.
    victim.write_text(f"const {_VAR} = '{_LIVE_SHAPED}';\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)

    loud = _scan(cwd=work)
    assert loud.returncode != 0, (
        "a live-shaped credential on an ACCEPTED line was not reported — the "
        "baseline has become a hole:\n" + loud.stdout)
    assert "Anthropic API key" in loud.stdout, loud.stdout


def test_the_accepted_set_did_not_balloon():
    """`--update-baseline` accepts everything it currently finds. If someone
    runs it over a tree containing a real credential and does not read the
    diff, this is the cheap tripwire.

    Deliberately a CEILING, not an equality: adding a legitimate fixture is
    normal and should not fail. A jump of tens means nobody reviewed."""
    n = len(_accepted())
    assert n <= 40, (
        f"{n} accepted secret findings — the baseline is meant to be a short, "
        "reviewed list of synthetic fixtures. Read the diff of the last "
        "`--update-baseline` before raising this ceiling.")

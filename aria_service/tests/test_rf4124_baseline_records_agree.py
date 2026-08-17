"""R-F4124 — the three baseline records must not drift apart.

§16 says there is ONE baseline and warns "do not create a fourth [file]". The
number is nonetheless kept in three places: `docs/suite_baseline.json` (written
by the tool, machine-read by the CI gate), `docs/suite_baseline.md` (the prose
record), and the CLAUDE.md §16 headline (what every session reads first).

They have drifted three times, and each time the drift was discovered by a human
noticing, not by a check:

* §16 records a headline of `112 / 13,725 @ 0c3e853d` that "matched neither the
  JSON nor `suite_baseline.md`" — a third figure.
* On 2026-08-17 the `.md` and the headline both said `89 @ e68f0088 / 1,731
  files` while the JSON said `90 @ 168674b2 / 1,738`. Three records, two stale.

A stale baseline number is not cosmetic: it is the reference every "is this a
regression?" question is answered against, so a session comparing against the
wrong figure reaches the wrong verdict about its own work.

The JSON is authoritative — it is the tool's own output and the only one a
machine reads. These tests assert the prose agrees with it.
"""
from __future__ import annotations

import json
import re

from aria_service.tests._source_probe import repo_path


def _baseline() -> dict:
    return json.loads(
        repo_path("docs/suite_baseline.json").read_text(encoding="utf-8"))


def _md() -> str:
    return repo_path("docs/suite_baseline.md").read_text(encoding="utf-8")


def _claude_md_headline() -> str:
    text = repo_path("CLAUDE.md").read_text(encoding="utf-8")
    i = text.index("**CURRENT BASELINE")
    return text[i:i + 400]


def test_the_json_is_a_valid_recording():
    """A baseline recorded from an invalid run must never be published."""
    b = _baseline()
    assert b.get("valid") is True, b.get("valid")
    t = b["totals"]
    assert t["failed"] + t["passed"] == t["total"], t
    assert len(b["failures"]) == t["failed"], (
        f"{len(b['failures'])} node ids listed but totals say {t['failed']}")


def test_the_prose_record_agrees_with_the_json():
    b, md = _baseline(), _md()
    t = b["totals"]
    for token in (f"{t['failed']} failed",
                  f"{t['passed']:,} passed",
                  f"{t['files']:,} files",
                  b["commit"]):
        assert token in md, (
            f"docs/suite_baseline.md does not carry {token!r} — the prose "
            "record has drifted from the JSON the tool wrote")


def test_the_claude_md_headline_agrees_with_the_json():
    """The headline is what every session reads first, so a stale one sends the
    whole session comparing against the wrong reference."""
    b, head = _baseline(), _claude_md_headline()
    t = b["totals"]
    for token in (f"{t['failed']} failed",
                  f"{t['passed']:,} passed",
                  f"{t['files']:,} files",
                  b["commit"]):
        assert token in head, (
            f"the CLAUDE.md §16 headline does not carry {token!r} — it has "
            f"drifted from docs/suite_baseline.json. Headline was: {head[:200]}")


def test_the_environment_is_stamped():
    """R-F3794: a failure set is a function of the code AND the installed
    packages. A baseline without a fingerprint cannot tell a dependency bump
    from a code regression."""
    env = _baseline().get("environment") or {}
    for key in ("python", "platform", "packages_sha256"):
        assert env.get(key), f"baseline carries no {key}"


def test_the_drift_guard_can_fail():
    """A guard that cannot fail is the defect this repo keeps finding."""
    b = _baseline()
    bogus = f"{b['totals']['failed'] + 7} failed"
    assert bogus not in _md(), (
        "the guard is matching something it should not — it would pass on a "
        "count that was never recorded")

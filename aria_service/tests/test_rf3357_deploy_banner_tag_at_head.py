"""R-F3357 — a deploy tag sitting AT HEAD made the banner claim it ships nothing.

R-F3247 identified this exact UNDER-CLAIM and then fixed only its symptom: the
fallback string was renamed from "no-r-tag" to "no-new-r-numbers", which reads
the same way ("this build ships nothing") on a build containing everything. The
CONDITION — `git log $LAST_TAG..HEAD` being empty because the newest deploy tag
points at HEAD — was untouched. Its guard,
`test_rf3247_empty_range_does_not_read_as_shipping_nothing`, asserts the new
STRING is present in both scripts, so a rename satisfied it. Wording, not property.

MEASURED LIVE 2026-07-28: aria-intel and aria-web were deployed from one commit
(521e32d2). The intel deploy tagged that commit, so minutes later the web deploy
found the range empty and aria-web served:

    build_rev: "521e32d2ce03 · no-new-r-numbers"

while actually shipping R-F3351 (the restored orphan alert) and R-F3352 (the
sensor labels). Anyone probing /api/health to learn what was live on web — which
is precisely what section 11 tells every agent to do — would have been told
nothing shipped. Deploying two apps from one commit is the NORMAL case here, so
this misreports routinely, not rarely.

The fix takes the newest deploy tag with at least one commit before HEAD, i.e.
the last DISTINCT deploy point, which is the question the banner answers.

This test drives REAL git against a temporary tag at HEAD rather than reading the
scripts' source text, because source-text assertions are what let the cosmetic
fix through in the first place.
"""
from __future__ import annotations

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
_PROBE_TAG = "deploy-zzzz-rf3357-probe"
BOOKKEEPING = re.compile(r"^chore:\s*(reserve|mark|ship)")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def _tags_newest_first() -> list[str]:
    out = _git("tag", "--list", "deploy-*", "--sort=-version:refname")
    return [t for t in out.splitlines() if t.strip()]


def _naive_last_tag() -> str:
    """The pre-R-F3357 rule: newest deploy tag, whatever it points at."""
    tags = _tags_newest_first()
    return tags[0] if tags else ""


def _fixed_last_tag() -> str:
    """R-F3357: newest deploy tag with at least one commit before HEAD."""
    for t in _tags_newest_first():
        count = _git("rev-list", "--count", f"{t}..HEAD")
        if count.isdigit() and int(count) > 0:
            return t
    return ""


def _r_numbers_for(last_tag: str) -> list[str]:
    rng = f"{last_tag}..HEAD" if last_tag else "HEAD"
    subjects = _git("log", rng, "--pretty=%s").splitlines()
    kept = [s for s in subjects if not BOOKKEEPING.match(s)]
    return sorted({m for s in kept for m in re.findall(r"R-F[0-9]+", s)})


def test_rf3357_a_tag_at_head_no_longer_empties_the_banner():
    """Reproduce the live condition: tag HEAD, then ask both rules what shipped."""
    assert _tags_newest_first(), "repo has no deploy-* tags; this test needs at least one"
    subprocess.run(["git", "tag", "-f", _PROBE_TAG, "HEAD"], cwd=ROOT,
                   capture_output=True, text=True)
    try:
        # CONTROL — the old rule picks the tag at HEAD, so the range is empty and
        # the banner falls back to "no-new-r-numbers" on a build containing work.
        naive = _naive_last_tag()
        assert naive == _PROBE_TAG, "probe tag must sort newest for this to reproduce"
        assert _r_numbers_for(naive) == [], (
            "the pre-fix condition did not reproduce, so this test proves nothing"
        )

        # FIXED — skips the tag at HEAD and reports against the last DISTINCT
        # deploy point, so the banner names what the build actually contains.
        fixed = _fixed_last_tag()
        assert fixed != _PROBE_TAG, "the fixed rule must skip a tag pointing at HEAD"
        assert int(_git("rev-list", "--count", f"{fixed}..HEAD")) > 0, \
            "the chosen tag must be strictly behind HEAD"
    finally:
        subprocess.run(["git", "tag", "-d", _PROBE_TAG], cwd=ROOT,
                       capture_output=True, text=True)
    assert _PROBE_TAG not in _tags_newest_first(), "probe tag leaked into the repo"


def test_rf3357_both_deploy_writers_carry_the_fix():
    """This repo has shipped the same defect twice by fixing one of two writers
    (R-F3019->R-F3031->R-F3038, R-F3039->R-F3050), which is why R-F3247 checks
    both. The condition fix must reach both too, not just the string."""
    ps1 = (ROOT / "scripts" / "deploy.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    for name, src in (("deploy.ps1", ps1), ("deploy.sh", sh)):
        assert "R-F3357" in src, f"{name} lost the fix"
        assert "rev-list --count" in src, (
            f"{name} still selects the newest tag unconditionally — renaming the "
            f"fallback string does not stop an empty range"
        )

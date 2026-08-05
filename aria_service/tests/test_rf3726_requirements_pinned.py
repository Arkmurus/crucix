"""R-F3726 — every aria_service dependency must be pinned with `==`.

Symptom (Cure Protocol census, docs/cure/defects.md C-01, 2026-08-05):
`aria_service/requirements.txt` declared 34 packages, ALL of them with `>=`
and none with `==`. Two builds of the same commit could therefore resolve
different package versions.

Why that matters beyond tidiness:

  * CLAUDE.md §11 makes `build_rev` the deploy control — it proves WHICH COMMIT
    is live. With a floating dependency set it does not prove WHICH CODE is
    live, because the installed third-party tree can differ between two builds
    of the same SHA.
  * CLAUDE.md §16 records a suite baseline (112 failed / 13,725 passed) that is
    only meaningful if the environment is fixed. A torch or chromadb minor bump
    can move it with no commit at all — and §16 already warns that a full-suite
    number without a validity record is not publishable.
  * Cure Protocol Phase 2 gates every PR on a frozen-fixture gold set. A fixture
    cannot be frozen on top of a moving dependency set.

The pins were taken from `pip freeze` inside the RUNNING aria-intel machine,
so every version here is one production has actually booted — not a resolver's
best guess from this Windows/ARM64 dev box, where five of these publish no
wheel at all (CLAUDE.md §16).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REQ = Path(__file__).resolve().parents[1] / "requirements.txt"

# name[extras] <op> version   (ignores comments and blank lines)
REQ_RE = re.compile(
    r"^\s*([A-Za-z0-9_.\-]+)(\[[A-Za-z0-9_,\-]+\])?\s*([=<>!~]+)\s*([^\s#]+)"
)


def _entries() -> list[tuple[str, str, str]]:
    """Return (name, operator, version) for every requirement line."""
    out = []
    for raw in REQ.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = REQ_RE.match(line)
        if m:
            out.append((m.group(1), m.group(3), m.group(4)))
    return out


def test_requirements_file_is_readable_and_non_trivial():
    assert REQ.exists(), f"{REQ} missing"
    entries = _entries()
    assert len(entries) >= 30, (
        f"only {len(entries)} requirements parsed — the parser or the file changed "
        "shape; a silently-empty list would make every assertion below vacuous"
    )


def test_every_requirement_is_pinned_exactly():
    """The defect: 34 of 34 entries used `>=`, so builds were not reproducible."""
    unpinned = [(n, op, v) for n, op, v in _entries() if op != "=="]
    assert not unpinned, (
        "unpinned dependencies make a build non-reproducible, which weakens "
        "build_rev as a deploy control:\n"
        + "\n".join(f"  {n}{op}{v}" for n, op, v in unpinned)
    )


def test_no_duplicate_requirements():
    """A duplicate lets the later line silently win and defeats the pin."""
    names = [n.lower().replace("_", "-") for n, _, _ in _entries()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate requirement entries: {sorted(dupes)}"


def test_pinned_versions_are_concrete():
    """`==` on a wildcard or a local-dev marker is not a pin."""
    bad = [
        (n, v) for n, op, v in _entries()
        if op == "==" and ("*" in v or v.lower() in {"", "latest"})
    ]
    assert not bad, f"non-concrete pins: {bad}"


def test_rationale_comments_survived_the_pinning_pass():
    """The comments carry R-number rationale — the only record of why several
    of these dependencies exist at all (orjson/R-F3551, defusedxml/R-F2370,
    fsrs/R-F664). A rewrite that dropped them would destroy that history."""
    text = REQ.read_text(encoding="utf-8")
    for marker in ("R-F3551", "R-F2370", "R-F664", "R-F715"):
        assert marker in text, f"rationale comment for {marker} was lost"


@pytest.mark.parametrize("critical", ["fastapi", "pydantic", "httpx", "cryptography"])
def test_security_and_core_packages_are_pinned(critical: str):
    """Spot-check the packages where a silent version drift is most damaging."""
    found = [(n, op, v) for n, op, v in _entries() if n.lower() == critical]
    assert found, f"{critical} not found in requirements.txt"
    name, op, ver = found[0]
    assert op == "==", f"{name} must be pinned exactly, found {op}{ver}"

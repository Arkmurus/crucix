"""R-F4286 / C-241 — a launcher's hash guard must pass on a fresh checkout.

Every training launcher opens with

    test "$(hash "$SFT")" = <sha256>

and `set -euo pipefail`, so a stale pin does not warn — **the cycle refuses to
start**. That is the user-visible symptom this pins.

R-F4283 canonicalised `data/eval_reports/**` and `data/training/**` to LF and
therefore changed the bytes those shas were taken over, staling 50 pins across 23
launchers. Reverting was not available: the repo's pins were mutually
inconsistent (the prompt-ablation verdict recorded LF, the launchers recorded
CRLF), so no single convention satisfied them and each was already wrong on some
other platform.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LAUNCHERS = sorted((ROOT / "scripts/train").glob("*.sh"))
_ASSIGN = re.compile(r'^(\w+)=(data/[^\s"\']+)\s*$', re.MULTILINE)
_PIN = re.compile(r'test\s+"\$\(hash\s+"\$(\w+)"\)"\s*=\s*([0-9a-f]{64})')

#: Pins whose CONTENT genuinely differs from the pin, not merely its line
#: endings. Their corpora are untracked, so a checkout cannot reproduce them.
#: SHRINK-ONLY: a new entry here means a pin was rewritten without proving the
#: content was unchanged, which is the one thing R-F4286 must never do.
KNOWN_UNRESOLVED = {
    ("run_tooluse_citation_contract_v9.sh", "SFT"),
    ("run_tooluse_citation_contract_v10.sh", "SFT"),
}


def _pins():
    for sh in LAUNCHERS:
        text = sh.read_text(encoding="utf-8")
        varmap = dict(_ASSIGN.findall(text))
        for var, pinned in _PIN.findall(text):
            rel = varmap.get(var)
            if rel:
                yield sh.name, var, rel, pinned


def _digests(path: pathlib.Path) -> dict:
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    return {"exact": hashlib.sha256(raw).hexdigest(),
            "crlf": hashlib.sha256(lf.replace(b"\n", b"\r\n")).hexdigest()}


ALL_PINS = list(_pins())


def test_there_are_pins_to_check() -> None:
    """A guard whose universe is empty always certifies (R-F3791)."""
    assert len(ALL_PINS) >= 40, len(ALL_PINS)


@pytest.mark.parametrize(
    "launcher,var,rel,pinned",
    [p for p in ALL_PINS
     if (ROOT / p[2]).is_file() and (p[0], p[1]) not in KNOWN_UNRESOLVED
     and "/checkpoints/" not in p[2]],
    ids=lambda v: v if isinstance(v, str) and len(v) < 40 else "",
)
def test_every_pin_matches_the_worktree_bytes(launcher, var, rel, pinned) -> None:
    """THE CAPABILITY TEST — this is the `test` the launcher itself runs."""
    assert _digests(ROOT / rel)["exact"] == pinned, (
        f"{launcher}:{var} pins {pinned[:12]}… but {rel} now hashes "
        f"{_digests(ROOT / rel)['exact'][:12]}… — this launcher would REFUSE to start"
    )


def test_the_unresolved_pins_are_a_CONTENT_divergence_not_line_endings() -> None:
    """The exception list must stay honest about WHY each entry is exempt.

    These two corpora are UNTRACKED, so the copy on this disk was regenerated
    locally and its content genuinely differs from what the launcher pinned —
    neither line-ending rendering matches. That is a different event from the
    R-F4286 re-pin and must never be silently rewritten: doing so would erase
    the evidence that the launcher and its corpus have diverged.
    """
    for launcher, var in KNOWN_UNRESOLVED:
        row = next(((r, pin) for n, v, r, pin in ALL_PINS
                    if n == launcher and v == var), None)
        if row is None:
            continue
        rel, pinned = row
        if not (ROOT / rel).is_file():
            continue                      # absent is also unresolvable
        digests = _digests(ROOT / rel)
        assert pinned not in (digests["exact"], digests["crlf"]), (
            f"{launcher}:{var} -> {rel} now matches its pin under some rendering; "
            f"re-pin it and drop the exemption rather than leaving it listed"
        )


def test_a_fabricated_pin_would_be_caught() -> None:
    """A check that cannot fail is not a check."""
    launcher, var, rel, pinned = next(
        (p for p in ALL_PINS if (ROOT / p[2]).is_file() and "/checkpoints/" not in p[2])
    )
    assert _digests(ROOT / rel)["exact"] != "0" * 64


# -- the encoding must stay pinned, or the pins go stale again ---------------

def test_gitattributes_pins_the_encoding_of_the_hashed_trees() -> None:
    """Without this, the next Windows checkout re-mangles the bytes."""
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "data/eval_reports/**" in attrs
    assert "data/training/**" in attrs


def test_binaries_under_those_trees_are_exempt_from_text_conversion() -> None:
    """`text eol=lf` over `data/training/**` would mark a 310MB .tgz adapter as
    TEXT and corrupt it on checkout. The checkpoints are untracked today, so
    nothing is at risk — this keeps it that way if one is ever committed."""
    attrs = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for suffix in ("*.tgz", "*.safetensors", "*.bin"):
        assert f"data/training/**/{suffix}" in attrs, suffix
    assert "-text" in attrs

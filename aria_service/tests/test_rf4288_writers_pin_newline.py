"""R-F4288 / C-243 — artifact writers must emit the same bytes on every platform.

`Path.write_text()` uses universal newlines, so on Windows it translates every
"\\n" to "\\r\\n". Measured 2026-08-24: **48 writers across 36 files** under
`scripts/train` emitted JSON and JSONL artifacts without pinning `newline`, so
the same corpus, manifest or verdict was CRLF on Windows and LF on Linux — one
file, two byte sequences, two sha256s.

THIS IS THE ROOT of the line-ending work, and it is why the earlier fixes were
not enough on their own:

  * R-F4283 pinned the STORAGE layer (`.gitattributes`), so a checkout is now LF;
  * R-F4286 re-pinned 50 stale launcher hashes onto those canonical bytes;
  * but the WRITERS were still non-reproducible, so the next artifact generated
    on Windows would be CRLF again and every pin taken over it would break on the
    next platform. The loop would simply restart.

Only Windows behaviour changes. On Linux `newline="\\n"` is already the effect,
so every consumer that works today keeps working.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TRAIN = ROOT / "scripts/train"

_CALL = re.compile(r"\.write_text\(")


def _call_body(src: str, open_paren: int) -> str:
    """The argument text of the call whose '(' is at `open_paren`."""
    depth, i = 0, open_paren
    while i < len(src):
        c = src[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1:i]
        elif c in "\"'":
            quote, i = c, i + 1
            while i < len(src) and src[i] != quote:
                i += 2 if src[i] == "\\" else 1
        i += 1
    return ""


def _unpinned_writers() -> list[str]:
    out = []
    for path in sorted(TRAIN.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        for m in _CALL.finditer(src):
            body = _call_body(src, m.end() - 1)
            if "newline=" in body:
                continue
            # only writers that actually emit newline-bearing text
            if "json.dumps" not in body and "\\n" not in body:
                continue
            line = src[:m.start()].count("\n") + 1
            out.append(f"{path.name}:{line}")
    return out


def test_no_artifact_writer_emits_platform_dependent_bytes() -> None:
    """THE CAPABILITY TEST — the symptom is a sha that differs by platform."""
    unpinned = _unpinned_writers()
    assert unpinned == [], (
        f"{len(unpinned)} writer(s) emit newline-bearing text without "
        f"newline=\"\\n\", so their artifacts are CRLF on Windows and LF on "
        f"Linux and every sha256 pinned over them breaks on the other platform: "
        f"{unpinned[:8]}"
    )


def test_the_scan_can_actually_find_something() -> None:
    """A guard whose universe is empty always certifies (R-F3791).

    Proven against a synthetic source rather than by trusting the empty result
    above: if the detector cannot see an unpinned writer, its silence means
    nothing.
    """
    src = 'p.write_text(json.dumps(x, indent=2) + "\\n", encoding="utf-8")\n'
    m = _CALL.search(src)
    body = _call_body(src, m.end() - 1)
    assert "newline=" not in body
    assert "json.dumps" in body

    pinned = 'p.write_text(json.dumps(x) + "\\n", encoding="utf-8", newline="\\n")\n'
    m2 = _CALL.search(pinned)
    assert "newline=" in _call_body(pinned, m2.end() - 1)


def test_there_are_writers_to_scan() -> None:
    """The denominator must be real, or the assertion above is vacuous."""
    total = sum(len(_CALL.findall(p.read_text(encoding="utf-8")))
                for p in TRAIN.glob("*.py"))
    assert total >= 40, total


@pytest.mark.parametrize("name", [
    "build_resolution_failure_correction.py",
    "build_mixed_tooluse_cycle.py",
    "parent_of_record.py",
    "adjudicate_sweep.py",
])
def test_the_manifest_and_verdict_writers_are_pinned(name: str) -> None:
    """These four write the hash-pinned records everything else is checked against."""
    src = (TRAIN / name).read_text(encoding="utf-8")
    for m in _CALL.finditer(src):
        body = _call_body(src, m.end() - 1)
        if "json.dumps" in body:
            assert "newline=" in body, f"{name}: unpinned manifest/verdict writer"

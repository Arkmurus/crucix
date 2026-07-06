"""R-F2372 — source files must not contain raw bidi control characters."""

from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_BIDI_CONTROLS = {
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
}


def test_rf2372_no_raw_bidi_controls_in_python_sources() -> None:
    offenders: list[str] = []
    for path in _ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if any(ch in line for ch in _BIDI_CONTROLS):
                offenders.append(f"{path.relative_to(_ROOT.parent)}:{line_no}")
    assert offenders == []

"""R-F959 — WhatsApp wake-word tolerates speech-to-text variants of "Aria".

Live (2026-05-28): a voice note "Hey Aria, tell me about Brazil" transcribed as
"Hey Arya … above Brazil"; the old wake-word /\\baria\\b/ didn't match "Arya", so
she stayed silent. The fix matches aria/arya/ariya but not area/Maria/Syria.
Source-level (the listener has no JS harness) + the regex semantics are also
verified in-test.
"""
from __future__ import annotations

import re
from pathlib import Path

from aria_service.routes import aria as a


def _wa() -> str:
    return (Path(a.__file__).resolve().parents[2] / "services" / "wa-listener"
            / "aria_wa_listener.mjs").read_text(encoding="utf-8")


def test_rf959_fuzzy_pattern_present():
    wa = _wa()
    assert "ar[iy]{1,3}a" in wa, "wake-word must tolerate STT variants of Aria"


def test_rf959_regex_matches_variants_not_false_positives():
    # mirror the JS pattern /\bar[iy]{1,3}a\b/i
    rx = re.compile(r"\bar[iy]{1,3}a\b", re.I)
    assert rx.search("Hey Arya can you tell me about Brazil")   # the live miss
    assert rx.search("Aria, review this")
    assert rx.search("ariya please help")
    assert rx.search("ARIA")
    # no false positives on common words / other names
    assert not rx.search("what is the area of the contract")
    assert not rx.search("Maria sent the file")
    assert not rx.search("tell me about Syria")

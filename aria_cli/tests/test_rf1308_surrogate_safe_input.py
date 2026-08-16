"""R-F1308 — capability test: a pasted line containing lone UTF-16 surrogates
(emoji halves from a Windows clipboard paste) must not crash history or the
input path.

Live incident 2026-06-03 ~21:00 (twice): FileHistory.store_string did
f.write(t.encode('utf-8')) on a surrogate-bearing line → UnicodeEncodeError
('surrogates not allowed') inside the prompt_toolkit key handler → unhandled
event-loop exception → the whole REPL froze ('Press ENTER to continue').
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from aria_cli.cli import PROMPT_TOOLKIT_AVAILABLE, _sanitize_input_line

# A string with a LONE surrogate — exactly what a Windows console paste of a
# multi-byte emoji can produce. Built with surrogatepass so the literal itself
# is constructible.
SURROGATE_LINE = "review this: " + "\ud83d" + " thanks"


def test_sanitize_input_line_strips_surrogates() -> None:
    """The sanitized line must be utf-8 encodable (the property every
    downstream consumer — history, json.dumps, brain wire — needs)."""
    safe = _sanitize_input_line(SURROGATE_LINE)
    safe.encode("utf-8")  # must not raise
    assert "review this:" in safe and "thanks" in safe  # content preserved


def test_sanitize_normal_line_unchanged() -> None:
    assert _sanitize_input_line("fix the bug in main.py") == "fix the bug in main.py"
    # Full (paired) emoji are valid utf-8 and must survive untouched.
    assert _sanitize_input_line("deploy 🚀 now") == "deploy 🚀 now"


@pytest.mark.skipif(not PROMPT_TOOLKIT_AVAILABLE, reason="prompt_toolkit not installed")
def test_safe_file_history_survives_surrogates() -> None:
    """The exact incident path: store_string on a surrogate line. The raw
    PTFileHistory crashes; PTSafeFileHistory must store a sanitized line."""
    from aria_cli.cli import PTSafeFileHistory

    tmp = Path(tempfile.mkdtemp()) / "hist.txt"
    h = PTSafeFileHistory(str(tmp))
    h.store_string(SURROGATE_LINE)  # must not raise
    # And the line was actually persisted (sanitized), not silently dropped.
    content = tmp.read_text(encoding="utf-8")
    assert "review this:" in content


@pytest.mark.skipif(not PROMPT_TOOLKIT_AVAILABLE, reason="prompt_toolkit not installed")
def test_raw_file_history_does_crash_proving_the_bug() -> None:
    """Regression witness: the UPSTREAM FileHistory raises on the same input —
    proving the subclass is load-bearing, not decorative."""
    from prompt_toolkit.history import FileHistory

    tmp = Path(tempfile.mkdtemp()) / "hist_raw.txt"
    h = FileHistory(str(tmp))
    # R-F4080 (C-129) — the WITNESS EXPIRED, and that is not a licence to delete
    # the subclass. prompt_toolkit 3.0.53 sanitizes internally, so upstream no
    # longer raises and `pytest.raises` failed with "DID NOT RAISE". The bug was
    # real (live incident 2026-06-03) and older versions still carry it, so
    # PTSafeFileHistory stays — the test above proves OUR behaviour and is the
    # one that must never be weakened. This one only records whether the
    # upstream defect is still present, and says which it observed.
    try:
        h.store_string(SURROGATE_LINE)
    except UnicodeEncodeError:
        return  # upstream still crashes — the subclass is load-bearing here
    import prompt_toolkit as _pt
    pytest.skip(
        f"upstream FileHistory no longer raises on lone surrogates "
        f"(prompt_toolkit {_pt.__version__}); PTSafeFileHistory is retained "
        f"because older versions still crash"
    )

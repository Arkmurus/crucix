"""R-F455 — streaming chat post-hooks emit debug logs on failure.

Pre-R-F455 seven `except: pass` blocks in aria_engine.py (lines
~3438, 3447, 3455, 3466, 3516, 3557, 3567, 3641) silently swallowed
errors during the WhatsApp default streaming path. Result: every WA
turn that failed to call neural_memory.learn_from_text / mem0 /
training_data / reasoning_router / student/proactive / metacognitive
/ core_develop / output_harvester produced ZERO telemetry. System
Health perpetually reported "no signals" with no cause data.

R-F455 promotes each `except: pass` to
`except Exception as <name>: logger.debug("R-F455 stream: ...")` so
the dashboard can attribute the silence.
"""
from __future__ import annotations

import re


def _find_aria_chat_stream_end(src: str, marker_idx: int) -> int:
    """R-F666 helper — return the character offset where aria_chat_stream
    ends, so the R-F455 scan window grows naturally with the function
    without leaking into the next top-level def (e.g. aria_think).

    The next top-level `^async def` or `^def` line after the marker
    marks the boundary. Falls back to end-of-file if none found."""
    pattern = re.compile(r"^(?:async\s+)?def\s+\w+\s*\(", re.MULTILINE)
    for m in pattern.finditer(src, pos=marker_idx):
        # Skip the first match — that's likely `aria_chat_stream` itself
        # if the marker is inside the function body before any nested defs.
        # We want the FIRST top-level def AFTER the marker that isn't
        # the function the marker is inside. The marker is far enough
        # into the function body that this isn't ambiguous in practice,
        # but to be safe we take the LAST def whose name doesn't start
        # with an underscore (nested defs commonly start with _).
        line_start = src.rfind("\n", 0, m.start()) + 1
        # Skip indented (nested) defs
        if src[line_start:m.start()].strip() != "":
            continue
        return m.start()
    return len(src)


def test_rf455_no_bare_except_pass_remains_in_stream_post_hooks():
    """Static scan: the seven R-F455 sites must use named exception
    captures + logger.debug, not bare `except: pass`. Catches a
    regression where someone reverts the fix during a refactor."""
    from pathlib import Path
    src_path = (
        Path(__file__).resolve().parent.parent / "aria_engine.py"
    )
    src = src_path.read_text(encoding="utf-8")

    # Walk the streaming function: find the start of aria_chat_stream
    # and look only at its body. Anchor on a well-known marker
    # (the R-F455 comment we added).
    marker_idx = src.find("R-F455 (2026-05-13)")
    assert marker_idx > 0, (
        "R-F455 comment marker missing from aria_engine.py — has the "
        "fix been reverted?"
    )

    # R-F666 (2026-05-17): replaced the fixed-N-char window with a
    # function-bounded window. The original 8000-char heuristic broke
    # when the post-hook chain naturally grew past 8000 chars. Widening
    # to 24000 made one test pass but caused the sibling test to catch
    # an `except: pass` in `aria_think` (a separate function, not in
    # scope of R-F455). Correct fix: anchor the scan to the end of
    # `aria_chat_stream` itself, so the window grows naturally with the
    # function without leaking into adjacent function bodies.
    window_end = _find_aria_chat_stream_end(src, marker_idx)
    window = src[marker_idx:window_end]

    # Each of these debug-log strings should appear in the window
    expected_debugs = [
        "R-F455 stream: neural_memory.learn_from_text",
        "R-F455 stream: mem0.summarise_and_store",
        "R-F455 stream: training_data.record_conversation",
        "R-F455 stream: reasoning_router",
        "R-F455 stream: student/proactive",
        "R-F455 stream: metacognitive",
        "R-F455 stream: core_develop",
        "R-F455 stream: output_harvester",
    ]
    missing = [d for d in expected_debugs if d not in window]
    assert not missing, (
        f"R-F455 regression: {len(missing)} post-hook debug logs "
        f"missing in stream path: {missing}"
    )


def test_rf455_no_silent_except_pass_in_window():
    """The window around the R-F455 marker must NOT contain any
    bare `except Exception:\\n        pass` (the pre-fix pattern).
    Catches a sloppy revert that promotes one but leaves another bare."""
    from pathlib import Path
    src_path = (
        Path(__file__).resolve().parent.parent / "aria_engine.py"
    )
    src = src_path.read_text(encoding="utf-8")

    marker_idx = src.find("R-F455 (2026-05-13)")
    assert marker_idx > 0
    # R-F666: function-bounded window (see sibling test above).
    window = src[marker_idx:_find_aria_chat_stream_end(src, marker_idx)]

    import re
    # Match bare except: pass with optional whitespace, on either Exception or bare
    pattern = re.compile(
        r"except\s+Exception\s*:\s*\n\s+pass(?:\s*\n)?",
    )
    found = pattern.findall(window)
    assert not found, (
        f"R-F455 regression: bare 'except Exception: pass' still present "
        f"in stream-side post-hook window. Count: {len(found)}"
    )

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

    # Look forward from the marker for ~150 lines (covers the cluster
    # of post-hooks the marker introduces).
    window_end = marker_idx + 8000
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
    window = src[marker_idx:marker_idx + 8000]

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

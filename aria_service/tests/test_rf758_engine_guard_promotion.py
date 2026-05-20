"""R-F758 — extend R-F752 guard exception promotion to aria_engine.py.

R-F752 promoted the chat-path post-response guards in
aria_service/routes/aria.py from debug/warning to ERROR + result.
guard_errors. R-F733 (a separate, earlier commit) wired the same two
guards (propaganda_guard + tool_claim_guard) directly inside
aria_service/aria_engine.py's aria_chat + aria_chat_stream — but
those except-blocks were still at logger.debug, silencing crashes on:

  - the engine's NON-stream guard pass that runs BEFORE the routes
    layer's R-F752 pass (so an engine crash means the routes pass
    operates on un-rewritten text — defeats defence in depth)
  - both guard passes inside aria_chat_stream (these are the ONLY
    chat-path guards on the stream — there is no routes-layer
    re-check on stream because the chunks already left the wire;
    instead they keep persisted history clean for future turns)

R-F758 promotes all 4 sites in aria_engine.py to logger.error +
exc_info=True, with explicit log messages naming whether it's the
engine or stream variant so triage can route the failure.
"""
from __future__ import annotations

from pathlib import Path


def _engine_src() -> str:
    p = Path(__file__).resolve().parents[1] / "aria_engine.py"
    return p.read_text(encoding="utf-8")


def test_rf758_engine_propaganda_guard_uses_error():
    src = _engine_src()
    # The non-stream propaganda guard except-block must use
    # logger.error + the explicit "engine pass" tagging.
    assert "R-F733 propaganda_guard failed (engine pass" in src, (
        "R-F758 regression: aria_engine.py non-stream propaganda_guard "
        "except-block lost its 'engine pass' ERROR-level tag."
    )


def test_rf758_engine_tool_claim_guard_uses_error():
    src = _engine_src()
    assert "R-F733 tool_claim_guard failed (engine pass" in src, (
        "R-F758 regression: aria_engine.py non-stream tool_claim_guard "
        "except-block lost its 'engine pass' ERROR-level tag."
    )


def test_rf758_stream_propaganda_guard_uses_error():
    src = _engine_src()
    assert "R-F733 stream propaganda_guard failed (persisted history" in src, (
        "R-F758 regression: aria_engine.py stream propaganda_guard "
        "except-block lost its 'persisted history' ERROR-level tag."
    )


def test_rf758_stream_tool_claim_guard_uses_error():
    src = _engine_src()
    assert "R-F733 stream tool_claim_guard failed (persisted history" in src, (
        "R-F758 regression: aria_engine.py stream tool_claim_guard "
        "except-block lost its 'persisted history' ERROR-level tag."
    )


def test_rf758_no_silent_debug_left_for_r733_failures():
    src = _engine_src()
    # No `logger.debug("R-F733 ... failed ...")` may remain.
    forbidden_patterns = (
        "R-F733 propaganda_guard failed (non-fatal)",
        "R-F733 tool_claim_guard failed (non-fatal)",
        "R-F733 stream propaganda_guard failed (non-fatal)",
        "R-F733 stream tool_claim_guard failed (non-fatal)",
    )
    leftovers = [p for p in forbidden_patterns if p in src]
    assert not leftovers, (
        f"R-F758 regression: silent debug fallback survived for "
        f"{leftovers}. Use logger.error with the explicit pass marker."
    )


def test_rf758_exc_info_threaded_through():
    """All 4 promoted log lines must carry exc_info=True so the stack
    trace lands in fly logs. Without it, the new ERROR log carries
    only the str(e) of the inner exception — much weaker triage signal."""
    src = _engine_src()
    # Crude global count check — at least 4 exc_info=True calls now
    # exist in aria_engine.py (the 4 promoted handlers). If a future
    # edit drops one to make the line shorter, this catches it.
    n = src.count("exc_info=True,")
    assert n >= 4, (
        f"R-F758 regression: only {n} exc_info=True occurrences in "
        "aria_engine.py — expected at least 4 (one per promoted "
        "guard handler). A stack trace was lost."
    )

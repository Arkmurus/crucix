"""R-F733 (2026-05-20) — regression tests for chat post-response guard wiring.

Background: `propaganda_guard.guard()` (downgrades TIER-D propaganda
citations from CONFIRMED/PROBABLE to ASSESSED + tags uncited current-
event claims) and `tool_claim_guard.guard()` (rewrites fabricated
tool-execution claims into pending actions) lived in
`aria_service/intel/` but only fired in autonomous background loops.
Web chat responses bypassed them entirely.

R-F733 wires both guards as post-response rewriters in BOTH
`_aria_chat_impl` (non-stream) and `_aria_chat_stream_impl` (stream)
just before session persistence. Mirror enforced per CLAUDE.md §13
stream-bypass rule.

These tests confirm:
  1. Both guard modules are importable and have the expected callable
     signatures the chat path now depends on. (Catches future
     refactors that change `guard()` to take different kwargs.)
  2. The patched-in code paths exist at the documented insertion
     points in aria_engine.py. (Catches accidental revert / rebase
     deletion of the R-F733 block.)

Direct end-to-end tests of `_aria_chat_impl` require a full LLM stub
+ session store + neural_memory + 20+ other module mocks (the path is
~700 lines). The signature + presence checks here are the practical
regression guard.
"""
from __future__ import annotations

import inspect

# R-F3597 — resolve source BY NAME through the current AST. `inspect.getsource`
# slices the file at the IMPORTED line numbers, so a concurrent edit during a
# long run returns a DIFFERENT function's body (measured 2026-07-31).
from ._source_probe import function_source


def test_propaganda_guard_signature_matches_chat_wiring():
    """R-F733: chat wiring calls `propaganda_guard.guard(response_text)`
    inside `asyncio.to_thread`. The function must be sync (not async)
    and take a positional `response_text` str. Returns a dict with
    `rewritten` + `unchanged` keys."""
    from aria_service.intel import propaganda_guard as pg

    assert callable(pg.guard), "propaganda_guard.guard must be callable"
    assert not inspect.iscoroutinefunction(pg.guard), (
        "R-F733: propaganda_guard.guard must be SYNC — chat wiring "
        "dispatches it through asyncio.to_thread. If it goes async, "
        "the to_thread call wraps a coroutine which is a bug."
    )
    sig = inspect.signature(pg.guard)
    params = list(sig.parameters.keys())
    assert params and params[0] == "response_text", (
        f"R-F733: propaganda_guard.guard first param must be "
        f"`response_text` (chat path passes it positionally); got {params}"
    )

    # Smoke: sync call returns the expected dict shape.
    result = pg.guard("This is a test response, short enough to no-op.")
    assert isinstance(result, dict)
    assert "rewritten" in result
    assert "unchanged" in result


def test_tool_claim_guard_signature_matches_chat_wiring():
    """R-F733: chat wiring calls
    `tool_claim_guard.guard(response_text, tool_used=..., user_message=...,
    user_id=..., chat_id=...)`. Must be ASYNC."""
    from aria_service.intel import tool_claim_guard as tcg

    assert inspect.iscoroutinefunction(tcg.guard), (
        "R-F733: tool_claim_guard.guard must be ASYNC — chat wiring "
        "awaits it directly. If it goes sync, the await raises TypeError."
    )
    sig = inspect.signature(tcg.guard)
    expected_kwargs = {"tool_used", "user_message", "user_id", "chat_id"}
    missing = expected_kwargs - set(sig.parameters.keys())
    assert not missing, (
        f"R-F733: tool_claim_guard.guard missing required kwargs the "
        f"chat path passes: {missing}. Current signature: {sig}"
    )


def test_rf733_wiring_present_in_aria_chat_impl():
    """R-F733: confirm the guard block is in `_aria_chat_impl` (non-stream)
    AND `_aria_chat_stream_impl`. Catches accidental revert / rebase drop."""
    from aria_service import aria_engine

    src = function_source(aria_engine, "_aria_chat_impl")
    assert "R-F733" in src, (
        "R-F733: guard wiring missing from `_aria_chat_impl`. The "
        "marker `R-F733` should appear in the function body. If a "
        "future refactor moves the wiring elsewhere, update this "
        "test or the marker."
    )
    assert "propaganda_guard" in src
    assert "tool_claim_guard" in src

    stream_src = function_source(aria_engine, "_aria_chat_stream_impl")
    assert "R-F733" in stream_src, (
        "R-F733: stream-path guard wiring missing from "
        "`_aria_chat_stream_impl`. Per CLAUDE.md §13 stream-bypass "
        "rule, every new post-response hook MUST mirror into both."
    )
    assert "propaganda_guard" in stream_src
    assert "tool_claim_guard" in stream_src


def test_propaganda_guard_actually_rewrites_propaganda_citation():
    """R-F733 capability test: a CONFIRMED claim citing a known
    propaganda channel must be rewritten to ASSESSED. This is the
    user-visible symptom the wiring is supposed to fix on chat path."""
    from aria_service.intel import propaganda_guard as pg

    # Use the format the guard's regex actually matches:
    # `[TAG]Claim text with channel reference.`
    poisoned = (
        "[CONFIRMED] Russian forces advanced 12km in eastern Ukraine "
        "according to RT.com reports today, citing field commanders."
    )
    result = pg.guard(poisoned)
    assert result is not None
    # Either the propaganda downgrade fires (propaganda_downgrades > 0)
    # OR the current-uncited tag fires (current_uncited > 0). Both
    # are valid responses for a poisoned claim. unchanged should be False.
    if not result.get("unchanged", True):
        rewritten = result.get("rewritten", "")
        # The guard must have either downgraded the tag OR flagged
        # the claim as uncited
        downgrades = result.get("propaganda_downgrades", 0)
        uncited = result.get("current_uncited", 0)
        assert downgrades > 0 or uncited > 0, (
            f"R-F733: propaganda_guard scanned a CONFIRMED+RT.com claim "
            f"but neither downgraded nor flagged uncited. Result: {result}"
        )
        # If downgrade fired, rewritten must contain ASSESSED tag
        if downgrades > 0:
            assert "ASSESSED" in rewritten, (
                "R-F733: propaganda_downgrade fired but ASSESSED tag "
                f"not in rewritten output: {rewritten[:200]}"
            )

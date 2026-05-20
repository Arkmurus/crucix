"""R-F752 — guard exceptions promoted from debug/warning to ERROR + surfaced
on the chat response as `result.guard_errors`.

Audit on 2026-05-20 flagged the chat-path post-LLM guards (officeholder,
citation_injector, response_verifier, commitment_guard, tool_claim_guard,
propaganda_guard, ground_truth_guard) as P1: every guard's except-block
swallowed exceptions at debug/warning level and the response shipped
without any modification — a user could not tell that a guard had
crashed. R-F752 promotes all 7 (+ the R-F448 stream-honesty wrapper) to
ERROR with exc_info=True, and records the failure on
`result["guard_errors"]` for the chat response.

Verification strategy: source-level grep on aria_service/routes/aria.py.
We want every guard's except-block to satisfy three conditions:
  1. uses `_log.error(...)` (not debug, not warning)
  2. carries `exc_info=True`
  3. populates `result.setdefault("guard_errors", []).append({"guard": ...})`

This catches both regression vectors:
  - A future edit demoting a log back to debug (silencing the failure)
  - A future edit forgetting to push the error onto guard_errors
    (silencing the failure to API consumers)
"""
from __future__ import annotations

import re
from pathlib import Path


GUARDS = [
    "officeholder_guard",
    "citation_injector",
    "response_verifier",
    "commitment_guard",
    "tool_claim_guard",
    "propaganda_guard",
    "ground_truth_guard",
]


def _aria_source() -> str:
    p = Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    return p.read_text(encoding="utf-8")


def test_rf752_all_seven_guards_use_log_error_on_failure():
    src = _aria_source()
    for guard in GUARDS:
        # Find the failure log line for this guard. It must contain
        # _log.error( and a string referencing the guard name.
        pat = re.compile(
            r"_log\.error\(\s*\n?\s*\"\[" + re.escape(guard) + r"\]"
            r"|_log\.error\(\s*\n?\s*\"" + re.escape(guard) + r"\]",
            re.MULTILINE,
        )
        # The simpler probe: at least one `_log.error("[<guard>]` exists.
        simple_pat = f'_log.error(\n                "[{guard}]'
        assert simple_pat in src or f'_log.error("[{guard}]' in src, (
            f"R-F752 regression: {guard} except-block does not use _log.error. "
            "Demoting to debug/warning silences guard crashes — a P1 "
            "honesty regression."
        )


def test_rf752_all_seven_guards_record_to_guard_errors():
    src = _aria_source()
    for guard in GUARDS:
        # Each guard's except-block must push onto result.guard_errors
        # with the right guard name. Source pattern:
        #   result.setdefault("guard_errors", []).append(
        #       {"guard": "<name>", "error": str(e)[:200]}
        #   )
        needle = f'"guard": "{guard}"'
        assert needle in src, (
            f"R-F752 regression: {guard} except-block does not push onto "
            f"result.guard_errors. API consumers can't see the failure. "
            f"Expected literal: {needle!r}"
        )


def test_rf752_all_seven_guards_exc_info():
    src = _aria_source()
    # Crude but effective: count `_log.error(` calls in aria.py that use
    # exc_info=True. We want at least 8 (7 chat guards + 1 stream wrapper).
    n_error_with_exc = src.count("exc_info=True,")
    assert n_error_with_exc >= 8, (
        f"R-F752 regression: only {n_error_with_exc} `exc_info=True` log "
        "lines in aria.py — expected ≥8 (7 chat-path guards + 1 stream "
        "R-F448 wrapper). A future edit dropped a stack trace."
    )


def test_rf752_stream_honesty_pass_uses_log_error():
    """R-F448 wrapper in chat_stream_ep must also use _log.error so a
    stream-path guard crash is treated with the same severity as the
    non-stream version."""
    src = _aria_source()
    # Look for the R-F448 except handler — must contain `_log.error(` AND
    # the "stream" wording so the log message identifies the path.
    assert "R-F448 stream honesty pass failed" in src
    # The promoted line:
    assert '_log.error(\n                        "R-F448 stream honesty pass failed' in src, (
        "R-F752 regression: R-F448 stream-honesty failure handler does "
        "not use _log.error. Stream path must match non-stream severity."
    )


def test_rf752_no_silent_guard_debug_remains():
    """No `_log.debug(` line in aria.py should reference a failure of
    one of the 7 hot-path guards. This guards against a regression where
    someone adds back a debug-level fallback that silences future
    crashes."""
    src = _aria_source()
    silent_pattern_problems = []
    for guard in GUARDS:
        # Reject any _log.debug(...) that names the guard's "failed".
        debug_needle = f'_log.debug("{guard} failed'
        debug_needle2 = f"_log.debug(\"{guard}\\] failed"
        if debug_needle in src or debug_needle2 in src:
            silent_pattern_problems.append(guard)
    assert not silent_pattern_problems, (
        f"R-F752 regression: silent debug-level failure log remains for "
        f"{silent_pattern_problems}. Use _log.error + guard_errors instead."
    )

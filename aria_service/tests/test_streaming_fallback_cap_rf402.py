"""R-F402 — streaming fallback chain capped at _MAX_FALLBACK_ATTEMPTS.

Live evidence (this assessment 2026-05-13): the non-streaming path
`FallbackLLM.complete()` capped its attempts at `_MAX_FALLBACK_ATTEMPTS`
(line 263, per R-F94 2026-04-30). The streaming path `stream()` did not.
With 6 providers configured (anthropic / deepseek / groq / openai /
gemini / ollama) and a default 120s timeout, an all-timeout cascade
could run for 12 minutes before raising.

Fix: mirror the same `attempted >= _MAX_FALLBACK_ATTEMPTS` check at the
top of the per-provider loop in `stream()`. Same break behaviour, same
warning log, same untried-providers count.

Source-level test — behavioural would need a mock provider chain plus
an event loop, which the existing test conventions in this repo avoid
(no Python locally). Static assertions pin the wiring.
"""
from __future__ import annotations

import inspect
import pathlib


def _src() -> str:
    return pathlib.Path(
        "C:/code/crucix/aria_service/llm/fallback.py"
    ).read_text(encoding="utf-8", errors="ignore")


def _stream_block() -> str:
    src = _src()
    idx = src.find("async def stream(")
    assert idx > 0, "R-F402: stream() function not found"
    # Read until next async def or 4000 chars
    nxt = src.find("\n    async def ", idx + 1)
    if nxt < 0:
        nxt = src.find("\n    def ", idx + 1)
    return src[idx:nxt if nxt > 0 else idx + 4000]


def _complete_block() -> str:
    src = _src()
    idx = src.find("async def complete(")
    assert idx > 0, "complete() function not found"
    nxt = src.find("\n    async def ", idx + 1)
    return src[idx:nxt if nxt > 0 else idx + 4000]


# ── 1. Wiring tests ────────────────────────────────────────────────

def test_rf402_stream_uses_attempted_counter():
    """The stream loop must increment an `attempted` counter so the
    cap can fire. Same shape as complete() at line 282 (`attempted += 1`)."""
    block = _stream_block()
    assert "attempted = 0" in block, (
        "R-F402 regression: attempted counter not initialized in stream()."
    )
    assert "attempted += 1" in block, (
        "R-F402 regression: attempted counter not incremented in stream() loop."
    )


def test_rf402_stream_checks_max_fallback_attempts():
    """The cap check must fire at the top of the per-provider loop,
    before any provider call."""
    block = _stream_block()
    assert "attempted >= self._MAX_FALLBACK_ATTEMPTS" in block, (
        "R-F402 regression: cap check missing from stream(). "
        "Unbounded cascade still possible on all-timeout."
    )


def test_rf402_stream_break_after_cap_with_warning():
    """When the cap fires, the loop must break with a warning log so
    operators see in fly logs that the cap saved them."""
    block = _stream_block()
    # Look for the warning that names "Stream fallback chain"
    assert "Stream fallback chain stopped" in block, (
        "R-F402: missing warning log when cap fires. Operators won't "
        "see in logs that fallback was truncated."
    )
    assert "logger.warning" in block
    # And the actual break statement
    assert "break" in block


def test_rf402_stream_cap_check_is_inside_loop():
    """The cap check MUST be inside the `for provider in self.providers`
    loop, not above it — otherwise a single skipped (cooling) provider
    consumes the slot without an attempt."""
    block = _stream_block()
    loop_idx = block.find("for provider in self.providers")
    cap_idx = block.find("attempted >= self._MAX_FALLBACK_ATTEMPTS")
    assert loop_idx >= 0
    assert cap_idx > loop_idx, (
        "R-F402: cap check is above the provider loop — it would fire "
        "before any attempt and prevent every fallback."
    )


# ── 2. Parity with complete() ──────────────────────────────────────

def test_rf402_stream_cap_uses_same_constant_as_complete():
    """The two paths must use the SAME _MAX_FALLBACK_ATTEMPTS constant
    so they stay in sync. If complete() uses 3, stream() must use 3."""
    stream_block = _stream_block()
    complete_block = _complete_block()
    # Both must reference the class constant, not a literal number
    assert "self._MAX_FALLBACK_ATTEMPTS" in stream_block
    assert "self._MAX_FALLBACK_ATTEMPTS" in complete_block


def test_rf402_max_fallback_attempts_constant_exists():
    """The class constant must be defined."""
    src = _src()
    assert "_MAX_FALLBACK_ATTEMPTS" in src
    # It should be a number assignment — find its value
    import re
    m = re.search(r"_MAX_FALLBACK_ATTEMPTS\s*=\s*(\d+)", src)
    assert m is not None, "R-F402: _MAX_FALLBACK_ATTEMPTS constant value not found"
    val = int(m.group(1))
    # Sanity: must be > 0 and <= total providers configured (~6)
    assert 1 <= val <= 10, (
        f"R-F402: _MAX_FALLBACK_ATTEMPTS = {val} is outside the expected "
        f"range [1, 10]. If intentional, update this assertion."
    )


# ── 3. Behavioural — async generator returns from import ──────────

def test_rf402_stream_is_async_generator():
    """The function must remain an async generator (uses `async for`
    + `yield`). The fix must not have accidentally turned it into a
    plain coroutine."""
    from aria_service.llm.fallback import FallbackLLM
    assert inspect.isasyncgenfunction(FallbackLLM.stream), (
        "R-F402: stream() is no longer an async generator. The yield "
        "statement may have been lost in the refactor."
    )


def test_rf402_complete_still_capped_no_regression():
    """The non-streaming cap must remain in place — fixing stream
    must not have broken complete()."""
    block = _complete_block()
    assert "attempted >= self._MAX_FALLBACK_ATTEMPTS" in block, (
        "R-F94 regression: complete() lost its cap during R-F402 refactor."
    )

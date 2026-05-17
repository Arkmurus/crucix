"""R-F646 — security_protocol.sanitize_output is mirrored into aria_chat_stream.

The non-stream path (aria_chat) calls security_protocol.sanitize_output on the
final response_text before the harvester writes it to the training corpus and
before downstream learning hooks see it. Pre-R-F646 the stream path skipped
this step entirely — leaked API keys / internal URLs / Redis keys / file paths
in LLM output would propagate into the harvested corpus on /chat/stream
(WhatsApp's default path).

CLAUDE.md rule #13 (stream-bypass) and memory/stream_bypass_pattern.md both
require every post-response hook in aria_chat to be mirrored into
aria_chat_stream. Output sanitization is exactly that class of hook.

These are static-source tests: aria_chat_stream is a generator that emits SSE
events, so the full end-to-end test would need a fake LLM streaming harness.
Verifying the call site exists and is positioned between the audit hook and
the downstream harvester catches the regression cheaply.
"""
from __future__ import annotations

import re
from pathlib import Path


_SRC = (Path(__file__).resolve().parent.parent / "aria_engine.py").read_text(
    encoding="utf-8"
)


def _stream_body() -> str:
    """Return the body of the streaming implementation. The public
    `aria_chat_stream` is a thin wrapper around `_aria_chat_stream_impl`
    (R-F56 contextvar split, mirroring `aria_chat` / `_aria_chat_impl`).
    The post-response hooks live in the impl, so the test targets that."""
    start = _SRC.find("async def _aria_chat_stream_impl(")
    assert start > 0, "_aria_chat_stream_impl missing from aria_engine.py"
    after = _SRC[start + 1:]
    next_match = re.search(r"\nasync def \w+\(", after)
    end = (start + 1 + next_match.start()) if next_match else len(_SRC)
    return _SRC[start:end]


def test_rf646_sanitize_output_called_in_stream_body():
    body = _stream_body()
    # Match `.sanitize_output(response_text)` — the import may be aliased
    # (e.g. `from .intel import security_protocol as _sp_stream`) so we
    # anchor on the method name + argument rather than the module name.
    matches = re.findall(
        r"\.sanitize_output\(\s*response_text\s*\)",
        body,
    )
    assert matches, (
        "R-F646 regression: aria_chat_stream is missing the "
        ".sanitize_output(response_text) mirror — "
        "leaked secrets in LLM output would flow to the harvester."
    )
    # And the call must reference security_protocol (directly or aliased)
    # somewhere in a nearby import line, to prove this isn't a stray
    # sanitize_output() from some other unrelated module.
    sp_imports = re.findall(
        r"from\s+\.intel\s+import\s+security_protocol(?:\s+as\s+\w+)?",
        body,
    )
    assert sp_imports, (
        "R-F646: sanitize_output() found in stream body but no "
        "security_protocol import nearby — wrong module?"
    )


def test_rf646_sanitize_runs_after_audit_before_harvester():
    """Ordering: audit captures the raw LLM output (truthful audit log),
    sanitize redacts secrets, harvester writes the sanitized corpus.
    This matches the non-stream order at aria_engine.py:3347-3398."""
    body = _stream_body()
    audit_idx = body.find("_verify_and_record_chat")
    sanitize_idx = body.find(".sanitize_output(response_text)")
    harvest_idx = body.find("output_harvester")
    assert audit_idx > 0, "audit hook missing in stream body"
    assert sanitize_idx > 0, "R-F646 sanitize call missing in stream body"
    assert harvest_idx > 0, "harvester hook missing in stream body"
    assert audit_idx < sanitize_idx < harvest_idx, (
        "R-F646 ordering violation: expected audit → sanitize → harvester. "
        f"Got audit@{audit_idx}, sanitize@{sanitize_idx}, harvester@{harvest_idx}"
    )


def test_rf646_marker_comment_present():
    """The R-F646 marker comment anchors the fix so future refactors that
    move the block know it's load-bearing for stream-bypass parity."""
    body = _stream_body()
    assert "R-F646" in body, (
        "R-F646 marker comment missing from aria_chat_stream — has the "
        "sanitize mirror been removed?"
    )

"""R-F557 (2026-05-16) — stream rewrite wiring proof tests.

memory/stream_bypass_pattern.md previously asserted that the 5 output
guards on /chat/stream were observation-only and bypassed live
rewrite. R-F448 (2026-05-13) closed that gap by wiring
`stream_honesty.apply_stream_honesty` into the SSE generator with
correction-banner emission. R-F557 audits the wiring is still in
place, the bypass-claim comment is gone from the live route, and the
banner shape survives JSON serialisation as an SSE chunk event.

These tests are STATIC + light runtime — they don't need to boot the
full FastAPI app or hit an LLM. The route source code is the ground
truth for whether the chain still fires on stream.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest


_ROUTE_PATH = Path(__file__).resolve().parents[1] / "routes" / "aria.py"


@pytest.fixture(scope="module")
def route_source() -> str:
    return _ROUTE_PATH.read_text(encoding="utf-8")


# ── Static-source guards ─────────────────────────────────────────────────


def test_stream_route_imports_stream_honesty(route_source: str) -> None:
    """Proves /chat/stream still imports the rewrite module."""
    assert "from ..intel import stream_honesty as _sh" in route_source, (
        "R-F557: /chat/stream must still import stream_honesty (R-F448 wiring)"
    )


def test_stream_route_calls_apply_stream_honesty(route_source: str) -> None:
    """Proves the rewrite is actually called inside the stream generator."""
    assert "_sh.apply_stream_honesty(" in route_source, (
        "R-F557: apply_stream_honesty call site missing from /chat/stream"
    )


def test_stream_route_emits_correction_banner_as_chunk(route_source: str) -> None:
    """Proves the rewritten correction is emitted as an SSE chunk before
    the done event — not log-only."""
    assert "build_correction_banner" in route_source
    # The route emits the banner as a 'type:chunk' SSE event.
    assert re.search(
        r'json\.dumps\(\{\s*"type"\s*:\s*"chunk"\s*,\s*"text"\s*:\s*_banner',
        route_source,
    ), "R-F557: correction banner must be emitted as SSE chunk event, not just logged"


def test_stream_route_no_observation_only_claim(route_source: str) -> None:
    """The stale 'bypasses all guards' comment must be gone — R-F557
    updated it to reflect R-F448 wiring."""
    bad = "/chat/stream bypasses all guards (they run only in /chat)"
    assert bad not in route_source, (
        f"R-F557: stale comment '{bad}' must be removed — R-F448 wired the rewrite path"
    )


def test_stream_route_runs_rewrite_before_done(route_source: str) -> None:
    """The rewrite must complete BEFORE the deferred done event so the
    client receives the correction inside the same SSE response."""
    # Find positions of both anchors.
    rewrite_pos = route_source.find("_sh.apply_stream_honesty(")
    done_emit_pos = route_source.find("_r412_deferred_done or")
    assert rewrite_pos > 0 and done_emit_pos > 0
    assert rewrite_pos < done_emit_pos, (
        "R-F557: apply_stream_honesty must be called BEFORE the deferred "
        "done event is emitted, otherwise the client closes before the "
        "correction lands."
    )


# ── Light runtime guard: banner survives SSE JSON encoding ───────────────


def test_correction_banner_serialises_as_sse_chunk() -> None:
    """Capability: the banner returned by stream_honesty must be
    JSON-encodable as an SSE chunk payload without losing the warning
    glyph or guard names. This is the wire-format contract."""
    from aria_service.intel import stream_honesty as sh

    fake_result = {
        "rewritten": "Original text. [CORRECTED inline].",
        "changed": True,
        "changes": [("commitment_guard", 1), ("ground_truth_guard", 2)],
        "details": {
            "commitment_guard": {"violations": 1},
            "ground_truth_guard": {"violations": 2},
        },
        "skipped": [],
    }
    banner = sh.build_correction_banner(fake_result)
    assert banner is not None and isinstance(banner, str)

    # SSE wire: data: {json} \n\n
    event = json.dumps({"type": "chunk", "text": banner})
    parsed = json.loads(event)
    assert parsed["type"] == "chunk"
    assert "commitment_guard" in parsed["text"]
    assert "ground_truth_guard" in parsed["text"]
    # Glyph survived JSON round-trip
    assert "\U0001f6e1" in parsed["text"] or "🛡" in parsed["text"]


def test_correction_banner_none_when_unchanged() -> None:
    """Capability: clean response must produce no banner so no
    correction chunk is emitted unnecessarily."""
    from aria_service.intel import stream_honesty as sh

    out = {
        "rewritten": "All good.",
        "changed": False,
        "changes": [],
        "details": {},
        "skipped": [],
    }
    assert sh.build_correction_banner(out) is None

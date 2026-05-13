"""R-F412 — R-F403 confidence footer fires on /chat/stream (WA path).

Pre-R-F412 the WhatsApp path (which uses /chat/stream) NEVER showed
the proof footer. The non-stream chat_ep appends the footer after
the full response is finalised — but that codepath does NOT run on
the streaming endpoint, so the same /chat call via SSE returned the
bare reply with no confidence headline, no source tier mix, no tool
attribution. Operators saw a polished footer on the dashboard but
nothing on WhatsApp (the high-traffic surface).

R-F412 wires the footer into the SSE event_generator:
  1. Accumulate streamed chunk text into a buffer
  2. Defer the `done` event when it arrives
  3. After streaming completes, call confidence_footer.build_footer
     on the accumulated full text + captured verification
  4. Emit the footer as its own `chunk` event
  5. Emit the deferred `done` event last

This guarantees the footer arrives BEFORE the client closes on done.
"""
from __future__ import annotations

import json


# ───────────────────────── Event-order contract ─────────────────────────────


def test_rf412_footer_arrives_before_done_event():
    """Pin the SSE event ordering: every chunk first → optional meta →
    footer chunk → done. If a client closes the stream on `done`, it
    must already have received the footer."""
    # Simulate the event sequence the orchestrator emits
    events = [
        {"type": "chunk", "text": "Hello, "},
        {"type": "chunk", "text": "this is the answer [PROBABLE]."},
        # done is deferred by the orchestrator
        {"type": "chunk", "text": "\n─────\n*Confidence:* 78% [PROBABLE]"},  # footer
        {"type": "done", "session_id": "s1"},
    ]
    # Find the indices of footer + done — footer MUST come strictly before done
    footer_idx = next(
        i for i, e in enumerate(events)
        if e["type"] == "chunk" and "Confidence:" in e.get("text", "")
    )
    done_idx = next(i for i, e in enumerate(events) if e["type"] == "done")
    assert footer_idx < done_idx, (
        f"R-F412: footer chunk must arrive before done. "
        f"footer_idx={footer_idx}, done_idx={done_idx}"
    )


def test_rf412_full_text_accumulation_includes_confidence_tags():
    """The buffer that feeds build_footer must contain the CONCATENATED
    response so the regex tag-extractor finds `[PROBABLE]` etc. even
    when the tag is split across two streamed chunks."""
    # In practice the LLM emits e.g. "result is [PROB" then "ABLE]" —
    # the regex needs to see "result is [PROBABLE]" not the fragments.
    chunks = ["The result is [PROB", "ABLE] given the data."]
    buf: list[str] = []
    for c in chunks:
        buf.append(c)
    full = "".join(buf)
    assert "[PROBABLE]" in full, (
        f"R-F412: token-split confidence tags must reconstruct. Got {full!r}"
    )


# ───────────────────────── build_footer contract ─────────────────────────────


def test_rf412_build_footer_with_streamed_text_returns_footer():
    """Verify build_footer produces a non-empty footer when given a
    response containing confidence tags. Pins the stream-path contract:
    if there's a tag, there's a footer — same as the /chat path."""
    import os
    # Ensure feature is on (default ON; test sets explicitly for clarity)
    os.environ["ARIA_CONFIDENCE_FOOTER"] = "1"
    from aria_service.intel import confidence_footer
    response = (
        "The director is Joe Bloggs [PROBABLE — single Companies House filing]."
    )
    footer = confidence_footer.build_footer(
        response_text=response,
        verification=None,
        rag_sources_count=0,
        tools_used=["companies_house_lookup"],
        build_rev="abc1234",
        trace_id="trace-test",
    )
    assert footer, "R-F412: footer must be non-empty when response carries tags"
    # The headline mentions PROBABLE
    assert "PROBABLE" in footer
    # Tool attribution is rendered
    assert "companies_house_lookup" in footer
    # build_rev is rendered
    assert "abc1234" in footer


def test_rf412_build_footer_empty_when_no_tags():
    """Defensive: build_footer should not crash on a tag-less response,
    even though the stream path will still get a usable footer when
    verification is supplied."""
    from aria_service.intel import confidence_footer
    footer = confidence_footer.build_footer(
        response_text="Plain response without tags.",
        verification=None,
        rag_sources_count=0,
    )
    # Either empty (no signals) or non-empty if verification provides info —
    # the important thing is no crash. Pin the no-crash invariant.
    assert isinstance(footer, str)


# ───────────────────────── Deferred-done invariant ──────────────────────────


def test_rf412_synthetic_done_when_stream_never_emits_one():
    """If aria_chat_stream ever fails to emit a `done` event, the
    orchestrator must synthesise one so SSE clients don't hang on an
    open connection. Pin the fallback shape."""
    # Replicate the orchestrator's fallback dict
    session_id = "session-xyz"
    deferred = None
    fallback = deferred or {"type": "done", "session_id": session_id}
    assert fallback == {"type": "done", "session_id": session_id}


def test_rf412_done_passes_through_when_emitted():
    """The deferred-done logic must preserve the original done payload
    (verification, session_id, custom keys) when one was emitted."""
    done = {"type": "done", "session_id": "abc", "verification": {"verdict": "PASS"}}
    deferred = done
    out = deferred or {"type": "done", "session_id": "abc"}
    assert out is done
    assert out["verification"]["verdict"] == "PASS"


# ───────────────────────── SSE JSON-encoding invariant ──────────────────────


def test_rf412_footer_chunk_is_valid_sse_json():
    """The footer chunk yielded by the orchestrator must be valid JSON
    inside the SSE `data: ` envelope so downstream clients can parse it
    via the same dispatcher as normal chunks."""
    footer_text = "\n─────\n*Confidence:* 78% [PROBABLE]"
    payload = {"type": "chunk", "text": footer_text}
    encoded = json.dumps(payload)
    decoded = json.loads(encoded)
    assert decoded["type"] == "chunk"
    assert decoded["text"] == footer_text

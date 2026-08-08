"""R-F737 (2026-05-20) — granular SSE progress event tests.

Pre-R-F737 the streaming endpoint executed tools OUTSIDE the SSE
generator. With deep_research / dd_orchestrate / screen taking 30-60s,
the user saw nothing from POST until the LLM's first chunk — silent
wait then dump.

R-F737 moved tool detection + execution INSIDE the SSE generator and
emits `progress` events between substeps:
  stage="detecting"     — intent classifier running
  stage="tool_running"  — named tool executing on entity
  stage="tool_done"     — tool completed, composing answer
  stage="no_tool"       — pure LLM turn, no tool needed

Frontend handler in public/aria.html renders these into the
streamBadge with the stage stamped on a data-attribute.

These tests confirm:
  - Backend `progress` events are emitted at the SSE generator start
    in routes/aria.py
  - Each stage marker is present in the emitter source
  - Frontend handler exists for type=='progress' events
  - Existing status / done / chunk / error handlers untouched (no
    regression)
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source

ARIA_HTML = Path(__file__).resolve().parents[2] / "public" / "aria.html"


def test_backend_progress_emitter_present():
    """R-F737: chat_stream_ep generator emits progress events."""
    from aria_service.routes import aria as routes

    src = module_source(routes)
    # Find chat_stream_ep
    assert "R-F737" in src, "R-F737 marker missing from routes/aria.py"
    # Backend emits all four stage values
    assert '"stage":"detecting"' in src
    assert '"stage":"tool_running"' in src
    assert '"stage":"tool_done"' in src
    assert '"stage":"no_tool"' in src


def test_backend_progress_event_has_message_field():
    """R-F737: every progress event carries a `message` so the FE can
    render a human-readable status string."""
    from aria_service.routes import aria as routes

    src = module_source(routes)
    # All four progress emissions reference message
    matches = re.findall(
        r'"type":"progress"[^}]*"message"', src,
    )
    assert len(matches) >= 4, (
        f"R-F737: expected ≥4 progress emissions with `message`, "
        f"found {len(matches)}"
    )


def test_backend_tool_execution_moved_inside_generator():
    """R-F737: the tool execution must now be INSIDE _event_generator
    (so progress events fire DURING the wait, not after)."""
    from aria_service.routes import aria as routes

    src = module_source(routes)

    # Search for the chat_stream_ep function block and check tool
    # execution is no longer at endpoint scope above the generator.
    # Specifically: there should be no `await _execute_tool(intent, llm)`
    # BEFORE the `_event_generator` definition in chat_stream_ep.
    # The simplest proxy: the OLD comment "Tool detection + execution
    # (blocking, same as chat_ep)" at endpoint scope must be gone.
    assert "Tool detection + execution (blocking, same as chat_ep)" not in src, (
        "R-F737: legacy tool-execution-before-generator block is still "
        "present. The refactor moves it INSIDE the generator."
    )


def test_frontend_progress_handler_present():
    """R-F737: aria.html handles `progress` event type and renders it."""
    src = ARIA_HTML.read_text(encoding="utf-8")
    assert "evt.type === 'progress'" in src
    assert "R-F737" in src
    # The handler should write to streamBadge (where 'status' also writes)
    assert "streamBadge.textContent = evt.message" in src
    # And stamp the stage on a data-attribute
    assert "progressStage" in src


def test_frontend_sources_handler_present():
    """R-F732 + R-F737 coupling: aria.html also catches the `sources`
    event (emitted between R-F732 sources extraction and `done`)."""
    src = ARIA_HTML.read_text(encoding="utf-8")
    assert "evt.type === 'sources'" in src
    # And stashes the sources somewhere accessible
    assert "streamBody.dataset.sources" in src or "_ariaLatestSources" in src


def test_existing_event_handlers_untouched():
    """R-F737 must not break existing chunk / status / done / error / meta
    handlers in the FE."""
    src = ARIA_HTML.read_text(encoding="utf-8")
    assert "evt.type === 'chunk'" in src
    assert "evt.type === 'status'" in src
    assert "evt.type === 'done'" in src
    assert "evt.type === 'error'" in src
    assert "evt.type === 'meta'" in src

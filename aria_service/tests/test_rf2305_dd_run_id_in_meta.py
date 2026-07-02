"""R-F2305 — the chat stream must surface the DD run_id in its meta SSE event so
the web chat can render a live DD status card + link to the persisted report.

The run_id was extracted (R-F1951, _dd_run_id) but never emitted — a dead variable.
chat_stream_ep now adds it to the `meta` event when a DD produced one. (The SSE
generator is too heavy to drive in a unit test; this pins the emit contract at the
source. The frontend card that consumes it is verified by a headless render.)
"""
import pathlib
import re


def _src():
    return pathlib.Path("C:/code/crucix/aria_service/routes/aria.py").read_text(encoding="utf-8", errors="ignore")


def test_meta_event_emits_dd_run_id():
    src = _src()
    # The meta event build must conditionally include dd_run_id from _dd_run_id.
    idx = src.find('"type": "meta", "tool_used": tool_used')
    assert idx > 0, "meta event build not found / shape changed"
    block = src[idx:idx + 320]
    assert 'locals().get("_dd_run_id")' in block or "_dd_run_id" in block, block[:200]
    assert '_meta_evt["dd_run_id"] = _dd_run_id' in block, block[:200]


def test_dd_run_id_still_extracted():
    # R-F1951 extraction must remain (the field's source).
    src = _src()
    assert 'Run ID:' in src and "_dd_run_id" in src

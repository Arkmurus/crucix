"""R-F1951 capability test: chat_ep and chat_stream_ep must include
dd_report.run_id in their response when a DD was run.

The DD report IS persisted to Redis by _persist_report, but the web UI
needs the run_id in the chat response to link to the DD Reports panel.
"""
import inspect
import aria_service.routes.aria as aria

# R-F3786/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source

src = module_source(aria)


def test_chat_ep_extracts_dd_run_id_from_tool_context():
    """chat_ep must extract run_id from tool_context after _execute_tool."""
    assert '_dd_run_id = ""' in src, (
        "chat_ep missing _dd_run_id initialisation"
    )
    assert 'result["dd_report"] = {"run_id": _dd_run_id}' in src, (
        "chat_ep missing dd_report attachment to result"
    )


def test_chat_stream_ep_extracts_dd_run_id_from_tool_context():
    """chat_stream_ep must extract run_id from tool_context after _execute_tool."""
    assert '_dd_run_id = ""' in src, (
        "chat_stream_ep missing _dd_run_id initialisation"
    )
    assert '_done_event["dd_report"] = {"run_id": _dd_run_id}' in src, (
        "chat_stream_ep missing dd_report in done event"
    )


def test_hallucination_guard_recognises_dd_citations():
    """Hallucination guard must recognise [from dd_orchestrate:...] citations."""
    from aria_service.intel import hallucination_guard as hg
    # A response with [CONFIRMED] claims cited via dd_orchestrate should pass
    response = (
        "The company is registered in Portugal. "
        "[CONFIRMED from dd_orchestrate:abc123]"
    )
    result = hg.check_response(response, tool_context="DD report data here")
    assert result["suggested_action"] != "block", (
        f"DD-cited CONFIRMED claim was blocked: {result['red_flags']}"
    )
    # A response with [CONFIRMED] claims WITHOUT any citation should still block
    bad_response = (
        "The company registration number is 516. "
        "[CONFIRMED] this is an unverified claim"
    )
    bad_result = hg.check_response(bad_response, tool_context="")
    assert bad_result["suggested_action"] == "block", (
        f"Uncited CONFIRMED claim should be blocked but got {bad_result['suggested_action']}: {bad_result['red_flags']}"
    )

"""R-F1950 capability test: chat_ep must pass user_id to _execute_tool.

Without user_id, _launch_deep_dd_bg silently no-ops (checks `if not user_id:
return False`), so the deep-bg DD never fires and the user gets a misleading
"background job started" message. This test statically asserts the call site
passes user_id.
"""
import inspect
import aria_service.routes.aria as aria

src = inspect.getsource(aria)


def test_chat_ep_passes_user_id_to_execute_tool():
    """The chat_ep _execute_tool call must include user_id=..."""
    # Find the _execute_tool call in chat_ep (not the definition, not eval, not stream)
    # The chat_ep call is the one that does NOT have dd_budget_s (stream has it)
    # and is NOT the eval path (which has no user context).
    # We look for the pattern: _execute_tool(intent, llm, ...user_id=...)
    assert 'user_id=getattr(req, "user_id", "") or ""' in src, (
        "chat_ep _execute_tool call missing user_id parameter — "
        "deep-bg DD will silently no-op"
    )


def test_chat_stream_ep_passes_user_id_to_execute_tool():
    """The chat_stream_ep _execute_tool call must also include user_id=..."""
    # Count occurrences — should be at least 2 (chat_ep + chat_stream_ep)
    count = src.count('user_id=getattr(req, "user_id", "") or ""')
    assert count >= 2, (
        f"Expected >=2 call sites passing user_id to _execute_tool, "
        f"found {count}. chat_stream_ep may be missing the parameter."
    )


def test_launch_deep_dd_bg_guards_against_empty_user_id():
    """_launch_deep_dd_bg must return False when user_id is empty."""
    # Static check: the guard exists
    assert "if not user_id:" in src, (
        "_launch_deep_dd_bg missing user_id guard — "
        "will attempt deep-bg DD without owner"
    )
    assert "return False" in src[src.find("if not user_id:"):src.find("if not user_id:") + 200], (
        "_launch_deep_dd_bg user_id guard must return False"
    )

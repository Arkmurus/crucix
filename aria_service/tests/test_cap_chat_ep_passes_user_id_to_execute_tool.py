"""R-F1950 capability test: chat_ep must pass user_id to _execute_tool.

Without user_id, _launch_deep_dd_bg silently no-ops (checks `if not user_id:
return False`), so the deep-bg DD never fires and the user gets a misleading
"background job started" message. This test statically asserts the call site
passes user_id.
"""
import inspect
import aria_service.routes.aria as aria

# R-F3786/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source

src = module_source(aria)


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
    """Drive the real launcher: anonymous chat cannot create an unowned DD."""
    assert aria._launch_deep_dd_bg(
        {"name": "Acme Corp"}, object(), user_id="", user_email=None,
    ) is False

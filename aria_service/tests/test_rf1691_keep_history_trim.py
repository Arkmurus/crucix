"""R-F1691 — edit-&-resend history trim.

When the web UI edits a previous user message, it sends keep_history = the number
of prior session messages to retain; the engine trims session["messages"] to that
length before the new turn so the stored thread matches the edited view. Drives
the real helper used by aria_chat_stream.
"""
from aria_service.aria_engine import _trim_session_for_resend


def _sess(n):
    return {"messages": [{"role": "user" if i % 2 == 0 else "aria", "content": str(i)} for i in range(n)]}


def test_trim_keeps_first_n():
    s = _trim_session_for_resend(_sess(6), 2)
    assert [m["content"] for m in s["messages"]] == ["0", "1"]


def test_none_is_noop():
    s = _trim_session_for_resend(_sess(6), None)
    assert len(s["messages"]) == 6


def test_keep_zero_clears_history():
    s = _trim_session_for_resend(_sess(4), 0)
    assert s["messages"] == []


def test_keep_more_than_present_is_noop():
    s = _trim_session_for_resend(_sess(3), 10)
    assert len(s["messages"]) == 3


def test_bad_input_is_defensive():
    assert len(_trim_session_for_resend(_sess(3), "nope")["messages"]) == 3
    assert len(_trim_session_for_resend(_sess(3), -5)["messages"]) == 0  # clamped to 0

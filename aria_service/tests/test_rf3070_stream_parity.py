"""R-F3070 §13 parity guard — chat_stream is a subset-fork of chat.

Every short-circuit path that answers a user must persist + index the turn on
BOTH endpoints. The whole defect existed because those paths were added to the
two handlers independently and neither got the persistence hook; §13 exists so
that class of drift is caught by a test rather than by a user losing a chat.

Source-level assertions (the two handlers are ~1600 lines apart in one 28k-line
module, so this is the practical way to pin the invariant).
"""
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "routes" / "aria.py"
_TEXT = _SRC.read_text(encoding="utf-8", errors="replace")


def _handler_body(name: str) -> str:
    """Slice from `async def <name>` to the next top-level `@router.` decorator."""
    start = _TEXT.index(f"async def {name}")
    nxt = _TEXT.find("\n@router.", start)
    return _TEXT[start:nxt if nxt != -1 else len(_TEXT)]


def test_both_handlers_persist_the_trivial_short_circuit():
    for handler in ("chat_ep", "chat_stream_ep"):
        body = _handler_body(handler)
        assert "trivial_reply" in body, f"{handler} lost its trivial short-circuit"
        assert "persist_trivial_turn" in body, (
            f"{handler} answers a trivial turn WITHOUT persisting it (R-F3070). "
            "Pre-fix that turn was absent from both the session history and the "
            "chat sidebar — the exchange simply vanished."
        )


def test_both_handlers_pass_the_owner_to_the_fast_lane():
    for handler in ("chat_ep", "chat_stream_ep"):
        body = _handler_body(handler)
        assert "fast_lane_chat" in body, f"{handler} lost its fast lane"
        call = re.search(r"fast_lane_chat\((.{0,200}?)\)", body, re.S)
        assert call, f"could not locate the fast_lane_chat call in {handler}"
        assert "user_id" in call.group(1), (
            f"{handler} calls fast_lane_chat WITHOUT user_id (R-F3070). Without "
            "the owner the fast lane cannot stamp session['userId'] or register "
            "the conversation, so a fast-lane-only session never reaches the "
            "sidebar at all."
        )


def test_fast_lane_registers_before_returning():
    """The engine-side half: one writer, called by both endpoints."""
    engine = (_SRC.resolve().parents[1] / "aria_engine.py").read_text(
        encoding="utf-8", errors="replace")
    start = engine.index("async def fast_lane_chat")
    body = engine[start:engine.index("\nasync def ", start + 10)]
    assert "_register_turn" in body, (
        "fast_lane_chat must register the conversation — it bypasses the main "
        "generator, which is the only other place that does"
    )


def _call_sites(func_name):
    """Real Call nodes only — prose and docstrings that mention the old code
    (this file's own explanation of the defect does) must not trip the guard."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    found = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name == "conversation_store.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name == func_name:
                found.append((f"{path.relative_to(root)}:{node.lineno}", node))
    return found


def test_no_call_site_decides_create_vs_touch_itself():
    """R-F3081 — the branch that caused the defect must not come back.

    Five sites used to run `if len(history) <= 2` and pick create-or-touch
    themselves. That length test is a PROXY for "is this the first turn";
    whenever it disagreed with reality the else branch created the conversation
    with no first message and the row stayed "New conversation" for life.
    conversation_store now owns the decision, from the authoritative check
    (does the meta hash exist). Nobody outside it may create directly.
    """
    offenders = [loc for loc, _ in _call_sites("create_conversation")]
    assert not offenders, (
        "create_conversation must only be called by conversation_store itself — "
        f"found: {offenders}. Use touch_conversation(session_id, user_id, "
        "first_message=<the user's message>)."
    )


def test_every_registration_passes_a_first_message():
    """A touch that can CREATE must carry a title (the R-F3070 root cause)."""
    bad = [loc for loc, node in _call_sites("touch_conversation")
           if not any(kw.arg == "first_message" for kw in node.keywords)
           and len(node.args) < 3]
    assert not bad, (
        "touch_conversation can create the conversation, so every call must pass "
        f"first_message or the sidebar row reads 'New conversation' forever: {bad}"
    )

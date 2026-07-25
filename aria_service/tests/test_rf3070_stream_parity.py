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
    assert "_register_shortcircuit_turn" in body, (
        "fast_lane_chat must register the conversation — it bypasses the main "
        "generator, which is the only other place that does"
    )

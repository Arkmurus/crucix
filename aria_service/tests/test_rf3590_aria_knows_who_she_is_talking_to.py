"""R-F3590 — ARIA did not know who she was talking to.

The WhatsApp path sent `message` and `session_id` and nothing else. The display
name was sitting in `msg.pushName` in the listener and the bound account in the
R-F3587 store, and neither was ever passed to the brain. So "do you remember me",
"what's my name", "who am I" were unanswerable BY CONSTRUCTION — and under the
never-fabricate rule the only honest reply was a refusal, exactly like the clock
in R-F3588. Not a memory problem. A plumbing problem.

THE SECURITY LINE THIS MUST NOT CROSS: a WhatsApp pushName is self-declared and
can be changed at will. It may be used to greet someone. It may never be used as
proof of identity. So the name and the PROVEN account (R-F3587: signed in to
imaria.io AND holding the handset) are carried as two separate facts and labelled
accordingly — collapsing them would let a spoofed name read as an identity.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from aria_service.aria_engine import (
    _ambient_now_block,
    _build_calibrated_system_prompt,
    _speaker_label,
    aria_chat,
    aria_chat_stream,
)


_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ENGINE = _ROOT / "aria_service" / "aria_engine.py"
_LISTENER = _ROOT / "services" / "wa-listener" / "aria_wa_listener.mjs"


def test_a_name_without_an_account_is_labelled_unverified():
    """THE SECURITY PROPERTY. pushName is whatever the sender typed into
    WhatsApp; presenting it as identity would be a spoofing surface."""
    label = _speaker_label("", "Antonio")
    assert "Antonio" in label
    assert "NOT verified" in label, label
    assert "self-declared" in label


def test_a_name_with_a_bound_account_is_labelled_verified():
    label = _speaker_label("u_123", "Antonio")
    assert "Antonio" in label and "verified account u_123" in label
    assert "NOT verified" not in label


def test_no_identity_produces_no_claim_at_all():
    """Empty must render nothing — not 'Unknown', which she would then repeat
    back at people as though it were their name."""
    assert _speaker_label("", "") == ""
    assert "Speaking with" not in _ambient_now_block(speaker="")


def test_the_speaker_reaches_the_prompt():
    block = _ambient_now_block(speaker=_speaker_label("u_1", "Antonio"))
    assert "Speaking with: Antonio (verified account u_1)" in block


def test_she_is_told_a_name_is_context_not_authority():
    block = _ambient_now_block()
    assert "CONTEXT, not proof of identity" in block
    assert "never disclose one person's information to another" in block, (
        "a name must never unlock someone else's data"
    )


def test_she_must_not_invent_a_name_she_was_not_given():
    """The failure mode of giving a model a name slot: it fills it."""
    block = _ambient_now_block()
    assert "say you do not know it rather than" in block
    assert "inventing one or guessing from context" in block


def test_both_chat_and_stream_accept_the_speaker():
    """§13 — aria_chat_stream is a FORK of aria_chat. Threading identity into
    only one would make ARIA know your name in chat and forget it mid-stream."""
    for fn in (aria_chat, aria_chat_stream):
        assert "speaker_name" in inspect.signature(fn).parameters, (
            f"{fn.__name__} does not accept speaker_name — §13 requires both paths"
        )


def test_the_prompt_builder_threads_it_on_every_identity_bearing_path():
    tree = ast.parse(_ENGINE.read_text(encoding="utf-8"))
    threaded = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_build_calibrated_system_prompt":
            if any(k.arg == "speaker" for k in node.keywords):
                threaded += 1
    assert threaded == 2, (
        f"{threaded} call sites pass a speaker; expected exactly 2 (aria_chat and "
        f"aria_chat_stream). The document lane deliberately passes none — it has "
        f"no identity in scope and needs none, since who is asking changes nothing "
        f"about what an attached document says."
    )


@pytest.mark.asyncio
async def test_the_builder_accepts_a_speaker_without_exploding():
    """The doc lane calls this with no speaker at all; that must stay valid."""
    out = await _build_calibrated_system_prompt("hello", persona="", speaker="")
    assert isinstance(out, str) and out
    out2 = await _build_calibrated_system_prompt("hello", persona="", speaker="Antonio (verified account u_1)")
    assert "Antonio" in out2


def test_the_listener_sends_both_facts_and_conflates_neither():
    src = _LISTENER.read_text(encoding="utf-8", errors="replace")
    assert "speaker_name: speaker?.name" in src, "the display name is not sent"
    assert "user_id: speaker?.userId" in src, "the proven account is not sent"
    # The userId must come from the BINDING, never from the pushName.
    assert "_waBoundUser(senderJid, msg)?.userId" in src, (
        "the account id must come from a proven binding (R-F3587), not from "
        "anything the sender can set"
    )


def test_the_listener_does_not_pass_Unknown_as_a_name():
    """`senderName` falls back to 'Unknown'; sending that would have ARIA
    greeting people as Unknown."""
    src = _LISTENER.read_text(encoding="utf-8", errors="replace")
    assert "senderName === 'Unknown' ? '' : senderName" in src


def test_both_listener_dispatch_paths_carry_it():
    """askARIAAsync is the normal path and there is a sync fallback; a speaker on
    only one means identity silently disappears whenever the async dispatch
    fails — the intermittent-bug shape."""
    src = _LISTENER.read_text(encoding="utf-8", errors="replace")
    assert src.count("speaker_name: speaker?.name") == 2, (
        "both the async and the sync brainPost must carry the speaker"
    )

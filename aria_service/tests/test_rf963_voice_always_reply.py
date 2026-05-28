"""R-F963 — WhatsApp voice notes are routed to ARIA even without a wake-word.

Live (2026-05-28): voice notes that DID contain "Aria" still got no reply because
STT (even the upgraded 'small' model + beam-search, R-F960) kept dropping/garbling
the short leading wake-word on accented speech (12:51 "Area…", 13:17/13:48 dropped
entirely). In name-only mode the listener only replies on a textual wake-word
match (MENTIONS_RE), so the notes went unanswered.

Operator chose: treat ALL voice notes as directed at ARIA. R-F963 sets an
_isVoiceNote flag on the transcribed-voice path and ORs it into the mention gate
(behind ARIA_VOICE_ALWAYS_REPLY, default on), so a voice note is an implicit
mention — and flows through the same handler that re-attaches recent documents
(R-F912). The listener has no JS harness; this verifies the wiring at source level.
"""
from __future__ import annotations

from pathlib import Path

from aria_service.routes import aria as a


def _wa() -> str:
    return (Path(a.__file__).resolve().parents[2] / "services" / "wa-listener"
            / "aria_wa_listener.mjs").read_text(encoding="utf-8")


def test_rf963_voice_always_reply_flag_present():
    wa = _wa()
    assert "VOICE_ALWAYS_REPLY" in wa, "R-F963: env-gated voice-reply flag must exist"
    assert "ARIA_VOICE_ALWAYS_REPLY" in wa, "R-F963: env var name must be wired"


def test_rf963_voice_note_flag_set_on_transcript():
    wa = _wa()
    assert "_isVoiceNote = true" in wa, "R-F963: voice path must flag the message as a voice note"


def test_rf963_mention_gate_includes_voice_note_clause():
    wa = _wa()
    # the reply gate must fire on a wake-word match OR a voice note
    assert "_isVoiceNote && VOICE_ALWAYS_REPLY" in wa, (
        "R-F963: mention gate must OR-in the voice-note implicit-mention clause"
    )


def test_rf963_voice_clause_is_in_the_mention_branch_not_autorespond():
    """The voice clause must gate the MENTION handler (which does R-F912 doc
    re-attach), not the AUTO_RESPOND keyword path — so a voice contract
    follow-up gets the document re-attached."""
    wa = _wa()
    gate_idx = wa.find("_isVoiceNote && VOICE_ALWAYS_REPLY")
    # search for the doc re-attach CALL that follows the gate (the function is
    # defined earlier, so search from the gate position, not from the file start)
    reattach_idx = wa.find("_recentDocsForFollowup", gate_idx)
    assert gate_idx != -1 and reattach_idx != -1
    # the doc re-attach lives just inside the mention branch, right after the gate
    assert 0 < (reattach_idx - gate_idx) < 1200, (
        "R-F963: voice gate should sit on the mention branch that re-attaches docs"
    )

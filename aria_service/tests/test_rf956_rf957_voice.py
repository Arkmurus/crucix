"""R-F956/F957 — WhatsApp voice-note transcription (OSS faster-whisper, local).

Real transcription is verified post-deploy (the model bakes into the image and
faster-whisper isn't a local dep). Here we cover the flag-gating, input
validation, and the listener wiring — the parts that must be right so shipping
it (flag OFF) can't destabilise the brain.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from aria_service.routes import aria as a


# ── R-F956 — brain transcription module: gating + validation ─────────────────

def test_rf956_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_VOICE_TRANSCRIBE_ENABLED", raising=False)
    from aria_service.intel import voice_transcribe as vt
    assert vt.is_enabled() is False
    r = asyncio.run(vt.transcribe_audio(b"x" * 500))
    assert r["ok"] is False and r["skipped"] == "disabled"


def test_rf956_empty_audio_when_enabled(monkeypatch):
    monkeypatch.setenv("ARIA_VOICE_TRANSCRIBE_ENABLED", "1")
    from aria_service.intel import voice_transcribe as vt
    assert vt.is_enabled() is True
    # empty/oversize are checked BEFORE the model loads — no faster-whisper needed
    assert asyncio.run(vt.transcribe_audio(b""))["error"] == "empty_audio"
    assert asyncio.run(vt.transcribe_audio(b"\x00" * 10))["error"] == "empty_audio"


def test_rf956_endpoint_present():
    src = open(a.__file__, encoding="utf-8").read()
    assert '@router.post("/transcribe")' in src
    assert "voice_transcribe" in src
    assert "audio_b64" in src


# ── R-F957 — listener wiring ─────────────────────────────────────────────────

def _wa() -> str:
    return (Path(a.__file__).resolve().parents[2] / "services" / "wa-listener"
            / "aria_wa_listener.mjs").read_text(encoding="utf-8")


def test_rf957_listener_transcribes_voice_into_text_path():
    wa = _wa()
    assert "msg.message?.audioMessage" in wa
    assert "/api/aria/transcribe" in wa
    assert "audio_b64: buffer.toString('base64')" in wa
    # the transcript must populate `text` so it flows through capture + wake-word
    assert "text = tr.text" in wa
    assert "let text =" in wa  # `text` made mutable for the transcript

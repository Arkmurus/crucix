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


# ── R-F980 — wrong-language auto-detect guard ────────────────────────────────

class _Seg:
    def __init__(self, text): self.text = text

class _Info:
    def __init__(self, language, prob=0.9, duration=3.0):
        self.language = language
        self.language_probability = prob
        self.duration = duration

class _FakeModel:
    """Stands in for faster-whisper's WhisperModel. Records the `language`
    kwarg of each transcribe() call so tests can assert the guard re-ran."""
    def __init__(self, first_lang, first_text, forced_text="forced text"):
        self.first_lang, self.first_text, self.forced_text = first_lang, first_text, forced_text
        self.calls = []
    def transcribe(self, fileobj, **kwargs):
        lang = kwargs.get("language")
        self.calls.append(lang)
        if lang is None:
            return [_Seg(self.first_text)], _Info(self.first_lang)
        return [_Seg(self.forced_text)], _Info(lang)


def _prep(monkeypatch, fake, *, pinned=None, allowed={"en", "pt", "es", "fr"}, fallback="en"):
    monkeypatch.setenv("ARIA_VOICE_TRANSCRIBE_ENABLED", "1")
    from aria_service.intel import voice_transcribe as vt
    monkeypatch.setattr(vt, "_model", fake)          # _get_model returns it, no faster-whisper
    monkeypatch.setattr(vt, "_PINNED_LANG", pinned)
    monkeypatch.setattr(vt, "_ALLOWED_LANGS", set(allowed))
    monkeypatch.setattr(vt, "_FALLBACK_LANG", fallback)
    return vt


def test_rf980_implausible_language_is_rejected_and_refallback(monkeypatch):
    """The Arabic-transcript bug: auto-detect returns 'ar' (not in allow-list)
    → re-transcribe forcing the fallback 'en', return the forced result."""
    fake = _FakeModel(first_lang="ar", first_text="arabic garbage", forced_text="what is your recommendation")
    vt = _prep(monkeypatch, fake)
    r = asyncio.run(vt.transcribe_audio(b"x" * 500))
    assert fake.calls == [None, "en"], "must auto-detect then force the fallback"
    assert r["ok"] is True
    assert r["text"] == "what is your recommendation"
    assert r["language"] == "en"


def test_rf980_allowed_language_accepted_no_retranscribe(monkeypatch):
    """A plausible detection (pt) is accepted as-is — no second decode."""
    fake = _FakeModel(first_lang="pt", first_text="ola aria")
    vt = _prep(monkeypatch, fake)
    r = asyncio.run(vt.transcribe_audio(b"x" * 500))
    assert fake.calls == [None], "an allowed language must not trigger a re-transcribe"
    assert r["text"] == "ola aria" and r["language"] == "pt"


def test_rf980_hard_pin_skips_detection(monkeypatch):
    """ARIA_WHISPER_LANGUAGE set → pin directly, single forced call, no detect."""
    fake = _FakeModel(first_lang="en", first_text="auto", forced_text="pinned text")
    vt = _prep(monkeypatch, fake, pinned="en")
    r = asyncio.run(vt.transcribe_audio(b"x" * 500))
    assert fake.calls == ["en"], "pinned language must be passed on the only call"
    assert r["text"] == "pinned text" and r["language"] == "en"


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

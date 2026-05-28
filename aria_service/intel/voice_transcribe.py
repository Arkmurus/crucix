"""R-F956 (2026-05-28) — WhatsApp voice-note transcription via faster-whisper.

OSS, self-hosted, native — audio never leaves ARIA's infra (no paid STT API),
per the "ARIA mirrors Claude / no paid dependency" rule (CLAUDE.md §6). The
listener downloads the Opus voice note and POSTs it to /api/aria/transcribe;
the transcript is then processed exactly like a text message (wake-word reply +
Compliance Watch capture — "ARIA hears everything", §20).

WEDGE-SAFETY (the priority, given the 2026-05 event-loop history):
  - lazy model load happens once, OFF the event loop (asyncio.to_thread)
  - every transcription runs OFF the loop (asyncio.to_thread) — CTranslate2
    releases the GIL during inference, but we still thread it
  - a concurrency SEMAPHORE (default 1) caps simultaneous transcriptions so a
    burst of voice notes cannot saturate CPU and re-wedge the loop
  - FLAG-GATED: does nothing unless ARIA_VOICE_TRANSCRIBE_ENABLED=1, so shipping
    it cannot destabilise the brain until it is explicitly turned on + verified.

Model is baked into the image (Dockerfile) so it loads from disk offline
(HF_HUB_OFFLINE, R-F938) — no cold-start network download.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time

logger = logging.getLogger("aria.voice")

_MODEL_SIZE = (os.getenv("ARIA_WHISPER_MODEL") or "base").strip()
_CONCURRENCY = max(1, int(os.getenv("ARIA_WHISPER_CONCURRENCY", "1") or "1"))
_MAX_AUDIO_BYTES = int(os.getenv("ARIA_WHISPER_MAX_BYTES", str(25 * 1024 * 1024)))  # 25MB

_model = None
_model_lock = asyncio.Lock()
_sem = asyncio.Semaphore(_CONCURRENCY)


def is_enabled() -> bool:
    return (os.getenv("ARIA_VOICE_TRANSCRIBE_ENABLED", "0").strip().lower()
            in ("1", "true", "yes", "on"))


async def _get_model():
    """Lazy-load the faster-whisper model once, off the event loop."""
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is None:
            def _load():
                from faster_whisper import WhisperModel
                # int8 on CPU: ~4x smaller/faster than fp32, fine for voice notes.
                return WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8")
            _model = await asyncio.to_thread(_load)
            logger.info("[voice] faster-whisper model '%s' loaded (int8/cpu)", _MODEL_SIZE)
    return _model


async def transcribe_audio(audio_bytes: bytes, *, mime: str = "") -> dict:
    """Transcribe voice audio → {ok, text, language, duration_s, elapsed_s}.

    Returns ok=False with a reason on disabled / empty / oversize / error — never
    raises, so a bad voice note can't break the listener's message loop.
    """
    if not is_enabled():
        return {"ok": False, "skipped": "disabled"}
    if not audio_bytes or len(audio_bytes) < 100:
        return {"ok": False, "error": "empty_audio"}
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        return {"ok": False, "error": f"audio_too_large ({len(audio_bytes)} > {_MAX_AUDIO_BYTES})"}
    t0 = time.time()
    try:
        model = await _get_model()
        async with _sem:   # cap concurrent transcriptions → no CPU saturation
            def _run():
                # faster-whisper decodes Opus/OGG/MP3/WAV via PyAV from a file-like.
                segments, info = model.transcribe(
                    io.BytesIO(audio_bytes), beam_size=1, vad_filter=True,
                )
                text = " ".join(s.text.strip() for s in segments).strip()
                return text, getattr(info, "language", "") or "", float(getattr(info, "duration", 0.0) or 0.0)
            text, lang, dur = await asyncio.to_thread(_run)
        elapsed = round(time.time() - t0, 1)
        if not text:
            logger.info("[voice] transcribed but empty (silence/noise?) %.1fs", elapsed)
            return {"ok": False, "error": "no_speech", "language": lang,
                    "duration_s": round(dur, 1), "elapsed_s": elapsed}
        logger.info("[voice] transcribed %.1fs audio in %.1fs (lang=%s, %d chars)",
                    dur, elapsed, lang, len(text))
        return {"ok": True, "text": text, "language": lang,
                "duration_s": round(dur, 1), "elapsed_s": elapsed}
    except Exception as e:
        logger.warning("[voice] transcription failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}

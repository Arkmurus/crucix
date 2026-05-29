"""R-F956 (2026-05-28) — Wh

atsApp voice-note transcription via faster-whisper.

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
(HF_HUB_OFFLINE, R-F938) — no cold-start network download."""
from __future__ import annotations
from .engine_wiring import wire_success

import asyncio
import io
import logging
import os
import time

logger = logging.getLogger("aria.voice")

# R-F960 (2026-05-28) — default upgraded base→small + beam search. The 'base'
# model with greedy decoding kept mis-transcribing the "Aria" wake-word (→"Area",
# or dropping the short leading word) on accented / non-native-English speech, so
# the WhatsApp name-only gate never fired and voice notes got no reply (live:
# 12:51 "Area…", 13:17 wake-word dropped — both silent). 'small' is markedly more
# accurate on accents (~460MB vs 142MB, staged on /data per R-F958 lazy-load), and
# beam_size=5 over greedy (1) further cuts word/wake-word errors. Both env-tunable
# so we can dial back without a redeploy if CPU/latency becomes a problem.
_MODEL_SIZE = (os.getenv("ARIA_WHISPER_MODEL") or "small").strip()
_BEAM_SIZE = max(1, int(os.getenv("ARIA_WHISPER_BEAM", "5") or "5"))
_CONCURRENCY = max(1, int(os.getenv("ARIA_WHISPER_CONCURRENCY", "1") or "1"))
_MAX_AUDIO_BYTES = int(os.getenv("ARIA_WHISPER_MAX_BYTES", str(25 * 1024 * 1024)))  # 25MB

# R-F980 (2026-05-28) — guard against Whisper's wrong-language auto-detect.
# Live: an operator voice note came back as an ARABIC transcript — faster-whisper
# auto-detects language from the first ~30s, and on short/accented/noisy audio it
# can confidently pick a language the speaker never used. Two levers:
#   ARIA_WHISPER_LANGUAGE      — if set, HARD-PIN (skip detection entirely).
#   ARIA_WHISPER_ALLOWED_LANGS — else auto-detect but reject any detected lang
#                                outside this set (default en/pt/es/fr) and
#                                re-transcribe forcing ARIA_WHISPER_FALLBACK_LANG.
# faster-whisper populates info.language BEFORE the (lazy) segment decode runs, so
# the allow-list check costs only the cheap detection pass — we decode once in the
# normal case, twice only when a detection is rejected.
_PINNED_LANG = (os.getenv("ARIA_WHISPER_LANGUAGE") or "").strip().lower() or None
_ALLOWED_LANGS = {
    s.strip().lower()
    for s in (os.getenv("ARIA_WHISPER_ALLOWED_LANGS") or "en,pt,es,fr").split(",")
    if s.strip()
}
_FALLBACK_LANG = (os.getenv("ARIA_WHISPER_FALLBACK_LANG") or "en").strip().lower()

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
                # R-F958 — load from the persistent /data volume. The model is
                # NOT baked into the image (that pushed it past Fly's 8GB cap and
                # blocked all deploys); it's downloaded to /data once, then loaded
                # from there offline. int8/CPU: ~4x smaller/faster than fp32.
                dl = os.getenv("ARIA_WHISPER_DIR") or "/data/whisper_models"
                try:
                    os.makedirs(dl, exist_ok=True)
                except Exception:
                    dl = None
                return WhisperModel(_MODEL_SIZE, device="cpu",
                                    compute_type="int8", download_root=dl)
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
                # R-F980 — pin or guard the language so a wrong auto-detect (the
                # Arabic-transcript bug) can't ship a wrong-script result.
                if _PINNED_LANG:
                    segments, info = model.transcribe(
                        io.BytesIO(audio_bytes), beam_size=_BEAM_SIZE,
                        vad_filter=True, language=_PINNED_LANG,
                    )
                else:
                    segments, info = model.transcribe(
                        io.BytesIO(audio_bytes), beam_size=_BEAM_SIZE, vad_filter=True,
                    )
                    detected = (getattr(info, "language", "") or "").lower()
                    # info.language is set before the lazy `segments` decode runs,
                    # so we can reject an implausible detection without decoding it.
                    if _ALLOWED_LANGS and detected and detected not in _ALLOWED_LANGS:
                        prob = float(getattr(info, "language_probability", 0.0) or 0.0)
                        logger.warning(
                            "[voice] R-F980 implausible language '%s' (p=%.2f) not in "
                            "%s — re-transcribing forced as '%s'",
                            detected, prob, sorted(_ALLOWED_LANGS), _FALLBACK_LANG,
                        )
                        segments, info = model.transcribe(
                            io.BytesIO(audio_bytes), beam_size=_BEAM_SIZE,
                            vad_filter=True, language=_FALLBACK_LANG,
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

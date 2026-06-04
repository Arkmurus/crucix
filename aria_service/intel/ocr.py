"""
ARIA OCR — Extract text from images. ZERO-SETUP, INDEPENDENT-FIRST.

Reads:
- Screenshots of procurement documents
- Scanned PDFs shared via WhatsApp
- Photos of contracts, business cards, tenders, invoices
- Images from email attachments

The extracted text is fed to ARIA's knowledge base and neural memory.

Pipeline order (works out of the box, no manual install required):
  1. EasyOCR        ← if installed (best quality, 100% local, offline)
  2. Tesseract      ← if installed (very fast, low memory, 100% local)
  3. Ollama vision  ← if running (best for diagrams + handwriting)
  4. OCR.space      ← FREE PUBLIC API, works immediately, no setup at all
  5. Cloud LLM      ← only if ARIA_VISION_PROVIDER is explicitly configured

PLUS: on first call, if no LOCAL backend is available, ARIA kicks off a
BACKGROUND `pip install easyocr Pillow pytesseract` so the second image
ever sent will be served fully locally. No human intervention needed.

────────────────────────────────────────────────────────────────────────────
DEDICATED VISION PROVIDER (recommended for DeepSeek users)
────────────────────────────────────────────────────────────────────────────
DeepSeek's public API does not currently support vision (only deepseek-chat
and deepseek-reasoner are exposed). If you use DeepSeek as your main LLM you
MUST set up a separate vision provider for image OCR. The cheapest options:

  1. Google Gemini (free tier — ~15 req/min, no card required):
       Get key:  https://aistudio.google.com/apikey
       Set env:  ARIA_VISION_PROVIDER=gemini
                 ARIA_VISION_API_KEY=AIza...
                 ARIA_VISION_MODEL=gemini-2.5-flash

  2. OpenRouter free vision model (also free):
       Get key:  https://openrouter.ai/keys
       Set env:  ARIA_VISION_PROVIDER=openrouter
                 ARIA_VISION_API_KEY=sk-or-...
                 ARIA_VISION_MODEL=google/gemini-flash-1.5:free

  3. Anthropic Claude (paid, highest quality for documents):
       Set env:  ARIA_VISION_PROVIDER=anthropic
                 ARIA_VISION_API_KEY=sk-ant-...
                 ARIA_VISION_MODEL=claude-sonnet-4-6

  4. Tesseract (free, local, no API — lower quality):
       Install:  pip install pytesseract Pillow
                 + the Tesseract binary on the host
       No env vars needed — automatic fallback.

If ARIA_VISION_PROVIDER is set, ocr.py uses it directly and never tries the
main LLM provider chain. This is the cleanest separation for DeepSeek users.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger("aria.ocr")


async def extract_text_from_image(
    image_data: bytes,
    filename: str = "image.jpg",
    context: str = "",
    llm=None,
) -> dict:
    """Extract text from an image. ZERO-SETUP — works out of the box.

    Pipeline (each step tried in order; first usable text wins):

      1. EasyOCR              — local, ~250MB, offline forever
      2. Tesseract            — local, fast, needs system binary
      3. Ollama vision        — local, best for diagrams/handwriting
      4. OCR.space PUBLIC     — free public API, no setup needed
      5. Cloud LLM vision     — opt-in via ARIA_VISION_PROVIDER

    Plus: when no LOCAL backend is installed, kicks off a BACKGROUND
    `pip install easyocr Pillow pytesseract` so the next image will be
    served from a fully local backend (no cloud, no third-party).

    Returns:
        {"text": str, "method": str, "confidence": float, ...}
        method ∈ {"easyocr", "tesseract", "ollama:<model>",
                  "ocrspace_free", "vision:<provider>", "none"}
    """
    if not image_data or len(image_data) < 100:
        return {"text": "", "method": "none", "confidence": 0}

    mime = _detect_mime(image_data, filename)
    prefer_cloud = (os.getenv("ARIA_OCR_PREFER_CLOUD", "") or "").lower() in ("1", "true", "yes")

    async def _try_easyocr():
        return await _ocr_via_easyocr(image_data)

    async def _try_tesseract():
        return await _ocr_via_tesseract(image_data)

    async def _try_ollama_vision():
        return await _ocr_via_ollama_vision(image_data, mime, context)

    async def _try_ocrspace():
        return await _ocr_via_ocrspace(image_data, mime)

    async def _try_cloud():
        if not llm or not hasattr(llm, "complete"):
            return None
        return await _ocr_via_llm(image_data, mime, context, llm)

    # Tesseract is the primary local OCR backend. EasyOCR has been REMOVED
    # from the chain entirely after repeated OOM kills on first-image
    # cold-load (200MB model + sync .encode() calls that block the event
    # loop and starve fly health checks). Tesseract is ~30MB resident,
    # already has eng/por/fra/spa lang packs installed in the image, and
    # handles ~95% of WhatsApp screenshots / PDFs / document photos.
    # Quality fallbacks: Ollama vision (if local Ollama running), then
    # OCR.space free public API, then the cloud LLM vision provider.
    # Past incident 2026-04-08: even with ARIA_PREWARM_EASYOCR set, the
    # first OCR call still OOM'd the worker, returning 502. Removing
    # EasyOCR entirely is the only way to make this bulletproof on a
    # 2GB fly.io machine.
    chain = (
        [_try_cloud, _try_tesseract, _try_ollama_vision, _try_ocrspace]
        if prefer_cloud
        else [_try_tesseract, _try_ollama_vision, _try_ocrspace, _try_cloud]
    )

    # Track whether any LOCAL backend was actually available so we know
    # whether to trigger background auto-install.
    # IMPORTANT: do NOT call _get_easyocr_reader() here. EasyOCR is removed
    # from the chain (incident 2026-04-08) but the lazy-loader still imports
    # easyocr and instantiates Reader(...), which loads the 200MB ONNX model
    # into RAM on every request and OOM-kills the worker → 502. Only probe
    # the backends actually used by the chain.
    local_backend_available = (
        _check_tesseract_installed()
        or (await _detect_ollama_vision_model() is not None)
    )

    last_method = "none"
    for step in chain:
        try:
            result = await step()
        except Exception as e:
            logger.debug("OCR chain step %s raised: %s", step.__name__, e)
            continue
        if result and result.get("text") and len(result["text"].strip()) >= 5:
            # SUCCESS — mark which path served it
            if not local_backend_available and result.get("method") in ("ocrspace_free",):
                # Kick off auto-install so the NEXT image is fully local
                _trigger_auto_install()
                result["auto_installing"] = True
                result["note"] = (
                    "Read via free public OCR fallback. Auto-installing local "
                    "backend (easyocr) in the background — next image will be "
                    "fully offline and ~10x faster."
                )
            # ── RAG ingest: chunk + index every successful OCR extraction
            # This is what makes "what does the MKE invoice say about payment
            # terms" answerable — the raw OCR text becomes searchable
            try:
                from . import rag_store
                await rag_store.ingest_document(
                    text=result["text"],
                    source=f"ocr:{filename}",
                    source_type="ocr",
                    title=filename,
                    extra_metadata={
                        "ocr_method": result.get("method", "unknown"),
                        "ocr_confidence": result.get("confidence", 0),
                        "context": (context or "")[:200],
                    },
                )
            except Exception as e:
                logger.debug("RAG ingest from OCR failed: %s", e)
            return result
        if result and result.get("method"):
            last_method = result["method"]

    # Nothing in the chain produced text. Trigger auto-install + return note.
    if not local_backend_available:
        _trigger_auto_install()

    return {
        "text": "",
        "method": last_method,
        "confidence": 0,
        "tried": [s.__name__ for s in chain],
        "auto_installing": not local_backend_available,
        "note": (
            "All OCR methods returned no readable text on this image. "
            "If this seems wrong, the image may be blank or extremely low-res. "
            + ("Auto-installing local OCR (easyocr) in the background so future images are faster."
               if not local_backend_available else "")
        ),
    }


def _check_tesseract_installed() -> bool:
    """Quick check if pytesseract is importable."""
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


# ── OCR.space free public API ───────────────────────────────────────────────
# OCR.space provides a free no-key tier via the well-known "helloworld" public
# API key (documented at https://ocr.space/OCRAPI). It's intended exactly for
# this kind of use: low-volume image OCR without a setup step. ~25k requests
# per month per IP, supports JPG/PNG/PDF up to 1MB.
#
# We use this as a IMMEDIATE fallback when no local backend is available so
# ARIA can read your image RIGHT NOW while easyocr installs in the background.
# Once easyocr is installed, ocrspace is never called again.

OCRSPACE_API = "https://api.ocr.space/parse/image"
OCRSPACE_PUBLIC_KEY = "helloworld"  # public test key, free 25k/month/IP


async def _ocr_via_ocrspace(image_data: bytes, mime: str) -> Optional[dict]:
    """Free public OCR via OCR.space — no installation, no key, no setup.

    Uses the base64Image parameter which has a 10MB limit (vs the 1MB limit
    for multipart file uploads). If the image is bigger than 8MB we try to
    downscale it via Pillow first; if Pillow isn't installed and the image
    is huge, we crop to a reasonable resolution as a defensive fallback.
    """
    # Try to downscale large images so we stay under the OCR.space 10MB limit
    # AND make the OCR job faster/more accurate
    processed = _downscale_for_ocr(image_data, mime)
    if processed is None:
        logger.warning("OCR.space: image too large to process (%d bytes) and Pillow not available for downscaling", len(image_data))
        return None
    image_data, mime = processed

    if len(image_data) > 9 * 1024 * 1024:
        logger.warning("OCR.space: image still too large after downscale (%d bytes > 9MB)", len(image_data))
        return None

    try:
        b64 = base64.b64encode(image_data).decode("ascii")
        # OCR.space accepts base64Image as form data with a data: URL prefix
        data_uri = f"data:{mime};base64,{b64}"
        data = {
            "apikey": OCRSPACE_PUBLIC_KEY,
            "language": "eng",
            "isOverlayRequired": "false",
            "OCREngine": "2",  # newer engine, better on documents
            "scale": "true",
            "isTable": "false",
            "detectOrientation": "true",
            "base64Image": data_uri,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(OCRSPACE_API, data=data)
            if resp.status_code != 200:
                logger.warning("OCR.space returned HTTP %s: %s", resp.status_code, resp.text[:300])
                return None
            try:
                result = resp.json()
            except Exception as e:
                logger.warning("OCR.space returned non-JSON response: %s", e)
                return None

        if result.get("IsErroredOnProcessing"):
            err = result.get("ErrorMessage") or result.get("ErrorDetails") or "unknown"
            if isinstance(err, list):
                err = "; ".join(str(e) for e in err)
            logger.warning("OCR.space processing error: %s", err)
            return None

        parsed = result.get("ParsedResults") or []
        if not parsed:
            logger.warning("OCR.space returned no parsed results")
            return None

        text = (parsed[0].get("ParsedText") or "").strip()
        if not text or len(text) < 3:
            logger.warning("OCR.space returned empty text")
            return None

        logger.info("OCR.space SUCCESS: %d chars extracted", len(text))
        return {
            "text": text,
            "method": "ocrspace_free",
            "confidence": 0.78,
            "provider": "OCR.space (free public)",
        }
    except Exception as e:
        logger.warning("OCR.space request failed: %s", e)
        return None


def _downscale_for_ocr(image_data: bytes, mime: str) -> Optional[tuple[bytes, str]]:
    """Downscale large images so OCR backends can handle them.

    Returns (downscaled_bytes, mime) or None if downscaling failed AND
    the image is too large to send as-is. If the image is already small
    enough, returns the original bytes unchanged.

    Uses Pillow if available. If Pillow isn't installed and the image
    is small enough to send as-is, we just return the original.
    """
    MAX_BYTES = 8 * 1024 * 1024   # 8MB safety threshold (under OCR.space 10MB)
    MAX_DIM = 2500                # 2500px max dimension for OCR efficiency

    if len(image_data) <= MAX_BYTES:
        return image_data, mime  # already small enough

    try:
        from PIL import Image
        import io
    except ImportError:
        # Pillow not available — can't downscale. If image is too big, give up.
        logger.warning("Pillow not installed, can't downscale %d-byte image", len(image_data))
        return None

    try:
        img = Image.open(io.BytesIO(image_data))
        # Convert to RGB if needed (JPEG can't be RGBA)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        # Resize to fit within MAX_DIM box, preserving aspect ratio
        img.thumbnail((MAX_DIM, MAX_DIM), Image.LANCZOS)
        # Re-encode as JPEG with quality 85 — good for OCR, much smaller than original
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=85, optimize=True)
        new_bytes = out.getvalue()
        logger.info("Downscaled image: %d → %d bytes (%.0f%% reduction)",
                    len(image_data), len(new_bytes),
                    (1 - len(new_bytes) / len(image_data)) * 100)
        return new_bytes, "image/jpeg"
    except Exception as e:
        logger.warning("Image downscale failed: %s — sending original", e)
        # Pillow failed but the image is huge — try anyway in case OCR.space accepts it
        return image_data, mime


def _ext_for_mime(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }.get(mime, ".jpg")


# ── Background auto-install of local OCR ───────────────────────────────────
# When no local backend is installed, we kick off a `pip install easyocr Pillow
# pytesseract` in a background thread. This is a one-shot operation tracked by
# a Redis flag so we don't retry on every image. Subsequent images will be
# served fully locally once the install completes (~30-60s for easyocr alone,
# ~5min for the first image because PyTorch model weights also download).
#
# Disable with: ARIA_OCR_AUTO_INSTALL=0

import sys
import subprocess
import threading

_auto_install_started = False
_auto_install_lock = threading.Lock()


def _trigger_auto_install() -> bool:
    """PERMANENTLY DISABLED — easyocr was removed from the chain (incident
    2026-04-08) because cold-loading its 200MB ONNX model OOM-killed the
    fly.io worker on every first image. Tesseract is now baked into the
    Docker image as the only local backend, so there is nothing left to
    auto-install. Kept as a no-op so older callers don't break.
    """
    return False

    # ── unreachable: original implementation retained for reference only ──
    global _auto_install_started

    if (os.getenv("ARIA_OCR_AUTO_INSTALL", "1") or "1").lower() in ("0", "false", "no"):
        return False

    with _auto_install_lock:
        if _auto_install_started:
            return False
        _auto_install_started = True

    def _install_worker():
        try:
            logger.info("OCR auto-install: starting `pip install --user easyocr Pillow pytesseract` in background")
            # Try --user first (works without root). If that fails, try without.
            cmd_user = [
                sys.executable, "-m", "pip", "install",
                "--user", "--quiet", "--no-input",
                "easyocr", "Pillow", "pytesseract",
            ]
            cmd_global = [
                sys.executable, "-m", "pip", "install",
                "--quiet", "--no-input",
                "easyocr", "Pillow", "pytesseract",
            ]
            # NOTE: runs in a daemon thread (not the event loop) — blocking is acceptable
            result = subprocess.run(cmd_user, capture_output=True, timeout=120)
            if result.returncode != 0:
                # Maybe --user isn't allowed in this environment, try global
                logger.info("OCR auto-install: --user failed, trying global install")
                result = subprocess.run(cmd_global, capture_output=True, timeout=120)

            if result.returncode == 0:
                logger.info(
                    "OCR auto-install: SUCCESS — local backends will be available "
                    "on the next image. EasyOCR will download model weights "
                    "(~250MB) on first use."
                )
                # Clear the cached failure so the next call retries the import
                global _easyocr_init_failed
                _easyocr_init_failed = False
            else:
                stderr_preview = (result.stderr or b"").decode("utf-8", errors="replace")[:500]
                logger.warning(
                    "OCR auto-install: FAILED (exit %s). stderr: %s. "
                    "Manual install: pip install easyocr Pillow pytesseract",
                    result.returncode, stderr_preview,
                )
        except subprocess.TimeoutExpired:
            logger.warning("OCR auto-install: timed out after 15 minutes")
        except Exception as e:
            logger.warning("OCR auto-install: exception: %s", e)

    t = threading.Thread(target=_install_worker, name="aria-ocr-autoinstall", daemon=True)
    t.start()
    logger.info("OCR auto-install: background thread started (PID %s)", os.getpid())
    return True


def get_auto_install_status() -> dict:
    """Report on the auto-install state."""
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="ocr",
        summary="Get Auto Install Status",
        source_id="ocr:R-F996",
    )

    return {
        "started": _auto_install_started,
        "enabled": (os.getenv("ARIA_OCR_AUTO_INSTALL", "1") or "1").lower() not in ("0", "false", "no"),
        # Reports whether ANY local OCR backend is usable. Do NOT call
        # _get_easyocr_reader() — see comment in extract_text_from_image.
        "easyocr_available": _check_tesseract_installed(),
    }


_OCR_SYSTEM = "You are an OCR + intelligence-extraction system. You read images carefully and extract every legible character along with structured intelligence."

def _ocr_prompt(context: str) -> str:
    return f"""Extract every piece of information from this image. It is a defence / procurement / intelligence document or photograph.

Context: {context or 'Image shared via WhatsApp by an Arkmurus operator.'}

Return your output in this exact format:

=== TEXT ===
<every word of text you can see, preserving layout. Tables as pipe-separated rows. Forms field-by-field.>

=== ENTITIES ===
- People: <names + roles/ranks>
- Organisations: <ministries, companies, military units>
- Locations: <countries, cities, bases, addresses>
- Dates: <any dates visible>
- Money / quantities: <amounts, calibres, quantities>
- Reference numbers: <contract IDs, tender numbers, document numbers, serials>

=== SIGNATURES & STAMPS ===
<describe any signatures, official stamps, seals, letterheads>

=== KEY FACTS ===
<3-7 bullet points of the most important intelligence in this image>

If the image has no readable content at all, output exactly: NO_TEXT_FOUND
Do NOT add commentary, markdown headers, or apologies — only the structured blocks above."""


# Provider vision-capability ranking. Higher = better at vision OCR.
# DeepSeek and Mistral chat models do NOT do vision (will fail or hallucinate).
# Ollama vision is hit-and-miss depending on model (llava works, llama3.1:8b doesn't).
_VISION_RANK = {
    "anthropic": 100,   # Claude 3.5/4.x — best in class for document OCR
    "openai":     90,   # gpt-4o family
    "gemini":     85,   # gemini-2.5-flash/pro
    "openrouter": 70,   # depends on routed model — assume mid-tier
    "ollama":     30,   # only if a vision model is loaded (llava, etc.)
    "minimax":    20,
    "deepseek":   -1,   # NO VISION SUPPORT — never use for OCR
    "mistral":    -1,   # NO VISION SUPPORT
}


def _vision_rank(name: str) -> int:
    return _VISION_RANK.get((name or "").lower(), 0)


def _collect_inner_providers(llm) -> list:
    """Walk a wrapped LLMProvider to find all underlying concrete providers."""
    if not llm:
        return []
    found = []
    seen_ids = set()

    def _add(p):
        if p is None: return
        pid = id(p)
        if pid in seen_ids: return
        seen_ids.add(pid)
        found.append(p)

    # The top-level provider itself if it's concrete
    if hasattr(llm, "_api_key"):
        _add(llm)

    # HybridProvider — primary, secondary
    for attr in ("primary", "secondary", "vision_provider", "_vision"):
        inner = getattr(llm, attr, None)
        if inner is not None:
            _add(inner)
            # Recurse one level for nested wrappers
            for sub_attr in ("primary", "secondary"):
                sub = getattr(inner, sub_attr, None)
                if sub: _add(sub)

    # FallbackProvider — list of providers
    providers_list = getattr(llm, "providers", None) or getattr(llm, "_providers", None)
    if isinstance(providers_list, (list, tuple)):
        for p in providers_list:
            _add(p)

    return found


def _unwrap_provider(llm) -> Any:
    """Unwrap HybridProvider / FallbackProvider to find the BEST vision-capable provider.

    Previously this function returned the first provider with `_api_key` set, which
    in production usually meant DeepSeek (no vision support) → silent OCR failure.

    The fix: collect all concrete providers reachable from the wrapper, score them
    by vision capability (Anthropic=100 > OpenAI=90 > Gemini=85 > … > DeepSeek=-1),
    and return the highest-scoring one that has credentials.
    """
    if not llm:
        return None

    candidates = _collect_inner_providers(llm)
    if not candidates:
        return llm

    # Score each candidate by vision capability and credential availability
    scored = []
    for p in candidates:
        name = (getattr(p, "name", "") or "").lower()
        api_key = getattr(p, "_api_key", "") or ""
        rank = _vision_rank(name)
        if rank < 0:
            continue  # known no-vision provider
        if not api_key and name != "ollama":
            continue  # no credentials
        scored.append((rank, p, name))

    if not scored:
        return None  # no vision-capable provider available

    scored.sort(key=lambda x: -x[0])
    best = scored[0]
    logger.info("OCR unwrap: chose %s (vision rank %d) from %d candidates",
                best[2], best[0], len(candidates))
    return best[1]


def get_vision_config() -> dict:
    """Return the active vision configuration. Used by /vision-status endpoint."""
    dedicated_provider = (os.getenv("ARIA_VISION_PROVIDER", "") or "").strip().lower()
    dedicated_key = os.getenv("ARIA_VISION_API_KEY", "") or ""
    dedicated_model = (os.getenv("ARIA_VISION_MODEL", "") or "").strip()
    return {
        "dedicated_provider": dedicated_provider or None,
        "dedicated_provider_configured": bool(dedicated_provider and dedicated_key),
        "dedicated_model": dedicated_model or None,
        "key_configured": bool(dedicated_key),
        "fallback_to_llm_chain": not bool(dedicated_provider and dedicated_key),
        "tesseract_available": _check_tesseract(),
    }


def _check_tesseract() -> bool:
    """Best-effort check whether Tesseract is importable."""
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


def _resolve_vision_provider(llm) -> tuple[str, str, str]:
    """Decide which vision provider to use. Returns (provider_name, api_key, model).

    Priority:
        1. ARIA_VISION_PROVIDER + ARIA_VISION_API_KEY env vars (dedicated config)
        2. Unwrap llm chain and pick the best vision-capable provider
        3. Return ("", "", "") to signal no vision provider available
    """
    # Path 1: dedicated env-var config (RECOMMENDED for DeepSeek users)
    dedicated_provider = (os.getenv("ARIA_VISION_PROVIDER", "") or "").strip().lower()
    dedicated_key = os.getenv("ARIA_VISION_API_KEY", "") or ""
    dedicated_model = (os.getenv("ARIA_VISION_MODEL", "") or "").strip()

    if dedicated_provider and dedicated_key:
        # Apply sensible default models per provider
        if not dedicated_model:
            dedicated_model = {
                "anthropic": "claude-sonnet-4-6",
                "openai": "gpt-4o",
                "gemini": "gemini-2.5-flash",
                "openrouter": "google/gemini-flash-1.5:free",
            }.get(dedicated_provider, "")
        logger.info("OCR: using dedicated vision provider %s (model=%s)",
                    dedicated_provider, dedicated_model or "default")
        return dedicated_provider, dedicated_key, dedicated_model

    # Path 2: unwrap the main LLM chain
    inner = _unwrap_provider(llm)
    if inner is None:
        return "", "", ""
    return (
        (getattr(inner, "name", "") or "").lower(),
        getattr(inner, "_api_key", "") or "",
        getattr(inner, "_model", "") or "",
    )


async def _ocr_via_llm(image_data: bytes, mime: str, context: str, llm) -> Optional[dict]:
    """Use the configured vision API to extract text + entities from an image.

    Resolution order:
      1. Dedicated vision provider via ARIA_VISION_PROVIDER + ARIA_VISION_API_KEY.
         RECOMMENDED for DeepSeek users — DeepSeek API has no vision support.
      2. Unwrap the main LLM provider chain and pick the best vision-capable one.

    The LLMProvider.complete() interface only handles text, so this function
    bypasses it and calls the underlying vendor APIs directly with the correct
    image content blocks.
    """
    import time as _t
    _vision_t0 = _t.time()
    try:
        b64 = base64.b64encode(image_data).decode("ascii")
        prompt = _ocr_prompt(context)

        provider_name, api_key, model = _resolve_vision_provider(llm)

        if not provider_name or (not api_key and provider_name != "ollama"):
            logger.warning(
                "OCR: no vision provider configured. DeepSeek does not support vision — "
                "set ARIA_VISION_PROVIDER + ARIA_VISION_API_KEY to a vision-capable provider "
                "(gemini/anthropic/openai/openrouter). See aria_service/intel/ocr.py docstring."
            )
            return None

        # M10: observability — cloud vision is the expensive tier. Log at
        # WARNING so operators see when OCR escalates off the local path.
        # The Ollama tier is free so we stay at INFO for that one.
        _log_level = logger.info if provider_name == "ollama" else logger.warning
        _log_level(
            "[ocr] cloud vision tier engaged — provider=%s model=%s bytes=%d",
            provider_name, model or "(default)", len(image_data),
        )
        # Treat OpenRouter like the OpenAI-compat branch
        if provider_name == "openrouter":
            provider_name = "openrouter"  # explicit — handled below

        text: str = ""

        # ── Anthropic Claude (vision-capable) ──────────────────────────────
        if provider_name == "anthropic" and api_key:
            payload = {
                "model": model or "claude-sonnet-4-6",
                "max_tokens": 4096,  # R-F1311: bumped from 3000 — complex images (tables, org charts) need more output budget
                "system": _OCR_SYSTEM,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                        {"type": "text", "text": prompt},
                    ],
                }],
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    json=payload,
                )
                if resp.status_code != 200:
                    logger.warning("Anthropic vision OCR failed: %s %s", resp.status_code, resp.text[:200])
                else:
                    data = resp.json()
                    for block in data.get("content", []):
                        if block.get("type") == "text":
                            text += block.get("text", "")

        # ── OpenAI-compatible (OpenAI, OpenRouter, Ollama vision models) ───
        # NOTE: DeepSeek + Mistral are EXCLUDED — their public APIs do not
        # accept image content blocks and would silently 400. Users who want
        # vision while running DeepSeek as their main LLM must set
        # ARIA_VISION_PROVIDER=gemini|anthropic|openai|openrouter explicitly.
        elif provider_name in ("openai", "openrouter", "ollama") and (api_key or provider_name == "ollama"):
            base_url = {
                "openai": "https://api.openai.com/v1",
                "openrouter": "https://openrouter.ai/api/v1",
            }.get(provider_name, "")
            if not base_url and provider_name == "ollama":
                ollama_base = (os.getenv("OLLAMA_URL", "http://localhost:11434") or "").rstrip("/")
                base_url = f"{ollama_base}/v1"
            if base_url:
                payload = {
                    "model": model or "gpt-4o",
                    "max_tokens": 4096,  # R-F1311: bumped from 3000
                    "messages": [
                        {"role": "system", "content": _OCR_SYSTEM},
                        {"role": "user", "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        ]},
                    ],
                }
                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
                    if resp.status_code != 200:
                        logger.warning("%s vision OCR failed: %s %s", provider_name, resp.status_code, resp.text[:200])
                    else:
                        data = resp.json()
                        choices = data.get("choices", [])
                        if choices:
                            text = (choices[0].get("message", {}) or {}).get("content", "") or ""

        # ── Google Gemini ──────────────────────────────────────────────────
        elif provider_name == "gemini" and api_key:
            gmodel = model or "gemini-2.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{gmodel}:generateContent?key={api_key}"
            payload = {
                "contents": [{
                    "parts": [
                        {"text": _OCR_SYSTEM + "\n\n" + prompt},
                        {"inline_data": {"mime_type": mime, "data": b64}},
                    ],
                }],
                "generationConfig": {"maxOutputTokens": 3000},
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    logger.warning("Gemini vision OCR failed: %s %s", resp.status_code, resp.text[:200])
                else:
                    data = resp.json()
                    for cand in data.get("candidates", []):
                        for part in (cand.get("content", {}) or {}).get("parts", []):
                            text += part.get("text", "") or ""

        text = (text or "").strip()
        if text and "NO_TEXT_FOUND" not in text:
            # M10: record vision call into cost_tracker so /cost can surface
            # the vision tier, and so user-level caps (future) can see it.
            # Token counts are best-effort estimates — vision APIs don't
            # consistently return usage blocks.
            try:
                from . import cost_tracker as _ct
                _approx_input_tokens = max(500, (len(image_data) // 750) + 200)
                _approx_output_tokens = max(50, len(text) // 4)
                await _ct.record_call(
                    model=f"{provider_name}:{model or 'vision'}",
                    input_tokens=_approx_input_tokens,
                    output_tokens=_approx_output_tokens,
                    latency_ms=int((_t.time() - _vision_t0) * 1000),
                    feature_name="ocr_vision",
                    provider_name=provider_name,
                    success=True,
                )
            except Exception as _ct_err:
                logger.debug("vision cost record failed (non-fatal): %s", _ct_err)
            return {"text": text, "method": f"vision:{provider_name or 'unknown'}", "confidence": 0.85}

    except Exception as e:
        logger.warning("LLM vision OCR failed: %s", e)

    return None


# ── Pre-warm hook ──────────────────────────────────────────────────────────
# Called from main.py lifespan in a background task so OCR backends are loaded
# deterministically at startup instead of cold-loading mid-request and racing
# against sentence-transformers / chromadb / inflight LLM calls for memory.
# Past incident: a 200MB EasyOCR cold-load on the first user image OOM-killed
# the fly.io worker. Pre-warming makes the memory cost predictable.
async def prewarm_ocr() -> dict:
    """Eager-load OCR backends so the first user image doesn't pay the cold-start
    cost (and doesn't OOM mid-request). Safe to call from a background task —
    runs the model load in an executor and never raises.

    Returns a status dict so the caller can log what got warmed.
    """
    import asyncio as _aio
    status = {"tesseract": False, "easyocr": False}
    # Tesseract: cheap probe — just import + version check
    try:
        loop = _aio.get_running_loop()
        def _probe_tesseract():
            import pytesseract
            from PIL import Image  # noqa: F401
            return str(pytesseract.get_tesseract_version())
        version = await loop.run_in_executor(None, _probe_tesseract)
        status["tesseract"] = True
        status["tesseract_version"] = version
        logger.info("OCR pre-warm: tesseract %s ready", version)
    except Exception as e:
        logger.info("OCR pre-warm: tesseract not available (%s)", e)

    # EasyOCR pre-warm is permanently disabled — EasyOCR has been removed
    # from the OCR chain entirely (incident 2026-04-08). Tesseract is now
    # the only local backend. Pre-warming it is unnecessary because the
    # python binding loads in milliseconds and the binary is in the image.
    logger.info("OCR pre-warm: EasyOCR is removed from the chain — skipping")

    return status


# ── Local OCR backend #1: EasyOCR (pure Python, no system binaries) ────────
# EasyOCR loads ONNX/PyTorch models lazily and caches them under ~/.EasyOCR.
# We initialise a single shared Reader on first use so subsequent calls are fast.
_easyocr_reader = None
_easyocr_init_failed = False

def _get_easyocr_reader():
    """Lazy-load a shared EasyOCR Reader. Returns None if EasyOCR isn't installed.

    MEMORY: each language adds ~150-200MB of model weights to RAM. The default
    is English-only so a small fly.io machine (1GB RAM) can comfortably run
    EasyOCR + sentence-transformers + the FastAPI server without OOM.
    Override with ARIA_OCR_LANGS=en,pt,fr,es to add more languages if you
    have ≥2GB RAM.
    """
    global _easyocr_reader, _easyocr_init_failed
    if _easyocr_reader is not None:
        return _easyocr_reader
    if _easyocr_init_failed:
        return None
    try:
        import easyocr  # type: ignore
        # Default to English-only to keep memory under ~400MB total.
        # Add Portuguese/French/Spanish/Arabic via env var only if you have
        # the headroom (each language ~+200MB).
        langs_env = (os.getenv("ARIA_OCR_LANGS", "en") or "en").strip()
        langs = [l.strip() for l in langs_env.split(",") if l.strip()] or ["en"]
        logger.info("EasyOCR loading model weights for languages: %s (this takes ~30s on first call)", langs)
        # Force CPU — most production hosts won't have CUDA, and the speed hit is
        # acceptable for the volume of WhatsApp images we expect.
        _easyocr_reader = easyocr.Reader(langs, gpu=False, verbose=False)
        logger.info("EasyOCR ready with languages: %s", langs)
        return _easyocr_reader
    except ImportError:
        _easyocr_init_failed = True
        logger.info("EasyOCR not installed — skipping (run `pip install easyocr` to enable)")
        return None
    except MemoryError as e:
        _easyocr_init_failed = True
        logger.warning("EasyOCR init failed (out of memory): %s — bump fly.io machine size or reduce ARIA_OCR_LANGS", e)
        return None
    except Exception as e:
        _easyocr_init_failed = True
        logger.warning("EasyOCR init failed: %s — skipping", e)
        return None


async def _ocr_via_easyocr(image_data: bytes) -> Optional[dict]:
    """Run EasyOCR on the image. Returns extracted text + confidence.

    EasyOCR returns a list of (bbox, text, confidence) tuples. We sort them
    top-to-bottom, left-to-right, join with newlines for line breaks, and
    compute the average confidence.
    """
    reader = _get_easyocr_reader()
    if reader is None:
        return None

    try:
        import asyncio
        # Run the (CPU-bound) inference in an executor so we don't block the loop
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: reader.readtext(image_data, detail=1, paragraph=False),
        )
    except Exception as e:
        logger.warning("EasyOCR readtext failed: %s", e)
        return None

    if not results:
        return None

    # Sort top-to-bottom, then left-to-right by bounding-box centre
    def _centre(bbox):
        ys = [p[1] for p in bbox]
        xs = [p[0] for p in bbox]
        return (sum(ys) / len(ys), sum(xs) / len(xs))

    sorted_results = sorted(results, key=lambda r: _centre(r[0]))

    # Group items into lines based on Y-centre proximity
    lines: list[list[str]] = []
    current_line: list[tuple[float, str]] = []
    last_y: float | None = None
    line_threshold = 15  # pixels

    for bbox, txt, conf in sorted_results:
        y, x = _centre(bbox)
        if last_y is None or abs(y - last_y) <= line_threshold:
            current_line.append((x, txt))
            last_y = y if last_y is None else (last_y + y) / 2
        else:
            current_line.sort(key=lambda t: t[0])
            lines.append([t[1] for t in current_line])
            current_line = [(x, txt)]
            last_y = y
    if current_line:
        current_line.sort(key=lambda t: t[0])
        lines.append([t[1] for t in current_line])

    text = "\n".join(" ".join(line) for line in lines).strip()
    if not text or len(text) < 5:
        return None

    avg_conf = sum(r[2] for r in sorted_results) / max(len(sorted_results), 1)
    return {
        "text": text,
        "method": "easyocr",
        "confidence": round(float(avg_conf), 3),
        "items_detected": len(sorted_results),
    }


# ── Tesseract output cleaning helpers ──────────────────────────────────────
# Tesseract on business cards / receipts / images-with-logos returns lots of
# junk lines from decorative graphics: things like "Hh i", "Lip", "Hp |i",
# "ES", "À". We strip those after extraction so the user sees clean text.
_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]{3,}")  # 3+ letter/digit run = a real word
_ALPHA_RE = re.compile(r"[A-Za-zÀ-ÿ]")


def _useful_word_count(text: str) -> int:
    """Number of word-like tokens (3+ letters/digits) in the text. Used to
    score competing tesseract PSM outputs — favours real content over long
    strings of OCR noise from decorative graphics."""
    return len(_WORD_RE.findall(text or ""))


def _clean_tesseract_output(text: str) -> str:
    """Drop lines that look like OCR garbage from logos / ornaments.

    A line is kept if it has at least one ≥3-letter word OR contains a
    recognisable contact-info marker (digit run for phone, '@' for email,
    '.' between letters for URL, ':' for label). Otherwise it's noise.
    Also collapses runs of >2 blank lines down to one.
    """
    if not text:
        return ""
    kept: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        # Quick keep conditions for contact-info markers
        has_word = bool(_WORD_RE.search(stripped))
        has_phone = bool(re.search(r"\d[\d\s\-+().]{4,}", stripped))
        has_email = "@" in stripped and "." in stripped
        has_url = bool(re.search(r"[A-Za-z]{2,}\.[A-Za-z]{2,}", stripped))
        has_label = ":" in stripped and bool(_ALPHA_RE.search(stripped))
        if has_word or has_phone or has_email or has_url or has_label:
            # Also check that the line isn't dominated by punctuation/symbols
            alpha_count = len(_ALPHA_RE.findall(stripped))
            if alpha_count >= 2 or has_phone or has_email or has_url:
                kept.append(line)
                continue
        # Drop the line — it's almost certainly logo/ornament noise
    # Collapse runs of blank lines
    out_lines: list[str] = []
    blank_run = 0
    for line in kept:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 1:
                out_lines.append("")
        else:
            blank_run = 0
            out_lines.append(line)
    return "\n".join(out_lines).strip()


# ── Local OCR backend #2: Tesseract with PIL preprocessing ─────────────────
async def _ocr_via_tesseract(image_data: bytes) -> Optional[dict]:
    """Use Tesseract OCR with image preprocessing for better accuracy.

    Improvements over the previous version:
      - Auto-grayscale conversion
      - Auto-contrast (PIL ImageOps.autocontrast)
      - Upscale tiny images to at least 1000px wide
      - Try multiple PSM (page segmentation mode) values and keep the best
      - Multilingual support: English + Portuguese + French + Spanish
    """
    try:
        import pytesseract
        from PIL import Image, ImageOps
        import io
    except ImportError:
        return None  # Tesseract not installed

    try:
        img = Image.open(io.BytesIO(image_data))

        # Convert to RGB then grayscale for consistent OCR
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img_gray = img.convert("L")

        # Upscale tiny images — Tesseract struggles below ~1000px wide
        if img_gray.width < 1000:
            scale = 1000 / max(img_gray.width, 1)
            new_size = (int(img_gray.width * scale), int(img_gray.height * scale))
            img_gray = img_gray.resize(new_size, Image.LANCZOS)

        # Auto-contrast to handle dim photos
        img_gray = ImageOps.autocontrast(img_gray)

        # Try several page segmentation modes — Tesseract is sensitive to
        # layout. PSM 3 = fully automatic, PSM 6 = uniform block, PSM 4 =
        # single column, PSM 11 = sparse text (best for business cards,
        # receipts, anything with logos and scattered text). We score each
        # candidate by useful-word count, not raw length, so a long string
        # of garbage from a decorative logo doesn't beat clean extracted
        # contact info.
        candidates: list[str] = []
        for psm in (3, 6, 4, 11):
            try:
                cfg = f"--oem 1 --psm {psm}"
                text = pytesseract.image_to_string(img_gray, lang="eng+por+fra+spa+ron", config=cfg)
                text = (text or "").strip()
                if text and len(text) > 5:
                    candidates.append(text)
            except pytesseract.TesseractError:
                # Likely a missing language pack — fall back to English only
                try:
                    text = pytesseract.image_to_string(img_gray, lang="eng", config=f"--oem 1 --psm {psm}")
                    text = (text or "").strip()
                    if text and len(text) > 5:
                        candidates.append(text)
                except Exception:
                    continue
            except Exception:
                continue

        if not candidates:
            return None

        cleaned = [_clean_tesseract_output(c) for c in candidates]
        cleaned = [c for c in cleaned if c]
        if not cleaned:
            # Even after cleaning every candidate became empty — return the
            # raw longest result rather than failing the chain entirely.
            return {
                "text": max(candidates, key=len),
                "method": "tesseract",
                "confidence": 0.55,
                "psm_variants_tried": len(candidates),
                "note": "all PSM variants returned mostly junk after cleaning",
            }

        best = max(cleaned, key=_useful_word_count)
        return {
            "text": best,
            "method": "tesseract",
            "confidence": 0.75,  # static — Tesseract doesn't expose per-word confidence here
            "psm_variants_tried": len(candidates),
        }
    except Exception as e:
        logger.debug("Tesseract OCR failed: %s", e)
        return None


# ── Local OCR backend #3: Ollama vision model auto-detection ───────────────
# If Ollama is running locally with a vision model loaded (llava, bakllava,
# moondream, minicpm-v), we can use it for richer image understanding —
# describing diagrams, reading handwriting, identifying objects in photos.

_ollama_vision_model: str | None = None
_ollama_checked: float = 0
_OLLAMA_VISION_MODELS = [
    # Ordered by quality for document OCR
    "minicpm-v:latest", "minicpm-v",
    "llava:13b", "llava:latest", "llava",
    "bakllava:latest", "bakllava",
    "moondream:latest", "moondream",
    "llama3.2-vision:latest", "llama3.2-vision",
]


async def _detect_ollama_vision_model() -> str | None:
    """Check if Ollama is running locally and find a vision-capable model.

    Cached for 5 minutes to avoid hammering the Ollama daemon.
    """
    global _ollama_vision_model, _ollama_checked
    import time
    now = time.time()
    if _ollama_vision_model is not None and (now - _ollama_checked) < 300:
        return _ollama_vision_model
    if _ollama_checked > 0 and (now - _ollama_checked) < 60 and _ollama_vision_model is None:
        return None  # recently failed, don't retry yet

    ollama_url = (os.getenv("OLLAMA_URL", "http://localhost:11434") or "").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code != 200:
                _ollama_checked = now
                return None
            data = resp.json()
            installed = {m.get("name", "") for m in data.get("models", [])}
            for candidate in _OLLAMA_VISION_MODELS:
                if candidate in installed:
                    _ollama_vision_model = candidate
                    _ollama_checked = now
                    logger.info("Ollama vision model detected: %s", candidate)
                    return candidate
    except Exception as e:
        logger.debug("Ollama detection failed: %s", e)

    _ollama_checked = now
    _ollama_vision_model = None
    return None


async def _ocr_via_ollama_vision(image_data: bytes, mime: str, context: str) -> Optional[dict]:
    """Use a locally-installed Ollama vision model for image understanding.

    This is the highest-fidelity LOCAL option — it can describe diagrams,
    read handwriting, identify weapons platforms in photos, etc. Requires the
    user to have Ollama running with one of the vision models pulled
    (e.g. `ollama pull minicpm-v` or `ollama pull llava`).
    """
    model = await _detect_ollama_vision_model()
    if not model:
        return None

    ollama_url = (os.getenv("OLLAMA_URL", "http://localhost:11434") or "").rstrip("/")
    b64 = base64.b64encode(image_data).decode("ascii")
    prompt = _ocr_prompt(context)

    payload = {
        "model": model,
        "prompt": prompt,
        "system": _OCR_SYSTEM,
        "images": [b64],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 2000},
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{ollama_url}/api/generate", json=payload)
            if resp.status_code != 200:
                logger.warning("Ollama vision call failed: %s %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            text = (data.get("response") or "").strip()
            if not text or "NO_TEXT_FOUND" in text:
                return None
            return {
                "text": text,
                "method": f"ollama:{model}",
                "confidence": 0.80,
            }
    except Exception as e:
        logger.warning("Ollama vision OCR failed: %s", e)
        return None


def _detect_mime(data: bytes, filename: str = "") -> str:
    """Detect image MIME type from magic bytes or filename."""
    if data[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return "image/gif"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return "image/webp"

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "gif": "image/gif",
        "webp": "image/webp", "bmp": "image/bmp",
    }.get(ext, "image/jpeg")

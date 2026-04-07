"""
ARIA OCR — Extract text from images.

Uses LLM vision (primary) or Tesseract (fallback) to read:
- Screenshots of procurement documents
- Scanned PDFs shared via WhatsApp
- Photos of contracts, business cards, tenders
- Images from email attachments

The extracted text is fed to ARIA's knowledge base and neural memory.

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
    """Extract text from an image. LOCAL-FIRST pipeline so ARIA is independent.

    Pipeline order (each step is tried in turn; first one with usable text wins):

      1. EasyOCR — pure-Python, zero system binaries needed (just `pip install easyocr`).
         Excellent quality on printed text, supports 80+ languages including Portuguese,
         French, Spanish, Arabic. ~250MB on first download for the model weights but
         then runs entirely offline.

      2. Tesseract — classic OCR with PIL preprocessing (grayscale + autocontrast).
         Requires the Tesseract binary on the host plus `pip install pytesseract Pillow`.
         Very fast, low memory, decent quality on clean printed text.

      3. Ollama vision model — if Ollama is running locally with a vision model loaded
         (llava, bakllava, moondream, minicpm-v), use it. This handles richer
         "describe the image" use cases that pure OCR can't (handwritten notes,
         diagrams, photos of physical objects).

      4. Cloud LLM vision (Anthropic / OpenAI / Gemini / OpenRouter) — only used if
         every local option failed AND a vision provider is configured. Set
         ARIA_VISION_PROVIDER + ARIA_VISION_API_KEY to enable.

    Set ARIA_OCR_PREFER_CLOUD=1 to invert the order (try cloud first). Default is
    LOCAL-first because the user's stated goal is for ARIA to be self-sufficient.

    Returns:
        {"text": str, "method": str, "confidence": float}
        method ∈ {"easyocr", "tesseract", "ollama:<model>", "vision:<provider>", "none"}
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

    async def _try_cloud():
        if not llm or not hasattr(llm, "complete"):
            return None
        return await _ocr_via_llm(image_data, mime, context, llm)

    chain = (
        [_try_cloud, _try_easyocr, _try_tesseract, _try_ollama_vision]
        if prefer_cloud
        else [_try_easyocr, _try_tesseract, _try_ollama_vision, _try_cloud]
    )

    last_method = "none"
    for step in chain:
        try:
            result = await step()
        except Exception as e:
            logger.debug("OCR chain step %s raised: %s", step.__name__, e)
            continue
        if result and result.get("text") and len(result["text"].strip()) >= 5:
            return result
        if result and result.get("method"):
            last_method = result["method"]

    return {
        "text": "",
        "method": last_method,
        "confidence": 0,
        "tried": [s.__name__ for s in chain],
        "note": (
            "All OCR methods returned no readable text. Install easyocr "
            "(`pip install easyocr`) or tesseract for local-only extraction, "
            "or set ARIA_VISION_PROVIDER + ARIA_VISION_API_KEY for cloud vision."
        ),
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
        # Treat OpenRouter like the OpenAI-compat branch
        if provider_name == "openrouter":
            provider_name = "openrouter"  # explicit — handled below

        text: str = ""

        # ── Anthropic Claude (vision-capable) ──────────────────────────────
        if provider_name == "anthropic" and api_key:
            payload = {
                "model": model or "claude-sonnet-4-6",
                "max_tokens": 3000,
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
                    "max_tokens": 3000,
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
            return {"text": text, "method": f"vision:{provider_name or 'unknown'}", "confidence": 0.85}

    except Exception as e:
        logger.warning("LLM vision OCR failed: %s", e)

    return None


# ── Local OCR backend #1: EasyOCR (pure Python, no system binaries) ────────
# EasyOCR loads ONNX/PyTorch models lazily and caches them under ~/.EasyOCR.
# We initialise a single shared Reader on first use so subsequent calls are fast.
_easyocr_reader = None
_easyocr_init_failed = False

def _get_easyocr_reader():
    """Lazy-load a shared EasyOCR Reader. Returns None if EasyOCR isn't installed."""
    global _easyocr_reader, _easyocr_init_failed
    if _easyocr_reader is not None:
        return _easyocr_reader
    if _easyocr_init_failed:
        return None
    try:
        import easyocr  # type: ignore
        # Load the languages most relevant to ARIA's defence/intelligence domain.
        # English first (always), Portuguese for Lusophone Africa, French for francophone
        # Africa, Spanish for LatAm, plus Arabic for MENA. EasyOCR supports concurrent
        # languages but Arabic uses a different script model so we keep it separate.
        langs = ["en", "pt", "fr", "es"]
        # Force CPU — most production hosts won't have CUDA, and the speed hit is
        # acceptable for the volume of WhatsApp images we expect.
        _easyocr_reader = easyocr.Reader(langs, gpu=False, verbose=False)
        logger.info("EasyOCR initialised with languages: %s", langs)
        return _easyocr_reader
    except ImportError:
        _easyocr_init_failed = True
        logger.info("EasyOCR not installed — skipping (run `pip install easyocr` to enable)")
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

        # Try a few page segmentation modes — Tesseract is sensitive to layout
        # PSM 3 = fully automatic, PSM 6 = assume uniform block of text,
        # PSM 4 = assume single column of text. The best one wins.
        candidates = []
        for psm in (3, 6, 4):
            try:
                cfg = f"--oem 1 --psm {psm}"
                text = pytesseract.image_to_string(img_gray, lang="eng+por+fra+spa", config=cfg)
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

        # Keep the longest result — typically the most complete extraction
        best = max(candidates, key=len)
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

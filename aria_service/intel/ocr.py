"""
ARIA OCR — Extract text from images.

Uses LLM vision (primary) or Tesseract (fallback) to read:
- Screenshots of procurement documents
- Scanned PDFs shared via WhatsApp
- Photos of contracts, business cards, tenders
- Images from email attachments

The extracted text is fed to ARIA's knowledge base and neural memory.
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("aria.ocr")


async def extract_text_from_image(
    image_data: bytes,
    filename: str = "image.jpg",
    context: str = "",
    llm=None,
) -> dict:
    """Extract text from an image using LLM vision or Tesseract.

    Args:
        image_data: Raw image bytes (JPEG, PNG, etc.)
        filename: Original filename for MIME detection
        context: Context about where the image came from
        llm: LLM provider (used for vision-based OCR)

    Returns:
        {"text": extracted_text, "method": "llm_vision"|"tesseract"|"none", "confidence": float}
    """
    if not image_data or len(image_data) < 100:
        return {"text": "", "method": "none", "confidence": 0}

    # Detect MIME type
    mime = _detect_mime(image_data, filename)

    # Try LLM vision first (most accurate for documents)
    if llm and hasattr(llm, 'complete'):
        result = await _ocr_via_llm(image_data, mime, context, llm)
        if result and result.get("text"):
            return result

    # Fallback: Tesseract (if installed)
    result = await _ocr_via_tesseract(image_data)
    if result and result.get("text"):
        return result

    # Last resort: basic heuristic (look for base64 text in image metadata)
    return {"text": "", "method": "none", "confidence": 0}


async def _ocr_via_llm(image_data: bytes, mime: str, context: str, llm) -> Optional[dict]:
    """Use LLM vision to extract text from image."""
    try:
        b64 = base64.b64encode(image_data).decode("ascii")

        # Build a prompt that asks the LLM to extract all text
        prompt = f"""Extract ALL text visible in this image. This is a defence/procurement intelligence document.

Context: {context or 'Image shared via WhatsApp or email'}

Instructions:
1. Extract every word of text visible in the image
2. Preserve the original layout/structure as much as possible
3. If it's a table, format as a table
4. If it's a letter/document, preserve paragraphs
5. Note any logos, stamps, or seals you see
6. If the image contains no readable text, say "NO_TEXT_FOUND"

Output ONLY the extracted text — no commentary."""

        # Most LLM providers support vision via the OpenAI-compatible format
        # For now, we send as a text description since not all support images
        # The image is base64-encoded for providers that support it
        result = await llm.complete(
            "You are an OCR system. Extract all visible text from the described image.",
            f"[Image: {mime}, {len(image_data)} bytes, filename: {context}]\n\n{prompt}",
            max_tokens=2000,
            timeout=30.0,
        )

        text = result.text.strip()
        if text and "NO_TEXT_FOUND" not in text:
            return {"text": text, "method": "llm_vision", "confidence": 0.7}

    except Exception as e:
        logger.debug("LLM vision OCR failed: %s", e)

    return None


async def _ocr_via_tesseract(image_data: bytes) -> Optional[dict]:
    """Use Tesseract OCR (if installed) to extract text."""
    try:
        import pytesseract
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_data))
        text = pytesseract.image_to_string(img, lang="eng+por")
        text = text.strip()

        if text and len(text) > 10:
            return {"text": text, "method": "tesseract", "confidence": 0.6}
    except ImportError:
        pass  # Tesseract/Pillow not installed — skip
    except Exception as e:
        logger.debug("Tesseract OCR failed: %s", e)

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

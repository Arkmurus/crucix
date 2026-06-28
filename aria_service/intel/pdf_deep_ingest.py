"""Deep PDF ingest — per-page extraction + image OCR + RAG chunking.

The old flat extractor (one big 200-500k text blob) truncated long PDFs
and ignored embedded images entirely. This module splits every PDF
into per-page chunks, extracts and OCRs embedded images, and ingests
each unit as a separate RAG chunk with page_number + image_index
metadata so retrieval can surface the exact page + figure that
contains a given fact.

LLM-dependency profile:
  - Text extraction: PyMuPDF (fitz) — pure Python, zero LLM
  - Image OCR chain: Tesseract → EasyOCR → cloud vision (via existing
    aria_service.intel.ocr). Local engines tried first, cloud last.
  - RAG ingestion: sentence-transformers embedding (local) via existing
    rag_store.add_chunk. Zero external API needed for retrieval.

Operator outcome: ARIA can now read scanned PDFs, forms with both text
and image content, and multi-hundred-page reports — all without Claude
or paid vision APIs.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any
from .wire import fail_wire  # R-F1789 §21 brain-wiring
from .engine_wiring import wire_success, wire_failure  # R-F2085 §21a explicit success/failure wiring

logger = logging.getLogger("aria.intel.pdf_deep_ingest")


_MIN_PAGE_TEXT_CHARS    = 40        # below this, we consider the page "image-only"
_MAX_IMAGES_PER_PAGE    = 4         # cap to avoid OCR-storm on graphics-heavy PDFs
_MAX_IMAGES_PER_PDF     = 20
_MIN_IMAGE_AREA_PIXELS  = 40_000    # skip tiny icons / bullets
_MAX_OCR_TIMEOUT_SEC    = 20.0


@fail_wire(module="pdf_deep_ingest", gap_type="file_parse")
async def ingest_pdf_multi_page(
    pdf_bytes: bytes,
    filename: str,
    source_context: str = "",
    ingest_images: bool = True,
) -> dict[str, Any]:
    """Split a PDF into per-page chunks + embedded-image OCR chunks,
    ingest each into RAG, return a summary.

    Returns:
      {
        "filename":        str,
        "total_pages":     int,
        "text_pages":      int,
        "image_pages":     int,   # pages that needed OCR for any content
        "images_ocrd":     int,
        "chunks_ingested": int,
        "total_chars":     int,
        "errors":          [str, ...],
      }
    """
    out: dict[str, Any] = {
        "filename": filename,
        "total_pages": 0,
        "text_pages": 0,
        "image_pages": 0,
        "images_ocrd": 0,
        "chunks_ingested": 0,
        "total_chars": 0,
        "errors": [],
    }

    try:
        import fitz  # PyMuPDF
    except ImportError:
        out["errors"].append("fitz (PyMuPDF) not available — cannot ingest PDF")
        logger.warning("fitz not installed — pdf_deep_ingest cannot run")
        wire_failure(module="pdf_deep_ingest",
                     detail="fitz (PyMuPDF) not available — cannot ingest PDF",
                     gap_type="file_parse", source="pdf_deep_ingest")
        return out

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        out["errors"].append(f"PDF open failed: {str(exc)[:200]}")
        wire_failure(module="pdf_deep_ingest",
                     detail=f"PDF open failed for {filename}: {str(exc)[:160]}",
                     gap_type="file_parse", source="pdf_deep_ingest")
        return out

    out["total_pages"] = doc.page_count
    pdf_hash = hashlib.sha1(pdf_bytes[:1024] + pdf_bytes[-1024:]).hexdigest()[:12]

    for page_idx in range(doc.page_count):
        # R-F2085 — yield the event loop between pages. The per-page work below
        # (fitz get_text / Pixmap / tobytes, and Tesseract OCR when images are
        # enabled) is synchronous CPU that runs ON the loop thread; without a
        # yield a multi-page PDF starves every other task (incl. the /health/live
        # probe the WA listener uses to decide the brain is "reachable"). A
        # sleep(0) lets pending handlers run between pages.
        await asyncio.sleep(0)
        try:
            page = doc[page_idx]
        except Exception as exc:
            out["errors"].append(f"page {page_idx}: open failed ({str(exc)[:80]})")
            continue

        # 1) Extract text
        try:
            page_text = page.get_text().strip()
        except Exception:
            page_text = ""

        has_text = len(page_text) >= _MIN_PAGE_TEXT_CHARS
        if has_text:
            await _ingest_chunk(
                text=page_text,
                source=f"{filename}#page_{page_idx + 1}",
                metadata={
                    "pdf_filename":   filename,
                    "pdf_hash":       pdf_hash,
                    "page_number":    page_idx + 1,
                    "total_pages":    doc.page_count,
                    "content_type":   "pdf_page_text",
                    "source_context": source_context,
                    "ingested_at":    datetime.now(timezone.utc).isoformat(),
                },
            )
            out["text_pages"] += 1
            out["chunks_ingested"] += 1
            out["total_chars"] += len(page_text)

        # 2) Extract + OCR embedded images
        if not ingest_images:
            continue
        if out["images_ocrd"] >= _MAX_IMAGES_PER_PDF:
            continue

        page_images_ocrd = 0
        try:
            imgs = page.get_images(full=True)
        except Exception:
            imgs = []

        for img_idx, img_info in enumerate(imgs[:_MAX_IMAGES_PER_PAGE]):
            if out["images_ocrd"] >= _MAX_IMAGES_PER_PDF:
                break
            try:
                xref = img_info[0]
                pix = fitz.Pixmap(doc, xref)
                # Skip tiny images (icons, bullets, page decoration)
                if pix.width * pix.height < _MIN_IMAGE_AREA_PIXELS:
                    pix = None
                    continue
                # Normalise to 3-channel RGB PNG bytes so the OCR chain
                # can consume anything fitz gives us
                if pix.colorspace and pix.colorspace.n > 3:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                img_bytes = pix.tobytes("png")
                pix = None
            except Exception as exc:
                out["errors"].append(
                    f"page {page_idx + 1} image {img_idx}: extract failed "
                    f"({str(exc)[:80]})"
                )
                continue

            # OCR the image via the existing chain (Tesseract → EasyOCR → cloud)
            try:
                from . import ocr as aria_ocr
                ocr_result = await aria_ocr.extract_text_from_image(
                    img_bytes,
                    filename=f"{filename}_p{page_idx + 1}_img{img_idx + 1}.png",
                    context=f"image from {filename} page {page_idx + 1}",
                    llm=None,  # No LLM needed for local OCR engines
                )
                ocr_text = (ocr_result or {}).get("text", "").strip()
            except Exception as exc:
                ocr_text = ""
                out["errors"].append(
                    f"page {page_idx + 1} image {img_idx}: OCR failed "
                    f"({str(exc)[:80]})"
                )

            if ocr_text and len(ocr_text) >= _MIN_PAGE_TEXT_CHARS:
                await _ingest_chunk(
                    text=ocr_text,
                    source=f"{filename}#page_{page_idx + 1}#image_{img_idx + 1}",
                    metadata={
                        "pdf_filename":   filename,
                        "pdf_hash":       pdf_hash,
                        "page_number":    page_idx + 1,
                        "image_index":    img_idx + 1,
                        "content_type":   "pdf_image_ocr",
                        "source_context": source_context,
                        "ingested_at":    datetime.now(timezone.utc).isoformat(),
                    },
                )
                out["chunks_ingested"] += 1
                out["images_ocrd"] += 1
                out["total_chars"] += len(ocr_text)
                page_images_ocrd += 1

        if not has_text and page_images_ocrd > 0:
            out["image_pages"] += 1

    try:
        doc.close()
    except Exception:
        pass

    # brain_hook — every deep-ingest is a sizeable learning event
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="pdf_deep_ingest",
            summary=(
                f"PDF deep-ingest: {filename} — {out['chunks_ingested']} chunks "
                f"({out['text_pages']} text pages, {out['images_ocrd']} images OCR'd)"
            ),
            success=out["chunks_ingested"] > 0,
            gap_type="pdf_parse_failure" if out["chunks_ingested"] == 0 else None,
            gap_detail=(out["errors"][0][:200] if out["errors"] else None),
        )
    except Exception:
        pass

    # R-F2085 §21a — explicit success/failure wiring (the brain_hook.absorb above
    # also wires it, but granular success vs failure helps the coder + the
    # proprioception surface see whether a PDF actually ingested).
    if out["chunks_ingested"] > 0:
        wire_success(
            module="pdf_deep_ingest",
            summary=(f"PDF deep-ingest: {filename} — {out['chunks_ingested']} chunks "
                     f"({out['text_pages']} text pages, {out['images_ocrd']} images OCR'd)"),
            source_id=pdf_hash,
        )
    else:
        wire_failure(
            module="pdf_deep_ingest",
            detail=(f"PDF deep-ingest produced 0 chunks for {filename}"
                    + (f" — {out['errors'][0][:160]}" if out["errors"] else "")),
            gap_type="file_parse", source="pdf_deep_ingest",
        )

    return out


async def _ingest_chunk(text: str, source: str, metadata: dict[str, Any]) -> None:
    """Best-effort RAG ingestion. No-op + log if rag_store is unavailable."""
    try:
        from . import rag_store
        if hasattr(rag_store, "add_chunk"):
            await rag_store.add_chunk(text=text, metadata={"source": source, **metadata})
    except Exception as exc:
        logger.debug("rag add_chunk failed for %s: %s", source, exc)


@fail_wire(module="pdf_deep_ingest", gap_type="file_parse")
def summary() -> dict[str, Any]:
    """Capability manifest summary."""
    return {
        "min_page_text_chars":   _MIN_PAGE_TEXT_CHARS,
        "max_images_per_page":   _MAX_IMAGES_PER_PAGE,
        "max_images_per_pdf":    _MAX_IMAGES_PER_PDF,
        "min_image_area_pixels": _MIN_IMAGE_AREA_PIXELS,
    }

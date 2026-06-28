"""R-F415 — magic-byte file-type detection.

Pre-R-F415 the /read-document upload path routed parsers by the
attacker-controlled `mimetype` (Content-Type header value) and
`filename.lower()` extension. An attacker could send a .pdf-named
file with `Content-Type: application/pdf` whose body was actually
a ZIP, executable, or other arbitrary blob — the PDF parser would
choke and either silently fail, OOM, or burn CPU on a malformed
input. Mime-confusion is a routine OWASP pattern.

This module exposes:
  - detect_file_type(raw_bytes) → canonical type or "unknown"
  - file_type_matches_claim(detected, claimed_mime, filename) →
    True iff the detection agrees with the operator-supplied
    routing hint

Canonical types: "pdf", "docx", "xlsx", "pptx", "zip" (generic),
"png", "jpeg", "gif", "webp", "txt" (best-effort utf8), "unknown".

Caller pattern (in aria/routes/aria.py /read-document):
    detected = detect_file_type(raw_bytes)
    if not file_type_matches_claim(detected, mimetype, filename):
        log + record telemetry; route by DETECTED type, not claim.
"""
from __future__ import annotations

import io
import logging
import zipfile
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger(__name__)


# ── Magic-byte signatures ──────────────────────────────────────────────────


_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_ZIP_EMPTY_MAGIC = b"PK\x05\x06"  # empty archive
_ZIP_SPANNED_MAGIC = b"PK\x07\x08"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_GIF87_MAGIC = b"GIF87a"
_GIF89_MAGIC = b"GIF89a"
_WEBP_HEAD = b"RIFF"
_WEBP_TAG = b"WEBP"
_BMP_MAGIC = b"BM"


def _is_zip(b: bytes) -> bool:
    return b[:4] in (_ZIP_MAGIC, _ZIP_EMPTY_MAGIC, _ZIP_SPANNED_MAGIC)


def _peek_office_zip(raw_bytes: bytes) -> str:
    """For ZIP-shaped uploads, peek at internal filenames to
    distinguish DOCX / XLSX / PPTX / generic ZIP. Returns one of:
    "docx", "xlsx", "pptx", "zip"."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        # Truncated / corrupt ZIP — treat as generic ZIP. Caller
        # will then try parsing or fall back.
        return "zip"
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    return "zip"


def _is_probably_text(b: bytes, sample_len: int = 1024) -> bool:
    """Very loose heuristic — decode the first sample_len bytes as
    UTF-8 strict. If it works AND the proportion of printable +
    whitespace chars is high, classify as txt."""
    if not b:
        return False
    sample = b[:sample_len]
    try:
        decoded = sample.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    if not decoded:
        return False
    printable = sum(
        1 for c in decoded
        if c.isprintable() or c in ("\n", "\r", "\t")
    )
    return (printable / len(decoded)) >= 0.95


@fail_wire(module="file_type_detector", gap_type="file_parse")
def detect_file_type(raw_bytes: bytes) -> str:
    """Return the canonical file type by magic bytes.

    Returns "unknown" when no signature matches and the bytes don't
    look like plain text. Callers should treat "unknown" as a
    parsing-refusal condition (don't blindly hand it to the PDF
    parser just because the filename ends in .pdf).
    """
    if not raw_bytes:
        return "unknown"
    head = raw_bytes[:16]
    if head.startswith(_PDF_MAGIC):
        return "pdf"
    if head.startswith(_PNG_MAGIC):
        return "png"
    if head.startswith(_JPEG_MAGIC):
        return "jpeg"
    if head.startswith(_GIF87_MAGIC) or head.startswith(_GIF89_MAGIC):
        return "gif"
    if (
        head.startswith(_WEBP_HEAD)
        and len(raw_bytes) >= 12
        and raw_bytes[8:12] == _WEBP_TAG
    ):
        return "webp"
    if head.startswith(_BMP_MAGIC):
        return "bmp"
    if _is_zip(head):
        return _peek_office_zip(raw_bytes)
    if _is_probably_text(raw_bytes):
        return "txt"
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="file_type_detector",
        summary="Detect File Type",
        source_id="file_type_detector:R-F996",
    )

    return "unknown"


# ── Disagreement-detection helper ─────────────────────────────────────────


# Canonical type → (mime substrings that legitimately match, extensions
# that legitimately match). Used to decide if the attacker-supplied
# claim disagrees with what the magic bytes say.
_TYPE_TO_HINTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "pdf":  (("pdf",),                                      (".pdf",)),
    "docx": (("wordprocessingml", "msword"),                (".docx", ".doc")),
    "xlsx": (("spreadsheetml", "excel"),                    (".xlsx", ".xls", ".xlsm")),
    "pptx": (("presentationml", "powerpoint"),              (".pptx", ".ppt")),
    "zip":  (("zip",),                                      (".zip",)),
    "png":  (("png", "image"),                              (".png",)),
    "jpeg": (("jpeg", "jpg", "image"),                      (".jpg", ".jpeg")),
    "gif":  (("gif", "image"),                              (".gif",)),
    "webp": (("webp", "image"),                             (".webp",)),
    "bmp":  (("bmp", "image"),                              (".bmp",)),
    "txt":  (("text", "plain"),                             (".txt", ".csv", ".md", ".log")),
}


@fail_wire(module="file_type_detector", gap_type="file_parse")
def file_type_matches_claim(
    detected: str,
    claimed_mime: str = "",
    filename: str = "",
) -> bool:
    """True iff the detected type is consistent with the operator's
    routing claim (mime header AND/OR filename extension).

    "unknown" detected always returns False — caller must refuse
    or fall back. A perfectly-matched claim returns True.

    When the operator provides NO claim (both mime and filename
    empty), this returns True because there's nothing to disagree
    with — caller can still proceed by detected type.
    """
    if detected == "unknown":
        return False
    mime_lc = (claimed_mime or "").lower()
    fname_lc = (filename or "").lower()
    # No claim → trivially "consistent" (caller routes by detected)
    if not mime_lc and not fname_lc:
        return True
    hints = _TYPE_TO_HINTS.get(detected)
    if not hints:
        # Detected type has no hints to compare against — be lenient.
        return True
    mime_subs, exts = hints
    if mime_lc and any(s in mime_lc for s in mime_subs):
        return True
    if fname_lc and any(fname_lc.endswith(e) for e in exts):
        return True
    return False

# R-F2119 §21a — wire failure handler for file_type_detector
try:
    wire_failure(module="file_type_detector", detail="module shutdown",
                gap_type="engine_failure", source="file_type_detector:shutdown")
except Exception:
    pass

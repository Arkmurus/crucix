"""R-F1131 — Content security scanner for untrusted file processing.

Scans downloaded files and content for malware, embedded scripts, compression
bombs, and other threats BEFORE they reach document_reader or any processing
pipeline.

Detection capabilities:
1. EICAR test string detection (standard AV test)
2. Compression/decompression bomb detection (zip bomb, ratio caps)
3. Embedded script detection (PDF JavaScript, DOCX macros, XLSX DDE)
4. Magic byte validation (file type vs claimed extension)
5. Known malware hash lookup (via integrated knowledge base)
6. Suspicious content patterns (base64-encoded executables, shellcode)

On detection: quarantines the file, records a security_threat gap to the brain,
and returns a block result — NEVER opens the file dangerously.

Usage:
    from aria_service.intel.content_scanner import scan_file, ScanResult
    result = await scan_file(file_path, claimed_type="pdf")
    if not result.safe:
        await result.quarantine()
        return {"error": "File blocked", "reason": result.reason}
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import struct
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.content_scanner")

# ── Constants ───────────────────────────────────────────────────────────────

# EICAR test string — standard antivirus test file.
# Constructed at runtime from base64 so the literal string NEVER sits on disk.
# Kaspersky (and ALL antivirus) detects + deletes any file containing the
# literal EICAR string. This is the standing fix for that class of bug.
import base64
from .wire import fail_wire  # R-F1789 §21 brain-wiring
_EICAR_B64 = "WDVPIVAlQEFQWzRcUFpYNTQoUF4pN0NDKTd9JEVJQ0FSLVNUQU5EQVJELUFOVElWSVJVUy1URVNULUZJTEUhJEgrSCo="
EICAR_STRING = base64.b64decode(_EICAR_B64)

# Max compression ratio (decompressed / compressed) before flagging as bomb
MAX_COMPRESSION_RATIO = 100  # 100:1 ratio threshold

# Max decompressed size for archives (prevents zip bombs)
# R-F1917 (G6): lowered 500MB -> 100MB, proportional to the 50MB ingress cap
# (main.py). 500MB let a ~40MB DOCX inflate to ~400MB and OOM the single-process
# brain while still passing the bomb check. Env-tunable.
MAX_DECOMPRESSED_SIZE = int(os.getenv("ARIA_MAX_DECOMPRESSED_MB", "100")) * 1024 * 1024

# Magic bytes for common file types
MAGIC_BYTES: dict[str, list[bytes]] = {
    "pdf": [b"%PDF"],
    "docx": [b"PK\x03\x04"],  # Also matches xlsx, pptx, zip
    "xlsx": [b"PK\x03\x04"],
    "pptx": [b"PK\x03\x04"],
    "zip": [b"PK\x03\x04"],
    "gzip": [b"\x1f\x8b"],
    "png": [b"\x89PNG"],
    "jpg": [b"\xff\xd8\xff"],
    "tiff": [b"II\x2a\x00", b"MM\x00\x2a"],
    "rtf": [b"{\\rtf"],
    "html": [b"<html", b"<!DOCTYPE html", b"<HTML"],
    "xml": [b"<?xml"],
    "json": [b"{"],
    "csv": [],  # No fixed magic bytes
    "txt": [],  # No fixed magic bytes
}

# ── R-F3457: pattern specificity vs. the binary noise floor ─────────────────
#
# These tables used to be flat (bytes, description) pairs matched against the WHOLE
# file with no type scoping. Several entries sat BELOW the noise floor of ordinary
# binary content, so benign documents were blocked as malware — measured at 6 of 13
# local files, and in production it refused a real customer PDF
# ("Offer Sikorsky UH-60A ... .pdf": PDF embedded JS + INT3 chain + Base64, all noise).
#
# The offenders and why they could not work:
#   b"DDE"                 3 ASCII bytes. Fired on a 434KB PDF and 4 images.
#   b"\x90\x90\x90\x90"    4 repeated bytes — routine padding in image/font data.
#   b"\xcc\xcc\xcc\xcc"    ditto.
#   b"/JS"                 3 bytes, unanchored, so it matched inside compressed streams.
#   "c2g"                  base64 of "sh" — a 3-char trigram present in almost any
#                          long base64 run, so it flagged every encoded blob.
#
# Two structural fixes, neither of which weakens detection:
#
#   1. SCOPE each pattern to the container it can actually appear in, keyed off the
#      SNIFFED magic bytes — never the caller-supplied `claimed_type`, which is
#      attacker-controlled (a malicious PDF renamed .png must still be scanned as a
#      PDF). An unrecognised container gets EVERY rule, so unknown = strictest.
#   2. ANCHOR the short patterns: PDF names must be followed by a PDF delimiter, and
#      shellcode runs must be long enough (>=16) to mean something.
#
# Scopes: "pdf" | "zip" (OOXML/zip container) | "binary" (unrecognised — strictest).
_SCOPE_PDF = "pdf"
_SCOPE_ZIP = "zip"
_SCOPE_BINARY = "binary"

# Minimum repeat length for a byte-run to count as shellcode. Four was noise; a real
# NOP sled / INT3 chain is far longer. Env-tunable for incident response.
_SHELLCODE_RUN = max(8, int(os.getenv("ARIA_SCAN_SHELLCODE_RUN", "16")))

# PDF delimiters (PDF 32000-1 §7.2.2). A name token ends at one of these, so
# `/JS(` and `/JS ` are real, while `/JSomething` and a chance `/JS` inside a
# compressed stream are not.
_PDF_DELIM = rb"[\s/<>\[\]()%]"

# (compiled regex, description, scope). Regex — not bare substrings — so short
# tokens can be delimiter-anchored.
EMBEDDED_SCRIPT_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # PDF JavaScript / actions. The long names are safe unanchored (11+ bytes);
    # the short ones must be anchored as PDF name tokens.
    (re.compile(rb"/JavaScript" + _PDF_DELIM), "PDF embedded JavaScript", _SCOPE_PDF),
    (re.compile(rb"/JS" + _PDF_DELIM), "PDF embedded JS", _SCOPE_PDF),
    (re.compile(rb"/Launch" + _PDF_DELIM), "PDF launch action", _SCOPE_PDF),
    (re.compile(rb"/EmbeddedFile" + _PDF_DELIM), "PDF embedded file", _SCOPE_PDF),
    (re.compile(rb"/OpenAction" + _PDF_DELIM), "PDF open action", _SCOPE_PDF),
    # DOCX macros — 14+ byte literals, specific enough to match raw.
    (re.compile(rb"word/vbaProject\.bin"), "DOCX VBA macro (full path)", _SCOPE_ZIP),
    (re.compile(rb"vbaProject\.bin"), "DOCX VBA macro", _SCOPE_ZIP),
    # XLSX DDE. Bare b"DDE" is REMOVED: 3 ASCII bytes cannot evidence a DDE formula,
    # and OOXML parts are deflate-compressed inside the zip so raw matching is noise
    # either way. DDEAUTO is the token that actually carries the attack.
    (re.compile(rb"DDEAUTO"), "XLSX DDE auto formula", _SCOPE_ZIP),
    # OLE objects.
    (re.compile(rb"\x01\x05\x00{10}"), "OLE object", _SCOPE_ZIP),
    # Shellcode indicators — only meaningful in a blob we could not identify, and
    # only at a length that cannot occur by accident.
    (re.compile(rb"\x90{%d,}" % _SHELLCODE_RUN),
     "NOP sled (potential shellcode)", _SCOPE_BINARY),
    (re.compile(rb"\xcc{%d,}" % _SHELLCODE_RUN),
     "INT3 breakpoint chain", _SCOPE_BINARY),
]

# ── Base64-embedded executables ─────────────────────────────────────────────
#
# Matching the ENCODED form by substring is what produced the false positives: any
# sufficiently long base64 run contains most short trigrams. Instead we locate long
# base64 runs and DECODE them, then test the decoded bytes against real file magic.
# That is both stricter (no trigram can fake a PE header) and more sensitive (it
# catches every base64 alignment, which a fixed encoded prefix cannot).
# The run floor bounds COST only — it is not what prevents false positives (the
# decoded-magic check is). So keep it low enough that a short embedded payload is
# still caught: the R-F1131 PE fixture is a 36-char run, and a detector must not
# need more evidence than that.
_B64_RUN_RE = re.compile(r"[A-Za-z0-9+/=]{24,}")

# (magic bytes searched in the DECODED payload, description). All >=4 bytes, so a
# chance hit in decoded noise is negligible.
SUSPICIOUS_B64_PATTERNS: list[tuple[bytes, str]] = [
    (b"MZ\x90\x00", "Base64-encoded PE executable"),
    (b"This program cannot be run in DOS mode", "Base64-encoded PE executable"),
    (b"\x7fELF\x01", "Base64-encoded ELF binary"),
    (b"\x7fELF\x02", "Base64-encoded ELF binary"),
    (b"#!/bin/sh", "Base64-encoded shell script"),
    (b"#!/bin/bash", "Base64-encoded shell script"),
]

# Cost ceilings — this runs on every upload, so the decode sweep is bounded.
_B64_MAX_RUNS = 64
_B64_MAX_RUN_CHARS = 3000

# R-F3469 — content that is DANGEROUS whatever it claims to be. Checked at
# offset 0 only: these are file headers, and an executable must start with one to
# be executable. A document claiming .pdf whose bytes begin with MZ or ELF magic
# is the disguised-executable case magic-byte validation exists to catch.
EXECUTABLE_MAGICS: list[tuple[bytes, str]] = [
    (b"MZ", "DOS/PE executable"),
    (b"\x7fELF", "ELF executable"),
    (b"\xca\xfe\xba\xbe", "Mach-O / Java class"),
    (b"\xcf\xfa\xed\xfe", "Mach-O 64-bit"),
    (b"\xce\xfa\xed\xfe", "Mach-O 32-bit"),
    (b"#!", "script with a shebang"),
    (b"\x00" + b"asm", "WebAssembly module"),
]

# Quarantine directory
QUARANTINE_DIR = Path("/data/quarantine")


# ── Result types ────────────────────────────────────────────────────────────

class ScanResult:
    """Result of a content scan."""

    def __init__(
        self,
        safe: bool,
        reason: str = "",
        threats: Optional[list[dict[str, Any]]] = None,
        file_path: Optional[Path] = None,
    ):
        self.safe = safe
        self.reason = reason
        self.threats = threats or []
        self.file_path = file_path

    @fail_wire(module="content_scanner", gap_type="file_parse")
    async def quarantine(self) -> Optional[Path]:
        """Move the file to quarantine. Returns the quarantine path or None."""
        if not self.file_path or not self.file_path.exists():
            return None
        try:
            QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
            dest = QUARANTINE_DIR / f"{self.file_path.name}.quarantined"
            shutil.move(str(self.file_path), str(dest))
            logger.warning(
                "[content_scanner] Quarantined %s -> %s (reason: %s)",
                self.file_path, dest, self.reason,
            )
            return dest
        except Exception as e:
            logger.error("[content_scanner] Quarantine failed: %s", e)
            return None

    @fail_wire(module="content_scanner", gap_type="file_parse")
    def to_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "reason": self.reason,
            "threats": self.threats,
        }


# ── Scanning functions ──────────────────────────────────────────────────────

@fail_wire(module="content_scanner", gap_type="file_parse")
def check_eicar(data: bytes) -> Optional[dict[str, Any]]:
    """Check for EICAR test string."""
    if EICAR_STRING in data:
        return {"type": "eicar", "severity": "CRITICAL", "detail": "EICAR test string detected"}
    return None


@fail_wire(module="content_scanner", gap_type="file_parse")
def check_compression_bomb(file_path: Path) -> Optional[dict[str, Any]]:
    """Check for compression/decompression bomb.

    Tests zip files for excessive compression ratio. A zip bomb may be small
    compressed (a few KB) but expand to gigabytes when decompressed.
    """
    if not file_path.exists():
        return None

    compressed_size = file_path.stat().st_size
    if compressed_size == 0:
        return None

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            total_decompressed = 0
            for info in zf.infolist():
                total_decompressed += info.file_size
                # Check per-file ratio
                if info.file_size > 0 and info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_COMPRESSION_RATIO:
                        return {
                            "type": "compression_bomb",
                            "severity": "CRITICAL",
                            "detail": (
                                f"File {info.filename} has compression ratio "
                                f"{ratio:.0f}:1 (max {MAX_COMPRESSION_RATIO}:1)"
                            ),
                        }

            # Check total decompressed size
            if total_decompressed > MAX_DECOMPRESSED_SIZE:
                return {
                    "type": "compression_bomb",
                    "severity": "CRITICAL",
                    "detail": (
                        f"Total decompressed size {total_decompressed} bytes "
                        f"exceeds max {MAX_DECOMPRESSED_SIZE} bytes"
                    ),
                }
    except zipfile.BadZipFile:
        pass  # Not a zip file — that's fine
    except Exception as e:
        logger.debug("[content_scanner] zip check failed: %s", e)

    return None


@fail_wire(module="content_scanner", gap_type="file_parse")
def check_magic_bytes(data: bytes, claimed_type: str) -> Optional[dict[str, Any]]:
    """Validate file magic bytes against the claimed type.

    R-F3469 — a MISLABELLED file is a routing problem; a DISGUISED EXECUTABLE is
    a threat. This used to return HIGH for any difference at all, and the callers
    block on any threat, so a JPEG saved as .png was refused as malware (5 of 16
    benign local files). Browsers and operating systems mislabel images and
    documents constantly; that is not an attack.

    The control keeps its real job. A PE/ELF/shebang shipped as ``.pdf`` is
    exactly what magic-byte validation exists to catch, and is now reported as
    CRITICAL and NAMED, where before it was an anonymous "unknown_magic_bytes".

    Benign family mismatches return None so the caller can route on the real type
    — which is what test_rf450_docx_renamed_as_pdf_routes_to_docx_parser and
    test_rf450_generic_zip_renamed_as_pdf_does_not_invoke_pdf_parser have been
    asserting (long-red, docs/suite_baseline_2026_07_30.md:204-205).
    """
    expected_magic = MAGIC_BYTES.get(claimed_type.lower(), [])
    if not expected_magic:
        return None  # No magic bytes defined for this type — pass

    for magic in expected_magic:
        if data.startswith(magic):
            return None  # Match — file type is as claimed

    # Mismatch. First question: is the ACTUAL content executable? That is the
    # threat this check exists for, and it does not depend on what was claimed.
    for magic, label in EXECUTABLE_MAGICS:
        if data.startswith(magic):
            return {
                "type": "disguised_executable",
                "severity": "CRITICAL",
                "detail": (
                    f"Claimed type '{claimed_type}' but content is a {label} — "
                    f"executable disguised as a document"
                ),
            }

    # Not executable. Is it some other RECOGNISED container (docx/xlsx/zip share
    # PK; jpg vs png; etc.)? Then the file is simply mislabelled — report it for
    # routing, do not block. Logged rather than silent (§21a: not dark).
    for file_type, magics in MAGIC_BYTES.items():
        for magic in magics:
            if magic and data.startswith(magic):
                logger.info(
                    "[R-F3469] mislabelled upload: claimed '%s', content is '%s' "
                    "— allowed; caller should route on the real type",
                    claimed_type, file_type,
                )
                return None

    return {
        "type": "unknown_magic_bytes",
        "severity": "HIGH",
        "detail": f"Claimed type '{claimed_type}' but magic bytes are unrecognised",
    }


# A PDF header need not sit at offset 0 — PDF 32000-1 §7.5.2 lets readers accept
# "%PDF" anywhere in the first 1024 bytes, and real readers are laxer still. A ZIP
# is likewise identified by its local-file-header signature, not only at offset 0.
# So container sniffing must look INSIDE the prefix, not just at byte 0, or a
# polyglot (PNG magic prepended to a malicious PDF) would skip every PDF rule.
_POLYGLOT_WINDOW = 1024

# Recognised, inert containers: an image or plain text cannot execute, so the
# shellcode / macro / PDF rule families do not apply to them.
_INERT_MAGICS = (
    b"\x89PNG", b"\xff\xd8\xff", b"II\x2a\x00", b"MM\x00\x2a",
    b"\x1f\x8b", b"{\\rtf", b"<?xml", b"<html", b"<!DOCTYPE",
)


def _sniff_container(data: bytes) -> set[str]:
    """R-F3457 — decide which rule families apply, from the CONTENT.

    Deliberately ignores any caller-supplied ``claimed_type``: that value comes from
    the upload's filename/mimetype and is therefore attacker-controlled. Scoping on
    it would let a malicious PDF evade every PDF rule by calling itself a .png.

    Returns the SET of applicable scopes. A file may legitimately be in more than
    one (an OOXML package is a zip; a polyglot is whatever it embeds). An
    unrecognised blob returns every scope — unknown is the STRICTEST case, never
    the most permissive.
    """
    head = data[:_POLYGLOT_WINDOW]
    scopes: set[str] = set()
    if b"%PDF" in head:
        scopes.add(_SCOPE_PDF)
    if b"PK\x03\x04" in head:
        scopes.add(_SCOPE_ZIP)
    if scopes:
        return scopes
    if any(data.startswith(m) for m in _INERT_MAGICS):
        return set()
    # Unidentified bytes: apply everything, including the shellcode family.
    return {_SCOPE_PDF, _SCOPE_ZIP, _SCOPE_BINARY}


@fail_wire(module="content_scanner", gap_type="file_parse")
def check_embedded_scripts(data: bytes) -> list[dict[str, Any]]:
    """Check for embedded scripts in documents.

    R-F3457: rules are scoped to the SNIFFED container (see _sniff_container) and
    short tokens are delimiter-anchored, so ordinary binary noise inside a
    compressed stream or an image can no longer read as malware.
    """
    scopes = _sniff_container(data)
    threats: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern, description, pat_scope in EMBEDDED_SCRIPT_PATTERNS:
        # An unrecognised blob gets every rule; a recognised container gets only
        # the rules that can apply to it.
        if pat_scope not in scopes:
            continue
        if description in seen:
            continue
        if pattern.search(data):
            seen.add(description)
            threats.append({
                "type": "embedded_script",
                "severity": "HIGH",
                "detail": description,
            })
    return threats


def _decode_b64_run(run: str) -> list[bytes]:
    """Decode one base64 run at all 4 alignments. Never raises."""
    stripped = run.replace("=", "")[:_B64_MAX_RUN_CHARS]
    out: list[bytes] = []
    for offset in range(4):
        chunk = stripped[offset:]
        chunk = chunk[: len(chunk) - (len(chunk) % 4)]
        if len(chunk) < 8:
            continue
        try:
            out.append(base64.b64decode(chunk, validate=False))
        except Exception:
            continue
    return out


@fail_wire(module="content_scanner", gap_type="file_parse")
def check_suspicious_content(data: bytes) -> list[dict[str, Any]]:
    """Check for base64-embedded executables.

    R-F3457: locates long base64 runs and inspects the DECODED payload for real
    file magic, instead of substring-matching the encoded form. Short encoded
    trigrams ("TVp", "c2g") occur in almost every long base64 blob and were the
    source of the false positives; a decoded ``MZ\\x90\\x00`` or ``\\x7fELF`` is
    unambiguous, and decoding covers every alignment for free.
    """
    text = data.decode("utf-8", errors="replace")
    threats: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, match in enumerate(_B64_RUN_RE.finditer(text)):
        if idx >= _B64_MAX_RUNS:
            break
        for decoded in _decode_b64_run(match.group(0)):
            for magic, description in SUSPICIOUS_B64_PATTERNS:
                if description in seen:
                    continue
                if magic in decoded:
                    seen.add(description)
                    threats.append({
                        "type": "suspicious_content",
                        "severity": "HIGH",
                        "detail": description,
                    })
    return threats


# ── Real AV scanning (R-F1139) ──────────────────────────────────────────────

async def _scan_with_clamav(file_path: Path) -> Optional[dict[str, Any]]:
    """Scan a file with ClamAV daemon (clamd) via HTTP if available.

    Connects to clamd's HTTP interface (default port 3310) to scan files.
    If clamd is not running, returns None (no AV scan performed).

    This is a best-effort scan — never blocks the caller. If the AV is not
    available or times out, the file passes through to the heuristic scanner.
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:  # no-breaker: content scanner is best-effort; breaker would block content discovery
            # ClamAV clamd HTTP interface
            resp = await client.get(f"http://127.0.0.1:3310/scan/{file_path}")
            if resp.status_code == 200:
                text = resp.text.strip()
                if text and "OK" not in text and "Error" not in text:
                    return {
                        "type": "av_malware",
                        "severity": "CRITICAL",
                        "detail": f"ClamAV: {text}",
                        "engine": "clamav",
                    }
                return None  # Clean
    except (httpx.ConnectError, httpx.TimeoutException):
        pass  # ClamAV not running
    except Exception as e:
        logger.debug("[content_scanner] ClamAV scan failed: %s", e)

    return None  # No AV available


# ── Main scan entry point ───────────────────────────────────────────────────

@fail_wire(module="content_scanner", gap_type="file_parse")
async def scan_file(
    file_path: Path,
    claimed_type: str = "",
    data: Optional[bytes] = None,
) -> ScanResult:
    """Scan a file for security threats.

    Args:
        file_path: Path to the file to scan.
        claimed_type: Claimed file type (e.g., "pdf", "docx"). If empty,
            inferred from extension.
        data: File data (if already loaded). If None, read from file_path.

    Returns:
        ScanResult with safe=True if no threats found, safe=False if blocked.
    """
    if data is None:
        try:
            data = file_path.read_bytes()
        except Exception as e:
            return ScanResult(
                safe=False,
                reason=f"Cannot read file: {e}",
                file_path=file_path,
            )

    if not data:
        return ScanResult(safe=True, file_path=file_path)

    # Infer type from extension if not provided
    if not claimed_type:
        ext = file_path.suffix.lower().lstrip(".")
        claimed_type = ext

    threats: list[dict[str, Any]] = []

    # 1. EICAR test
    eicar = check_eicar(data)
    if eicar:
        threats.append(eicar)

    # 2. Compression bomb (zip files only)
    # R-F3469 — gate on the CONTENT, not the claimed extension. This used to read
    # `claimed_type in (...)`, which was safe only because a zip mislabelled as
    # .pdf was blocked outright by the magic-byte mismatch. Now that a benign
    # container mismatch is allowed through to the router, gating on the claim
    # would let a zip bomb renamed "invoice.pdf" skip the bomb check entirely —
    # attacker-controlled input selecting which checks run, the same flaw
    # R-F3457 removed from the scope logic.
    if claimed_type in ("zip", "docx", "xlsx", "pptx") or data.startswith(b"PK\x03\x04"):
        bomb = check_compression_bomb(file_path)
        if bomb:
            threats.append(bomb)

    # 3. Magic byte validation
    magic = check_magic_bytes(data, claimed_type)
    if magic:
        threats.append(magic)

    # 4. Embedded scripts
    threats.extend(check_embedded_scripts(data))

    # 5. Suspicious content
    threats.extend(check_suspicious_content(data))

    # 6. Real AV scan (ClamAV if available) — R-F1139
    # Run regardless of heuristic results — AV may catch what heuristics miss
    av_result = await _scan_with_clamav(file_path)
    if av_result:
        threats.append(av_result)

    if threats:
        threat_summary = "; ".join(
            f"{t['detail']} ({t['severity']})" for t in threats
        )
        result = ScanResult(
            safe=False,
            reason=threat_summary,
            threats=threats,
            file_path=file_path,
        )

        # Wire to brain
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="content_scanner",
                detail=f"Security threat: {threat_summary}",
                gap_type="security_threat",
                source=f"content_scanner:{file_path.name}",
            )
        except Exception:
            logger.debug("[content_scanner] brain wiring failed", exc_info=True)

        return result

    return ScanResult(safe=True, file_path=file_path)


@fail_wire(module="content_scanner", gap_type="file_parse")
async def scan_bytes(
    data: bytes,
    claimed_type: str = "",
    source_name: str = "unknown",
) -> ScanResult:
    """Scan raw bytes for security threats (without a file on disk).

    Args:
        data: Raw bytes to scan.
        claimed_type: Claimed file type (e.g., "pdf", "docx").
        source_name: Source identifier for logging.

    Returns:
        ScanResult with safe=True if no threats found.
    """
    if not data:
        return ScanResult(safe=True, file_path=Path(source_name or "bytes"))

    # R-F1303: run the byte-level (in-memory) checks BEFORE writing anything to
    # disk. EICAR and signature/heuristic hits are caught here and returned
    # immediately, so a malware-signature file is NEVER materialised on disk —
    # which an on-access AV (e.g. Kaspersky on a dev machine) quarantines the
    # instant it is written ("deleting objects"), and which left a delete=False
    # temp file lingering. Only content that PASSES every in-memory check (i.e.
    # is not a known signature) is written to a temp file for the path-based
    # checks (compression bomb / ClamAV).
    pre_threats: list[dict[str, Any]] = []
    eicar = check_eicar(data)
    if eicar:
        pre_threats.append(eicar)
    magic = check_magic_bytes(data, claimed_type)
    if magic:
        pre_threats.append(magic)
    pre_threats.extend(check_embedded_scripts(data))
    pre_threats.extend(check_suspicious_content(data))

    if pre_threats:
        summary = "; ".join(f"{t['detail']} ({t['severity']})" for t in pre_threats)
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="content_scanner",
                detail=f"Security threat: {summary}",
                gap_type="security_threat",
                source=f"content_scanner:{source_name}",
            )
        except Exception:
            logger.debug("[content_scanner] brain wiring failed", exc_info=True)
        return ScanResult(safe=False, reason=summary, threats=pre_threats,
                          file_path=Path(source_name or "bytes"))

    # Clean in memory — now safe to write a temp file for the path-based checks
    # (archive bomb / ClamAV). Clean content is not quarantined by an AV.
    suffix = f".{claimed_type}" if claimed_type else ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)

    try:
        result = await scan_file(tmp_path, claimed_type=claimed_type, data=data)
        return result
    finally:
        # Clean up temp file (unless quarantined)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

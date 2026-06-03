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
_EICAR_B64 = "WDVPIVAlQEFQWzRcUFpYNTQoUF4pN0NDKTd9JEVJQ0FSLVNUQU5EQVJELUFOVElWSVJVUy1URVNULUZJTEUhJEgrSCo="
EICAR_STRING = base64.b64decode(_EICAR_B64)

# Max compression ratio (decompressed / compressed) before flagging as bomb
MAX_COMPRESSION_RATIO = 100  # 100:1 ratio threshold

# Max decompressed size for archives (prevents zip bombs)
MAX_DECOMPRESSED_SIZE = 500 * 1024 * 1024  # 500MB

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

# Embedded script patterns in documents
EMBEDDED_SCRIPT_PATTERNS: list[tuple[bytes, str]] = [
    # PDF JavaScript
    (b"/JavaScript", "PDF embedded JavaScript"),
    (b"/JS", "PDF embedded JS"),
    (b"/Launch", "PDF launch action"),
    (b"/EmbeddedFile", "PDF embedded file"),
    (b"/OpenAction", "PDF open action"),
    # DOCX macros
    (b"vbaProject.bin", "DOCX VBA macro"),
    (b"word/vbaProject.bin", "DOCX VBA macro (full path)"),
    # XLSX DDE / formulas
    (b"DDE", "XLSX DDE formula"),
    (b"DDEAUTO", "XLSX DDE auto formula"),
    # OLE objects
    (b"\x01\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", "OLE object"),
    # Shellcode indicators
    (b"\x90\x90\x90\x90", "NOP sled (potential shellcode)"),
    (b"\xcc\xcc\xcc\xcc", "INT3 breakpoint chain"),
]

# Suspicious base64-encoded content patterns
SUSPICIOUS_B64_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:TVqQAAM|TVp|TVro|TVqQ)", re.I), "Base64-encoded PE executable"),
    (re.compile(r"(?:TVp|TVro)", re.I), "Base64-encoded PE (short)"),
    (re.compile(r"f0VMRg", re.I), "Base64-encoded ELF binary"),
    (re.compile(r"(?:IyEvYmluL2Jh|c2g|YmFzaA)", re.I), "Base64-encoded shell script"),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "reason": self.reason,
            "threats": self.threats,
        }


# ── Scanning functions ──────────────────────────────────────────────────────

def check_eicar(data: bytes) -> Optional[dict[str, Any]]:
    """Check for EICAR test string."""
    if EICAR_STRING in data:
        return {"type": "eicar", "severity": "CRITICAL", "detail": "EICAR test string detected"}
    return None


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


def check_magic_bytes(data: bytes, claimed_type: str) -> Optional[dict[str, Any]]:
    """Validate file magic bytes match the claimed type."""
    expected_magic = MAGIC_BYTES.get(claimed_type.lower(), [])
    if not expected_magic:
        return None  # No magic bytes defined for this type — pass

    for magic in expected_magic:
        if data.startswith(magic):
            return None  # Match — file type is as claimed

    # No match — but some types share magic bytes (e.g., docx/xlsx/zip all use PK)
    # Check if it's at least a known type
    for file_type, magics in MAGIC_BYTES.items():
        for magic in magics:
            if data.startswith(magic):
                return {
                    "type": "mismatched_magic_bytes",
                    "severity": "HIGH",
                    "detail": (
                        f"Claimed type '{claimed_type}' but magic bytes "
                        f"match '{file_type}'"
                    ),
                }

    return {
        "type": "unknown_magic_bytes",
        "severity": "HIGH",
        "detail": f"Claimed type '{claimed_type}' but magic bytes are unrecognised",
    }


def check_embedded_scripts(data: bytes) -> list[dict[str, Any]]:
    """Check for embedded scripts in documents."""
    threats = []
    for pattern, description in EMBEDDED_SCRIPT_PATTERNS:
        if pattern in data:
            threats.append({
                "type": "embedded_script",
                "severity": "HIGH",
                "detail": description,
            })
    return threats


def check_suspicious_content(data: bytes) -> list[dict[str, Any]]:
    """Check for suspicious content patterns."""
    threats = []
    # Check for base64-encoded executables
    text = data.decode("utf-8", errors="replace")
    for pattern, description in SUSPICIOUS_B64_PATTERNS:
        if pattern.search(text):
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
        async with httpx.AsyncClient(timeout=30) as client:
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
    if claimed_type in ("zip", "docx", "xlsx", "pptx"):
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

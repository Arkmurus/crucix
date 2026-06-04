"""R-F1131 — Capability tests for the content security scanner.

Tests that scan_file correctly BLOCKS real attack payloads:
1. EICAR test string — blocked and flagged
2. Zip bomb — blocked (compression ratio exceeds threshold)
3. PDF with embedded JavaScript — blocked
4. Mismatched magic bytes — blocked (claims PDF, is actually ZIP)
5. Suspicious base64-encoded content — blocked
6. Clean PDF — passes through
7. Brain wiring — threats are wired to the brain
"""
from __future__ import annotations

import sys
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from aria_service.intel.content_scanner import (
    EICAR_STRING,
    MAX_COMPRESSION_RATIO,
    ScanResult,
    check_compression_bomb,
    check_eicar,
    check_embedded_scripts,
    check_magic_bytes,
    check_suspicious_content,
    scan_bytes,
    scan_file,
)


# ── Tests for individual checks ─────────────────────────────────────────────

class TestEicarDetection:
    """Proves EICAR test string is detected."""

    def test_eicar_detected(self):
        """EICAR string in data is detected."""
        data = b"prefix " + EICAR_STRING + b" suffix"
        result = check_eicar(data)
        assert result is not None
        assert result["type"] == "eicar"
        assert result["severity"] == "CRITICAL"

    def test_clean_data_no_false_positive(self):
        """Clean data does not trigger EICAR detection."""
        data = b"clean file content here"
        result = check_eicar(data)
        assert result is None


class TestCompressionBomb:
    """Proves zip bombs are detected."""

    def test_zip_bomb_detected(self, tmp_path: Path):
        """A zip with excessive compression ratio is detected."""
        # Create a zip bomb: highly compressible data (all zeros) compresses
        # to a tiny fraction of its original size
        bomb_path = tmp_path / "bomb.zip"
        with zipfile.ZipFile(bomb_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 10MB of zeros compresses to ~10KB — ratio ~1000:1
            zf.writestr("huge_file.bin", b"\x00" * (10 * 1024 * 1024))

        result = check_compression_bomb(bomb_path)
        assert result is not None
        assert result["type"] == "compression_bomb"

    def test_normal_zip_not_flagged(self, tmp_path: Path):
        """A normal zip file is not flagged."""
        zip_path = tmp_path / "normal.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Random-looking data that doesn't compress well
            import hashlib
            data = b"".join(
                hashlib.sha256(f"block{i}".encode()).digest()
                for i in range(100)
            )  # ~3.2KB of incompressible data
            zf.writestr("normal.txt", data)

        result = check_compression_bomb(zip_path)
        assert result is None


class TestMagicBytes:
    """Proves magic byte validation works."""

    def test_matching_magic_passes(self):
        """Matching magic bytes pass validation."""
        data = b"%PDF-1.4 some pdf content"
        result = check_magic_bytes(data, "pdf")
        assert result is None

    def test_mismatched_magic_detected(self):
        """Mismatched magic bytes are detected."""
        data = b"PK\x03\x04 this is actually a zip"
        result = check_magic_bytes(data, "pdf")
        assert result is not None
        assert result["type"] == "mismatched_magic_bytes"

    def test_unknown_type_passes(self):
        """Types without defined magic bytes pass through."""
        data = b"arbitrary content"
        result = check_magic_bytes(data, "txt")
        assert result is None


class TestEmbeddedScripts:
    """Proves embedded scripts are detected."""

    def test_pdf_javascript_detected(self):
        """PDF with /JavaScript is detected."""
        data = b"%PDF-1.4\n1 0 obj\n/JavaScript\nendobj"
        threats = check_embedded_scripts(data)
        assert len(threats) >= 1
        assert any("JavaScript" in t["detail"] for t in threats)

    def test_docx_macro_detected(self):
        """DOCX with VBA macro is detected."""
        data = b"PK\x03\x04...vbaProject.bin..."
        threats = check_embedded_scripts(data)
        assert len(threats) >= 1
        assert any("VBA" in t["detail"] for t in threats)

    def test_clean_pdf_not_flagged(self):
        """Clean PDF without scripts is not flagged."""
        data = b"%PDF-1.4\n1 0 obj\n/Type /Catalog\nendobj"
        threats = check_embedded_scripts(data)
        script_threats = [t for t in threats if t["type"] == "embedded_script"]
        assert len(script_threats) == 0


class TestSuspiciousContent:
    """Proves suspicious content patterns are detected."""

    def test_base64_pe_detected(self):
        """Base64-encoded PE executable is detected."""
        data = b"TVqQAAMAAAAEAAAA//8AALgAAAAAAAAAQAAA"  # MZ header in base64
        threats = check_suspicious_content(data)
        assert len(threats) >= 1
        assert any("PE" in t["detail"] for t in threats)

    def test_clean_content_not_flagged(self):
        """Clean content is not flagged."""
        data = b"This is a normal document about defence procurement."
        threats = check_suspicious_content(data)
        assert len(threats) == 0


# ── Integration tests ───────────────────────────────────────────────────────

class TestScanFile:
    """Proves the full scan pipeline blocks real attacks."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="R-F1303: writing the literal EICAR string to disk is quarantined "
               "by on-access AV (Kaspersky) before scan_file can read it — an OS/AV "
               "reality, not a code bug. EICAR detection is covered in-memory by "
               "TestEicar/scan_bytes, which never touch disk. CI (Linux) runs this.",
    )
    async def test_eicar_file_blocked(self, tmp_path: Path):
        """A file containing EICAR string is blocked (path-based scan)."""
        file_path = tmp_path / "test.txt"
        file_path.write_bytes(EICAR_STRING)

        result = await scan_file(file_path)

        assert result.safe is False
        assert "EICAR" in result.reason

    async def test_zip_bomb_blocked(self, tmp_path: Path):
        """A zip bomb file is blocked."""
        bomb_path = tmp_path / "bomb.zip"
        with zipfile.ZipFile(bomb_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 10MB of zeros compresses to ~10KB — ratio ~1000:1
            zf.writestr("huge.bin", b"\x00" * (10 * 1024 * 1024))

        result = await scan_file(bomb_path, claimed_type="zip")

        assert result.safe is False
        assert "compression" in result.reason.lower()

    async def test_pdf_with_javascript_blocked(self, tmp_path: Path):
        """A PDF with embedded JavaScript is blocked."""
        file_path = tmp_path / "malicious.pdf"
        file_path.write_bytes(b"%PDF-1.4\n/JavaScript\nendobj")

        result = await scan_file(file_path, claimed_type="pdf")

        assert result.safe is False
        assert "JavaScript" in result.reason

    async def test_mismatched_type_blocked(self, tmp_path: Path):
        """A file claiming to be PDF but actually ZIP is blocked."""
        file_path = tmp_path / "fake.pdf"
        file_path.write_bytes(b"PK\x03\x04 this is a zip file")

        result = await scan_file(file_path, claimed_type="pdf")

        assert result.safe is False
        assert "magic" in result.reason.lower()

    async def test_clean_file_passes(self, tmp_path: Path):
        """A clean PDF file passes through."""
        file_path = tmp_path / "clean.pdf"
        file_path.write_bytes(b"%PDF-1.4\n1 0 obj\n/Type /Catalog\nendobj")

        result = await scan_file(file_path, claimed_type="pdf")

        assert result.safe is True

    async def test_scan_bytes_works(self):
        """scan_bytes works without a file on disk."""
        data = EICAR_STRING
        result = await scan_bytes(data, claimed_type="txt")

        assert result.safe is False
        assert "EICAR" in result.reason

    async def test_wires_threats_to_brain(self):
        """Threats are wired to the brain via wire_failure.

        R-F1328: scan IN-MEMORY via scan_bytes instead of writing eicar.txt to
        disk. scan_bytes shares the same wire_failure path as scan_file
        (content_scanner.py:436 — identical module/gap_type), so the brain-wiring
        is fully covered, but the literal EICAR signature is NEVER materialised on
        disk. An on-access AV (Kaspersky on the dev machine) was quarantining
        eicar.txt as EICAR-Test-File on every test run. Extends R-F1303 to tests.
        """
        with patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:
            result = await scan_bytes(EICAR_STRING, claimed_type="txt")

        assert result.safe is False
        mock_wf.assert_called_once()
        args, kwargs = mock_wf.call_args
        assert kwargs.get("module") == "content_scanner"
        assert kwargs.get("gap_type") == "security_threat"

    async def test_clamav_integration(self, tmp_path: Path):
        """ClamAV scan is called when available (graceful fallback when not)."""
        file_path = tmp_path / "clean.pdf"
        file_path.write_bytes(b"%PDF-1.4 clean content")

        with patch("aria_service.intel.content_scanner._scan_with_clamav",
                   return_value=None):
            result = await scan_file(file_path, claimed_type="pdf")

        # Should pass through when ClamAV is not available
        assert result.safe is True

    async def test_clamav_detects_threat(self, tmp_path: Path):
        """ClamAV detection is reported as malware."""
        file_path = tmp_path / "clean.txt"
        file_path.write_bytes(b"clean file content that passes heuristics")

        with patch("aria_service.intel.content_scanner._scan_with_clamav",
                   return_value={
                       "type": "av_malware",
                       "severity": "CRITICAL",
                       "detail": "ClamAV: Win.Trojan.Test-1",
                       "engine": "clamav",
                   }):
            result = await scan_file(file_path, claimed_type="txt")

        assert result.safe is False
        assert "ClamAV" in result.reason

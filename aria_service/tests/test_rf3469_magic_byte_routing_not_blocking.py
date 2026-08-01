"""R-F3469 — a mislabelled file is a ROUTING problem, not a security threat.

Surfaced by the same measurement as R-F3457. After that fix removed the malware
false positives, 5 of 16 benign local files were still blocked — all by a
different control:

    document blocked by content scan: Claimed type 'png' but magic bytes match 'jpg' (HIGH)

``check_magic_bytes`` returned a HIGH threat for ANY difference between the
claimed extension and the sniffed magic, and ``scan_file``/``scan_bytes`` block on
any threat. So a JPEG saved as ``.png`` — which browsers and operating systems
produce constantly — was refused as malware.

Two long-red guards in docs/suite_baseline.md have been
asserting exactly this for months:

    test_rf450_generic_zip_renamed_as_pdf_does_not_invoke_pdf_parser
    test_rf450_docx_renamed_as_pdf_routes_to_docx_parser

Both expect the endpoint to ROUTE on the real type and return 200/400. They failed
with 422 because the scanner blocked before routing could happen.

The control still has a real job: a DISGUISED EXECUTABLE (a PE/ELF/shebang shipped
as ``.pdf``) is a genuine threat and must stay blocked. The distinction is between
"the content is a different benign document type" and "the content is executable".
"""
from __future__ import annotations

import io
import zipfile

import pytest

from aria_service.intel.content_scanner import (
    check_magic_bytes,
    scan_bytes,
    scan_file,
)


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


class TestBenignMismatchIsNotAThreat:

    def test_jpeg_named_png_is_not_a_threat(self):
        """The live case: 5 of 16 benign files blocked on exactly this."""
        assert check_magic_bytes(_JPG, "png") is None

    def test_docx_named_pdf_is_not_a_threat(self):
        """R-F450's guard: this must reach the router, not a 422."""
        assert check_magic_bytes(_docx(), "pdf") is None

    def test_generic_zip_named_pdf_is_not_a_threat(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("README.txt", "just a zip")
        assert check_magic_bytes(buf.getvalue(), "pdf") is None

    @pytest.mark.asyncio
    async def test_full_scan_allows_a_jpeg_named_png(self):
        """End to end through the path read-document actually calls."""
        result = await scan_bytes(_JPG, claimed_type="png", source_name="photo.png")
        assert result.safe, f"benign mislabelled image blocked: {result.reason!r}"

    @pytest.mark.asyncio
    async def test_full_scan_allows_a_docx_named_pdf(self):
        result = await scan_bytes(_docx(), claimed_type="pdf", source_name="report.pdf")
        assert result.safe, f"benign mislabelled document blocked: {result.reason!r}"

    def test_matching_type_still_passes(self):
        assert check_magic_bytes(_PDF, "pdf") is None
        assert check_magic_bytes(_PNG, "png") is None


class TestDisguisedExecutablesStillBlocked:
    """The control's real job. Every relaxation above is paired with one of these."""

    def test_pe_executable_named_pdf_is_blocked(self):
        threat = check_magic_bytes(b"MZ\x90\x00" + b"\x00" * 64, "pdf")
        assert threat is not None, "a PE executable disguised as a PDF was allowed"
        assert threat["severity"] == "CRITICAL", threat
        assert "executable" in threat["detail"].lower()

    def test_elf_executable_named_docx_is_blocked(self):
        threat = check_magic_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 64, "docx")
        assert threat is not None, "an ELF binary disguised as a docx was allowed"
        assert threat["severity"] == "CRITICAL", threat

    def test_shell_script_named_pdf_is_blocked(self):
        threat = check_magic_bytes(b"#!/bin/bash\nrm -rf /\n", "pdf")
        assert threat is not None, "a shell script disguised as a PDF was allowed"
        assert threat["severity"] == "CRITICAL", threat

    @pytest.mark.asyncio
    async def test_full_scan_blocks_a_disguised_executable(self):
        result = await scan_bytes(
            b"MZ\x90\x00" + b"\x00" * 512, claimed_type="pdf", source_name="invoice.pdf",
        )
        assert not result.safe, "a disguised executable passed the full scan"

    def test_unrecognised_content_claiming_a_known_type_still_flagged(self):
        """Not a known container and not executable — still cannot be verified."""
        threat = check_magic_bytes(b"\x01\x02\x03\x04 nothing known", "pdf")
        assert threat is not None
        assert threat["type"] == "unknown_magic_bytes"


class TestZipBombGateFollowsContent:
    """Regression found in R-F3469's own verify pass 2.

    Allowing a mislabelled zip through opened a hole: scan_file gated the
    compression-bomb check on `claimed_type in ("zip","docx","xlsx","pptx")`, so a
    zip bomb renamed "invoice.pdf" would have skipped it entirely. Gating a
    security check on attacker-controlled input is the same flaw R-F3457 removed
    from the scope logic. The gate now follows the CONTENT.
    """

    @pytest.mark.asyncio
    async def test_zip_bomb_renamed_as_pdf_is_still_caught(self, tmp_path):
        bomb = tmp_path / "invoice.pdf"
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("big.txt", b"\x00" * (8 * 1024 * 1024))   # ~8MB -> tiny
        result = await scan_file(bomb, claimed_type="pdf")
        assert not result.safe, "a zip bomb renamed .pdf skipped the bomb check"
        assert ("compression" in result.reason.lower()
                or "ratio" in result.reason.lower())

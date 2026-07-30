"""R-F3457 — the content scanner blocked benign documents on sub-noise-floor byte patterns.

Live evidence (aria-intel, 2026-07-30 08:5xZ):

    R-F1853 read-document blocked unsafe upload (Offer Sikorsky UH-60A 29th of July 2026_draft.pdf):
      PDF embedded JS (HIGH); INT3 breakpoint chain (HIGH); Base6...
    R-F873 async read-document job b3394fe9ac10 failed: 422: document blocked by content scan

A real customer document was refused. Measured against 13 benign local files
(PDFs + images), the pre-fix scanner blocked **6 of 13**.

Root cause: ``check_embedded_scripts`` / ``check_suspicious_content`` matched raw
byte substrings over the WHOLE file with no file-type scoping. Several patterns sit
below the noise floor of ordinary binary content:

  * ``b"DDE"``               — 3 bytes, "XLSX DDE formula". Fired on a 434KB PDF and 4 images.
  * ``b"\\xcc\\xcc\\xcc\\xcc"``  — 4 repeated bytes, "INT3 breakpoint chain". Routine in image data.
  * ``b"\\x90\\x90\\x90\\x90"``  — same class, "NOP sled".
  * ``b"/JS"``               — 3 bytes, unanchored, so it also matched inside compressed streams.
  * ``c2g`` (base64 "sh")    — a 3-char case-insensitive trigram; appears in almost any long base64 run.

Why the existing R-F1131 guards never caught it: their "clean" fixtures are ~40-byte
hand-written strings (``b"%PDF-1.4\\n1 0 obj\\n/Type /Catalog\\nendobj"``) with no
compressed stream, no image data — nothing that could contain binary noise. A guard
whose negative fixture cannot contain the defect cannot fail. So this file BUILDS the
condition: real zlib-compressed streams carrying the exact adversarial byte runs.

The tests below are two-sided on purpose. Removing false positives must not weaken
detection, so every benign assertion is paired with a malicious one.
"""
from __future__ import annotations

import base64
import io
import zlib
import zipfile

import pytest

from aria_service.intel.content_scanner import (
    check_embedded_scripts,
    check_suspicious_content,
    scan_bytes,
)

# ── Fixtures: benign files that CONTAIN the adversarial byte runs ────────────

# The exact byte sequences that produced the false positives in production.
_NOISE = (
    b"DDE"                      # bare 3-byte "XLSX DDE formula"
    + b"\xcc" * 8               # INT3 chain
    + b"\x90" * 8               # NOP sled
    + b"/JScript-ish/JS0"       # unanchored /JS
    + b"c2g" + b"TVp"           # degenerate base64 trigrams
)


def _benign_pdf(noise: bytes = _NOISE, pad: int = 24_000) -> bytes:
    """A structurally valid PDF whose stream carries raw binary noise.

    This is what a real PDF looks like to a byte-substring scanner: the object
    dictionaries are plaintext, and the page content is a binary blob whose bytes
    are effectively random. No JavaScript, no actions — genuinely benign.

    The noise is embedded RAW (not zlib-compressed) because that is what the
    scanner actually sees on disk: already-compressed image data (DCTDecode JPEG,
    embedded font programs) is passed through into the stream verbatim. An earlier
    draft of this fixture compressed the noise, which hid the very bytes under
    test — the fixture was green because the defect could not reach it.
    """
    payload = noise + zlib.compress(bytes(range(256)) * (pad // 256), 9) + noise
    return (
        b"%PDF-1.7\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length " + str(len(payload)).encode() + b" >>\n"
        b"stream\n" + payload + b"\nendstream\nendobj\n"
        b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )


def _benign_png(noise: bytes = _NOISE) -> bytes:
    """A PNG-headered blob carrying the same noise in its IDAT-ish payload."""
    return b"\x89PNG\r\n\x1a\n" + b"IHDR" + noise + zlib.compress(bytes(range(256)) * 64, 9)


# ── The capability test: the operator's actual path ─────────────────────────

class TestBenignDocumentsAreNotBlocked:
    """scan_bytes() is the exact call read-document makes (routes/aria.py:13813).

    Pre-fix these assert False — that is the point (§3c: run before the fix).
    """

    @pytest.mark.asyncio
    async def test_benign_pdf_with_binary_noise_is_allowed(self):
        result = await scan_bytes(
            _benign_pdf(), claimed_type="pdf", source_name="Offer_UH-60A_draft.pdf",
        )
        assert result.safe, (
            "A benign PDF whose compressed stream contains ordinary binary noise "
            f"was blocked. reason={result.reason!r}"
        )

    @pytest.mark.asyncio
    async def test_benign_png_with_binary_noise_is_allowed(self):
        result = await scan_bytes(
            _benign_png(), claimed_type="png", source_name="diagram.png",
        )
        assert result.safe, f"A benign PNG was blocked. reason={result.reason!r}"

    def test_no_threat_from_binary_noise_in_a_pdf(self):
        """The unit-level property: noise inside a PDF yields NO threat."""
        threats = check_embedded_scripts(_benign_pdf())
        assert threats == [], f"false positives on a benign PDF: {threats}"

    def test_bare_dde_trigram_is_not_a_threat(self):
        """`DDE` as a raw 3-byte substring is not evidence of anything."""
        assert check_embedded_scripts(_benign_pdf(noise=b"BIDDER ADDED DDE")) == []

    def test_short_byte_runs_are_not_shellcode(self):
        """4 repeated bytes is noise; it must not read as a NOP sled / INT3 chain."""
        assert check_embedded_scripts(_benign_pdf(noise=b"\xcc" * 4 + b"\x90" * 4)) == []

    def test_degenerate_base64_trigram_is_not_a_threat(self):
        """`c2g` appears in almost any long base64 run — it cannot mean 'shell script'."""
        # A long, valid base64 run that genuinely contains the trigram but decodes
        # to nothing executable. Under the old `c2g` pattern this was a HIGH threat.
        blob = b"A" * 48 + b"c2g" + b"B" * 48
        assert b"c2g" in blob.lower(), "fixture must actually contain the trigram"
        assert check_suspicious_content(_benign_pdf(noise=blob)) == []
        assert check_suspicious_content(blob) == []


# ── The paired regression guards: detection MUST NOT weaken ─────────────────

class TestRealThreatsStillBlocked:
    """Every false-positive removal above is paired with a true-positive here."""

    @pytest.mark.asyncio
    async def test_eicar_still_blocked(self):
        eicar = base64.b64decode(
            "WDVPIVAlQEFQWzRcUFpYNTQoUF4pN0NDKTd9JEVJQ0FSLVNUQU5EQVJELUFOVElWSVJVUy1URVNULUZJTEUhJEgrSCo="
        )
        result = await scan_bytes(eicar, claimed_type="txt", source_name="eicar.txt")
        assert not result.safe and "EICAR" in result.reason

    def test_pdf_javascript_action_still_detected(self):
        data = (
            b"%PDF-1.7\n1 0 obj\n<< /Type /Action /S /JavaScript "
            b"/JS (app.alert\\(1\\);) >>\nendobj\n%%EOF\n"
        )
        threats = check_embedded_scripts(data)
        assert any("JavaScript" in t["detail"] or "JS" in t["detail"] for t in threats), threats

    def test_pdf_launch_action_still_detected(self):
        data = b"%PDF-1.7\n1 0 obj\n<< /S /Launch /F (cmd.exe) >>\nendobj\n%%EOF\n"
        assert any("launch" in t["detail"].lower() for t in check_embedded_scripts(data))

    def test_docx_vba_macro_still_detected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("word/vbaProject.bin", "\x00\x01macro payload")
        assert any("VBA" in t["detail"] for t in check_embedded_scripts(buf.getvalue()))

    def test_xlsx_ddeauto_still_detected(self):
        """DDEAUTO is the real dangerous token — it must survive the DDE removal."""
        data = b"PK\x03\x04" + b"x" * 32 + b"DDEAUTO c:\\\\windows\\\\system32\\\\cmd.exe"
        assert any("DDE" in t["detail"] for t in check_embedded_scripts(data))

    def test_base64_pe_still_detected(self):
        data = b"payload=TVqQAAMAAAAEAAAA//8AALgAAAAAAAAAQAAAAAAAAAAA"
        assert any("PE" in t["detail"] for t in check_suspicious_content(data))

    def test_base64_elf_still_detected(self):
        data = b"blob=f0VMRgIBAQAAAAAAAAAAAAACADAAAQAAAAAAAAAAAAAAAAAAAA"
        assert any("ELF" in t["detail"] for t in check_suspicious_content(data))

    def test_base64_shell_script_still_detected(self):
        data = b"payload=IyEvYmluL2Jhc2gKcm0gLXJmIC8KZWNobyBwd25lZAo=AAAAAAAA"
        assert any("shell" in t["detail"].lower() for t in check_suspicious_content(data))

    def test_long_nop_sled_in_unknown_binary_still_detected(self):
        """A genuine sled (16+ bytes) in an untyped blob is still shellcode."""
        data = b"\x00\x01\x02" + b"\x90" * 32 + b"\xeb\xfe"
        assert any("NOP" in t["detail"] for t in check_embedded_scripts(data))

    def test_polyglot_png_wrapped_pdf_still_scanned_as_pdf(self):
        """Regression found in R-F3457's own verify pass 2.

        Scoping on the LEADING magic byte alone let a polyglot evade every PDF
        rule: readers accept "%PDF" anywhere in the first 1024 bytes, so a PNG
        header prepended to a malicious PDF sniffed as an inert image. Scoping
        must look inside the prefix window, not just at offset 0.
        """
        data = (
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
            + b"%PDF-1.7\n1 0 obj\n<< /S /JavaScript /JS (evil()) >>\nendobj\n%%EOF\n"
        )
        threats = check_embedded_scripts(data)
        assert any("JavaScript" in t["detail"] or "JS" in t["detail"] for t in threats), (
            f"polyglot escaped the PDF rules: {threats}"
        )

    def test_unidentified_blob_gets_every_rule_family(self):
        """Unknown must be the strictest case, never the most permissive."""
        from aria_service.intel.content_scanner import _sniff_container
        scopes = _sniff_container(b"\x00\x01\x02\x03 not a known container")
        assert {"pdf", "zip", "binary"} <= scopes, scopes

    @pytest.mark.asyncio
    async def test_malicious_pdf_mislabelled_as_png_still_blocked(self):
        """Scoping must key off SNIFFED type, not the attacker-controlled claim.

        If the scope were taken from `claimed_type`, an attacker would evade every
        PDF rule by calling their PDF a PNG.
        """
        data = (
            b"%PDF-1.7\n1 0 obj\n<< /S /JavaScript /JS (evil()) >>\nendobj\n%%EOF\n"
        )
        result = await scan_bytes(data, claimed_type="png", source_name="not_really.png")
        assert not result.safe, "a mislabelled malicious PDF escaped the scan"

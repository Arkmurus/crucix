"""R-F415 — magic-byte file-type detection on uploaded documents.

Pre-R-F415 the /read-document upload path routed parsers by the
attacker-controlled `mimetype` header and `filename` extension. An
attacker could send a .pdf-named blob with `Content-Type:
application/pdf` whose body was actually a ZIP or arbitrary data;
the PDF parser would choke (OOM / CPU burn / silent corruption).
Routine OWASP mime-confusion surface.

R-F415 adds aria_service/intel/file_type_detector.py:
  - detect_file_type(raw_bytes) → "pdf" | "docx" | "xlsx" | "pptx" |
    "zip" | "png" | "jpeg" | "gif" | "webp" | "bmp" | "txt" | "unknown"
  - file_type_matches_claim(detected, mime, filename) → bool

The upload path now ROUTES BY DETECTED TYPE (not the claimed strings)
and rejects "unknown" bytes early so they never reach the parsers.
"""
from __future__ import annotations


# ───────────────────────── Magic-byte signatures ─────────────────────────────


def test_rf415_detects_pdf_by_magic_bytes():
    """%PDF- in the first 5 bytes → "pdf"."""
    from aria_service.intel.file_type_detector import detect_file_type
    assert detect_file_type(b"%PDF-1.7\nfake content") == "pdf"


def test_rf415_detects_png_by_magic_bytes():
    from aria_service.intel.file_type_detector import detect_file_type
    assert detect_file_type(b"\x89PNG\r\n\x1a\nbody") == "png"


def test_rf415_detects_jpeg_by_magic_bytes():
    from aria_service.intel.file_type_detector import detect_file_type
    assert detect_file_type(b"\xff\xd8\xff\xe0body") == "jpeg"


def test_rf415_detects_gif_both_versions():
    from aria_service.intel.file_type_detector import detect_file_type
    assert detect_file_type(b"GIF87abody") == "gif"
    assert detect_file_type(b"GIF89abody") == "gif"


def test_rf415_detects_webp_via_riff_plus_webp_tag():
    """WebP magic is RIFF....WEBP — both pieces required (RIFF alone
    is also WAV / AVI / etc)."""
    from aria_service.intel.file_type_detector import detect_file_type
    assert detect_file_type(b"RIFF\x00\x00\x00\x00WEBPbody") == "webp"
    # RIFF without WEBP tag — not WebP
    assert detect_file_type(b"RIFF\x00\x00\x00\x00WAVEbody") != "webp"


def test_rf415_detects_bmp():
    from aria_service.intel.file_type_detector import detect_file_type
    assert detect_file_type(b"BMbody") == "bmp"


# ───────────────────────── ZIP-shaped office formats ─────────────────────────


def test_rf415_detects_docx_via_zip_internals():
    """A ZIP with `word/document.xml` inside is a DOCX."""
    import io, zipfile
    from aria_service.intel.file_type_detector import detect_file_type
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<document/>")
        zf.writestr("[Content_Types].xml", "")
    assert detect_file_type(buf.getvalue()) == "docx"


def test_rf415_detects_xlsx_via_zip_internals():
    import io, zipfile
    from aria_service.intel.file_type_detector import detect_file_type
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", "<workbook/>")
    assert detect_file_type(buf.getvalue()) == "xlsx"


def test_rf415_detects_pptx_via_zip_internals():
    import io, zipfile
    from aria_service.intel.file_type_detector import detect_file_type
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/presentation.xml", "<presentation/>")
    assert detect_file_type(buf.getvalue()) == "pptx"


def test_rf415_generic_zip_when_no_office_markers():
    import io, zipfile
    from aria_service.intel.file_type_detector import detect_file_type
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hi")
    assert detect_file_type(buf.getvalue()) == "zip"


def test_rf415_corrupt_zip_falls_back_to_zip():
    """A truncated ZIP (magic bytes present but archive malformed)
    falls back to "zip" not "unknown" — caller still gets routing."""
    from aria_service.intel.file_type_detector import detect_file_type
    # PK\x03\x04 header + garbage
    assert detect_file_type(b"PK\x03\x04" + b"\x00" * 50 + b"corrupted") == "zip"


# ───────────────────────── txt heuristic ─────────────────────────────────────


def test_rf415_text_recognised_when_mostly_printable():
    from aria_service.intel.file_type_detector import detect_file_type
    assert detect_file_type(b"Hello, this is plain text.\nLine 2") == "txt"


def test_rf415_binary_garbage_is_unknown():
    """Random binary that isn't a known signature and isn't printable
    must return "unknown" — caller refuses parsing."""
    from aria_service.intel.file_type_detector import detect_file_type
    garbage = bytes(range(256)) * 4
    assert detect_file_type(garbage) == "unknown"


def test_rf415_empty_bytes_is_unknown():
    from aria_service.intel.file_type_detector import detect_file_type
    assert detect_file_type(b"") == "unknown"


# ───────────────────────── Mismatch detection ────────────────────────────────


def test_rf415_claim_matches_when_detected_aligns_with_mime():
    from aria_service.intel.file_type_detector import file_type_matches_claim
    assert file_type_matches_claim("pdf", "application/pdf", "doc.pdf") is True
    assert file_type_matches_claim("docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "report.docx") is True


def test_rf415_claim_disagrees_when_attacker_renames_zip_as_pdf():
    """Classic attack: ZIP body with .pdf filename + application/pdf
    mime. file_type_matches_claim must return False so the caller
    routes by detected ("zip"), not the claimed PDF."""
    from aria_service.intel.file_type_detector import file_type_matches_claim
    # Detected says ZIP; operator claims PDF mime + .pdf filename
    assert file_type_matches_claim("zip", "application/pdf", "evil.pdf") is False


def test_rf415_unknown_detection_always_disagrees():
    """Even if the operator's mime/filename are 'consistent' with
    each other, an "unknown" detection means we don't trust them."""
    from aria_service.intel.file_type_detector import file_type_matches_claim
    assert file_type_matches_claim("unknown", "application/pdf", "x.pdf") is False


def test_rf415_no_claim_is_trivially_consistent():
    """When the operator provides neither mime nor filename, there's
    nothing to disagree with — caller routes by detected type."""
    from aria_service.intel.file_type_detector import file_type_matches_claim
    assert file_type_matches_claim("pdf", "", "") is True


def test_rf415_either_mime_or_extension_matching_is_enough():
    """A correct mime with wrong extension still validates (mime wins).
    A correct extension with no/empty mime also validates."""
    from aria_service.intel.file_type_detector import file_type_matches_claim
    assert file_type_matches_claim("pdf", "application/pdf", "no-extension") is True
    assert file_type_matches_claim("pdf", "", "report.pdf") is True

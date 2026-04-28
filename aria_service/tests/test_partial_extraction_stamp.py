"""Test the partial-extraction stamp on the document-ingest path.

Past incident 2026-04-28: ARIA confidently asserted "GESPI is NOT
listed in Annex 1" of a signed agency agreement. The PDF parser had
silently truncated before Annex 1 and ARIA had no signal that the
extraction was partial. The stamp prevents the negative-claim
fabrication by forcing a banner into the [ATTACHED DOCUMENT] block
whenever the parser dropped content.
"""

from aria_service.routes.aria import _stamp_partial_extraction


def test_no_stamp_when_full_extraction():
    """No banner when the parser captured everything."""
    out = _stamp_partial_extraction("Full document text.", "")
    assert out == "Full document text."
    assert "PARTIAL EXTRACTION" not in out


def test_no_stamp_when_summary_empty():
    """Empty summary signals 'no truncation' even if extracted is non-empty."""
    out = _stamp_partial_extraction("Some text", "")
    assert out == "Some text"


def test_no_stamp_when_extracted_empty():
    """Empty extracted text — nothing to stamp."""
    out = _stamp_partial_extraction("", "PyMuPDF: 100 of 200 pages")
    assert out == ""


def test_stamp_when_truncated():
    """Banner is prepended verbatim when truncation occurred."""
    out = _stamp_partial_extraction(
        "Page 1 content...",
        "PyMuPDF returned 200 pages totalling 1000000 chars; only the first 500000 chars are below",
    )
    assert out.startswith("[!PARTIAL EXTRACTION")
    assert "200 pages" in out
    assert "1000000 chars" in out
    assert "Page 1 content..." in out


def test_stamp_includes_negative_claim_guard():
    """Banner explicitly forbids 'X is NOT in document' assertions."""
    out = _stamp_partial_extraction("body", "DOCX truncated")
    # Core guard language — verbatim from the helper
    assert "MUST NOT assert" in out
    assert "absent from the document" in out
    assert "not present in the extracted portion" in out
    assert "Negative claims" in out


def test_stamp_calls_out_annex_risk():
    """Banner names the categories most likely to live past truncation."""
    out = _stamp_partial_extraction("body", "PyMuPDF: 50 of 200 pages")
    for keyword in ("annexes", "schedules", "signature pages", "appendices"):
        assert keyword in out, f"missing keyword: {keyword}"


def test_stamp_lands_at_top_of_text():
    """Banner is prepended (not appended) so any downstream slice keeps it."""
    out = _stamp_partial_extraction("BODY-AT-TOP", "DOCX truncated")
    assert out.index("[!PARTIAL EXTRACTION") < out.index("BODY-AT-TOP")


def test_constitution_clause_12_has_partial_guard():
    """Clause 12 of the constitution must mention the new banner + guard
    language so the LLM honours it. This is the safety-net check that
    prevents a future refactor from silently dropping the rule."""
    from aria_service import aria_engine
    import inspect
    src = inspect.getsource(aria_engine)
    assert "PARTIAL EXTRACTION DISCIPLINE" in src
    assert "[!PARTIAL EXTRACTION" in src
    assert "not present in the extracted portion" in src
    # Past-incident anchor — keeps the lesson sticky.
    assert "GESPI" in src and "Annex 1" in src

"""
R-F1311 capability tests — async OCR + bumped char limits + max_chars override.

Tests:
  1. Async OCR: POST /api/aria/ocr with async=true returns job_id immediately
  2. Async OCR: polling /api/aria/ocr/result/{job_id} returns status
  3. max_chars override: /extract-document-json respects max_chars param
  4. Bumped cap: 150K doc passes through without truncation (was 60K)
  5. Vision max_tokens: verify ocr.py and document_reader.py have bumped values
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Test 1: Async OCR returns job_id immediately ─────────────────────────────

@pytest.mark.asyncio
async def test_async_ocr_returns_job_id():
    """POST /api/aria/ocr with async=true must return a job_id immediately
    without blocking on the OCR pipeline."""
    from aria_service.routes.aria import ocr_ep
    from fastapi import Request

    # Build a mock request with async=true
    mock_request = MagicMock(spec=Request)
    mock_request.json = AsyncMock(return_value={
        "image": "AAAA",  # tiny valid base64 (decodes to 3 bytes)
        "filename": "test.jpg",
        "context": "test image",
        "async": True,
    })
    mock_request.app = MagicMock()
    mock_request.app.state = MagicMock()

    result = await ocr_ep(mock_request)
    assert isinstance(result, dict)
    assert result.get("async") is True
    assert isinstance(result.get("job_id"), str)
    assert len(result["job_id"]) == 12
    assert result.get("status") == "processing"
    assert "poll_url" in result
    assert "/api/aria/ocr/result/" in result["poll_url"]


# ── Test 2: Async OCR polling returns status ─────────────────────────────────

@pytest.mark.asyncio
async def test_async_ocr_polling():
    """After submitting an async OCR job, polling the result endpoint must
    return a valid status (processing, done, or failed)."""
    from aria_service.routes.aria import ocr_ep, ocr_result_ep
    from fastapi import Request

    mock_request = MagicMock(spec=Request)
    mock_request.json = AsyncMock(return_value={
        "image": "AAAA",
        "filename": "test.jpg",
        "context": "test",
        "async": True,
    })
    mock_request.app = MagicMock()
    mock_request.app.state = MagicMock()

    # Submit async job
    submit_result = await ocr_ep(mock_request)
    job_id = submit_result["job_id"]

    # Poll immediately — should be "processing" or "done"
    poll_result = await ocr_result_ep(job_id)
    assert isinstance(poll_result, dict)
    assert poll_result.get("job_id") == job_id
    assert poll_result.get("status") in ("processing", "done", "failed")


# ── Test 3: max_chars override on /extract-document-json ─────────────────────

def test_extract_document_json_respects_max_chars_override():
    """The /extract-document-json endpoint must accept a max_chars parameter
    that overrides the default cap."""
    from aria_service.routes.aria import _capped_doc_text
    from types import SimpleNamespace

    # A 50K doc with a 10K override should truncate
    big = "D" * 50000
    result = SimpleNamespace(
        text=big, method="PyMuPDF", pages_extracted=10, total_pages=10,
    )
    out = _capped_doc_text(result, max_chars=10000)
    assert out["truncated"] is True
    assert out["total_chars"] == 50000
    assert out["returned_chars"] < 50000
    assert "[!PARTIAL EXTRACTION" in out["text"]

    # Same doc with no override (default 200K) should NOT truncate
    out2 = _capped_doc_text(result)
    assert out2["truncated"] is False
    assert out2["text"] == big


# ── Test 4: 150K doc passes through without truncation ───────────────────────

def test_150k_doc_passes_through_new_cap():
    """With the cap raised to 200K, a 150K document must pass through
    without truncation. This was the live failure class (R-F849)."""
    from aria_service.routes.aria import _capped_doc_text, _EXTRACT_DOC_MAX_CHARS
    from types import SimpleNamespace

    assert _EXTRACT_DOC_MAX_CHARS >= 200000, (
        f"Cap must be at least 200K, got {_EXTRACT_DOC_MAX_CHARS}"
    )

    big = "C" * 150000
    result = SimpleNamespace(
        text=big, method="PyMuPDF", pages_extracted=30, total_pages=30,
    )
    out = _capped_doc_text(result)
    assert out["truncated"] is False
    assert out["text"] == big
    assert out["total_chars"] == 150000
    assert out["returned_chars"] == 150000
    assert "PARTIAL EXTRACTION" not in out["text"]


# ── Test 5: Vision max_tokens bumped ─────────────────────────────────────────

def test_vision_max_tokens_bumped():
    """Verify the vision max_tokens values have been bumped in ocr.py and
    document_reader.py."""
    import ast
    import re

    # Check ocr.py for max_tokens: 4096 (was 3000)
    with open("aria_service/intel/ocr.py", "r", encoding="utf-8") as f:
        ocr_source = f.read()
    # Find all "max_tokens": NUMBER occurrences
    ocr_tokens = [int(m) for m in re.findall(r'"max_tokens":\s*(\d+)', ocr_source)]
    assert all(t >= 4096 for t in ocr_tokens), (
        f"All ocr.py max_tokens should be >= 4096, got {ocr_tokens}"
    )

    # Check document_reader.py for max_tokens: 6000 (was 4096) and 4096 (was 2000)
    with open("aria_service/intel/document_reader.py", "r", encoding="utf-8") as f:
        dr_source = f.read()
    dr_tokens = [int(m) for m in re.findall(r"max_tokens=(\d+)", dr_source)]
    # The vision strategy should have 6000, image strategy 4096
    assert any(t >= 6000 for t in dr_tokens), (
        f"document_reader.py should have max_tokens >= 6000, got {dr_tokens}"
    )
    assert any(t >= 4096 for t in dr_tokens), (
        f"document_reader.py should have max_tokens >= 4096, got {dr_tokens}"
    )


# ── Test 6: Contract intelligence synthesis cap bumped ───────────────────────

def test_contract_synthesis_cap_bumped():
    """Verify contract_intelligence.py synthesis cap is bumped to 200K."""
    with open("aria_service/intel/contract_intelligence.py", "r", encoding="utf-8") as f:
        source = f.read()
    assert "[:200000]" in source, (
        "contract_intelligence.py must use [:200000] for document_excerpt"
    )

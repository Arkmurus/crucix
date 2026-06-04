"""R-F849 — /extract-document* must not silently truncate a contract.

Symptom (operator, 2026-05-24): "Aria, do an analysis of this contract"
→ ARIA repeatedly reported it could not see / the contract was truncated.

Root cause: both /extract-document (multipart, the aria.html 📎 path) and
/extract-document-json returned ``result.text[:10000]`` with NO banner. A
real multi-page contract exceeds 10K chars (≈2.5K tokens); the tail —
annexes, payment schedules, signature page — was dropped silently. ARIA
then either reviewed a truncated contract or, downstream, reported "no
document". 10K is the SOLE binding truncation point: aria_engine builds
``user_prompt = message + context`` verbatim, so the attached-doc block is
NOT re-capped before the LLM.

Fix: cap raised to _EXTRACT_DOC_MAX_CHARS (60K, ~15K tokens — fits a full
contract inside DeepSeek's window alongside the 20K recall budget). When
the extract still exceeds the cap, _stamp_partial_extraction prepends the
[!PARTIAL EXTRACTION] banner so the chat LLM, the contract self-review
pass, and the user all know the tail is gone.

Unit: the _capped_doc_text contract.
Capability: the live HTTP endpoint returns the banner + raised cap.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from aria_service.routes.aria import _capped_doc_text, _EXTRACT_DOC_MAX_CHARS


def _result(text: str, *, method="PyMuPDF", pages=0, total=0):
    return SimpleNamespace(
        text=text, method=method, pages_extracted=pages, total_pages=total,
    )


# ── Unit: _capped_doc_text ────────────────────────────────────────────────

def test_cap_raised_above_legacy_10k():
    """The old silent cap was 10000. A contract just over that must now
    pass through whole — this is the literal regression that lost the
    contract tail."""
    body = "C" * 12000  # would have been chopped to 10000 pre-R-F849
    out = _capped_doc_text(_result(body))
    assert out["truncated"] is False
    assert out["text"] == body
    assert out["total_chars"] == 12000
    assert out["returned_chars"] == 12000
    assert "PARTIAL EXTRACTION" not in out["text"]


def test_no_truncation_under_cap():
    body = "Short contract body. Payment: 50% on signature, 50% on delivery."
    out = _capped_doc_text(_result(body))
    assert out["truncated"] is False
    assert out["text"] == body
    assert out["returned_chars"] == out["total_chars"] == len(body)


def test_truncation_stamps_banner_over_cap():
    """Past the cap → banner prepended + accurate char accounting."""
    body = "Z" * (_EXTRACT_DOC_MAX_CHARS + 5000)
    out = _capped_doc_text(_result(body, method="pdfplumber", pages=40, total=40))
    assert out["truncated"] is True
    assert out["text"].startswith("[!PARTIAL EXTRACTION")
    # total_chars reflects the WHOLE document, not the kept slice
    assert out["total_chars"] == _EXTRACT_DOC_MAX_CHARS + 5000
    # returned text = banner + exactly the cap worth of body
    assert out["text"].count("Z") == _EXTRACT_DOC_MAX_CHARS
    assert out["returned_chars"] == len(out["text"])
    # dropped_summary names method, both char counts and pages
    assert "pdfplumber" in out["text"]
    assert f"{_EXTRACT_DOC_MAX_CHARS:,}" in out["text"]
    assert f"{_EXTRACT_DOC_MAX_CHARS + 5000:,}" in out["text"]
    assert "40/40 pages" in out["text"]


def test_truncation_banner_carries_negative_claim_guard():
    """The banner must forbid 'X is absent' claims — the GESPI-in-Annex-1
    fabrication this whole mechanism exists to prevent."""
    body = "A" * (_EXTRACT_DOC_MAX_CHARS + 1)
    out = _capped_doc_text(_result(body))
    assert "MUST NOT assert" in out["text"]
    assert "not present in the extracted portion" in out["text"]


def test_pages_omitted_when_unknown():
    body = "B" * (_EXTRACT_DOC_MAX_CHARS + 1)
    out = _capped_doc_text(_result(body, total=0))
    assert out["truncated"] is True
    # When total_pages is unknown the summary ends at the char count — no
    # ", N/M pages" fragment is appended before the banner's body.
    assert "extracted chars. Content past" in out["text"]
    assert "0/0 pages" not in out["text"]


def test_empty_text_is_safe():
    out = _capped_doc_text(_result(""))
    assert out["text"] == ""
    assert out["truncated"] is False
    assert out["total_chars"] == 0


# ── Capability: the live HTTP endpoint ────────────────────────────────────

def _make_app():
    from fastapi import FastAPI
    from aria_service.routes import aria as aria_routes

    app = FastAPI()
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    app.state.llm_provider = MagicMock()
    return app


def test_endpoint_returns_banner_and_raised_cap(monkeypatch):
    """End-to-end: a 150K-char contract through /extract-document-json
    returns truncated=True, the banner, full total_chars, and more than
    the legacy 10K — proving the silent chop is gone."""
    from fastapi.testclient import TestClient
    from aria_service.intel import document_reader

    big = "X" * 250000  # R-F1311: bumped from 150K to exceed new 200K cap
    res = document_reader.ExtractionResult(
        text=big, method="PyMuPDF", confidence=0.92,
        pages_extracted=44, total_pages=44,
    )

    async def _fake_read_document(*a, **k):
        return res

    monkeypatch.setattr(document_reader, "read_document", _fake_read_document)

    client = TestClient(_make_app())
    r = client.post(
        "/api/aria/extract-document-json",
        json={"source": "/tmp/contract.pdf", "query": "review payment terms"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["truncated"] is True
    assert data["total_chars"] == 250000  # R-F1311: bumped from 150K to exceed new 200K cap
    assert data["text"].startswith("[!PARTIAL EXTRACTION")
    assert data["returned_chars"] > 10000  # legacy cap is gone
    assert "44/44 pages" in data["text"]
    # other fields still present (no key collision from the dict spread)
    assert data["method"] == "PyMuPDF"
    assert data["is_usable"] is True


def test_endpoint_small_doc_untouched(monkeypatch):
    from fastapi.testclient import TestClient
    from aria_service.intel import document_reader

    body = "Mutual NDA. Term: 2 years. Governing law: England & Wales."
    res = document_reader.ExtractionResult(text=body, method="PLAINTEXT", confidence=0.8)

    async def _fake_read_document(*a, **k):
        return res

    monkeypatch.setattr(document_reader, "read_document", _fake_read_document)

    client = TestClient(_make_app())
    r = client.post("/api/aria/extract-document-json", json={"source": "/tmp/nda.txt"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["truncated"] is False
    assert data["text"] == body
    assert "PARTIAL EXTRACTION" not in data["text"]


# ── Regression guard: the silent slice must never come back ───────────────

def test_no_legacy_10000_slice_in_source():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")
    assert "result.text[:10000]" not in src, (
        "R-F849 regression: a `result.text[:10000]` silent slice is back in "
        "an extract-document endpoint. Route text through _capped_doc_text "
        "so truncation carries the [!PARTIAL EXTRACTION] banner instead."
    )

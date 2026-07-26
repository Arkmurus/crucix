"""R-F3146 — the issuer's annual report was downloaded twice, then truncated past its
own balance sheet, and the resulting timeout rendered as an empty error.

MEASURED ON THE LIVE BABCOCK DD (dd_6e11c978dc86, 2026-07-26):

    issuer_financials: {"ok": false,
      "reason": "retrieved the document but could not parse it: ",
      "gates": {"provenance": true, "retrievable": true, "text_layer": false}}

G1 and G2 PASSED — production DID find and fetch Babcock's own annual report
(babcockinternational.com/.../Babcock-Annual-Report-and-Financial-Statements-2025.pdf).
Three separate defects then combined to produce "financial capacity UNRESOLVED":

1. THE REASON ENDS AT THE COLON because `str(asyncio.TimeoutError())` is the EMPTY
   STRING and the handler interpolated it blindly. The customer was told the document
   could not be parsed. It could not be parsed IN TIME — a different statement, with a
   different remedy, about a document that is probably perfectly readable.

2. IT TIMED OUT BECAUSE THE FILE WAS DOWNLOADED TWICE. G2 fetches the complete bytes
   into `_pre_bytes`, then `read_document(url)` re-resolves the URL and DOWNLOADS IT
   AGAIN (document_reader.py:1152-1170). A FTSE annual report is hundreds of pages and
   tens of MB, so the 45s budget paid for the same transfer twice before parsing began.
   Per §1 the fix is to remove the redundant fetch, NOT to raise the timeout.

3. EVEN ON SUCCESS THE FIGURES WERE SLICED AWAY. The prompt was built from
   `text[:120_000]` — the FIRST 120k characters, which in an annual report is the
   strategic report and governance sections. The consolidated balance sheet sits well
   past halfway. The model was asked for total assets while being shown the pages that
   do not contain them, and the route would report "figures incomplete" on a document
   that plainly contains them: a false data gap manufactured by our own slicing.
"""
import asyncio

import pytest

from aria_service.intel import financial_health as fh


ISSUER_URL = (
    "https://www.babcockinternational.com/wp-content/uploads/2025/07/"
    "Babcock-Annual-Report-and-Financial-Statements-2025.pdf"
)


def _annual_report_text(balance_at_fraction: float = 0.6, total: int = 400_000) -> str:
    """A document shaped like a real annual report: the balance sheet is NOT near the
    front."""
    head = "strategic report governance remuneration directors review " * 4000
    sheet = (
        "CONSOLIDATED BALANCE SHEET as at 31 March 2025 "
        "Non-current assets 4,110.0 Current liabilities 1,220.0 "
        "Total assets 5,231.0 Total liabilities 3,110.0 Net assets 2,121.0 "
        "Total equity 2,121.0 "
    )
    tail = "notes to the financial statements accounting policies " * 4000
    body = head + sheet + tail
    return body[:total] if len(body) > total else body


# ── defect 3: truncation ──────────────────────────────────────────────────────

def test_rf3146_excerpt_contains_the_balance_sheet():
    doc = _annual_report_text()
    assert len(doc) > fh._ISSUER_FIN_MAX_CHARS
    excerpt = fh._financial_excerpt(doc)
    assert "total assets" in excerpt.lower(), (
        "the excerpt must contain the figures the prompt asks for")
    assert "net assets" in excerpt.lower()
    assert "total liabilities" in excerpt.lower()


def test_rf3146_the_old_head_slice_really_did_lose_it():
    """Proves the defect was real, not theoretical."""
    doc = _annual_report_text()
    head = doc[:fh._ISSUER_FIN_MAX_CHARS]
    assert "total assets" not in head.lower(), (
        "if the head slice contains the balance sheet this fixture no longer models "
        "the defect — rebuild it before trusting the test above")


def test_rf3146_excerpt_respects_the_char_budget():
    doc = _annual_report_text(total=900_000)
    assert len(fh._financial_excerpt(doc)) <= fh._ISSUER_FIN_MAX_CHARS


def test_rf3146_short_documents_are_returned_whole():
    doc = "Total assets 10 Total liabilities 4 Net assets 6"
    assert fh._financial_excerpt(doc) == doc


def test_rf3146_no_anchor_falls_back_to_the_head():
    """Unchanged behaviour when there is nothing to anchor on."""
    doc = "lorem ipsum " * 40_000
    out = fh._financial_excerpt(doc)
    assert out == doc[:fh._ISSUER_FIN_MAX_CHARS]


def test_rf3146_empty_text_is_safe():
    assert fh._financial_excerpt("") == ""


def test_rf3146_picks_the_statement_not_a_passing_mention():
    """'total assets' also appears in the highlights near the front; the real statement
    is the anchor-dense region and must win."""
    mention = "Highlights: total assets grew this year. " + ("filler " * 20_000)
    sheet = ("CONSOLIDATED BALANCE SHEET Non-current assets 4,110.0 "
             "Total assets 5,231.0 Total liabilities 3,110.0 Net assets 2,121.0 "
             "Total equity 2,121.0 Current liabilities 1,220.0 ")
    doc = mention + ("filler " * 20_000) + sheet + ("tail " * 20_000)
    excerpt = fh._financial_excerpt(doc)
    assert "5,231.0" in excerpt, "the excerpt must include the actual balance sheet"


# ── defects 1 + 2: the capability path ────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, content=b"%PDF-1.7 fake"):
        self.status_code = status
        self.content = content


def _patch_fetch(monkeypatch, resp=None):
    """Stub the G2 fetch so the gate passes and we reach the parse step."""
    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, **kw):
            return resp or _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)


class _LLM:
    def __init__(self, text='{"currency":null}'):
        self._text = text
    async def complete(self, system, prompt, **kw):
        self.prompt = prompt
        return type("R", (), {"text": self._text})()


def _sources():
    return [{"url": ISSUER_URL, "title": "Annual Report and Financial Statements 2025"}]


def test_rf3146_capability_timeout_is_named_not_blank(monkeypatch):
    """THE LIVE SYMPTOM: 'could not parse it: ' with nothing after the colon."""
    _patch_fetch(monkeypatch)

    async def _slow(path, **kw):
        raise asyncio.TimeoutError()

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _slow)

    out = asyncio.run(fh.extract_issuer_financials(
        _sources(), "Babcock International Group plc", _LLM(), timeout=5.0))

    assert out["ok"] is False
    reason = out["reason"]
    assert not reason.rstrip().endswith(":"), (
        f"R-F3146 REGRESSION: an empty exception message again renders as a bare "
        f"colon: {reason!r}")
    assert "did not finish parsing" in reason, reason
    assert "NOT a statement about the filing" in reason, (
        "the reader must not be told the issuer's filing is unreadable when the limit "
        f"was ours: {reason!r}")
    assert out["gates"]["retrievable"] is True
    assert out["gates"]["text_layer"] is False


def test_rf3146_capability_document_is_not_downloaded_twice(monkeypatch):
    """THE ROOT CAUSE: read_document re-resolved the URL and fetched the file again."""
    _patch_fetch(monkeypatch, _Resp(content=b"%PDF-1.7 " + b"x" * 5000))
    seen = {}

    async def _reader(source, **kw):
        seen["source"] = source
        return type("R", (), {"text": _annual_report_text()})()

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _reader)

    asyncio.run(fh.extract_issuer_financials(
        _sources(), "Babcock International Group plc", _LLM(), timeout=30.0))

    src = str(seen.get("source") or "")
    assert src, "read_document was never called"
    assert not src.startswith("http"), (
        "R-F3146 REGRESSION: the parser is being handed the URL again, so a "
        f"hundreds-of-MB report is downloaded twice inside one budget: {src!r}")
    assert src.endswith(".pdf"), (
        f"read_document dispatches on extension — PDF bytes need a .pdf path: {src!r}")


def test_rf3146_capability_temp_file_is_cleaned_up(monkeypatch):
    """A DD per customer must not leave annual reports on disk."""
    _patch_fetch(monkeypatch, _Resp(content=b"%PDF-1.7 " + b"x" * 5000))
    seen = {}

    async def _reader(source, **kw):
        seen["source"] = source
        return type("R", (), {"text": _annual_report_text()})()

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _reader)

    asyncio.run(fh.extract_issuer_financials(
        _sources(), "Babcock International Group plc", _LLM(), timeout=30.0))

    import os
    assert not os.path.exists(seen["source"]), "temp document left behind"


def test_rf3146_capability_html_bytes_get_an_html_suffix(monkeypatch):
    """Not every issuer publishes a PDF; the extension must follow the magic bytes."""
    _patch_fetch(monkeypatch, _Resp(content=b"Total assets 5,231.0 " * 200))
    seen = {}

    async def _reader(source, **kw):
        seen["source"] = source
        return type("R", (), {"text": _annual_report_text()})()

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _reader)

    asyncio.run(fh.extract_issuer_financials(
        _sources(), "Babcock International Group plc", _LLM(), timeout=30.0))
    assert str(seen["source"]).endswith(".html")


def test_rf3146_capability_model_is_shown_the_balance_sheet(monkeypatch):
    """END TO END: the prompt handed to the model must contain the figures it is asked
    for — the whole point of the excerpt change."""
    _patch_fetch(monkeypatch, _Resp(content=b"%PDF-1.7 " + b"x" * 5000))

    async def _reader(source, **kw):
        return type("R", (), {"text": _annual_report_text()})()

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _reader)

    llm = _LLM()
    asyncio.run(fh.extract_issuer_financials(
        _sources(), "Babcock International Group plc", llm, timeout=30.0))

    assert hasattr(llm, "prompt"), "the model was never called"
    body = llm.prompt.lower()
    assert "total assets" in body and "net assets" in body, (
        "the model was asked for figures it was never shown — R-F3146's defect")


def test_rf3146_a_genuine_parse_error_still_says_so(monkeypatch):
    """Do not over-correct: a real parse failure must not be relabelled a timeout."""
    _patch_fetch(monkeypatch)

    async def _boom(source, **kw):
        raise ValueError("damaged xref table")

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _boom)

    out = asyncio.run(fh.extract_issuer_financials(
        _sources(), "Babcock International Group plc", _LLM(), timeout=30.0))
    assert "could not parse it" in out["reason"]
    assert "damaged xref table" in out["reason"]


def test_rf3146_exception_with_empty_message_falls_back_to_its_type(monkeypatch):
    """The general guard behind defect 1: never render a bare colon."""
    _patch_fetch(monkeypatch)

    class _Silent(Exception):
        pass

    async def _boom(source, **kw):
        raise _Silent()

    import aria_service.intel.document_reader as dr
    monkeypatch.setattr(dr, "read_document", _boom)

    out = asyncio.run(fh.extract_issuer_financials(
        _sources(), "Babcock International Group plc", _LLM(), timeout=30.0))
    assert "_Silent" in out["reason"], out["reason"]
    assert not out["reason"].rstrip().endswith(":")

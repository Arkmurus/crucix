"""R-F1675 — researcher.read_document must analyse chunks CONCURRENTLY.

This is the REAL WhatsApp doc-review path (routes/aria.py imports
researcher.read_document; text is extracted Node-side). A 62KB contract chunks
into ~18 windows; the old loop ran one LLM analysis per chunk SEQUENTIALLY
("# No limit") → 10-18 min, beyond the WA poll window → the all-day review
failure. R-F1675 pre-computes the analyses concurrently (bounded). These drive
the real function with mocked deps and assert concurrency actually happens.
"""
import asyncio
import inspect

import pytest

from aria_service.intel import researcher as R

# R-F3754/§16 — NOT inspect.getsource: it slices at the line numbers captured
# AT IMPORT, so an edit mid-run returns a DIFFERENT function's body, silently.
from ._source_probe import function_source


def test_read_document_uses_concurrent_gather():
    src = function_source(R, "read_document")
    assert "asyncio.gather(" in src, (
        "R-F1675: read_document must analyse chunks via concurrent gather, not a serial loop."
    )
    assert "Semaphore(" in src, "R-F1675: chunk concurrency must be bounded."


@pytest.mark.asyncio
async def test_read_document_analyses_chunks_concurrently(monkeypatch):
    import aria_service.intel.rag_store as RS
    import aria_service.intel.brain_hook as BH

    class _LLM:
        is_configured = True
        async def complete(self, *a, **k):
            class _R:
                text = "{}"
            return _R()

    state = {"cur": 0, "max": 0}

    async def fake_analyse(llm, doc_text, src, ekb, hyp):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.05)  # simulate the LLM call
        state["cur"] -= 1
        return {"facts": [], "hypotheses": []}

    async def fake_ingest(**k):
        return {"ingested": False, "reason": "test"}

    async def fake_process(parsed, src, hyp):
        return (0, 0)

    async def fake_store(*a, **k):
        return {}

    async def fake_absorb(**k):
        return {}

    async def fake_save(h):
        return None

    async def fake_load():
        return []

    monkeypatch.setattr(R, "_analyse_article", fake_analyse)
    monkeypatch.setattr(R, "_process_analysis", fake_process)
    monkeypatch.setattr(R, "_save_hypotheses", fake_save)
    monkeypatch.setattr(R, "_load_hypotheses", fake_load)
    monkeypatch.setattr(R, "search_knowledge", lambda q: [])
    monkeypatch.setattr(R, "_is_compliance_content", lambda *a: False)
    monkeypatch.setattr(R, "store_fact", fake_store)
    monkeypatch.setattr(RS, "ingest_document", fake_ingest)
    monkeypatch.setattr(BH, "absorb", fake_absorb)

    content = "Clause text for the agreement. " * 2000  # ~62KB -> many chunks
    res = await R.read_document(_LLM(), content, filename="test.pdf", source="pdf")

    assert res.get("chunks_processed", 0) > 1, "doc should split into multiple chunks"
    assert state["max"] > 1, (
        f"R-F1675: chunks analysed sequentially (max concurrency={state['max']})."
    )

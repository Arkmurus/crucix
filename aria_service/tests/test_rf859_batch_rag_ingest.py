"""R-F859 — batch web_search RAG ingest (FIX 1b of the event-loop wedge).

Pre-R-F859, web_search.py (R-F184 pay-once ingest) looped ingest_document once
per result → ~25 separate "Batches:1/1" sentence-transformer encodes per burst,
each a GIL-holding encode that starved the asyncio event loop (finding #1 wedge,
timed out the contract's read-document). add_search_results_batch collapses them
into ONE batched upsert (chromadb encodes the whole batch in a single pass).
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from aria_service.intel import rag_store as rs


def _patch_collection(monkeypatch):
    async def _ok():
        return True
    monkeypatch.setattr(rs, "_ensure_async", _ok)
    coll = MagicMock()
    monkeypatch.setattr(rs, "_documents_collection", coll)
    return coll


def test_one_upsert_for_many_results(monkeypatch):
    """5 results → exactly ONE upsert call (not 5). This is the encode-count
    reduction that fixes the wedge."""
    coll = _patch_collection(monkeypatch)
    items = [
        {"text": f"Defence tender {i} — " + ("detail " * 12),
         "source": f"web_search:src{i}", "source_type": "search_result",
         "title": f"t{i}", "url": f"http://x/{i}",
         "metadata": {"credibility_tier": 2, "language": "en"}}
        for i in range(5)
    ]
    n = asyncio.run(rs.add_search_results_batch(items))
    assert n == 5
    assert coll.upsert.call_count == 1, "must be ONE batched upsert, not per-result"
    _, kwargs = coll.upsert.call_args
    assert len(kwargs["ids"]) == 5
    assert len(kwargs["documents"]) == 5
    assert len(kwargs["metadatas"]) == 5


def test_skips_short_and_dedups(monkeypatch):
    coll = _patch_collection(monkeypatch)
    long_a = "Alpha contract " + ("x" * 60)
    items = [
        {"text": "too short", "source": "web_search:a"},          # < 40 → skip
        {"text": long_a, "source": "web_search:a"},               # kept
        {"text": long_a, "source": "web_search:a"},               # dup → deduped
    ]
    n = asyncio.run(rs.add_search_results_batch(items))
    assert n == 1
    _, kwargs = coll.upsert.call_args
    assert len(kwargs["ids"]) == 1


def test_empty_is_noop(monkeypatch):
    coll = _patch_collection(monkeypatch)
    assert asyncio.run(rs.add_search_results_batch([])) == 0
    coll.upsert.assert_not_called()


def test_no_none_metadata_values(monkeypatch):
    """chromadb rejects None metadata values — they must be filtered."""
    coll = _patch_collection(monkeypatch)
    items = [{"text": "Beta agreement " + ("y" * 60), "source": "web_search:b",
              "metadata": {"language": None, "credibility_tier": 1}}]
    asyncio.run(rs.add_search_results_batch(items))
    _, kwargs = coll.upsert.call_args
    assert all(v is not None for m in kwargs["metadatas"] for v in m.values())


def test_web_search_uses_batch_not_per_result():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "intel" / "web_search.py").read_text(encoding="utf-8")
    assert "add_search_results_batch(" in src, (
        "R-F859 regression: web_search no longer routes the R-F184 ingest "
        "through the batched helper — back to per-result encodes (wedge)."
    )

"""R-F663 — controller bookmarks + persistent state.

Phase B final slice. Thin per-topic state layer so the controller
(R-F662) can:
  - Debounce: skip topics studied within the last 5 minutes
  - See how many cycles a topic has been through
  - See cumulative chunks_seen

Verifies:
  - bookmarks CRUD (record_bookmark UPSERT + cycles_n increment +
    chunks_seen accumulation, get_bookmark, list_recent, since_last_studied)
  - learning_controller.study_topic uses the debounce
  - learning_controller.study_topic records a bookmark on success
  - OSS-only static guard
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time

import pytest

from aria_service.learning import bookmarks as bm
from aria_service.learning import learning_controller as lc


@pytest.fixture
def isolated_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    monkeypatch.setenv("ARIA_BOOKMARKS_DB_PATH", path)
    asyncio.run(bm._close_conn())
    yield path
    asyncio.run(bm._close_conn())
    try:
        os.unlink(path)
    except OSError:
        pass


# ── bookmarks CRUD ────────────────────────────────────────────────────────

def test_rf663_record_bookmark_inserts(isolated_db):
    async def runner():
        ok = await bm.record_bookmark("sanctions", chunks_count=10, agreement=0.8)
        row = await bm.get_bookmark("sanctions")
        return ok, row
    ok, row = asyncio.run(runner())
    assert ok is True
    assert row["topic"] == "sanctions"
    assert row["cycles_n"] == 1
    assert row["chunks_seen"] == 10
    assert row["last_agreement"] == pytest.approx(0.8)


def test_rf663_record_bookmark_upsert_increments(isolated_db):
    """Repeated record_bookmark calls UPSERT — cycles_n +=1 each time,
    chunks_seen accumulates."""
    async def runner():
        await bm.record_bookmark("sanctions", chunks_count=10, agreement=0.8)
        await bm.record_bookmark("sanctions", chunks_count=5, agreement=0.7)
        return await bm.get_bookmark("sanctions")
    row = asyncio.run(runner())
    assert row["cycles_n"] == 2
    assert row["chunks_seen"] == 15
    assert row["last_agreement"] == pytest.approx(0.7)


def test_rf663_get_bookmark_missing_topic(isolated_db):
    async def runner():
        return await bm.get_bookmark("never_seen")
    assert asyncio.run(runner()) is None


def test_rf663_record_bookmark_rejects_empty(isolated_db):
    async def runner():
        return await bm.record_bookmark("", chunks_count=5)
    assert asyncio.run(runner()) is False


def test_rf663_list_recent_order_by_last_studied(isolated_db):
    async def runner():
        await bm.record_bookmark("sanctions", chunks_count=10)
        await asyncio.sleep(0.01)
        await bm.record_bookmark("aviation", chunks_count=10)
        await asyncio.sleep(0.01)
        await bm.record_bookmark("compliance", chunks_count=10)
        return await bm.list_recent(limit=10)
    rows = asyncio.run(runner())
    topics = [r["topic"] for r in rows]
    assert topics == ["compliance", "aviation", "sanctions"]


def test_rf663_since_last_studied(isolated_db):
    async def runner():
        await bm.record_bookmark("sanctions", chunks_count=5)
        await asyncio.sleep(0.05)
        return await bm.since_last_studied("sanctions")
    s = asyncio.run(runner())
    assert s is not None
    assert 0.05 <= s <= 1.0


def test_rf663_since_last_studied_missing_topic(isolated_db):
    async def runner():
        return await bm.since_last_studied("never")
    assert asyncio.run(runner()) is None


# ── No cloud LLM ──────────────────────────────────────────────────────────

def test_rf663_no_llm_imports():
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "learning" / "bookmarks.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code = re.sub(r"#.*$", "", code, flags=re.MULTILINE)
    for pat in (r"\bimport\s+anthropic\b", r"\bimport\s+openai\b",
                 r"\bdeepseek[._]", r"\bllm\.complete\b"):
        m = re.search(pat, code, re.IGNORECASE)
        assert not m, f"R-F663: bookmarks must not import {pat!r}"


# ── learning_controller debounce + bookmark recording ────────────────────

def test_rf663_study_topic_debounces_recent_study(isolated_db, monkeypatch):
    """If a topic was studied less than 5 minutes ago, study_topic must
    short-circuit without re-running RAG or update_mastery."""
    monkeypatch.setenv(lc._ENABLE_ENV, "1")
    rag_called = {"n": 0}

    async def _rag_spy(*a, **kw):
        rag_called["n"] += 1
        return [{"score": 0.9}]

    async def _fake_update(*a, **kw):
        return None

    async def _fake_completion(*a, **kw):
        return {"complete": False, "blockers": []}

    from aria_service.intel import rag_store, student, topic_completion

    async def runner():
        # First study — should run and record a bookmark
        await bm.record_bookmark("sanctions", chunks_count=1, agreement=0.9)
        with patch_all(rag_store, _rag_spy, student, _fake_update,
                       topic_completion, _fake_completion):
            res = await lc.study_topic("sanctions")
        return res

    # Helper to patch all three at once
    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def patch_all(rs, rag, st, upd, tc, cmpl):
        with patch.object(rs, "search", rag), \
             patch.object(st, "update_mastery", upd), \
             patch.object(tc, "topic_completion", cmpl):
            yield

    result = asyncio.run(runner())
    assert result.get("debounced") is True
    assert "seconds_since_last_study" in result
    assert rag_called["n"] == 0, "RAG must NOT be hit when debounced"


def test_rf663_study_topic_records_bookmark(isolated_db, monkeypatch):
    """A successful study pass must persist a bookmark for future debounce."""
    monkeypatch.setenv(lc._ENABLE_ENV, "1")

    async def _fake_rag(*a, **kw):
        return [{"score": 0.85}, {"score": 0.8}]

    async def _fake_update(*a, **kw):
        return None

    async def _fake_completion(*a, **kw):
        return {"complete": False, "blockers": []}

    from aria_service.intel import rag_store, student, topic_completion
    from unittest.mock import patch

    async def runner():
        with patch.object(rag_store, "search", _fake_rag), \
             patch.object(student, "update_mastery", _fake_update), \
             patch.object(topic_completion, "topic_completion", _fake_completion):
            res = await lc.study_topic("fresh_topic")
        return res, await bm.get_bookmark("fresh_topic")

    result, mark = asyncio.run(runner())
    assert "debounced" not in result  # ran the full pass
    assert mark is not None
    assert mark["chunks_seen"] == 2
    assert mark["last_agreement"] == pytest.approx(0.825, abs=0.01)

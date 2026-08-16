"""R-F661 — failed-quiz → reading-list auto-enrollment.

Phase B slice (explicit operator override granted 2026-05-17). Slice 4
of the OSS-only learning-framework buildout. Closes the loop opened by
R-F664: failed quizzes now produce persistent reading-queue entries
that the Phase B controller (R-F662) will drain using local code only.

Verifies:
  - reading_queue.enqueue + dedup
  - reading_queue.pop_pending order (oldest-first)
  - reading_queue.mark_processed status transitions
  - student.self_quiz auto-enrols failed topics
  - reading_queue uses NO cloud-LLM imports
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from aria_service.learning import reading_queue as rq


@pytest.fixture
def isolated_db(monkeypatch):
    """Each test gets a fresh temp SQLite DB and a closed connection at
    setup so subsequent _ensure_conn opens this one."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    monkeypatch.setenv("ARIA_READING_QUEUE_DB_PATH", path)
    # Drop any prior connection so the fixture path is honoured
    asyncio.run(rq._close_conn())
    yield path
    asyncio.run(rq._close_conn())
    try:
        os.unlink(path)
    except OSError:
        pass


def test_rf661_enqueue_returns_id(isolated_db):
    async def runner():
        return await rq.enqueue("sanctions", source_question="What is OFAC?",
                                  source_case_id="case_001")
    new_id = asyncio.run(runner())
    assert isinstance(new_id, int)
    assert new_id > 0


def test_rf661_enqueue_empty_topic_returns_none(isolated_db):
    async def runner():
        return await rq.enqueue("", source_question="x")
    assert asyncio.run(runner()) is None


def test_rf661_dedup_same_topic_and_case_id(isolated_db):
    async def runner():
        a = await rq.enqueue("sanctions", source_case_id="c1")
        b = await rq.enqueue("sanctions", source_case_id="c1")
        return a, b
    a, b = asyncio.run(runner())
    assert a == b, "dedup must return the existing id, not insert a duplicate"


def test_rf661_no_dedup_on_different_case_id(isolated_db):
    async def runner():
        a = await rq.enqueue("sanctions", source_case_id="c1")
        b = await rq.enqueue("sanctions", source_case_id="c2")
        return a, b
    a, b = asyncio.run(runner())
    assert a != b


def test_rf661_pop_pending_oldest_first(isolated_db):
    async def runner():
        ids = []
        for i in range(3):
            ids.append(await rq.enqueue("sanctions", source_case_id=f"c{i}"))
            await asyncio.sleep(0.01)  # ensure enqueued_at differs
        items = await rq.pop_pending(limit=10)
        return ids, items
    ids, items = asyncio.run(runner())
    assert [i["id"] for i in items] == ids  # oldest first matches insertion order


def test_rf661_pop_pending_excludes_done(isolated_db):
    async def runner():
        i1 = await rq.enqueue("sanctions", source_case_id="c1")
        i2 = await rq.enqueue("aviation", source_case_id="c2")
        ok = await rq.mark_processed(i1, status="done")
        items = await rq.pop_pending(limit=10)
        return ok, items
    ok, items = asyncio.run(runner())
    assert ok is True
    topics = {it["topic"] for it in items}
    assert "aviation" in topics
    assert "sanctions" not in topics


def test_rf661_mark_processed_idempotent(isolated_db):
    async def runner():
        i1 = await rq.enqueue("sanctions", source_case_id="c1")
        a = await rq.mark_processed(i1, status="done")
        b = await rq.mark_processed(i1, status="done")
        return a, b
    a, b = asyncio.run(runner())
    assert a is True and b is True


def test_rf661_stats(isolated_db):
    async def runner():
        for i in range(3):
            await rq.enqueue("sanctions", source_case_id=f"c{i}")
        i1 = (await rq.pop_pending(limit=10))[0]["id"]
        await rq.mark_processed(i1, status="done")
        return await rq.stats()
    s = asyncio.run(runner())
    assert s["pending"] == 2
    assert s["done"] == 1


def test_rf661_can_re_enqueue_after_done(isolated_db):
    """Done entries DO NOT block a fresh enqueue — same case can be
    re-queued months later if mastery drifts back."""
    async def runner():
        i1 = await rq.enqueue("sanctions", source_case_id="c1")
        await rq.mark_processed(i1, status="done")
        i2 = await rq.enqueue("sanctions", source_case_id="c1")
        return i1, i2
    i1, i2 = asyncio.run(runner())
    assert i1 != i2  # new pending row


# ── No cloud LLM in reading_queue ─────────────────────────────────────────

def test_rf661_no_llm_imports():
    """OSS-only constraint: reading_queue.py must not import cloud-LLM SDKs."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "learning" / "reading_queue.py").read_text(encoding="utf-8")
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code_only = re.sub(r"#.*$", "", code_only, flags=re.MULTILINE)
    for pat in (r"\bimport\s+anthropic\b", r"\bimport\s+openai\b",
                 r"\bdeepseek[._]", r"\bllm\.complete\b"):
        m = re.search(pat, code_only, re.IGNORECASE)
        assert not m, f"R-F661: reading_queue must not import {pat!r}"


# ── student.self_quiz wires into the queue ────────────────────────────────

def test_rf661_self_quiz_failure_enrolls_to_queue(isolated_db, monkeypatch):
    """End-to-end: when student.self_quiz computes passed_quiz=False, the
    reading_queue must receive an enqueue call for each detected topic."""
    from aria_service.intel import student

    captured: list[dict] = []

    async def _fake_enqueue(topic, *, source_question="", source_case_id="",
                            reason="failed_quiz", notes=""):
        captured.append({
            "topic": topic,
            "case_id": source_case_id,
            "reason": reason,
        })
        return len(captured)

    monkeypatch.setattr(rq, "enqueue", _fake_enqueue)

    # Stub the library + reasoning_router to force a failed quiz
    from aria_service.intel import reasoning_library, reasoning_router

    async def _fake_load_index():
        return [{"id": "case_xyz", "ts_last_used": 0, "confidence_score": 0.5}]

    async def _fake_get_json(key):
        # Return case-shaped dict only for the case-blob key; the
        # quiz-history key wants a list (or None).
        if isinstance(key, str) and "case" in key:
            return {
                "id": "case_xyz",
                "question": "What is the OFAC SDN list?",
                "response": "OFAC's Specially Designated Nationals list...",
            }
        return None

    async def _fake_try_local(question, *, silent: bool = False,
                              exclude_topic: str | None = None):
        # R-F4052 (C-108) — the stub must accept the REAL signature. It was
        # written before `try_local_reasoning` grew `exclude_topic` (student.py
        # passes it), so every call raised TypeError and this test had been
        # permanently red — carrying no information about the behaviour it
        # names. Keyword-only, mirroring the real function, so the next
        # parameter added there fails loudly here rather than silently.
        # Return a divergent answer → similarity will be ~0 → passed_quiz=False
        return {"answered": True, "response": "Totally unrelated answer",
                "source": "local"}

    # Avoid touching the real mastery store
    isolated_mastery: dict = {}

    async def _fake_load_mastery():
        return isolated_mastery

    async def _fake_save_mastery():
        return None

    monkeypatch.setattr(reasoning_library, "_load_index", _fake_load_index)
    monkeypatch.setattr(student.rs, "get_json", _fake_get_json)
    monkeypatch.setattr(reasoning_router, "try_local_reasoning", _fake_try_local)
    monkeypatch.setattr(student, "_load_mastery", _fake_load_mastery)
    monkeypatch.setattr(student, "_save_mastery", _fake_save_mastery)

    async def runner():
        return await student.self_quiz(num_questions=1)

    result = asyncio.run(runner())
    # Quiz ran, didn't pass
    assert result.get("quizzed", 0) >= 1
    assert result.get("passed", 0) == 0
    # And the reading queue saw at least one enqueue
    assert captured, "R-F661: failed quiz did not enqueue any reading-list entry"
    assert all(c["reason"] == "failed_quiz" for c in captured)
    assert all(c["case_id"] == "case_xyz" for c in captured)

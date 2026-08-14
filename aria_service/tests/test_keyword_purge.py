"""Tests for the surgical keyword-purge functions across the absorbed-
knowledge stores. Uses asyncio.run() per repo convention (pytest-asyncio
not installed).
"""
from __future__ import annotations

import asyncio


# ── knowledge_facts ───────────────────────────────────────────────────────

def test_knowledge_purge_dry_run_does_not_mutate(monkeypatch):
    from aria_service.intel import knowledge as kb
    monkeypatch.setattr(kb, "_cache", {
        "facts": [
            {"id": "f1", "topic": "openclaw_gateway",
             "content": "OpenClaw v2026.3.13 has a bug.",
             "source": "brave_answer", "confidence": "CONFIRMED"},
            {"id": "f2", "topic": "iran_sanctions",
             "content": "OFAC added entities.", "source": "ofac.gov",
             "confidence": "CONFIRMED"},
        ],
        "queries": [], "learnings": [], "version": 1,
    })
    save_calls = []

    # **kwargs so this double tracks `_save`'s signature rather than pinning a
    # copy of it — R-F4022 added record=/kind=/structural= and a stub that
    # hardcodes the arg list breaks on every future change without ever
    # asserting anything about it.
    async def _fake_save(**_kw):
        save_calls.append(dict(kb._cache))
    monkeypatch.setattr(kb, "_save", _fake_save)

    async def run():
        return await kb.purge_by_keywords(["openclaw"], dry_run=True)
    out = asyncio.run(run())
    assert out["scanned"] == 2
    assert out["removed"] == 1
    assert out["dry_run"] is True
    assert len(kb._cache["facts"]) == 2  # unchanged
    assert save_calls == []  # _save not called


def test_knowledge_purge_removes_matching_facts(monkeypatch):
    from aria_service.intel import knowledge as kb
    monkeypatch.setattr(kb, "_cache", {
        "facts": [
            {"id": "f1", "topic": "openclaw_gateway",
             "content": "OpenClaw v2026.3.13 has a bug.",
             "source": "brave_answer", "confidence": "CONFIRMED"},
            {"id": "f2", "topic": "iran_sanctions",
             "content": "OFAC added entities.", "source": "ofac.gov",
             "confidence": "CONFIRMED"},
            {"id": "f3", "topic": "whatsapp_listener",
             "content": "Arkmurus platform restart procedure.",
             "source": "internal", "confidence": "ASSESSED"},
            {"id": "f4", "topic": "general",
             "content": "Baileys is the WhatsApp library.",
             "source": "internal", "confidence": "CONFIRMED"},
        ],
        "queries": [], "learnings": [], "version": 1,
    })
    saves = []

    # **kwargs so this double tracks `_save`'s signature rather than pinning a
    # copy of it — R-F4022 added record=/kind=/structural= and a stub that
    # hardcodes the arg list breaks on every future change without ever
    # asserting anything about it.
    async def _fake_save(**_kw):
        saves.append(True)
    monkeypatch.setattr(kb, "_save", _fake_save)

    async def run():
        return await kb.purge_by_keywords(
            ["openclaw", "arkmurus platform"], dry_run=False
        )
    out = asyncio.run(run())
    assert out["removed"] == 2
    remaining_topics = {f["topic"] for f in kb._cache["facts"]}
    assert "openclaw_gateway" not in remaining_topics
    assert "whatsapp_listener" not in remaining_topics
    assert "iran_sanctions" in remaining_topics
    assert "general" in remaining_topics
    assert len(saves) == 1  # _save called once


def test_knowledge_purge_empty_keywords_returns_zero(monkeypatch):
    from aria_service.intel import knowledge as kb
    monkeypatch.setattr(kb, "_cache", {
        "facts": [{"id": "f1", "topic": "x", "content": "y", "source": "z"}],
        "queries": [], "learnings": [], "version": 1,
    })
    monkeypatch.setattr(kb, "_save", lambda **_kw: _aio_noop())

    async def run_empty():
        return await kb.purge_by_keywords([], dry_run=False)
    out = asyncio.run(run_empty())
    assert out["scanned"] == 0
    assert out["removed"] == 0

    async def run_blank():
        return await kb.purge_by_keywords([""], dry_run=False)
    out = asyncio.run(run_blank())
    assert out["removed"] == 0


def test_knowledge_purge_case_insensitive(monkeypatch):
    from aria_service.intel import knowledge as kb
    monkeypatch.setattr(kb, "_cache", {
        "facts": [
            {"id": "f1", "topic": "X", "content": "Mentions OpenClaw inside.",
             "source": "z", "confidence": "ASSESSED"},
        ],
        "queries": [], "learnings": [], "version": 1,
    })

    async def _save(**_kw):
        pass
    monkeypatch.setattr(kb, "_save", _save)

    async def run():
        return await kb.purge_by_keywords(["OPENCLAW"], dry_run=False)
    out = asyncio.run(run())
    assert out["removed"] == 1
    assert kb._cache["facts"] == []


# ── reasoning_library ─────────────────────────────────────────────────────

def test_reasoning_library_purge_removes_matching_cases(monkeypatch):
    from aria_service.intel import reasoning_library as rl
    cases = {
        "rl:case:c1": {
            "question": "Why isn't my listener delivering?",
            "response": "OpenClaw gateway bug v2026.3.13.",
        },
        "rl:case:c2": {
            "question": "What is the latest sanctions news?",
            "response": "OFAC added new entities.",
        },
        "rl:case:c3": {
            "question": "How does Baileys work?",
            "response": "Arkmurus platform uses it under the hood.",
        },
    }

    class _RS:
        async def get_json(self, key):
            return cases.get(key)
        async def set_json(self, key, value, *, ex=None):
            cases[key] = value
        async def delete(self, key):
            cases.pop(key, None)
    monkeypatch.setattr(rl, "rs", _RS())
    monkeypatch.setattr(rl, "_case_key", lambda case_id: f"rl:case:{case_id}")

    fake_index = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]

    async def _load_index():
        return list(fake_index)

    async def _save_index():
        pass

    async def _load_meta():
        return {}

    async def _save_meta():
        pass
    monkeypatch.setattr(rl, "_load_index", _load_index)
    monkeypatch.setattr(rl, "_save_index", _save_index)
    monkeypatch.setattr(rl, "_load_meta", _load_meta)
    monkeypatch.setattr(rl, "_save_meta", _save_meta)
    monkeypatch.setattr(rl, "_index_cache_set", lambda new: None)

    async def run():
        return await rl.purge_by_keywords(
            ["openclaw", "arkmurus platform"], dry_run=False
        )
    out = asyncio.run(run())
    assert out["removed"] == 2
    # c2 (sanctions) is the only case without fabricated tokens — must remain.
    assert "rl:case:c2" in cases
    assert "rl:case:c1" not in cases
    assert "rl:case:c3" not in cases


def test_reasoning_library_purge_dry_run(monkeypatch):
    from aria_service.intel import reasoning_library as rl
    cases = {
        "rl:case:c1": {
            "question": "Why isn't my listener?",
            "response": "OpenClaw answer.",
        },
        "rl:case:c2": {
            "question": "Sanctions?",
            "response": "OFAC.",
        },
    }
    before = dict(cases)

    class _RS:
        async def get_json(self, key):
            return cases.get(key)
        async def set_json(self, key, value, *, ex=None):
            cases[key] = value
        async def delete(self, key):
            cases.pop(key, None)
    monkeypatch.setattr(rl, "rs", _RS())
    monkeypatch.setattr(rl, "_case_key", lambda i: f"rl:case:{i}")

    async def _load_index():
        return [{"id": "c1"}, {"id": "c2"}]
    monkeypatch.setattr(rl, "_load_index", _load_index)
    monkeypatch.setattr(rl, "_save_index", lambda: _aio_noop())
    monkeypatch.setattr(rl, "_load_meta", lambda: _aio_return({}))
    monkeypatch.setattr(rl, "_save_meta", lambda: _aio_noop())
    monkeypatch.setattr(rl, "_index_cache_set", lambda new: None)

    async def run():
        return await rl.purge_by_keywords(["openclaw"], dry_run=True)
    out = asyncio.run(run())
    assert out["removed"] == 1
    assert cases == before  # untouched


def test_reasoning_library_purge_empty_keywords_zero(monkeypatch):
    from aria_service.intel import reasoning_library as rl

    async def _load_index():
        return []
    monkeypatch.setattr(rl, "_load_index", _load_index)

    async def run():
        return await rl.purge_by_keywords([], dry_run=False)
    out = asyncio.run(run())
    assert out["removed"] == 0


# ── helpers ───────────────────────────────────────────────────────────────

async def _aio_noop():
    return None


async def _aio_return(value):
    return value

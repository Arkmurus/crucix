"""R-F1612 — deploy proprioception: boot records the build/deploy event to the
brain so ARIA has a persistent, RAG-queryable record of what she shipped.

Pairs with R-F1611 (live build_rev in self_introspect). Drives the real
_record_deploy_event() with the redis + brain sink FUNCTIONS patched, asserting
it writes the deploy-history entry + absorbs to the brain and never raises.
"""
from __future__ import annotations

import pytest

import aria_service.main as m
from aria_service.intel import redis_store as rs
from aria_service.intel import brain_hook as bh


@pytest.mark.asyncio
async def test_rf1612_records_to_history_and_brain(monkeypatch):
    monkeypatch.setattr(m, "ARIA_BUILD_REV", "R-F1612 · sha cafef00d", raising=False)
    pushed, absorbed = {}, {}

    async def _lpush(key, val):
        pushed["key"], pushed["val"] = key, val

    async def _ltrim(key, a, b):
        pushed["trim"] = (key, a, b)

    async def _absorb(**kw):
        absorbed.update(kw)

    monkeypatch.setattr(rs, "lpush", _lpush, raising=False)
    monkeypatch.setattr(rs, "ltrim", _ltrim, raising=False)
    monkeypatch.setattr(bh, "absorb", _absorb, raising=False)

    entry = await m._record_deploy_event()

    assert entry["build_rev"] == "R-F1612 · sha cafef00d"
    assert "booted_at" in entry
    assert pushed.get("key") == "crucix:aria:deploy:history"
    assert "cafef00d" in pushed.get("val", "")
    assert pushed.get("trim") == ("crucix:aria:deploy:history", 0, 49)
    assert "R-F1612" in absorbed.get("summary", "")
    assert absorbed.get("module") == "deploy"


@pytest.mark.asyncio
async def test_rf1612_never_raises_when_sinks_fail(monkeypatch):
    """If redis/brain are down, the recorder must NOT raise — it's a
    fire-and-forget boot task that must never break boot."""
    monkeypatch.setattr(m, "ARIA_BUILD_REV", "x", raising=False)

    async def _boom(*a, **k):
        raise RuntimeError("sink down")

    monkeypatch.setattr(rs, "lpush", _boom, raising=False)
    monkeypatch.setattr(rs, "ltrim", _boom, raising=False)
    monkeypatch.setattr(bh, "absorb", _boom, raising=False)

    entry = await m._record_deploy_event()  # must not raise
    assert entry["build_rev"] == "x"

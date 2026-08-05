"""R-F3735 — CAPABILITY: a chain read-out must not invent a genesis head.

Both chain modules answer "is the chain intact?" through a stats surface, and
both read the head with the NON-strict reader. That reader returns the GENESIS
constant on a store FAILURE as well as on a genuinely empty log — so the field
told a reader "the log is at genesis", i.e. empty or wiped, when the truth was
"the store is unreadable".

R-F3711/R-F3716 fixed exactly this collapse on the WRITE path of both chains and
left the READ-OUT still reporting it. On a surface whose entire job is integrity
reporting, a fabricated genesis is the worst possible answer: it is the one
reading that looks like catastrophic data loss.

Run: python -m pytest aria_service/tests/test_rf3735_chain_stats_no_fake_genesis.py -v
"""
from __future__ import annotations

import asyncio

import pytest


def test_audit_log_stats_does_not_report_a_fake_genesis(monkeypatch):
    from aria_service.intel import audit_log, redis_store

    async def _boom(*a, **k):
        raise redis_store.StoreReadError("no read connection")

    async def _empty(*a, **k):
        return []

    async def _zero(*a, **k):
        return 0

    monkeypatch.setattr(audit_log, "_read_head_hash", _boom)
    monkeypatch.setattr(redis_store, "lrange", _empty)
    monkeypatch.setattr(redis_store, "llen", _zero)

    out = asyncio.run(audit_log.stats())
    assert out.get("head_unreadable") is True, (
        "an unreadable store must be reported as unreadable"
    )
    assert not out.get("head_hash"), (
        f"reported head_hash={out.get('head_hash')!r} — a genesis constant here "
        f"reads as 'the audit log was wiped'"
    )


def test_mistake_ledger_stats_does_not_report_a_fake_genesis(monkeypatch):
    from aria_service.intel import mistake_ledger, redis_store

    async def _boom(*a, **k):
        raise mistake_ledger.MistakeChainUnreadable("store down")

    async def _empty(*a, **k):
        return []

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(mistake_ledger, "_read_head", _boom)
    monkeypatch.setattr(redis_store, "lrange", _empty)
    monkeypatch.setattr(redis_store, "get", _none)

    out = asyncio.run(mistake_ledger.stats())
    assert out.get("head_unreadable") is True
    assert not out.get("head_hash"), (
        f"reported head_hash={out.get('head_hash')!r} — a genesis constant here "
        f"reads as 'the mistake ledger was wiped'"
    )


def test_a_healthy_read_still_reports_the_real_head(monkeypatch):
    """The guard must not blind the normal path."""
    from aria_service.intel import mistake_ledger, redis_store

    async def _head(*a, **k):
        return "abc123def456"

    async def _empty(*a, **k):
        return []

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(mistake_ledger, "_read_head", _head)
    monkeypatch.setattr(redis_store, "lrange", _empty)
    monkeypatch.setattr(redis_store, "get", _none)

    out = asyncio.run(mistake_ledger.stats())
    assert out["head_hash"] == "abc123def456"
    assert out.get("head_unreadable") is False


def test_both_chains_got_the_same_remedy():
    """Two chains, one defect — they must not diverge in how they report it."""
    from aria_service.intel import audit_log, mistake_ledger
    from ._source_probe import function_source

    for mod in (audit_log, mistake_ledger):
        src = function_source(mod, "stats")
        assert "head_unreadable" in src, (
            f"{mod.__name__}.stats must distinguish unreadable from genesis"
        )
        assert "strict=True" in src

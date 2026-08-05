"""R-F3716/R-F3717 — CAPABILITY: the last two read-then-write clobbers.

Both are the R-F2664 class: `await rs.get(...) or <default>` treats a STORE
FAILURE exactly like an absent value, because redis_store returns None on error.
The code then writes the default back, destroying what was there.

  R-F3716  mistake_ledger._read_head — the exact sibling of the audit-chain
           defect fixed in R-F3711. A store blip made `record()` chain the next
           mistake to genesis, forking the hash chain. It also blinds
           `lookup_similar`, which asks "any past mistakes matching this
           signature?" before every task — so a forked chain is a lobotomy as
           well as an integrity break.
  R-F3717  honesty_judge.record_judgment — `get_json(...) or []` on a failed
           read, then insert one entry and write back, replacing an index of up
           to 500 judgments with a list of ONE. That index is the input to
           `get_honesty_stats`, which supplies 25% of the Phase A gate-#1
           composite — the signal R-F3696/R-F3701 was just spent enabling.

Run: python -m pytest aria_service/tests/test_rf3716_3717_chain_and_index_clobber.py -v
"""
from __future__ import annotations

import asyncio

import pytest

# §16/R-F3597 — NOT `inspect.getsource`. It slices the file at the line numbers
# captured AT IMPORT, so a peer commit landing mid-run returns a DIFFERENT
# function's body, silently (the wrong slice is still valid Python). These
# assertions read source on a tree a second agent commits to; resolve by NAME
# through the current AST instead.
from ._source_probe import function_source


# ══════════════════════════════════════════════════════════════════════════
# R-F3716 — the mistake-ledger hash chain
# ══════════════════════════════════════════════════════════════════════════

def test_a_store_failure_is_not_genesis(monkeypatch):
    from aria_service.intel import mistake_ledger, redis_store

    async def _boom(key):
        raise redis_store.StoreReadError("sqlite timeout")

    monkeypatch.setattr(redis_store, "get_strict", _boom)
    with pytest.raises(mistake_ledger.MistakeChainUnreadable):
        asyncio.run(mistake_ledger._read_head(strict=True))


def test_an_empty_ledger_still_chains_to_genesis(monkeypatch):
    from aria_service.intel import mistake_ledger, redis_store

    async def _absent(key):
        return None

    monkeypatch.setattr(redis_store, "get_strict", _absent)
    assert asyncio.run(mistake_ledger._read_head(strict=True)) == mistake_ledger._GENESIS


def test_a_real_head_is_returned(monkeypatch):
    from aria_service.intel import mistake_ledger, redis_store

    async def _head(key):
        return "deadbeef"

    monkeypatch.setattr(redis_store, "get_strict", _head)
    assert asyncio.run(mistake_ledger._read_head(strict=True)) == "deadbeef"


def test_record_refuses_rather_than_forking(monkeypatch):
    from aria_service.intel import mistake_ledger

    async def _unreadable(*a, **k):
        raise mistake_ledger.MistakeChainUnreadable("store down")

    monkeypatch.setattr(mistake_ledger, "_read_head", _unreadable)
    out = asyncio.run(mistake_ledger.record("hallucination", "t", "d", "w", "y", "f"))
    assert out.get("recorded") is False
    assert out.get("reason") == "mistake_chain_unreadable"


def test_the_two_chains_got_the_SAME_remedy():
    """Two chains with one defect should not get two different fixes."""
    from aria_service.intel import audit_log, mistake_ledger

    for mod, exc in ((audit_log, "AuditChainUnreadable"),
                     (mistake_ledger, "MistakeChainUnreadable")):
        assert hasattr(mod, exc), f"{mod.__name__} must distinguish unreadable from empty"
        src = function_source(mod, "_read_head_hash" if mod is audit_log else "_read_head")
        assert "strict" in src


# ══════════════════════════════════════════════════════════════════════════
# R-F3717 — the honesty index
# ══════════════════════════════════════════════════════════════════════════

def test_a_failed_index_read_does_not_overwrite_it(monkeypatch):
    """The headline: one transient read must not destroy 500 judgments."""
    from aria_service.intel import honesty_judge, redis_store

    writes: list = []

    async def _set(key, val, **kw):
        writes.append((key, val))

    async def _strict_boom(key):
        raise redis_store.StoreReadError("WAL recovery")

    monkeypatch.setattr(honesty_judge.rs, "set_json", _set)
    monkeypatch.setattr(honesty_judge.rs, "get_json_strict", _strict_boom)

    asyncio.run(honesty_judge.record_judgment(
        {"status": "ok", "honesty_score": 1.0, "claims": [], "supported_count": 0},
        question_preview="q", response_preview="r",
    ))

    index_writes = [v for k, v in writes if k == honesty_judge.JUDGMENTS_KEY]
    assert index_writes == [], (
        "the index was REWRITTEN after a failed read — that replaces up to 500 "
        "judgments with a list of one, and this index feeds 25% of gate #1"
    )


def test_the_individual_judgment_is_still_persisted(monkeypatch):
    """Skipping the index must not lose the judgment itself."""
    from aria_service.intel import honesty_judge, redis_store

    writes: list = []

    async def _set(key, val, **kw):
        writes.append(key)

    async def _strict_boom(key):
        raise redis_store.StoreReadError("WAL recovery")

    monkeypatch.setattr(honesty_judge.rs, "set_json", _set)
    monkeypatch.setattr(honesty_judge.rs, "get_json_strict", _strict_boom)

    asyncio.run(honesty_judge.record_judgment(
        {"status": "ok", "honesty_score": 1.0, "claims": [], "supported_count": 0},
        question_preview="q", response_preview="r",
    ))
    assert any(k.startswith(honesty_judge.JUDGMENT_KEY_PREFIX) for k in writes), (
        "the judgment record is stored under its own key and must survive an "
        "index failure — only the index entry is skipped"
    )


def test_a_healthy_index_is_still_appended(monkeypatch):
    """The guard must not stop normal recording."""
    from aria_service.intel import honesty_judge

    writes: dict = {}

    async def _set(key, val, **kw):
        writes[key] = val

    async def _get_ok(key):
        return [{"id": "old"}]

    monkeypatch.setattr(honesty_judge.rs, "set_json", _set)
    monkeypatch.setattr(honesty_judge.rs, "get_json_strict", _get_ok)

    asyncio.run(honesty_judge.record_judgment(
        {"status": "ok", "honesty_score": 0.9, "claims": [], "supported_count": 0},
        question_preview="q", response_preview="r",
    ))
    idx = writes.get(honesty_judge.JUDGMENTS_KEY)
    assert idx is not None and len(idx) == 2, (
        f"a healthy index must gain the new entry and KEEP the old: {idx}"
    )
    assert any(e.get("id") == "old" for e in idx)


def test_the_clobber_is_wired_to_the_brain():
    """§21a — losing gate #1's input must not be silent."""
    from aria_service.intel import honesty_judge

    src = function_source(honesty_judge, "record_judgment")
    assert "wire_failure" in src and "data_integrity" in src


def test_neither_module_still_uses_the_lenient_read_before_a_write():
    """The R-F2664 signature: `get...(...) or <default>` feeding a write-back."""
    from aria_service.intel import honesty_judge, mistake_ledger

    hj = function_source(honesty_judge, "record_judgment")
    assert "get_json(JUDGMENTS_KEY) or []" not in hj

    ml = function_source(mistake_ledger, "record")
    assert "_read_head(strict=True)" in ml

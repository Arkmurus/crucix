"""R-F4100 (C-144) — CAPABILITY: a store blip must not fork the CHAT audit chain.

`chat_audit_log.record_chat` read its chain head as:

    prev_hash = await rs.get(_K_HEAD) or _GENESIS_HASH

`redis_store.get` returns None on a store FAILURE (the documented
None-on-error contract), so a transient read failure was indistinguishable
from "this log is empty" — and the next entry got chained to genesis,
SILENTLY FORKING the evidentiary chain and orphaning every prior entry.

This is verbatim the defect **R-F3711** fixed in the sibling `audit_log.py`.
`chat_audit_log` was never ported. Measured live on aria-intel 2026-08-17:

    Capability gap recorded: [data_integrity] audit chain BROKEN:
      16 link(s) failed over 1233 of 1233 entries; first at index 434

and, in the same 23-minute window, the trigger itself — 26 state_store read
timeouts across 25 distinct keys, each logging "Returning None."

THE DISTINCTION THAT MATTERS (and that a naive fix destroys): an ABSENT key
is the genuine empty-log case and MUST still chain to genesis, because the
very first entry has no predecessor. Only a store FAILURE is refused. Tests
2 and 3 exist to keep a future fix from over-correcting into "refuse always".

Run: python -m pytest aria_service/tests/test_rf4100_chat_audit_chain_fork.py -v
"""
from __future__ import annotations

import asyncio

import pytest


class _Recorder:
    """Minimal redis_store stand-in: records writes so the test can assert
    that a refused write wrote NOTHING."""

    def __init__(self, *, head, strict_raises=False):
        self._head = head
        self._strict_raises = strict_raises
        self.lpushed: list[tuple[str, str]] = []
        self.sets: list[tuple[str, str]] = []

    async def get(self, key):
        # The None-on-error contract: a store failure is swallowed into None.
        return None if self._strict_raises else self._head

    async def get_strict(self, key):
        if self._strict_raises:
            from aria_service.intel.redis_store import StoreReadError
            raise StoreReadError("sqlite timeout after 5s")
        return self._head

    async def lpush(self, key, val):
        self.lpushed.append((key, val))

    async def ltrim(self, key, a, b):
        pass

    async def set(self, key, val, **kw):
        self.sets.append((key, val))

    async def expire(self, key, ttl):
        pass


def _install(monkeypatch, rec):
    from aria_service.intel import chat_audit_log, redis_store

    for name in ("get", "get_strict", "lpush", "ltrim", "set", "expire"):
        monkeypatch.setattr(redis_store, name, getattr(rec, name), raising=False)
    return chat_audit_log


def _record(mod):
    return asyncio.run(mod.record_chat(
        session_id="s1",
        user_message="who is BAE Systems plc",
        response_text="BAE Systems plc is a British defence contractor. " * 12,
        verification_status="grounded",
    ))


# ══════════════════════════════════════════════════════════════════════
# 1. THE DEFECT — a store failure must refuse, not fork
# ══════════════════════════════════════════════════════════════════════

def test_a_store_failure_does_not_fork_the_chat_chain(monkeypatch):
    rec = _Recorder(head="realheadhash1234", strict_raises=True)
    mod = _install(monkeypatch, rec)

    out = _record(mod)

    assert not rec.lpushed, (
        "record_chat wrote an entry while the chain head was UNREADABLE. "
        "That entry is chained to genesis, which orphans every prior entry "
        "and makes the whole chat audit trail unverifiable. Losing one "
        "record is bad; silently invalidating the trail is worse."
    )
    assert out.get("recorded") is False
    assert out.get("reason") == "chat_audit_chain_unreadable"
    assert not out.get("response_hash"), (
        "a refusal must not look like a written record — aria_engine reads "
        "response_hash off this dict to enqueue a reconcile"
    )


def test_the_refusal_reaches_the_brain(monkeypatch):
    """§21a — a refused audit write is exactly the thing that must not be silent."""
    seen: list[dict] = []
    from aria_service.intel import engine_wiring

    monkeypatch.setattr(
        engine_wiring, "wire_failure",
        lambda **kw: seen.append(kw), raising=False,
    )
    rec = _Recorder(head="realheadhash1234", strict_raises=True)
    mod = _install(monkeypatch, rec)
    monkeypatch.setattr(mod, "wire_failure",
                        lambda **kw: seen.append(kw), raising=False)

    _record(mod)

    assert seen, "a refused chat-audit write reached no brain sink"
    assert any(k.get("gap_type") == "data_integrity" for k in seen)


# ══════════════════════════════════════════════════════════════════════
# 2. THE OVER-CORRECTION GUARD — absence is not failure
# ══════════════════════════════════════════════════════════════════════

def test_an_empty_chat_log_still_chains_to_genesis(monkeypatch):
    """The FIRST entry legitimately has no predecessor. A fix that refuses
    here has broken the log's ability to ever start."""
    rec = _Recorder(head=None, strict_raises=False)
    mod = _install(monkeypatch, rec)

    out = _record(mod)

    assert rec.lpushed, "an absent head is an EMPTY log, not a broken one"
    assert out.get("prev_hash") == mod._GENESIS_HASH


def test_a_real_head_is_chained(monkeypatch):
    rec = _Recorder(head="realheadhash1234", strict_raises=False)
    mod = _install(monkeypatch, rec)

    out = _record(mod)

    assert rec.lpushed
    assert out.get("prev_hash") == "realheadhash1234"
    assert rec.sets, "the new head must be published"


# ══════════════════════════════════════════════════════════════════════
# 3. The verifier stays LENIENT — it reports, it does not extend
# ══════════════════════════════════════════════════════════════════════

def test_verify_chain_is_unaffected_by_the_strict_write_path(monkeypatch):
    """R-F3711 kept the read-only verifier lenient on purpose: a genesis
    fallback there is visible in its own output, and hardening it would make
    a store blip look like tampering."""
    from aria_service.intel import chat_audit_log

    src = chat_audit_log.verify_chain.__doc__ or ""
    assert "verify" in chat_audit_log.verify_chain.__name__
    # The verifier must not have acquired a raise-on-unreadable contract.
    assert "AuditChainUnreadable" not in src

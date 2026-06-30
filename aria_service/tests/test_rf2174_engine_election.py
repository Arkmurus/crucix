"""R-F2174 — Tier-3 engine-role election for multi-worker scaling.

When N uvicorn workers run they all inherit the same env, so ARIA_ROLE alone
can't elect one engine. These tests cover:
  1. the state_store atomic claim primitive (set_if_absent / renew_lease) —
     exactly one of two concurrent claimers wins; an EXPIRED lease can be taken
     over; a LIVE lease cannot be stolen.
  2. main._elect_engine_role — opt-in (default no-op), explicit-role override,
     winner→engine / loser→web, and FAIL-SAFE-to-'all' on store error.

Backward-compat contract: with election OFF (default), _aria_role() is 'all' —
bit-for-bit the current single-worker behaviour.
"""
from __future__ import annotations

import asyncio

import pytest


# ───────────────────────── 1. atomic claim primitive ───────────────────────

@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    """A real, isolated state_store SQLite for claim-primitive tests."""
    monkeypatch.setenv("ARIA_STATE_BACKEND", "sqlite")
    monkeypatch.setenv("ARIA_STATE_DB_PATH", str(tmp_path / "rf2174_state.db"))
    from aria_service.intel import state_store as ss

    async def _connect():
        # Force a fresh connection bound to the tmp db.
        await ss.connect(str(tmp_path / "rf2174_state.db"))
    asyncio.run(_connect())
    yield ss


def test_rf2174_only_one_claimer_wins(fresh_store):
    ss = fresh_store

    async def run():
        a = await ss.set_if_absent("crucix:test:lease", "worker-A", ex=30)
        b = await ss.set_if_absent("crucix:test:lease", "worker-B", ex=30)
        return a, b

    a, b = asyncio.run(run())
    assert a is True, "first claimer must win"
    assert b is False, "second claimer must lose a live lease"


def test_rf2174_expired_lease_can_be_taken_over(fresh_store):
    ss = fresh_store

    async def run():
        # Claim with a tiny TTL, let it expire, then a new claimer must win.
        won1 = await ss.set_if_absent("crucix:test:lease2", "old", ex=1)
        await asyncio.sleep(1.2)
        won2 = await ss.set_if_absent("crucix:test:lease2", "new", ex=30)
        return won1, won2

    won1, won2 = asyncio.run(run())
    assert won1 is True
    assert won2 is True, "an EXPIRED lease must be claimable by a new worker"


def test_rf2174_renew_only_by_owner(fresh_store):
    ss = fresh_store

    async def run():
        await ss.set_if_absent("crucix:test:lease3", "owner", ex=30)
        ok_owner = await ss.renew_lease("crucix:test:lease3", "owner", ex=30)
        ok_other = await ss.renew_lease("crucix:test:lease3", "imposter", ex=30)
        return ok_owner, ok_other

    ok_owner, ok_other = asyncio.run(run())
    assert ok_owner is True, "owner must be able to renew"
    assert ok_other is False, "a non-owner must NOT be able to renew"


# ───────────────────────── 2. election logic (main) ────────────────────────

@pytest.fixture
def reset_role(monkeypatch):
    import aria_service.main as m
    m._resolved_role = None
    m._engine_lease_id = None
    monkeypatch.delenv("ARIA_ROLE", raising=False)
    monkeypatch.delenv("ARIA_ENGINE_ELECTION", raising=False)
    return m


def test_rf2174_default_off_is_all(reset_role):
    """Election OFF (default) → role 'all' (today's behaviour, unchanged)."""
    m = reset_role
    asyncio.run(m._elect_engine_role())
    assert m._resolved_role is None
    assert m._aria_role() == "all"
    assert m._runs_singletons() is True


def test_rf2174_explicit_role_overrides_election(reset_role, monkeypatch):
    m = reset_role
    monkeypatch.setenv("ARIA_ENGINE_ELECTION", "1")
    monkeypatch.setenv("ARIA_ROLE", "web")
    asyncio.run(m._elect_engine_role())
    assert m._resolved_role is None, "explicit ARIA_ROLE must skip the election"
    assert m._aria_role() == "web"
    assert m._runs_singletons() is False


def test_rf2174_winner_is_engine_loser_is_web(reset_role, monkeypatch):
    m = reset_role
    monkeypatch.setenv("ARIA_ENGINE_ELECTION", "1")

    calls = {"n": 0}

    async def fake_claim(key, value, ex):
        calls["n"] += 1
        return calls["n"] == 1  # first caller wins, rest lose

    monkeypatch.setattr("aria_service.intel.state_store.set_if_absent", fake_claim)

    async def run_two():
        m._resolved_role = None
        await m._elect_engine_role()
        first = m._resolved_role
        m._resolved_role = None
        await m._elect_engine_role()
        second = m._resolved_role
        return first, second

    first, second = asyncio.run(run_two())
    assert first == "engine"
    assert second == "web"


def test_rf2174_failsafe_to_all_on_store_error(reset_role, monkeypatch):
    """If the claim raises (store error), the worker must fall back to 'all'
    (run singletons) — never leave the engine unowned."""
    m = reset_role
    monkeypatch.setenv("ARIA_ENGINE_ELECTION", "1")

    async def boom(key, value, ex):
        raise RuntimeError("state_store down")

    monkeypatch.setattr("aria_service.intel.state_store.set_if_absent", boom)
    asyncio.run(m._elect_engine_role())
    assert m._resolved_role == "all", "store error must fail-safe to ALL (singletons run)"
    assert m._runs_singletons() is True


def test_rf2174_web_concurrency_defaults_to_one(reset_role, monkeypatch):
    m = reset_role
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    assert m._web_concurrency() == 1
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    assert m._web_concurrency() == 4
    monkeypatch.setenv("WEB_CONCURRENCY", "garbage")
    assert m._web_concurrency() == 1

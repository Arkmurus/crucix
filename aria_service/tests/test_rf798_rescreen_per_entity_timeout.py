"""R-F798 (2026-05-22): per-entity timeout in dd_orchestrator.rescreen_watchlist.

Live evidence 2026-05-22 16:03:49 UTC: `[Watchlist] Re-screen: 1
entities, 1 changes, 0 errors, 1,197,936ms` — a single wedged
sanctions screen burned 19m 58s. R-F798 caps each entity's screen at
_RESCREEN_PER_ENTITY_TIMEOUT_S (default 60s, env-tunable via
ARIA_RESCREEN_PER_ENTITY_TIMEOUT_S).

On timeout the entity is recorded as an error and the loop moves on.
"""
from __future__ import annotations

import asyncio
import time

from aria_service.intel import dd_orchestrator


def _setup(monkeypatch, screen_impl, watchlist):
    """Patch the real sanctions module's screen_with_aliases so the
    function-local `from . import sanctions` picks up our stub.
    Also patches the redis_store + classify_matches + brain_hook
    surface so the test is hermetic."""
    from aria_service.intel import sanctions as _real_sanctions
    from aria_service.intel import _sanctions_classify
    from aria_service.intel import redis_store as rs
    from aria_service.intel import brain_hook

    monkeypatch.setattr(
        _real_sanctions, "screen_with_aliases", screen_impl, raising=False,
    )

    def _classify(matches, query_name=""):
        # classify_matches is sync per its import shape in dd_orchestrator
        return {"summary": "", "tier": "none"}

    monkeypatch.setattr(_sanctions_classify, "classify_matches", _classify)

    # _looks_like_entity_name is used for the polluted-entry purge —
    # always accept so the test watchlist isn't stripped before screening.
    monkeypatch.setattr(
        _real_sanctions, "_looks_like_entity_name", lambda s: bool(s and len(s) >= 2),
        raising=False,
    )

    async def _get_json(key):
        if key == dd_orchestrator.WATCHLIST_KEY:
            return watchlist
        return None

    async def _get_json_strict(key):
        # R-F3520 — R-F3506 moved the watchlist read-modify-writes to the STRICT
        # reader; a fake stubbing only `get_json` is bypassed, so rescreen_watchlist
        # returns early with entities_screened: 0 and these tests fail with a
        # PLAUSIBLE empty result rather than an error.
        return await _get_json(key)

    async def _set_json(key, val, ex=None):
        return None

    async def _lpush(*a, **kw):
        return None

    async def _ltrim(*a, **kw):
        return None

    async def _expire(*a, **kw):
        return None

    monkeypatch.setattr(rs, "get_json", _get_json)
    monkeypatch.setattr(rs, "get_json_strict", _get_json_strict)
    monkeypatch.setattr(rs, "set_json", _set_json)
    monkeypatch.setattr(rs, "lpush", _lpush)
    monkeypatch.setattr(rs, "ltrim", _ltrim)
    monkeypatch.setattr(rs, "expire", _expire)

    async def _absorb(**kw):
        return {}

    monkeypatch.setattr(brain_hook, "absorb", _absorb)


def test_rf798_screen_timeout_records_error_and_moves_on(monkeypatch):
    """When sanctions.screen_with_aliases hangs past the per-entity
    timeout, the entity is recorded as an error and rescreen returns
    in bounded time."""
    async def _hang(name):
        await asyncio.sleep(60.0)

    _setup(monkeypatch, _hang, [{"name": "Acme Corp"}])
    monkeypatch.setenv("ARIA_RESCREEN_PER_ENTITY_TIMEOUT_S", "0.1")

    t0 = time.monotonic()
    result = asyncio.run(dd_orchestrator.rescreen_watchlist())
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, (
        f"R-F798 regression: rescreen took {elapsed:.2f}s, should be "
        f"capped at ~0.1s × N + overhead. Pre-R-F798 a wedged sanctions "
        f"screen burned 19m 58s on a single entity."
    )
    assert len(result["errors"]) == 1, f"errors: {result['errors']}"
    assert "timeout" in result["errors"][0]["error"].lower()
    assert result["errors"][0]["entity"] == "Acme Corp"


def test_rf798_normal_screen_completes(monkeypatch):
    """A normal sanctions screen returns quickly and the entity is
    not recorded as an error."""
    async def _quick(name):
        return {"matches": []}

    _setup(monkeypatch, _quick, [{"name": "Clean Corp"}])
    monkeypatch.setenv("ARIA_RESCREEN_PER_ENTITY_TIMEOUT_S", "5.0")

    result = asyncio.run(dd_orchestrator.rescreen_watchlist())
    assert result["entities_screened"] == 1
    assert result["errors"] == [], f"errors: {result['errors']}"


def test_rf798_timeout_zero_disables(monkeypatch):
    """ARIA_RESCREEN_PER_ENTITY_TIMEOUT_S=0 disables the wrapper."""
    async def _quick(name):
        return {"matches": []}

    _setup(monkeypatch, _quick, [{"name": "Clean Corp"}])
    monkeypatch.setenv("ARIA_RESCREEN_PER_ENTITY_TIMEOUT_S", "0")

    result = asyncio.run(dd_orchestrator.rescreen_watchlist())
    assert result["entities_screened"] == 1
    assert result["errors"] == [], f"errors: {result['errors']}"


def test_rf798_multiple_entities_one_timeout_others_succeed(monkeypatch):
    """If one entity wedges, the other entities in the same cycle
    still complete. Operational guarantee — a single bad entity
    doesn't block the cycle."""
    calls = {"count": 0}

    async def _mixed(name):
        calls["count"] += 1
        if name == "Wedge Corp":
            await asyncio.sleep(60.0)
        return {"matches": []}

    _setup(monkeypatch, _mixed, [
        {"name": "First Corp"},
        {"name": "Wedge Corp"},
        {"name": "Third Corp"},
    ])
    monkeypatch.setenv("ARIA_RESCREEN_PER_ENTITY_TIMEOUT_S", "0.1")

    result = asyncio.run(dd_orchestrator.rescreen_watchlist())
    assert result["entities_screened"] == 3
    timeout_errs = [e for e in result["errors"] if "timeout" in e["error"].lower()]
    assert len(timeout_errs) == 1, f"errors: {result['errors']}"
    assert timeout_errs[0]["entity"] == "Wedge Corp"

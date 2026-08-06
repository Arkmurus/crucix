"""R-F3758 — CAPABILITY: an unreadable store must not un-suppress delivery.

`operating_modes.get_mode()` read with `rs.get`, which returns None on a store
FAILURE as well as on an absent key, then fell through to `Mode.NORMAL`. An
unreadable store did not report "I don't know" — it ASSERTED NORMAL.

OPEN is the unsafe direction here. DEGRADED suppresses external delivery
(`operating_modes.py:189`) and the autonomous engine SKIPS tasks on it
(`autonomous/engine.py:670`). So a store blip silently un-suppressed delivery a
degraded mode had deliberately stopped, and let skipped tasks fire — a safety
control switching itself off because a read failed, saying nothing.

Found live 2026-08-06: aria-intel's /health reported `operating_mode_degraded`
while a fresh process on the SAME machine got `NORMAL` from this function, because
that process could not reach the store ("no read connection"). The running app was
right; this function was manufacturing the safe-looking answer. Same class as
R-F3722, and the fix is deliberately identical.

Run: python -m pytest aria_service/tests/test_rf3758_operating_mode_fails_safe.py -v
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import operating_modes as om
from aria_service.intel import redis_store


@pytest.fixture(autouse=True)
def _clean():
    om._MODE_CACHE["val"] = None
    yield
    om._MODE_CACHE["val"] = None


def test_an_unreadable_store_does_not_assert_NORMAL(monkeypatch):
    """THE HEADLINE: a blip must not un-suppress a DEGRADED mode."""
    async def _ok(key):
        return str(om.Mode.DEGRADED.value)

    monkeypatch.setattr(redis_store, "get_strict", _ok)
    assert asyncio.run(om.get_mode()) is om.Mode.DEGRADED

    async def _boom(key):
        raise redis_store.StoreReadError("no read connection (reconnect in progress)")

    monkeypatch.setattr(redis_store, "get_strict", _boom)
    assert asyncio.run(om.get_mode()) is om.Mode.DEGRADED, (
        "an unreadable store reported NORMAL, which un-suppresses external "
        "delivery (operating_modes.py:189) and lets the engine fire tasks it was "
        "skipping (autonomous/engine.py:670)"
    )


def test_an_absent_key_is_genuinely_NORMAL(monkeypatch):
    """The guard must not freeze the mode: absent-and-readable IS normal."""
    async def _absent(key):
        return None

    om._MODE_CACHE["val"] = om.Mode.DEGRADED
    monkeypatch.setattr(redis_store, "get_strict", _absent)
    assert asyncio.run(om.get_mode()) is om.Mode.NORMAL


def test_a_successful_read_still_changes_the_mode(monkeypatch):
    """A flip via /autonomous must be seen immediately, not held by the cache."""
    om._MODE_CACHE["val"] = om.Mode.DEGRADED

    async def _normal(key):
        return str(om.Mode.NORMAL.value)

    monkeypatch.setattr(redis_store, "get_strict", _normal)
    assert asyncio.run(om.get_mode()) is om.Mode.NORMAL


def test_a_corrupt_value_is_not_a_licence_to_be_NORMAL(monkeypatch):
    om._MODE_CACHE["val"] = om.Mode.DEGRADED

    async def _junk(key):
        return "not-a-mode"

    monkeypatch.setattr(redis_store, "get_strict", _junk)
    assert asyncio.run(om.get_mode()) is om.Mode.DEGRADED, (
        "a corrupt stored value fell back to NORMAL — that is the same "
        "fail-open, reached by a different route"
    )


def test_with_no_prior_read_an_unreadable_store_still_answers(monkeypatch):
    """First call ever + dead store: must not raise into the health handler."""
    async def _boom(key):
        raise redis_store.StoreReadError("dead")

    monkeypatch.setattr(redis_store, "get_strict", _boom)
    assert asyncio.run(om.get_mode()) is om.Mode.NORMAL


def test_the_blindness_is_wired_to_the_brain():
    """§21a — a safety control going blind must not be a silent log."""
    from ._source_probe import function_source
    src = function_source(om, "get_mode")
    assert "wire_failure" in src and "get_strict" in src

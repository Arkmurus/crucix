"""R-F2486 — restore the missing rs.hget().

Without it, dd_trigger_pipeline's `rs.hget(...)` raised AttributeError, which the
callers' broad except swallowed: _dd_trigger_guard_record recorded nothing and
_dd_trigger_guard_check always returned (True, "ok") — the DD trigger guard
failed OPEN, so repeated failing DD runs were never suppressed.
"""
import os
import tempfile

import pytest

from aria_service.intel import state_store as _ss
from aria_service.intel import redis_store as rs
from aria_service.intel import dd_trigger_pipeline as dtp


class TestHgetDdGuard:
    @pytest.fixture(autouse=True)
    async def _fresh_store(self, monkeypatch):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        monkeypatch.setenv("ARIA_STATE_BACKEND", "sqlite")
        monkeypatch.setenv("ARIA_STATE_DB_PATH", tmp.name)
        try:
            await _ss.close()
        except Exception:
            pass
        await _ss.connect()
        yield
        try:
            await _ss.close()
        except Exception:
            pass
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    @pytest.mark.asyncio
    async def test_rf2486_hget_primitive(self):
        await rs.hset("test:rf2486:h", {"f1": "v1"})
        assert await rs.hget("test:rf2486:h", "f1") == "v1"
        assert await rs.hget("test:rf2486:h", "absent") is None
        assert await rs.hget("test:rf2486:missing_key", "f1") is None

    @pytest.mark.asyncio
    async def test_rf2486_guard_suppresses_after_3_same_layer_failures(self):
        entity = "Acme Trading Ltd"
        # First trigger is allowed (no history yet).
        ok0, _ = await dtp._dd_trigger_guard_check(entity)
        assert ok0 is True
        # Record 3 consecutive failures on the SAME layer.
        for _ in range(3):
            await dtp._dd_trigger_guard_record(entity, succeeded=False, failed_layer="sanctions")
        # The guard must now SUPPRESS (fail-closed) — this is the whole point.
        ok, reason = await dtp._dd_trigger_guard_check(entity)
        assert ok is False, f"guard must suppress after 3 same-layer failures (got ok={ok}, reason={reason!r})"
        assert "consecutive DD failures" in reason

    @pytest.mark.asyncio
    async def test_rf2486_guard_allows_when_failures_differ(self):
        entity = "Beta Corp"
        for layer in ("sanctions", "network", "identity"):
            await dtp._dd_trigger_guard_record(entity, succeeded=False, failed_layer=layer)
        # 3 failures but on DIFFERENT layers -> not a persistent single-root failure -> allowed.
        ok, _ = await dtp._dd_trigger_guard_check(entity)
        assert ok is True

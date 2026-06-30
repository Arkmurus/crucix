"""R-F2152 — the public /health status endpoint must NEVER hang.

Witnessed live 2026-06-30: GET /health hung >90s while /health/live and
/api/aria/health stayed fast. The only awaitable in the handler is the
state-backend read (get_json offloads json.loads of large blobs to the worker
thread pool, which can saturate). A hung /health made web's cross-health probe
report the brain as DOWN even though it was serving. This test drives the real
`health()` coroutine with the state read stalled and asserts it returns bounded,
degrading the diagnostic indicator to UNKNOWN.
"""
import asyncio
import time
from unittest.mock import MagicMock

from aria_service import main as aria_main
from aria_service.intel import redis_store as rs


def test_health_is_bounded_when_state_read_stalls(monkeypatch):
    # Minimal app.state so health() doesn't trip on missing attrs.
    llm = MagicMock()
    llm.get_stats.return_value = {}
    llm.get_health.return_value = {"resilient": True}
    llm.name = "test"
    llm.is_configured = True
    aria_main.app.state.llm_provider = llm
    aria_main.app.state.state_backend = "sqlite"
    aria_main.app.state.state_backend_reachable = True

    async def _hang(*a, **k):
        await asyncio.sleep(60)   # simulate a saturated-thread-pool stall

    monkeypatch.setattr(rs, "get_json", _hang)
    monkeypatch.setenv("ARIA_HEALTH_READ_TIMEOUT_S", "1")

    t = time.monotonic()
    result = asyncio.run(aria_main.health())
    elapsed = time.monotonic() - t

    assert elapsed < 5, f"/health hung {elapsed:.1f}s — R-F2152 bound failed"
    assert result["diagnostic"]["overall"] == "UNKNOWN"
    # Still produces a usable status payload from in-memory state.
    assert result["status"] in ("operational", "degraded")
    assert result["service"] == "aria"

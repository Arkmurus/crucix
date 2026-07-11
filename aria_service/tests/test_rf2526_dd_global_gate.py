"""R-F2526 — scale-ready global DD concurrency gate. No-op on a single machine
(byte-identical), a GLOBAL cap when a shared Redis backend is live. Fail-open."""
import asyncio
import aria_service.intel.redis_store as rs
from aria_service.routes import aria


def test_gate_is_noop_on_single_machine(monkeypatch):
    monkeypatch.setattr(rs, "is_shared", lambda: False)
    async def boom(*a, **k):
        raise AssertionError("redis must NOT be touched when not shared")
    monkeypatch.setattr(rs, "incrbyfloat", boom)
    assert asyncio.run(aria._dd_admit_global("deep", 3)) is True   # no-op admit
    asyncio.run(aria._dd_release_global("deep"))                    # no-op release, no error


def test_gate_enforces_global_cap_when_shared(monkeypatch):
    monkeypatch.setattr(rs, "is_shared", lambda: True)
    counter = {"v": 0.0}
    async def incr(key, amt):
        counter["v"] += amt
        return counter["v"]
    async def expire(key, ttl):
        return True
    monkeypatch.setattr(rs, "incrbyfloat", incr)
    monkeypatch.setattr(rs, "expire", expire)

    async def run():
        assert await aria._dd_admit_global("deep", 3) is True   # 1
        assert await aria._dd_admit_global("deep", 3) is True   # 2
        assert await aria._dd_admit_global("deep", 3) is True   # 3
        assert await aria._dd_admit_global("deep", 3) is False  # 4 > cap -> rollback
        assert counter["v"] == 3.0                              # rolled back to cap
        await aria._dd_release_global("deep")                   # -> 2
        assert counter["v"] == 2.0
    asyncio.run(run())


def test_gate_fails_open_on_redis_error(monkeypatch):
    monkeypatch.setattr(rs, "is_shared", lambda: True)
    async def boom(*a, **k):
        raise RuntimeError("redis down")
    monkeypatch.setattr(rs, "incrbyfloat", boom)
    assert asyncio.run(aria._dd_admit_global("deep", 3)) is True  # fail-open, never blocks


if __name__ == "__main__":
    import types
    class MP:
        def setattr(self, o, n, v): setattr(o, n, v)
    test_gate_is_noop_on_single_machine(MP()); print("PASS noop")
    print("ALL PASS")

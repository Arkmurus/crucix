"""R-F2543 — ARIA-Coder R-number partition (codex F1).

self_coder allocates R-numbers via r_counter.next() = redis.incr("crucix:aria:r_counter").
It was NEVER seeded, so it handed out R-F1, R-F2, … colliding with ancient numbers AND
Claude's git-serialized file registry (~R-F25xx). R-F2543 seeds the counter to a high
reserved base (900000) at coder startup so ARIA-Coder's allocations can never collide
with the file-registry sequential range.
"""
from __future__ import annotations

import asyncio


class _FakeRedis:
    """Minimal async redis stand-in for RNumberCounter."""
    def __init__(self, seeded: int | None = None):
        self.store: dict = {}
        if seeded is not None:
            self.store["crucix:aria:r_counter"] = seeded

    async def incr(self, k):
        self.store[k] = int(self.store.get(k, 0)) + 1
        return self.store[k]

    async def get(self, k):
        v = self.store.get(k)
        return None if v is None else str(v).encode()

    async def set(self, k, v):
        self.store[k] = int(v)


def test_unseeded_counter_collides_with_low_numbers():
    """Documents the bug: an unseeded counter hands out R-F1, R-F2 … (collision)."""
    from aria_service.autonomous.r_counter import RNumberCounter
    r = RNumberCounter(_FakeRedis())
    assert asyncio.run(r.next()) == 1  # would render "R-F1" — collides with ancient
    assert asyncio.run(r.next()) == 2


def test_real_seed_helper_partitions_a_low_counter():
    """§3c: drive the ACTUAL startup helper (_seed_coder_r_counter) on a fresh (unseeded)
    counter — it must seed to the high base so every allocation clears the file-registry
    range. If F1's guard were inverted/removed this fails."""
    from aria_service.autonomous.r_counter import RNumberCounter
    from aria_service.autonomous.coder_entrypoint import _seed_coder_r_counter, R_CODER_BASE
    r = RNumberCounter(_FakeRedis())               # unseeded → current()=0
    asyncio.run(_seed_coder_r_counter(r))
    n1 = asyncio.run(r.next())
    assert n1 == R_CODER_BASE + 1
    assert n1 > 100000                              # far above any real file-registry R-number


def test_real_seed_helper_is_idempotent_never_lowers():
    """§3c: the ACTUAL helper must NOT lower an already-high counter (reboot no-op)."""
    from aria_service.autonomous.r_counter import RNumberCounter
    from aria_service.autonomous.coder_entrypoint import _seed_coder_r_counter, R_CODER_BASE
    r = RNumberCounter(_FakeRedis(seeded=R_CODER_BASE + 50))
    asyncio.run(_seed_coder_r_counter(r))           # guard: 900050 >= base → no-op
    assert asyncio.run(r.next()) == R_CODER_BASE + 51   # continues, not reset


# ── F4: coder-loop respawn supervision ────────────────────────────────────
def test_bg_task_registers_respawn_factory():
    """§3c F4 mechanism: _bg_task(task, name, factory) must register the factory into
    _BG_RESPAWN so the supervisor can revive the coder loop if it dies."""
    from aria_service import main as _m

    async def _noop():
        return None

    async def _run():
        t = asyncio.create_task(_noop(), name="rf2543_probe")
        _m._bg_task(t, name="rf2543_probe", factory=_noop)
        await t
        assert "rf2543_probe" in _m._BG_RESPAWN, "factory not registered for respawn"
        _m._BG_RESPAWN.pop("rf2543_probe", None)     # cleanup
    asyncio.run(_run())


def test_main_wires_coder_self_coder_respawn_factory():
    """§3c F4 revert-guard: main.py must register the coder's self_coder loop with a
    respawn factory (it was held only in aria_coder_tasks for GC before R-F2543)."""
    import pathlib
    src = pathlib.Path("aria_service/main.py").read_text(encoding="utf-8")
    assert "factory=_coder.run_forever" in src
    assert 'name="aria_coder.self_coder"' in src or "aria_coder.self_coder" in src


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

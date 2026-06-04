"""R-F802 — Atomic R-number allocator.

Used by ARIACoder to claim the next R-number for an autonomous fix.
Backed by Redis INCR so two concurrent coder instances cannot collide.

This complements (does NOT replace) the file-based reservation log at
`scripts/admin/reserve_r_number.py` + `data/r_number_reservations.json`.
The file log is canonical; this Redis counter is the runtime fast-path
for autonomous fixes to avoid forking subprocesses.

A nightly job (R-F-X to be reserved) reconciles the Redis counter against
the reservation file and writes any in-flight autonomous R-numbers back
to the file with `claimed_by="aria-coder"`.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aria.autonomous.r_counter")

# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.r_counter",
        summary="R Counter active",
        source_id="autonomous:r_counter:R-F1320",
    )
except Exception:
    pass

R_COUNTER_KEY = "crucix:aria:r_counter"


class RNumberCounter:
    """Atomic R-number allocation via Redis INCR."""

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def next(self) -> int:
        """Atomically increment and return the next R-number."""
        return int(await self.redis.incr(R_COUNTER_KEY))

    async def current(self) -> int:
        """Read the current R-number without incrementing."""
        val = await self.redis.get(R_COUNTER_KEY)
        if val is None:
            return 0
        return int(val.decode("utf-8") if isinstance(val, bytes) else val)

    async def seed(self, value: int) -> None:
        """One-time seed — only call when reconciling with file log."""
        await self.redis.set(R_COUNTER_KEY, str(value))
        logger.info("[r_counter] seeded to %d", value)

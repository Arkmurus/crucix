"""R-F1706 — audit_log.record() must not block the event loop serializing
large DD/compliance entries.

Pre-fix, record() ran FOUR JSON serializations inline on the loop per call
(signature, hash, lpush, by-hash) with the slower default=str encoder. entry
inputs/outputs can be tens of KB and record() fires several times per DD run —
a real per-DD-run stall (the audit_log serialization wedge).

Cure: serialise each off the loop via asyncio.to_thread, and serialise the
stored entry ONCE (reused for the chain + the by-hash index). The signed/hashed
BYTES are unchanged — the tamper-evident chain must still verify.

This drives the REAL record() and asserts both the off-loop behaviour and that
the chain integrity is byte-preserved.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import audit_log, redis_store


@pytest.mark.asyncio
async def test_record_serializes_off_loop_once_and_chain_verifies():
    writes = {"lpush": [], "set": []}

    async def _cap_lpush(key, val, *a, **k):
        writes["lpush"].append((key, val))

    async def _cap_set(key, val, *a, **k):
        writes["set"].append((key, val))

    # Spy on to_thread but still execute the work (so record() really runs).
    offloaded = []
    _real_to_thread = asyncio.to_thread

    async def _spy_to_thread(fn, *a, **k):
        offloaded.append(getattr(fn, "__name__", str(fn)))
        return await _real_to_thread(fn, *a, **k)

    big = {f"field_{i}": "x" * 300 for i in range(40)}  # ~12 KB of inputs/outputs

    with patch.object(audit_log, "_read_head_hash", AsyncMock(return_value="")), \
         patch.object(redis_store, "lpush", _cap_lpush), \
         patch.object(redis_store, "set", _cap_set), \
         patch.object(audit_log.asyncio, "to_thread", _spy_to_thread):
        entry = await audit_log.record(
            "sanctions_screen",
            entity_name="Test Subject Ltd",
            inputs=big,
            outputs=big,
            decision="clear",
            feed_brain=False,  # keep hermetic — no brain side-effects
        )

    # 1) OFF-LOOP: the three serializations moved to to_thread (the wedge cure).
    assert "dumps" in offloaded, "entry JSON must be serialized off the loop"
    assert "_compute_signature" in offloaded, "signature must be computed off the loop"
    assert "_serialise_for_hash" in offloaded, "hash serialization must be off the loop"

    # 2) SERIALIZED ONCE: the chain entry and the by-hash index get the SAME bytes.
    log_vals = [v for k, v in writes["lpush"] if k == audit_log._KEY_LOG]
    assert len(log_vals) == 1
    entry_json = log_vals[0]
    set_vals = [v for _, v in writes["set"]]
    assert entry_json in set_vals, "the by-hash index must store the same serialized entry"

    # 3) CHAIN INTEGRITY byte-preserved: re-verify the hash over the body.
    parsed = json.loads(entry_json)
    assert parsed["entry_hash"] == entry["entry_hash"]
    body = {k: v for k, v in parsed.items() if k != "entry_hash"}
    recomputed = hashlib.sha256(audit_log._serialise_for_hash(body)).hexdigest()
    assert recomputed == parsed["entry_hash"], "tamper-evident hash must still verify"

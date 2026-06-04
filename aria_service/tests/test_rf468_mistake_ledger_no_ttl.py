"""R-F468 — mistake_ledger invalidation TTL removal.

Per [[aria_infinite_memory]] ARIA must never forget — and forgetting
that a mistake entry was invalidated would let a poisoned mistake
re-enter the lookup_similar pool after 365 days. The original 365 * 86400
TTL was a stale carry-over from short-lived caches.

R-F468 removes the TTL from both keys:
  - crucix:mistake_ledger:invalidated (the set of invalidated mistake IDs)
  - crucix:mistake_ledger:reason:{mid} (per-entry rationale)
"""
from __future__ import annotations

import asyncio
import pytest


def test_rf468_no_explicit_ttl_in_invalidate_path():
    """Static-source guard: the invalidate() function must not call
    set_json with an explicit ex= argument on _KEY_INVALIDATED or
    _KEY_INVALIDATION_REASON."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "aria_service" / "intel" / "mistake_ledger.py"
    text = src.read_text(encoding="utf-8")
    # Locate the invalidate function and inspect just its body.
    start = text.index("async def invalidate(")
    # The next top-level "async def" or "def " starts the next function
    end = text.find("\nasync def ", start + 10)
    if end < 0:
        end = text.find("\ndef ", start + 10)
    body = text[start:end]
    assert "365 * 86400" not in body, (
        "R-F468 regression: 365-day TTL re-introduced into invalidate()"
    )
    # No ex= TTL kwargs on set_json calls inside invalidate
    import re
    assert not re.search(r"set_json\([^)]*ex\s*=", body, re.DOTALL), (
        "R-F468 regression: set_json with ex= TTL appeared inside invalidate()"
    )


def test_rf468_runtime_persistence_uses_no_ttl(monkeypatch):
    """When invalidate() runs, the underlying set_json calls must NOT
    include an `ex` kwarg."""
    from aria_service.intel import mistake_ledger as _ml

    captured_calls = []

    async def _fake_set_json(key, value, **kwargs):
        captured_calls.append({"key": key, "kwargs": kwargs})

    async def _fake_get_json(key):
        return None if "invalidated" in key else None

    class _FakeRS:
        get_json = staticmethod(_fake_get_json)
        set_json = staticmethod(_fake_set_json)
        async def lrange(self, *a, **k):  # noqa: ARG002
            return []
        async def llen(self, *a, **k):    # noqa: ARG002
            return 0

    import sys
    # Patch redis_store in sys.modules. The relative import
    # `from . import redis_store as rs` resolves via sys.modules,
    # so this is the correct place to patch.
    monkeypatch.setitem(sys.modules, "aria_service.intel.redis_store", _FakeRS)
    # Also patch the module's internal _invalidated_set helper which
    # does its own lazy import of redis_store.
    from aria_service.intel import mistake_ledger as _ml
    # Patch the _invalidated_set function's globals directly so its
    # lazy import `from . import redis_store as rs` uses our fake.
    if hasattr(_ml, '_invalidated_set') and hasattr(_ml._invalidated_set, '__globals__'):
        monkeypatch.setitem(_ml._invalidated_set.__globals__, 'redis_store', _FakeRS)

    # The implementation also imports its own helper _invalidated_set, which
    # already short-circuits via the patched get_json above.
    async def run():
        return await _ml.invalidate(["mid_aaa", "mid_bbb"], reason="poisoned by adversarial test")

    result = asyncio.run(run())
    assert result["invalidated"] == 2, f"expected 2 invalidated, got {result}"

    # Inspect every set_json call captured during invalidate()
    target_calls = [c for c in captured_calls
                    if "invalidated" in c["key"] or ":reason:" in c["key"]]
    assert target_calls, "expected at least one persist call on invalidated/reason keys"
    for call in target_calls:
        assert "ex" not in call["kwargs"], (
            f"R-F468: persist call on {call['key']!r} still uses ex= TTL "
            f"({call['kwargs']!r}) — must be permanent per aria_infinite_memory"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

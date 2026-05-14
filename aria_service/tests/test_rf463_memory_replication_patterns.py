"""R-F463 — memory_replication brain-keyspace expansion.

DD-audit P0 #5: pre-R-F463, _CRITICAL_KEYS excluded the brain
(aria:verified_facts:*, crucix:aria:knowledge:shard:*,
crucix:aria:conversations:*, crucix:mistake_ledger:by_*) so the daily
backup covered ~15% of the brain. A seenode disk wipe (see
[[seenode_disk_ephemeral]]) plus a fly snapshot loss would have lost
the rest.

R-F463 adds _CRITICAL_PATTERNS (wildcard glob + per-pattern max_keys
cap) and wires it into run_daily_backup, plus surfaces it in summary().
"""
from __future__ import annotations

import pytest


def test_rf463_critical_keys_now_include_brain_anchors():
    from aria_service.learning.memory_replication import _CRITICAL_KEYS
    # Anchors that point at the sharded brain keyspaces — without these
    # the sharded data can't be reassembled even if shards are restored.
    assert "crucix:aria:knowledge" in _CRITICAL_KEYS
    assert "crucix:aria:knowledge:meta" in _CRITICAL_KEYS
    assert "crucix:mistake_ledger:log" in _CRITICAL_KEYS
    assert "crucix:mistake_ledger:head_hash" in _CRITICAL_KEYS


def test_rf463_critical_patterns_cover_brain_keyspaces():
    from aria_service.learning.memory_replication import _CRITICAL_PATTERNS
    patterns = {p for p, _ in _CRITICAL_PATTERNS}
    assert "aria:verified_facts:*" in patterns, "per-fact VERIFIED claims must be backed up"
    assert "crucix:aria:knowledge:shard:*" in patterns, "knowledge shards must be backed up"
    assert "crucix:aria:conversations:*" in patterns, "per-user conversations must be backed up"
    assert "crucix:mistake_ledger:by_sig:*" in patterns
    assert "crucix:mistake_ledger:by_id:*" in patterns


def test_rf463_each_pattern_has_max_keys_cap():
    """Every pattern carries a numeric cap so a runaway keyspace can't
    blow the backup file size unboundedly."""
    from aria_service.learning.memory_replication import _CRITICAL_PATTERNS
    for pattern, max_keys in _CRITICAL_PATTERNS:
        assert isinstance(max_keys, int) and max_keys > 0, (
            f"pattern {pattern!r} must have positive integer max_keys, got {max_keys!r}"
        )
        # Caps too small are nearly as bad as no cap — would silently drop the brain
        assert max_keys >= 1000, (
            f"pattern {pattern!r} cap {max_keys} is suspiciously low; reduce only after measuring"
        )


def test_rf463_summary_surfaces_patterns():
    from aria_service.learning.memory_replication import summary
    s = summary()
    assert "critical_patterns_tracked" in s
    assert s["critical_patterns_tracked"] >= 5
    assert "critical_patterns" in s
    assert any("verified_facts" in p for p in s["critical_patterns"])


def test_rf463_run_daily_backup_picks_up_pattern_keys(monkeypatch):
    import asyncio as _asyncio
    _asyncio.run(_async_run_daily_backup_picks_up_pattern_keys(monkeypatch))


async def _async_run_daily_backup_picks_up_pattern_keys(monkeypatch):
    """run_daily_backup must call scan_keys for each pattern and include
    the matched keys in the snapshot."""
    from aria_service.learning import memory_replication as _mr

    # Stub redis_store with controllable in-memory behaviour.
    class _FakeRS:
        store = {
            "crucix:aria:student:mastery": {"hi": "from singleton"},
            "aria:verified_facts:fact_aaa": {"claim": "VERIFIED a"},
            "aria:verified_facts:fact_bbb": {"claim": "VERIFIED b"},
            "crucix:aria:knowledge:shard:0": {"shard": 0},
        }
        @staticmethod
        async def get_json(key):
            v = _FakeRS.store.get(key)
            return v if isinstance(v, (dict, list)) else None
        @staticmethod
        async def get(key):
            v = _FakeRS.store.get(key)
            return v if isinstance(v, str) else None
        @staticmethod
        async def scan_keys(pattern, count=200):
            # Trivial glob: ":*" suffix matches everything starting with prefix.
            if pattern.endswith(":*"):
                prefix = pattern[:-1]
                return [k for k in _FakeRS.store if k.startswith(prefix)]
            return [k for k in _FakeRS.store if k == pattern]

    import sys
    monkeypatch.setitem(sys.modules, "aria_service.intel.redis_store", _FakeRS)
    # Some functions inside the module also touch the disk-resident files
    # and shipping path; stub the file writes to /tmp so the test stays
    # fast and isolated.
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    monkeypatch.setattr(_mr, "_BACKUP_DIR", tmp)
    # Disable email shipping for the test
    monkeypatch.setenv("ARIA_BACKUP_EMAIL_ENABLED", "0")
    # Disable optional disk-resident-files snapshot (may not exist in test env)
    monkeypatch.setattr(_mr, "_DISK_FILES_TO_BACKUP", (), raising=False)

    result = await _mr.run_daily_backup()
    assert "error" not in result, f"backup failed: {result.get('error')}"
    # The keys we expect to land in the snapshot (loaded back from disk)
    import gzip, json
    fpath = tmp / result["file"]
    with gzip.open(fpath, "rt", encoding="utf-8") as fh:
        snap = json.load(fh)
    keys = snap["keys"]
    assert "crucix:aria:student:mastery" in keys, "singleton key dropped"
    assert "aria:verified_facts:fact_aaa" in keys, "R-F463 pattern key dropped"
    assert "aria:verified_facts:fact_bbb" in keys, "R-F463 pattern key dropped"
    assert "crucix:aria:knowledge:shard:0" in keys, "R-F463 shard pattern dropped"
    assert "truncated_patterns" in snap


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

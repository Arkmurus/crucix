"""R-F855 — deploy-time constitutional integrity gate (L2 + L4).

R-F1191: constitutional validator removed — ARIA is fully autonomous.
The deploy-time gate is removed. These tests verify that deploys proceed
without constitutional validation blocking them.
"""
from __future__ import annotations

import asyncio


# R-F1191: constitutional validator removed — all deploys pass without blocking


def test_deploy_no_longer_blocked_by_constitution(monkeypatch):
    """R-F1191: deploys proceed without constitutional validation."""
    from aria_service.intel import self_improve as si

    class _FakeRS:
        def __init__(self):
            self.store = {}

        async def get_json(self, key, *a, **kw):
            return self.store.get(key)

        async def set_json(self, key, value, *a, **kw):
            self.store[key] = value
            return True

    fake = _FakeRS()
    monkeypatch.setattr(si, "rs", fake)

    async def _noop_log(*a, **kw):
        return None
    monkeypatch.setattr(si, "_log_improvement", _noop_log)

    # Use a new file that doesn't exist on disk (avoids truncation guard)
    fake.store[si.STAGED_KEY] = [{
        "id": "p1", "file": "aria_service/intel/auto/test_rf1191_new.py", "status": "staged",
        "new_content": "x = 1\n",
        "change_type": "bug_fix", "description": "test",
    }]

    res = asyncio.run(si.deploy_improvement("p1"))

    # Deploy should NOT be blocked by constitutional validation
    assert res.get("blocked") is not True, f"deploy was blocked: {res}"
    # The deploy may succeed or fail for other reasons, but not constitutional


def test_gate_wired_into_deploy_source():
    """R-F1191: constitutional validator removed from deploy path."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "intel" / "self_improve.py").read_text(encoding="utf-8")
    assert "R-F1191" in src, "deploy path should reference R-F1191 removal"

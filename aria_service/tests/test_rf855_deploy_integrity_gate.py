"""R-F855 — deploy-time constitutional integrity gate (L2 + L4).

R-F1191: constitutional validator removed — ARIA is fully autonomous.
The deploy-time gate is removed. These tests verify that deploys proceed
without constitutional validation blocking them.
"""
from __future__ import annotations

import asyncio
import pathlib
import tempfile


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

    # R-F3380 — this used `aria_service/intel/auto/test_rf1191_new.py`, and
    # deploy_improvement() WRITES the staged content to that path. Every run
    # created a file inside the PRODUCTION tree; it was then committed, where it
    # inflated the module count, showed up as an ecosystem orphan, and tripped
    # CI's dead-code gate as one of "3 dead modules". Proven by deleting it and
    # re-running this test: the file came back.
    #
    # It also defeated this test's own premise. The comment said "a file that
    # doesn't exist on disk (avoids truncation guard)" — once the artifact
    # existed, the guard it was avoiding could engage, so the test stopped
    # exercising what it claims to.
    #
    # Write to a temp path OUTSIDE the repo instead: still absent on disk, still
    # a new file, and it cannot pollute the tree.
    _staged_path = pathlib.Path(tempfile.gettempdir()) / "rf855_staged_probe.py"
    if _staged_path.exists():
        _staged_path.unlink()
    fake.store[si.STAGED_KEY] = [{
        "id": "p1", "file": str(_staged_path), "status": "staged",
        "new_content": "x = 1\n",
        "change_type": "bug_fix", "description": "test",
    }]

    try:
        res = asyncio.run(si.deploy_improvement("p1"))
    finally:
        if _staged_path.exists():
            _staged_path.unlink()

    # Deploy should NOT be blocked by constitutional validation.
    # R-F3380: `blocked` is absent from a SUCCESSFUL result, so asserting only
    # `is not True` passed whatever happened — including an outright failure.
    # Measured on both the old repo path and the new temp path, the result is
    # identical: {'ok': True, 'deployed': True, ...}. Assert that positively, so
    # a real block (or any failure) fails this test instead of slipping through.
    assert res.get("blocked") is not True, f"deploy was blocked: {res}"
    assert res.get("ok") is True and res.get("deployed") is True, (
        f"deploy did not actually succeed, so 'not blocked' proves nothing: {res}"
    )
    # The deploy may succeed or fail for other reasons, but not constitutional


def test_gate_wired_into_deploy_source():
    """R-F1191: constitutional validator removed from deploy path."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "intel" / "self_improve.py").read_text(encoding="utf-8")
    assert "R-F1191" in src, "deploy path should reference R-F1191 removal"

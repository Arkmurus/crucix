"""R-F1766 — capability test for deploy proprioception (reconcile_live_deploys).

Drives the REAL self_improve.reconcile_live_deploys() and asserts the guarantee:
a committed change is only marked verified_live once the LIVE build_rev provably
serves its SHA; a change that never lands (past the CI grace) is flagged as a
deploy_verification_failure (self-heal/coder fuel), NOT a confabulated success;
a change still within the CI grace stays pending. This is the "changes actually
land, not just say so" gate.
"""
from __future__ import annotations

import asyncio
import time

import pytest


class _StubRS:
    """Captures STAGED_KEY get/set so we can drive + inspect reconcile."""
    def __init__(self, staged):
        self._staged = staged
        self.saved = None

    async def get_json(self, key):
        return self._staged

    async def set_json(self, key, value, *a, **kw):
        self.saved = value
        self._staged = value


def _run(staged, *, live_build_rev, monkeypatch):
    from aria_service.intel import self_improve as si
    from aria_service.autonomous import deploy_verifier as dv

    stub = _StubRS(staged)
    monkeypatch.setattr(si, "rs", stub)

    async def _fake_fetch(app):
        return live_build_rev
    monkeypatch.setattr(dv, "_http_fetch_build_rev", _fake_fetch)

    wins, fails, gaps = [], [], []
    monkeypatch.setattr(si, "wire_success", lambda **k: wins.append(k))
    monkeypatch.setattr(si, "wire_failure", lambda **k: fails.append(k))

    import aria_service.intel.capability_gaps as cg
    async def _spy_gap(**k):
        gaps.append(k)
    monkeypatch.setattr(cg, "record_gap", _spy_gap)

    result = asyncio.run(si.reconcile_live_deploys())
    return result, stub.saved, wins, fails, gaps


def test_landed_change_is_verified_live(monkeypatch):
    staged = [{
        "id": "imp1", "status": "deployed", "commit_sha": "aaaa1111deadbeef",
        "verified_live": False, "deployed_at": time.time(),
        "change_type": "bug_fix", "file": "aria_service/intel/x.py",
    }]
    result, saved, wins, fails, gaps = _run(
        staged, live_build_rev="R-F1766 · sha aaaa1111", monkeypatch=monkeypatch)

    assert result["verified"] == 1 and result["failed"] == 0
    item = [s for s in saved if s["id"] == "imp1"][0]
    assert item["verified_live"] is True, "must flip to verified once live"
    assert any("VERIFIED LIVE" in (w.get("summary") or "") for w in wins)
    assert not fails and not gaps


def test_unlanded_past_grace_is_flagged_failure_not_success(monkeypatch):
    """The honesty gate: a change that NEVER reached the live server (past the
    CI grace) is a deploy_verification_failure — NOT a confabulated success."""
    monkeypatch.setenv("ARIA_DEPLOY_VERIFY_GRACE_S", "60")
    staged = [{
        "id": "imp2", "status": "deployed", "commit_sha": "bbbb2222",
        "verified_live": False, "deployed_at": time.time() - 9999,  # long past grace
        "change_type": "bug_fix", "file": "aria_service/intel/y.py",
    }]
    result, saved, wins, fails, gaps = _run(
        staged, live_build_rev="R-F1760 · sha deadbeef", monkeypatch=monkeypatch)

    assert result["failed"] == 1 and result["verified"] == 0
    item = [s for s in saved if s["id"] == "imp2"][0]
    assert item.get("verified_live") is not True, "must NOT claim live"
    assert item.get("verify_failed") is True
    assert any(f.get("gap_type") == "deploy_verification_failure" for f in fails)
    assert any(g.get("gap_type") == "deploy_verification_failure" for g in gaps), \
        "must record a gap so self-heal/coder retries the deploy"


def test_unlanded_within_grace_stays_pending(monkeypatch):
    """A change still within the CI grace is pending, not failed (CI building)."""
    monkeypatch.setenv("ARIA_DEPLOY_VERIFY_GRACE_S", "1200")
    staged = [{
        "id": "imp3", "status": "deployed", "commit_sha": "cccc3333",
        "verified_live": False, "deployed_at": time.time(),  # just committed
        "change_type": "bug_fix", "file": "aria_service/intel/z.py",
    }]
    result, saved, wins, fails, gaps = _run(
        staged, live_build_rev="R-F1760 · sha deadbeef", monkeypatch=monkeypatch)

    assert result["pending"] == 1 and result["failed"] == 0 and result["verified"] == 0
    assert not fails and not gaps

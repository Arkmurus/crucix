"""R-F1773 — capability test for the UNIVERSAL deploy-intent ledger.

The disease this kills: a RAW `git push` (as ARIA did for R-F1770) bypasses the
self_improve reconciler and can confabulate "deployed" with no proof it went live.
The fix: a pre-push hook records EVERY push's commit SHA as a deploy-intent, and the
proprioception loop verifies each one actually landed → deploy_verification_failure
gap if it never did.

These drive the REAL path end-to-end:
  • add_deploy_intent (record, dedup, cap)
  • reconcile_deploy_intents with an injected fetcher (verified / pending / failed)
  • the POST/GET endpoints round-trip (pod/hook → brain)
asserting the user-visible outcome: a landed push is marked verified, an un-landed
push past grace emits exactly one failure gap, and within-grace stays pending.
"""
from __future__ import annotations

import asyncio

from aria_service.autonomous import deploy_verifier as dv


def _fetcher(build_rev):
    async def f(_app):
        return build_rev
    return f


def test_add_intent_dedup_and_cap():
    intents = []
    intents = dv.add_deploy_intent(intents, "abc12345def", "pre_push_hook")
    intents = dv.add_deploy_intent(intents, "abc12345def", "ci_deploy")  # same sha re-push
    assert len(intents) == 1  # deduped by sha
    assert intents[0]["source"] == "ci_deploy"  # latest wins, clock reset
    assert intents[0]["verified_live"] is False
    # empty sha is a no-op
    assert len(dv.add_deploy_intent(intents, "", "x")) == 1
    # cap
    many = []
    for i in range(dv.MAX_INTENTS + 10):
        many = dv.add_deploy_intent(many, f"{i:040x}", "h")
    assert len(many) == dv.MAX_INTENTS


def test_reconcile_marks_verified_when_live():
    intents = dv.add_deploy_intent([], "1a939896c0ffee", "pre_push_hook")
    updated, gaps = asyncio.run(dv.reconcile_deploy_intents(
        intents, fetcher=_fetcher("R-F1773 · sha 1a939896")))
    assert updated[0]["verified_live"] is True
    assert gaps == []  # nothing failed


def test_reconcile_pending_within_grace_no_gap():
    # at = now → still within grace → not yet verified, but NO failure gap.
    intents = dv.add_deploy_intent([], "deadbeef1234", "pre_push_hook", at=1000.0)
    updated, gaps = asyncio.run(dv.reconcile_deploy_intents(
        intents, fetcher=_fetcher("R-F1 · sha 99999999"), grace_s=1200.0, now=1100.0))
    assert updated[0]["verified_live"] is False
    assert updated[0]["failure_recorded"] is False
    assert gaps == []  # within grace → patience, not a failure


def test_reconcile_emits_failure_gap_past_grace_once():
    intents = dv.add_deploy_intent([], "deadbeef1234", "pre_push_hook", at=1000.0)
    # now is well past grace and the live build_rev never served the sha
    updated, gaps = asyncio.run(dv.reconcile_deploy_intents(
        intents, fetcher=_fetcher("R-F1 · sha 99999999"), grace_s=1200.0, now=5000.0))
    assert updated[0]["verified_live"] is False
    assert updated[0]["failure_recorded"] is True
    assert len(gaps) == 1
    assert gaps[0]["commit_sha"] == "deadbeef1234"
    # re-running must NOT re-emit (failure_recorded guards it)
    updated2, gaps2 = asyncio.run(dv.reconcile_deploy_intents(
        updated, fetcher=_fetcher("R-F1 · sha 99999999"), grace_s=1200.0, now=6000.0))
    assert gaps2 == []


def test_superseded_intent_does_not_cry_wolf():
    # The real scenario: 92dc032e was live, then 712f5c53 deployed over it before the
    # loop verified 92dc032e (the loop had been down). 92dc032e is past grace and NOT
    # in the live build_rev — but it was SUPERSEDED, not failed. No gap may fire.
    old = dv.add_deploy_intent([], "92dc032e0000", "pre_push_hook", at=1000.0)
    both = dv.add_deploy_intent(old, "712f5c530000", "pre_push_hook", at=2000.0)
    # live now serves the NEWER commit; the older is well past grace
    updated, gaps = asyncio.run(dv.reconcile_deploy_intents(
        both, fetcher=_fetcher("R-F1773 · sha 712f5c53"), grace_s=1200.0, now=9000.0))
    newer = [i for i in updated if i["commit_sha"] == "712f5c530000"][0]
    older = [i for i in updated if i["commit_sha"] == "92dc032e0000"][0]
    assert newer["verified_live"] is True
    assert older["verified_live"] is False
    assert older.get("superseded") is True
    assert gaps == []  # superseded ≠ failed — NO cry-wolf gap


class _FakeReq:
    def __init__(self, body):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeRS:
    def __init__(self):
        self.store = {}

    async def set_json(self, key, value, *a, **kw):
        self.store[key] = value

    async def get_json(self, key):
        return self.store.get(key)


def test_endpoint_post_then_get_roundtrip(monkeypatch):
    from aria_service.routes import aria as a
    rs = _FakeRS()
    monkeypatch.setattr(a, "rs", rs)

    out = asyncio.run(a.deploy_intent_post_ep(_FakeReq({"commit_sha": "feedface0001", "source": "pre_push_hook"})))
    assert out["ok"] is True
    assert out["recorded"] == "feedface"
    assert out["pending"] == 1

    got = asyncio.run(a.deploy_intents_get_ep())
    assert got["total"] == 1
    assert got["pending"] == 1
    assert got["intents"][0]["commit_sha"] == "feedface0001"


def test_endpoint_rejects_missing_sha(monkeypatch):
    from aria_service.routes import aria as a
    from fastapi import HTTPException
    rs = _FakeRS()
    monkeypatch.setattr(a, "rs", rs)
    try:
        asyncio.run(a.deploy_intent_post_ep(_FakeReq({"source": "x"})))
        assert False, "should have raised"
    except HTTPException as e:
        assert e.status_code == 400


def test_reconcile_via_store_drives_whole_glue():
    # Catches the dead-loop bug: the proprioception loop's glue (read store →
    # reconcile → persist → record gaps) is exercised end-to-end with a fake store.
    # A landed intent flips verified; an un-landed one past grace records a gap.
    store = _FakeRS()
    # The failing commit must be the NEWEST push (no later live commit supersedes it),
    # else the supersede guard correctly treats it as live-by-ancestry. at=2000 > 1000.
    landed = dv.add_deploy_intent([], "1a939896c0ffee", "pre_push_hook", at=1000.0)
    stale = dv.add_deploy_intent(landed, "deadbeef1234", "pre_push_hook", at=2000.0)
    asyncio.run(store.set_json(dv.DEPLOY_INTENTS_KEY, stale))

    recorded = []

    async def gap_recorder(g):
        recorded.append(g)

    res = asyncio.run(dv.reconcile_intents_via_store(
        store, fetcher=_fetcher("R-F1773 · sha 1a939896"),
        grace_s=1200.0, gap_recorder=gap_recorder))
    # 1a93… is "live" → verified; deadbeef never lived + past grace → failure gap
    assert res["verified"] == 1
    assert res["failed"] == 1
    assert len(recorded) == 1 and recorded[0]["commit_sha"] == "deadbeef1234"
    # ledger persisted back through the store
    persisted = asyncio.run(store.get_json(dv.DEPLOY_INTENTS_KEY))
    assert any(i["commit_sha"] == "1a939896c0ffee" and i["verified_live"] for i in persisted)


def test_loop_glue_imports_resolve():
    # Regression guard for the exact dead-loop cause: the loop imports the store as
    # `from .intel import redis_store` (NOT the nonexistent `.state_store`) and that
    # module must expose the async get_json/set_json the glue calls.
    from aria_service.intel import redis_store as rs_module
    assert hasattr(rs_module, "get_json") and hasattr(rs_module, "set_json")
    import importlib
    try:
        importlib.import_module("aria_service.state_store")
        raise AssertionError("aria_service.state_store should NOT exist (was the bad import)")
    except ModuleNotFoundError:
        pass  # correct — the bad path is genuinely absent

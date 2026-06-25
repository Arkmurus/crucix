"""R-F1920 — deploy-reconciler: alert the operator when origin/main sits ahead
of the live build_rev past a threshold.

The §19e gap this closes: R-F1773's intent ledger detects pushed-but-not-live
commits but only records a CODER gap — which the coder can't action for
human/Claude-authored commits — so a batch of undeployed commits never reached
the operator (he discovered it himself). This reconciler uses GitHub as the
authoritative origin truth and ALERTS the operator.

Capability tests drive the REAL reconcile functions with injected fetchers (no
network) and assert the operator-alert fires exactly when it should, once.
"""
from __future__ import annotations

import asyncio

from aria_service.autonomous import deploy_verifier as dv


def _gh(sha):
    async def f(repo, branch, token):
        return sha
    return f


def _live(build_rev):
    async def f(app):
        return build_rev
    return f


GRACE = 600.0
ORIGIN = "abcdef1234567890abcdef1234567890abcdef12"  # short = abcdef12
LIVE_BEHIND = "R-F1900 · sha 99999999"               # serves a different sha
LIVE_AT_ORIGIN = "R-F1920 · sha abcdef12"            # serves origin's short


def test_origin_live_clears_state():
    r = asyncio.run(dv.reconcile_origin_vs_live(
        state={"origin_sha": "abcdef12", "first_seen_at": 0.0, "alerted": True},
        gh_fetcher=_gh(ORIGIN), live_fetcher=_live(LIVE_AT_ORIGIN),
        behind_alert_s=GRACE, now=10_000.0))
    assert r["behind"] is False
    assert r["state"] == {}  # cleared
    assert r["alert"] is None


def test_behind_within_grace_no_alert():
    # first time we see this head behind → clock starts, age ~0 < grace
    r = asyncio.run(dv.reconcile_origin_vs_live(
        state={}, gh_fetcher=_gh(ORIGIN), live_fetcher=_live(LIVE_BEHIND),
        behind_alert_s=GRACE, now=10_000.0))
    assert r["behind"] is True
    assert r["alert"] is None
    assert r["state"]["origin_sha"] == "abcdef12"
    assert r["state"]["first_seen_at"] == 10_000.0
    assert r["state"]["alerted"] is False


def test_behind_past_grace_alerts_once():
    # head first seen at t=10_000; now t=10_700 (700s > 600s grace) → ALERT
    st = {"origin_sha": "abcdef12", "first_seen_at": 10_000.0, "alerted": False}
    r1 = asyncio.run(dv.reconcile_origin_vs_live(
        state=st, gh_fetcher=_gh(ORIGIN), live_fetcher=_live(LIVE_BEHIND),
        behind_alert_s=GRACE, now=10_700.0))
    assert r1["behind"] is True
    assert r1["alert"] is not None
    assert r1["alert"]["origin_sha"] == "abcdef12"
    assert r1["alert"]["age_s"] == 700.0
    assert r1["state"]["alerted"] is True

    # next tick, still behind, same head → must NOT re-alert
    r2 = asyncio.run(dv.reconcile_origin_vs_live(
        state=r1["state"], gh_fetcher=_gh(ORIGIN), live_fetcher=_live(LIVE_BEHIND),
        behind_alert_s=GRACE, now=11_000.0))
    assert r2["behind"] is True
    assert r2["alert"] is None  # already alerted for this head


def test_new_head_resets_clock():
    # an OLDER head was being tracked; a brand-new commit landed on origin →
    # reset the clock (give the new deploy time), do not alert immediately.
    new_origin = "ffffffff0000ffffffff0000ffffffff0000ffff"  # short ffffffff
    st = {"origin_sha": "abcdef12", "first_seen_at": 0.0, "alerted": True}
    r = asyncio.run(dv.reconcile_origin_vs_live(
        state=st, gh_fetcher=_gh(new_origin), live_fetcher=_live(LIVE_BEHIND),
        behind_alert_s=GRACE, now=10_000.0))
    assert r["behind"] is True
    assert r["alert"] is None              # clock reset → age ~0
    assert r["state"]["origin_sha"] == "ffffffff"
    assert r["state"]["first_seen_at"] == 10_000.0
    assert r["state"]["alerted"] is False


def test_origin_undetermined_is_noop():
    # GH API down / no token → don't touch state, don't alert
    st = {"origin_sha": "abcdef12", "first_seen_at": 0.0, "alerted": False}
    r = asyncio.run(dv.reconcile_origin_vs_live(
        state=st, gh_fetcher=_gh(None), live_fetcher=_live(LIVE_BEHIND),
        behind_alert_s=GRACE, now=10_000.0))
    assert r["behind"] is False
    assert r["alert"] is None
    assert r["state"] == st  # untouched


def test_via_store_persists_and_notifies():
    """The proprioception-loop glue: reads/writes state through a store and
    fires the operator_notifier when past grace."""
    store = {dv.ORIGIN_RECONCILE_STATE_KEY: {
        "origin_sha": "abcdef12", "first_seen_at": 0.0, "alerted": False}}

    class _RS:
        async def get_json(self, k):
            return store.get(k)
        async def set_json(self, k, v, ex=None):
            store[k] = v

    alerts = []

    async def _notify(alert):
        alerts.append(alert)

    # first_seen_at=0 with real now() → age is enormous > grace → must alert
    res = asyncio.run(dv.reconcile_origin_via_store(
        _RS(), token="x", gh_fetcher=_gh(ORIGIN), live_fetcher=_live(LIVE_BEHIND),
        behind_alert_s=GRACE, operator_notifier=_notify))
    assert res["behind"] is True
    assert res["alerted"] is True
    assert len(alerts) == 1
    assert alerts[0]["origin_sha"] == "abcdef12"
    # state persisted with alerted=True so the next tick won't re-fire
    assert store[dv.ORIGIN_RECONCILE_STATE_KEY]["alerted"] is True

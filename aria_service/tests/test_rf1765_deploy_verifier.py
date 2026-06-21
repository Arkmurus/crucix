"""R-F1765 — capability tests for the self-verifying-deploy primitive.

Asserts the CORE honesty property: is_sha_live returns True ONLY when the live
build_rev provably contains the expected commit SHA, and returns False (never a
confabulated success) on mismatch or unreachable. This is what turns "I committed
it" into "it is provably running" for ARIA's autonomous deploys.
"""
from __future__ import annotations

import asyncio

from aria_service.autonomous import deploy_verifier as dv


def test_build_rev_matches_substring_and_rejects_mismatch():
    # Real build_rev shape from /health/live: "R-F1765 · sha 1a939896"
    assert dv.build_rev_matches("R-F1765 · sha 1a939896", "1a939896c0ffee00")
    assert dv.build_rev_matches("sha 1a939896", "1a939896")
    # Mismatch / empty / None must NEVER match (no confabulation).
    assert not dv.build_rev_matches("R-F1760 · sha deadbeef", "1a939896")
    assert not dv.build_rev_matches("", "1a939896")
    assert not dv.build_rev_matches(None, "1a939896")
    assert not dv.build_rev_matches("sha 1a939896", "")


def test_extract_live_sha():
    assert dv.extract_live_sha("R-F1760+R-F1761 · sha 1a939896") == "1a939896"
    assert dv.extract_live_sha(None) is None


def test_is_sha_live_true_when_live_matches():
    async def _fetch(app):
        return "R-F1765 · sha 1a939896"
    out = asyncio.run(dv.is_sha_live("1a939896c0ffee", attempts=1, interval_s=0, fetcher=_fetch))
    assert out is True


def test_is_sha_live_false_on_timeout_is_honest():
    """THE honesty gate: if the live build_rev never advances to the expected
    SHA, is_sha_live returns False — it does NOT assume the deploy landed."""
    calls = {"n": 0}
    async def _fetch_stale(app):
        calls["n"] += 1
        return "R-F1760 · sha deadbeef"  # never the expected sha
    out = asyncio.run(dv.is_sha_live("1a939896", attempts=3, interval_s=0, fetcher=_fetch_stale))
    assert out is False, "must NOT confabulate success when SHA isn't live"
    assert calls["n"] == 3, "must actually poll all attempts before giving up"


def test_is_sha_live_false_when_unreachable():
    async def _fetch_none(app):
        return None  # /health/live unreachable
    out = asyncio.run(dv.is_sha_live("1a939896", attempts=2, interval_s=0, fetcher=_fetch_none))
    assert out is False


def test_is_sha_live_eventually_true_after_rolling_restart():
    """Realistic: build_rev is stale for the first polls (rolling restart),
    then advances — is_sha_live must catch the transition, not give up early."""
    seq = ["sha old11111", "sha old11111", "R-F1765 · sha 1a939896"]
    idx = {"i": 0}
    async def _fetch_seq(app):
        v = seq[min(idx["i"], len(seq) - 1)]
        idx["i"] += 1
        return v
    out = asyncio.run(dv.is_sha_live("1a939896", attempts=5, interval_s=0, fetcher=_fetch_seq))
    assert out is True


def test_verify_deploy_landed_reports_truth():
    async def _live(app):
        return "R-F1765 · sha 1a939896"
    landed = asyncio.run(dv.verify_deploy_landed("1a939896", fetcher=_live))
    assert landed["landed"] is True
    assert landed["live_sha"] == "1a939896"
    assert landed["expected"] == "1a939896"

    async def _stale(app):
        return "sha deadbeef"
    not_landed = asyncio.run(dv.verify_deploy_landed("1a939896", fetcher=_stale))
    assert not_landed["landed"] is False
    assert not_landed["live_sha"] == "deadbeef"


def test_reconcile_flags_landed_and_unlanded():
    """The proprioception reconcile: a committed item whose SHA is live →
    verified_live True; one whose SHA never landed → verified_live False
    (becomes deploy_verification_failure fuel). Items w/o sha / already-verified
    are skipped."""
    async def _fetch(app):
        return "R-F1765 · sha aaaa1111"  # only aaaa1111 is live

    items = [
        {"id": "landed", "commit_sha": "aaaa1111deadbeef", "verified_live": False},
        {"id": "stuck", "commit_sha": "bbbb2222", "verified_live": False},
        {"id": "nosha", "verified_live": False},          # skip — no commit_sha
        {"id": "already", "commit_sha": "cccc3333", "verified_live": True},  # skip
    ]
    verdicts = asyncio.run(dv.reconcile_committed_deploys(items, fetcher=_fetch))
    by_id = {v["id"]: v for v in verdicts}
    assert set(by_id) == {"landed", "stuck"}, "must skip no-sha + already-verified"
    assert by_id["landed"]["verified_live"] is True
    assert by_id["stuck"]["verified_live"] is False  # honest: not live → flagged

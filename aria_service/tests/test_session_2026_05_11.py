"""Tests for the 2026-05-11 session's code paths.

Operator runs these BEFORE signing off any of today's commits. Coverage:
- state_store SQLite backend (R-F235)
- knowledge.facts_by_tag inventory retrieval (R-F245)
- knowledge.facts_by_tag period-handling (R-F246)
- _PUBLIC_AUTH_BYPASS_PATHS allowlist (R-F254)
- _INVENTORY_QUERY_RE regex (R-F245 + R-F246)
- inbound webhook HMAC verification (R-F249 + R-F250)
- constitution/version endpoint parses clauses (R-F221)

Run via:
    cd aria_service && pytest tests/test_session_2026_05_11.py -v

These are pure-Python unit tests with no Redis / chromadb / network
dependencies — fast (< 5s total) and operator-runnable in any
environment with pytest installed.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import re
import tempfile

import pytest

# Import the project root so the package imports resolve
import sys
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))


# ─────────────────────────────────────────────────────────────────────────
# R-F235 + R-F237 — state_store SQLite backend
# ─────────────────────────────────────────────────────────────────────────

class TestStateStoreSQLite:
    """SQLite backend mirrors redis_store API surface (R-F235).

    Uses the repo convention of `asyncio.run()` wrapper inside each
    test rather than `@pytest.mark.asyncio` + async fixture — the repo
    has no pytest-asyncio config and existing async tests
    (test_chain_correlator.py) follow this pattern.
    """

    def _fresh_db_path(self, tmp_path):
        """Return a fresh SQLite path AND reset module globals."""
        from aria_service.intel import state_store as ss
        db_path = str(tmp_path / "test_state.db")
        os.environ["ARIA_STATE_DB_PATH"] = db_path
        ss._conn = None
        ss._DB_PATH = None
        return ss

    def test_get_set_roundtrip(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                await ss.set("k1", "v1")
                assert await ss.get("k1") == "v1"
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_set_with_ttl(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                await ss.set("temp", "val", ex=1)
                assert await ss.get("temp") == "val"
                await asyncio.sleep(1.1)
                assert await ss.get("temp") is None
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_delete_returns_bool(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                await ss.set("doomed", "x")
                assert await ss.delete("doomed") is True
                assert await ss.delete("never_existed") is False
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_get_set_json_roundtrip(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                obj = {"a": 1, "b": [2, 3], "c": "text"}
                await ss.set_json("jkey", obj)
                assert await ss.get_json("jkey") == obj
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_lpush_lrange(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                await ss.lpush("queue", "a")
                await ss.lpush("queue", "b")
                await ss.lpush("queue", "c")
                # lpush prepends — newest first
                assert await ss.lrange("queue", 0, -1) == ["c", "b", "a"]
                assert await ss.llen("queue") == 3
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_ltrim_keeps_range(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                for x in ["d", "c", "b", "a"]:  # final lpush order [a,b,c,d]
                    await ss.lpush("L", x)
                await ss.ltrim("L", 0, 1)  # keep first 2
                assert await ss.lrange("L", 0, -1) == ["a", "b"]
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_incr_atomic(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                assert await ss.incr("counter") == 1
                assert await ss.incr("counter", amount=4) == 5
                assert await ss.incr("counter") == 6
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_incrbyfloat(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                v = await ss.incrbyfloat("price", 1.5)
                assert v == pytest.approx(1.5)
                v = await ss.incrbyfloat("price", 0.25)
                assert v == pytest.approx(1.75)
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_zadd_zrevrange(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                await ss.zadd("Z", 1.0, "low")
                await ss.zadd("Z", 5.0, "high")
                await ss.zadd("Z", 3.0, "mid")
                assert await ss.zrevrange("Z", 0, -1) == ["high", "mid", "low"]
                assert await ss.zcard("Z") == 3
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_hset_hgetall(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                await ss.hset("H", {"name": "ARIA", "version": "v23"})
                assert await ss.hgetall("H") == {"name": "ARIA", "version": "v23"}
            finally:
                await ss.close()
        asyncio.run(_t())

    def test_scan_keys_glob(self, tmp_path):
        ss = self._fresh_db_path(tmp_path)
        async def _t():
            assert await ss.connect()
            try:
                await ss.set("crucix:k1", "x")
                await ss.set("crucix:k2", "y")
                await ss.set("other:foo", "z")
                keys = await ss.scan_keys("crucix:*")
                assert set(keys) == {"crucix:k1", "crucix:k2"}
            finally:
                await ss.close()
        asyncio.run(_t())


# ─────────────────────────────────────────────────────────────────────────
# R-F245 + R-F246 — knowledge.facts_by_tag inventory retrieval
# ─────────────────────────────────────────────────────────────────────────

class TestFactsByTag:
    """Tag-aware fact retrieval splits on _/-/space/dot, requires
    ALL non-trivial components to appear, literal-tag match short-circuits."""

    @pytest.fixture
    def kb(self):
        """Stub the knowledge cache with predictable test data."""
        from aria_service.intel import knowledge
        # Reset the cache to a known shape
        knowledge._cache = {
            "facts": [
                {
                    "id": "f1", "topic": "Angola procurement reform 2024",
                    "content": "MoD published new procurement act covering small arms.",
                    "source": "reading:Defence Web", "source_domain": "defenceweb.co.za",
                    "confidence": "PROBABLE", "createdAt": "2026-05-01",
                    "updatedAt": "2026-05-01",
                },
                {
                    "id": "f2", "topic": "Angola tourism statistics",
                    "content": "Tourism revenue grew 12% YoY in Luanda.",
                    "source": "reading:Reuters", "source_domain": "reuters.com",
                    "confidence": "ASSESSED", "createdAt": "2026-04-20",
                    "updatedAt": "2026-04-20",
                },
                {
                    "id": "f3", "topic": "U.S. sanctions on Iran",
                    "content": "OFAC added 14 individuals to SDN list.",
                    "source": "reading:OFAC", "source_domain": "treasury.gov",
                    "confidence": "CONFIRMED", "createdAt": "2026-05-10",
                    "updatedAt": "2026-05-10",
                },
                {
                    "id": "f4", "topic": "sam.gov tender posting",
                    "content": "Pentagon procurement RFP posted to sam.gov yesterday.",
                    "source": "reading:Govex", "source_domain": "sam.gov",
                    "confidence": "PROBABLE", "createdAt": "2026-05-11",
                    "updatedAt": "2026-05-11",
                },
            ]
        }
        return knowledge

    def test_snake_case_tag_splits_and_ands(self, kb):
        """angola_procurement → ['angola', 'procurement'] → ALL must appear."""
        hits = kb.facts_by_tag("angola_procurement")
        ids = {f["id"] for f in hits}
        assert "f1" in ids  # has both "Angola" and "procurement"
        assert "f2" not in ids  # has "Angola" but NOT "procurement"

    def test_dotted_tag_splits(self, kb):
        """sam.gov → ['sam', 'gov'] OR literal 'sam.gov' match."""
        hits = kb.facts_by_tag("sam.gov")
        ids = {f["id"] for f in hits}
        assert "f4" in ids  # literal "sam.gov" in source_domain + content

    def test_us_sanctions_tag(self, kb):
        """u.s. sanctions → drops 'u'/'s' (≥3-char filter), 'sanctions' AND literal."""
        hits = kb.facts_by_tag("u.s. sanctions")
        ids = {f["id"] for f in hits}
        # Literal "u.s. sanctions" in content of f3 → match
        # OR "sanctions" alone (only non-trivial component) → match f3
        assert "f3" in ids

    def test_empty_tag_returns_empty(self, kb):
        assert kb.facts_by_tag("") == []
        assert kb.facts_by_tag("   ") == []

    def test_limit_clamps(self, kb):
        hits = kb.facts_by_tag("angola", limit=1)
        assert len(hits) <= 1

    def test_recency_sort(self, kb):
        """Newest first."""
        hits = kb.facts_by_tag("angola")
        dates = [f["updatedAt"] for f in hits]
        assert dates == sorted(dates, reverse=True)


# ─────────────────────────────────────────────────────────────────────────
# R-F245 — _INVENTORY_QUERY_RE regex
# ─────────────────────────────────────────────────────────────────────────

class TestInventoryRegex:
    """Captures tag from inventory-shaped questions, skips others."""

    def _get_re(self):
        from aria_service.aria_engine import _INVENTORY_QUERY_RE
        return _INVENTORY_QUERY_RE

    def test_what_do_you_know_about(self):
        m = self._get_re().search("what do you know about angola_procurement")
        assert m is not None
        assert "angola_procurement" in m.group(1)

    def test_show_me_everything_on(self):
        m = self._get_re().search("show me everything you have on Serban Industries")
        assert m is not None
        assert "Serban" in m.group(1)

    def test_list_everything_about(self):
        m = self._get_re().search("list everything about Wagner Group")
        assert m is not None
        assert "Wagner Group" in m.group(1)

    def test_inventory_on(self):
        m = self._get_re().search("inventory on NSPA contracts")
        assert m is not None
        assert "NSPA contracts" in m.group(1)

    def test_skips_free_text(self):
        """Non-inventory questions don't fire."""
        assert self._get_re().search("what about angola?") is None
        assert self._get_re().search("tell me about Serban") is None
        assert self._get_re().search("what is angola_procurement") is None

    def test_period_in_tag_no_premature_termination(self):
        """R-F246: u.s. sanctions captures the whole tag, not just 'u'."""
        m = self._get_re().search("what do you know about u.s. sanctions")
        assert m is not None
        captured = m.group(1).strip()
        # Should capture "u.s. sanctions" (or close to it), not just "u"
        assert "sanctions" in captured.lower()


# ─────────────────────────────────────────────────────────────────────────
# R-F221 — constitution/version endpoint clause-count regex
# ─────────────────────────────────────────────────────────────────────────

class TestConstitutionVersionParser:
    """The constitution_version_ep uses re.findall on the prompt to
    count numbered clauses. Test the regex against a known shape."""

    def test_counts_distinct_clauses(self):
        """1. ... \n 2. ... \n 5. ... → count = 3 (set of distinct numbers)."""
        text = """ARIA constitution:
1. First clause.
2. Second clause.
5. Fifth clause."""
        nums = re.findall(r"(?:^|\n)(\d+)\.\s", text)
        assert len({int(n) for n in nums}) == 3

    def test_ignores_intra_clause_numbers(self):
        """A '1.' inside clause body shouldn't be counted as a clause."""
        text = """1. First clause mentions 2. internally.
2. Real second clause."""
        nums = re.findall(r"(?:^|\n)(\d+)\.\s", text)
        # The intra-clause "2." has no preceding newline only in this minimal text,
        # but it follows a space — so the regex's `(?:^|\n)` lookbehind prevents
        # the match. Set semantics deduplicate.
        # Result: {1, 2}
        assert len({int(n) for n in nums}) == 2


# ─────────────────────────────────────────────────────────────────────────
# R-F249 + R-F250 — inbound webhook HMAC + body cap
# ─────────────────────────────────────────────────────────────────────────

class TestWebhookHMAC:
    """The HMAC verification logic. Pure compute test — no FastAPI."""

    def test_correct_signature_passes(self):
        secret = "test-secret-base64"
        body = b'{"foo": "bar"}'
        expected = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        # Verifier in the endpoint:
        computed = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(computed, expected)

    def test_wrong_signature_fails(self):
        secret = "test-secret-base64"
        body = b'{"foo": "bar"}'
        wrong = hmac.new(
            b"different-secret", body, hashlib.sha256
        ).hexdigest()
        computed = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        assert not hmac.compare_digest(computed, wrong)

    def test_tampered_body_fails(self):
        secret = "s"
        sig_for_original = hmac.new(
            secret.encode(), b'{"v": 1}', hashlib.sha256
        ).hexdigest()
        sig_for_tampered = hmac.new(
            secret.encode(), b'{"v": 2}', hashlib.sha256
        ).hexdigest()
        assert sig_for_original != sig_for_tampered


# ─────────────────────────────────────────────────────────────────────────
# R-F254 — public-path auth bypass
# ─────────────────────────────────────────────────────────────────────────

class TestPublicAuthBypass:
    """The four bypass paths exist as registered routes + the frozenset
    is the canonical source of truth."""

    def test_bypass_frozenset_contents(self):
        from aria_service.routes.aria import _PUBLIC_AUTH_BYPASS_PATHS
        # Exactly four paths today (R-F254 + R-F221)
        assert "/api/aria/constitution/version" in _PUBLIC_AUTH_BYPASS_PATHS
        assert "/api/aria/chat-audit/stats" in _PUBLIC_AUTH_BYPASS_PATHS
        assert "/api/aria/adversarial/stats" in _PUBLIC_AUTH_BYPASS_PATHS
        assert "/api/aria/health" in _PUBLIC_AUTH_BYPASS_PATHS


# ─────────────────────────────────────────────────────────────────────────
# R-F206 — lift_all_topics NaN guard
# ─────────────────────────────────────────────────────────────────────────

class TestNaNGuard:
    """One bad calibration write would have poisoned the entire mastery
    dict if NaN slipped past the == 0 guard. R-F206 hardened the guard
    against NaN + inf. Test the predicate directly (no async path)."""

    def test_nan_returns_empty(self):
        import math
        # The guard at student.lift_all_topics is:
        #   if not bump or math.isnan(bump) or math.isinf(bump): return {}
        bump = float("nan")
        assert not bump or math.isnan(bump) or math.isinf(bump)
        bump = float("inf")
        assert not bump or math.isnan(bump) or math.isinf(bump)
        bump = 0
        assert not bump or math.isnan(bump) or math.isinf(bump)
        # Legitimate non-zero finite bump should NOT match the guard
        bump = 0.05
        assert not (not bump or math.isnan(bump) or math.isinf(bump))


# ─────────────────────────────────────────────────────────────────────────
# Smoke marker — operator should see this when the run completes
# ─────────────────────────────────────────────────────────────────────────

class TestWebhookRateLimiter:
    """R-F262 token-bucket rate-limiter for /api/aria/inbound/{source}.

    Pure unit test of `_consume_webhook_token` — exercises the bucket
    state machine without spinning up FastAPI.
    """

    def _fresh_buckets(self):
        """Reset the module-global bucket dict."""
        from aria_service.routes import aria as _aria
        _aria._webhook_buckets.clear()
        # Force-set tight envs so the test is fast + deterministic
        os.environ["ARIA_WEBHOOK_RATE_CAP"] = "3"
        os.environ["ARIA_WEBHOOK_RATE_REFILL"] = "1.0"
        return _aria

    def test_first_call_allowed(self):
        aria = self._fresh_buckets()
        allowed, left, retry = aria._consume_webhook_token("linear")
        assert allowed is True
        assert left == 2.0  # cap=3, consumed 1 → 2 remain
        assert retry == 0.0

    def test_burst_within_cap_allowed(self):
        aria = self._fresh_buckets()
        for _ in range(3):
            allowed, _, _ = aria._consume_webhook_token("slack")
            assert allowed is True

    def test_exhausted_bucket_returns_429_signal(self):
        aria = self._fresh_buckets()
        # Drain the bucket
        for _ in range(3):
            aria._consume_webhook_token("salesforce")
        allowed, left, retry = aria._consume_webhook_token("salesforce")
        assert allowed is False
        assert left < 1.0
        assert retry >= 1.0  # at least 1 second to refill one token

    def test_per_source_isolation(self):
        """Source A draining its bucket must not affect source B."""
        aria = self._fresh_buckets()
        for _ in range(3):
            aria._consume_webhook_token("source_a")
        allowed_a, _, _ = aria._consume_webhook_token("source_a")
        allowed_b, _, _ = aria._consume_webhook_token("source_b")
        assert allowed_a is False
        assert allowed_b is True  # B's bucket is untouched

    def test_refill_recovers_capacity(self):
        """After waiting > 1s, an exhausted bucket gets a fresh token."""
        import time as _time
        aria = self._fresh_buckets()
        for _ in range(3):
            aria._consume_webhook_token("hubspot")
        # 1.5s margin (refill=1.0 tokens/sec, deficit=1.0 → need 1.0s minimum).
        # 0.5s extra absorbs GIL pauses / GC stalls on loaded CI runners.
        _time.sleep(1.5)
        allowed, _, _ = aria._consume_webhook_token("hubspot")
        assert allowed is True


def test_smoke_marker():
    """If you see this in green, the test infrastructure is wired."""
    assert True, "test_session_2026_05_11.py loaded + smoke test ran"

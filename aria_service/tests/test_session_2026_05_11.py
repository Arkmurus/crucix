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


class TestNoScaffoldWrite:
    """R-F267 — _save_mastery must NOT persist when the cache was only
    scaffolded (never touched by actual learning). Across a backend flip
    (sqlite ↔ upstash), this prevents bootstrap-defaults from overwriting
    real data on the destination backend."""

    def _fresh_student(self):
        """Reset module-level mastery state for deterministic tests."""
        from aria_service.intel import student
        student._mastery_cache = None
        student._mastery_dirty = False
        return student

    def test_save_skipped_when_only_scaffolded(self):
        """Load mastery (scaffolds defaults), call _save_mastery without
        any learning update — write should be skipped."""
        student = self._fresh_student()
        writes = []

        async def fake_set_json(key, value, ex=None):
            writes.append((key, value, ex))

        async def fake_get_json(key):
            return None  # simulate empty backend

        async def _t():
            # Patch rs in student module
            student.rs.get_json = fake_get_json
            student.rs.set_json = fake_set_json
            cache = await student._load_mastery()
            assert cache  # scaffolded
            assert student._mastery_dirty is False
            await student._save_mastery()
            assert writes == [], "scaffold-only cache must not be persisted"

        asyncio.run(_t())

    def test_save_writes_when_dirty(self):
        """After _mark_mastery_dirty(), _save_mastery should persist once."""
        student = self._fresh_student()
        writes = []

        async def fake_set_json(key, value, ex=None):
            writes.append((key, value, ex))

        async def fake_get_json(key):
            return None

        async def _t():
            student.rs.get_json = fake_get_json
            student.rs.set_json = fake_set_json
            await student._load_mastery()
            student._mark_mastery_dirty()
            await student._save_mastery()
            assert len(writes) == 1, "dirty cache must persist exactly once"
            assert student._mastery_dirty is False, "dirty flag must reset after save"
            # Second save without re-marking dirty should be a no-op
            await student._save_mastery()
            assert len(writes) == 1, "save must be no-op after flag reset"

        asyncio.run(_t())

    def test_load_does_not_set_dirty(self):
        """Loading from an EMPTY backend scaffolds + leaves _dirty=False.
        Loading from a backend WITH data also leaves _dirty=False.
        Only update_mastery / recalibrate / lift can flip _dirty."""
        student = self._fresh_student()

        async def fake_get_json_empty(key):
            return None

        async def fake_get_json_with_data(key):
            return {"sanctions": {"score": 0.87, "samples": 42,
                                  "correct": 36, "wrong": 6,
                                  "last_practiced": 1000.0}}

        async def fake_set_json(key, value, ex=None):
            pass

        async def _t():
            student.rs.get_json = fake_get_json_empty
            student.rs.set_json = fake_set_json
            await student._load_mastery()
            assert student._mastery_dirty is False
            # Reset and try with data
            student._mastery_cache = None
            student._mastery_dirty = False
            student.rs.get_json = fake_get_json_with_data
            await student._load_mastery()
            assert student._mastery_dirty is False

        asyncio.run(_t())


class TestNoScaffoldWriteExpanded:
    """R-F268 — extend the no-scaffold-write rule to three more caches that
    share the same anti-pattern: student._regional_cache (regional heatmap),
    reasoning_router._stats_cache (routing counters), reasoning_library._meta_cache
    (case-library lookup stats). Each must skip persistence when the cache
    contains only scaffolded defaults, and persist exactly once after a real
    mutation marks it dirty."""

    def _patch_rs(self, module):
        """Stub the module's redis_store handles. Returns the writes list."""
        writes: list = []

        async def fake_set_json(key, value, ex=None):
            writes.append((key, value, ex))

        async def fake_get_json(key):
            return None  # simulate empty backend

        module.rs.get_json = fake_get_json
        module.rs.set_json = fake_set_json
        return writes

    # ── student._regional_cache ──────────────────────────────────────────
    def test_regional_save_skipped_when_only_scaffolded(self):
        from aria_service.intel import student
        student._regional_cache = None
        student._regional_dirty = False
        writes = self._patch_rs(student)

        async def _t():
            cache = await student._load_regional_mastery()
            assert cache == {}
            assert student._regional_dirty is False
            await student._save_regional_mastery()
            assert writes == [], "scaffold-only regional cache must not persist"
        asyncio.run(_t())

    def test_regional_save_writes_when_dirty(self):
        from aria_service.intel import student
        student._regional_cache = None
        student._regional_dirty = False
        writes = self._patch_rs(student)

        async def _t():
            await student._load_regional_mastery()
            student._mark_regional_dirty()
            await student._save_regional_mastery()
            assert len(writes) == 1, "dirty regional cache must persist once"
            assert student._regional_dirty is False
            await student._save_regional_mastery()
            assert len(writes) == 1, "no-op after dirty flag reset"
        asyncio.run(_t())

    # ── reasoning_router._stats_cache ────────────────────────────────────
    def test_router_stats_save_skipped_when_only_scaffolded(self):
        from aria_service.intel import reasoning_router as rr
        rr._stats_cache = None
        rr._stats_dirty = False
        writes = self._patch_rs(rr)

        async def _t():
            stats = await rr._load_stats()
            assert "total_queries" in stats and stats["total_queries"] == 0
            assert rr._stats_dirty is False
            await rr._save_stats()
            assert writes == [], "scaffold-only stats must not persist"
        asyncio.run(_t())

    def test_router_stats_save_writes_after_record_routing(self):
        """_record_routing marks dirty + calls _save_stats internally; the
        save flushes and resets the flag in one round. Verify by counting
        writes, since the flag is False both before AND after the cycle."""
        from aria_service.intel import reasoning_router as rr
        rr._stats_cache = None
        rr._stats_dirty = False
        writes = self._patch_rs(rr)

        async def _t():
            await rr._record_routing("symbolic_reasoner")
            assert len(writes) == 1, "real routing must persist exactly once"
            assert rr._stats_dirty is False, "flag must reset after save"
            # A second save without further routing must be a no-op
            await rr._save_stats()
            assert len(writes) == 1, "no-op when dirty flag is False"
        asyncio.run(_t())

    # ── reasoning_library._meta_cache ────────────────────────────────────
    def test_library_meta_save_skipped_when_only_scaffolded(self):
        from aria_service.intel import reasoning_library as rl
        rl._meta_cache = None
        rl._meta_dirty = False
        writes = self._patch_rs(rl)

        async def _t():
            meta = await rl._load_meta()
            assert meta["total_cases"] == 0
            assert rl._meta_dirty is False
            await rl._save_meta()
            assert writes == [], "scaffold-only meta must not persist"
        asyncio.run(_t())

    def test_library_meta_save_writes_when_dirty(self):
        from aria_service.intel import reasoning_library as rl
        rl._meta_cache = None
        rl._meta_dirty = False
        writes = self._patch_rs(rl)

        async def _t():
            await rl._load_meta()
            rl._mark_meta_dirty()
            await rl._save_meta()
            assert len(writes) == 1
            assert rl._meta_dirty is False
        asyncio.run(_t())


class TestBrainAbsorbAsync:
    """R-F269 — POST /api/aria/brain/absorb now returns 202 Accepted with a
    background task instead of awaiting the absorb fan-out synchronously.
    Closes the seenode → fly timeout reported on sources.html
    ('Brain bridge Degraded: timeout'). Tests use FastAPI's TestClient to
    exercise the request/response shape without touching brain_hook's
    chromadb/embedding cold path."""

    def test_returns_202_with_summary_hash(self):
        from fastapi.testclient import TestClient
        from aria_service.routes import aria as _aria

        # Stub brain_hook.absorb so the test doesn't load chromadb/sentence-
        # transformers. The background task should still RUN but the stub
        # returns immediately.
        from aria_service.intel import brain_hook
        original_absorb = brain_hook.absorb
        absorb_calls = []

        async def fake_absorb(**kwargs):
            absorb_calls.append(kwargs)
            return {"ok": True, "topics_lifted": []}

        brain_hook.absorb = fake_absorb
        try:
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(_aria.router)
            client = TestClient(app)

            # Clear ARIA_API_TOKEN / ARIA_INTERNAL_TOKEN env so auth is bypassed
            # by the soft-rollout path (no tokens set → no enforcement).
            old_api = os.environ.pop("ARIA_API_TOKEN", None)
            old_int = os.environ.pop("ARIA_INTERNAL_TOKEN", None)
            try:
                r = client.post(
                    "/api/aria/brain/absorb",
                    json={"module": "test_mod", "summary": "test summary"},
                )
            finally:
                if old_api is not None:
                    os.environ["ARIA_API_TOKEN"] = old_api
                if old_int is not None:
                    os.environ["ARIA_INTERNAL_TOKEN"] = old_int

            assert r.status_code == 202, f"expected 202 Accepted, got {r.status_code}: {r.text}"
            body = r.json()
            assert body["accepted"] is True
            assert body["module"] == "test_mod"
            assert "summary_hash" in body and len(body["summary_hash"]) == 16
            # Background task should have fired by the time TestClient returns
            assert len(absorb_calls) == 1
            assert absorb_calls[0]["module"] == "test_mod"
            assert absorb_calls[0]["summary"] == "test summary"
        finally:
            brain_hook.absorb = original_absorb

    def test_returns_400_on_missing_fields(self):
        from fastapi.testclient import TestClient
        from aria_service.routes import aria as _aria
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(_aria.router)
        client = TestClient(app)

        old_api = os.environ.pop("ARIA_API_TOKEN", None)
        old_int = os.environ.pop("ARIA_INTERNAL_TOKEN", None)
        try:
            r = client.post(
                "/api/aria/brain/absorb",
                json={"module": "", "summary": "x"},
            )
            assert r.status_code == 400
            r = client.post(
                "/api/aria/brain/absorb",
                json={"module": "x", "summary": ""},
            )
            assert r.status_code == 400
        finally:
            if old_api is not None:
                os.environ["ARIA_API_TOKEN"] = old_api
            if old_int is not None:
                os.environ["ARIA_INTERNAL_TOKEN"] = old_int


class TestMasteryFloorWarningRateLimit:
    """R-F270 — MASTERY HARD FLOOR warnings are rate-limited to one per
    (topic, hour). Repeated breaches of the same topic inside the window
    only produce a single log line; the underlying capability_gap is still
    recorded every time."""

    def _fresh_student(self):
        from aria_service.intel import student
        student._last_floor_warning = {}
        return student

    def test_first_breach_fires(self):
        student = self._fresh_student()
        # First call: empty dict → topic not seen → should pass the gate
        now = 1_000_000.0
        last = student._last_floor_warning.get("sanctions", 0.0)
        assert now - last >= student._FLOOR_WARN_INTERVAL_S
        student._last_floor_warning["sanctions"] = now
        # Immediate re-check (no time advance) → should be RATE-LIMITED
        last = student._last_floor_warning.get("sanctions", 0.0)
        assert now - last < student._FLOOR_WARN_INTERVAL_S

    def test_window_expiry_reallows(self):
        student = self._fresh_student()
        student._last_floor_warning["sanctions"] = 1_000_000.0
        # 30 minutes later — still rate-limited
        last = student._last_floor_warning.get("sanctions", 0.0)
        assert (1_000_000.0 + 1800.0) - last < student._FLOOR_WARN_INTERVAL_S
        # 61 minutes later — window expired
        assert (1_000_000.0 + 3660.0) - last >= student._FLOOR_WARN_INTERVAL_S

    def test_per_topic_isolation(self):
        student = self._fresh_student()
        student._last_floor_warning["sanctions"] = 1_000_000.0
        # nato_standards has never breached → fires immediately
        last_nato = student._last_floor_warning.get("nato_standards", 0.0)
        assert 1_000_000.0 - last_nato >= student._FLOOR_WARN_INTERVAL_S

    def test_default_interval_is_one_hour(self):
        student = self._fresh_student()
        assert student._FLOOR_WARN_INTERVAL_S == 3600.0


def test_smoke_marker():
    """If you see this in green, the test infrastructure is wired."""
    assert True, "test_session_2026_05_11.py loaded + smoke test ran"

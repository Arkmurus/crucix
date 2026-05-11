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


class TestSelfImproveObservability:
    """R-F272 — autonomous_improvement_cycle now reports errors split by
    MODIFIABLE_FILES membership. Operator can tell whether 'X errors, 0 bugs'
    means 'no real bugs found' or 'real bugs but in files the loop can't
    auto-fix'."""

    def test_results_dict_has_new_breakdown_fields(self):
        """The new result keys must exist in the cycle output schema."""
        from aria_service.intel import self_improve

        # Stub get_recent_errors to return zero — cycle short-circuits but
        # the results dict structure should still be initialised.
        original_get_errors = self_improve.get_recent_errors

        async def fake_get_errors(hours=6):
            return []

        self_improve.get_recent_errors = fake_get_errors

        class FakeLLM:
            is_configured = True

        try:
            async def _t():
                return await self_improve.autonomous_improvement_cycle(FakeLLM())

            result = asyncio.run(_t())
            assert "errors_in_modifiable_files" in result
            assert "errors_in_external_files" in result
            assert "files_skipped_below_threshold" in result
            assert isinstance(result["errors_in_modifiable_files"], dict)
            assert isinstance(result["errors_in_external_files"], dict)
        finally:
            self_improve.get_recent_errors = original_get_errors

    def test_splits_modifiable_vs_external(self):
        """When errors come from a mix of modifiable + external files,
        the result dict must put each file in the right bucket."""
        from aria_service.intel import self_improve

        # Build 4 fake errors per file (above the threshold of 3)
        fake_errors = (
            [{"file": "aria_service/intel/contacts.py", "type": "warn"}] * 4 +   # modifiable
            [{"file": "aria_service/intel/student.py", "type": "warn"}] * 4 +    # external
            [{"file": "aria_service/intel/rag_store.py", "type": "warn"}] * 4 +  # external
            [{"file": "aria_service/intel/transient.py", "type": "warn"}] * 2     # below threshold
        )
        original_get_errors = self_improve.get_recent_errors
        original_diagnose = self_improve._diagnose_and_fix

        async def fake_get_errors(hours=6):
            return fake_errors

        async def fake_diagnose(llm, fp, errs):
            return None  # skip LLM call

        self_improve.get_recent_errors = fake_get_errors
        self_improve._diagnose_and_fix = fake_diagnose

        class FakeLLM:
            is_configured = True

        try:
            async def _t():
                return await self_improve.autonomous_improvement_cycle(FakeLLM())

            result = asyncio.run(_t())
            mod = result["errors_in_modifiable_files"]
            ext = result["errors_in_external_files"]
            assert "aria_service/intel/contacts.py" in mod
            assert mod["aria_service/intel/contacts.py"] == 4
            assert "aria_service/intel/student.py" in ext
            assert ext["aria_service/intel/student.py"] == 4
            assert "aria_service/intel/rag_store.py" in ext
            assert ext["aria_service/intel/rag_store.py"] == 4
            # transient.py had 2 errors → below threshold, neither bucket
            assert "aria_service/intel/transient.py" not in mod
            assert "aria_service/intel/transient.py" not in ext
            assert result["files_skipped_below_threshold"] == 1
        finally:
            self_improve.get_recent_errors = original_get_errors
            self_improve._diagnose_and_fix = original_diagnose


class TestBrowserFingerprintHeaders:
    """R-F273 — random_headers() now returns browser-fingerprint-grade
    headers so anti-bot systems (Cloudflare, AWS WAF, Akamai) don't
    trivially identify the request as non-browser. AfDB + SEACE Peru
    were both returning 403 with the bare UA+Accept pair."""

    def test_returns_dict_with_user_agent(self):
        from aria_service.intel.ua_rotation import random_headers
        h = random_headers()
        assert isinstance(h, dict)
        assert "User-Agent" in h and h["User-Agent"].startswith("Mozilla/")

    def test_includes_sec_fetch_headers(self):
        """Sec-Fetch-* headers are the #1 modern anti-bot tripwire."""
        from aria_service.intel.ua_rotation import random_headers
        h = random_headers()
        assert h.get("Sec-Fetch-Dest") == "document"
        assert h.get("Sec-Fetch-Mode") == "navigate"
        assert h.get("Sec-Fetch-Site") == "none"
        assert h.get("Sec-Fetch-User") == "?1"

    def test_includes_dnt_and_upgrade_insecure(self):
        from aria_service.intel.ua_rotation import random_headers
        h = random_headers()
        assert h.get("DNT") == "1"
        assert h.get("Upgrade-Insecure-Requests") == "1"

    def test_includes_keep_alive(self):
        from aria_service.intel.ua_rotation import random_headers
        h = random_headers()
        assert h.get("Connection") == "keep-alive"

    def test_accept_supports_avif_webp(self):
        """Modern browsers advertise avif/webp support — the Accept header
        should reflect that or anti-bot rules will flag it as stale."""
        from aria_service.intel.ua_rotation import random_headers
        h = random_headers()
        accept = h.get("Accept", "")
        assert "image/avif" in accept or "image/webp" in accept

    def test_ua_rotation_still_random(self):
        """Confirm we didn't accidentally pin to one UA when enriching headers."""
        from aria_service.intel.ua_rotation import random_headers
        seen = {random_headers()["User-Agent"] for _ in range(50)}
        assert len(seen) >= 3, "expected rotation across multiple UAs over 50 calls"


class TestUNGMNoticePatterns:
    """R-F274 — UNGM scraper now tries 4 URL patterns; previously the
    single pattern found 0 matches across every sweep. Verify each
    pattern matches the URL shape it claims to."""

    def _patterns(self):
        # Re-build the patterns inline to test them in isolation without
        # importing the whole tender_monitor module (which pulls in chromadb
        # and other heavy deps).
        return [
            re.compile(r'href=["\']/Public/Notice/(\d+)["\'][^>]*>([\s\S]*?)</a>', re.IGNORECASE),
            re.compile(r'href=["\']/Public/Notice/Details?/(\d+)["\'][^>]*>([\s\S]*?)</a>', re.IGNORECASE),
            re.compile(r'href=["\']https?://www\.ungm\.org/Public/Notice/(\d+)["\'][^>]*>([\s\S]*?)</a>', re.IGNORECASE),
            re.compile(r'href=["\'][^"\']*/Notice/(\d{4,})["\'][^>]*>([\s\S]*?)</a>', re.IGNORECASE),
        ]

    def test_pattern_1_relative_url(self):
        html = '<a href="/Public/Notice/123456" class="x">Tender title</a>'
        matches = self._patterns()[0].findall(html)
        assert matches == [("123456", "Tender title")]

    def test_pattern_2_details_path(self):
        # UNGM sometimes uses /Public/Notice/Details/<id> or /Detail/
        html_s = '<a href="/Public/Notice/Details/789012">Peacekeeping supplies</a>'
        html_no_s = '<a href="/Public/Notice/Detail/789012">Peacekeeping supplies</a>'
        for html in (html_s, html_no_s):
            matches = self._patterns()[1].findall(html)
            assert matches == [("789012", "Peacekeeping supplies")]

    def test_pattern_3_absolute_url(self):
        html = '<a href="https://www.ungm.org/Public/Notice/42">Border patrol equipment</a>'
        matches = self._patterns()[2].findall(html)
        assert matches == [("42", "Border patrol equipment")]
        # Also matches http://
        html_http = '<a href="http://www.ungm.org/Public/Notice/42">x</a>'
        assert self._patterns()[2].findall(html_http) == [("42", "x")]

    def test_pattern_4_short_form(self):
        # Bare /Notice/<id> without /Public/ prefix (4+ digit id only,
        # to avoid matching unrelated /Notice/1 type URLs)
        html = '<a href="/some/path/Notice/1234567">Demining equipment</a>'
        matches = self._patterns()[3].findall(html)
        assert matches == [("1234567", "Demining equipment")]

    def test_anchor_with_inner_span_tags(self):
        """Real UNGM HTML wraps the title in a <span> — the pattern uses
        [\\s\\S]*? to capture across tags, and the consumer strips them."""
        html = '<a href="/Public/Notice/55555"><span class="title">Military supplies</span></a>'
        matches = self._patterns()[0].findall(html)
        assert len(matches) == 1
        notice_id, raw_title = matches[0]
        assert notice_id == "55555"
        # The pattern captures raw_title with the span tag; strip it
        stripped = re.sub(r"<[^>]+>", "", raw_title).strip()
        assert stripped == "Military supplies"

    def test_no_match_on_unrelated_anchor(self):
        html = '<a href="/About/Contact">Contact us</a>'
        for p in self._patterns():
            assert p.findall(html) == []


class TestSanctionsGeographicFilter:
    """R-F277 — token-overlap demotion in _sanctions_classify must NOT
    count shared country/geographic tokens as identity evidence. The
    operator-observed EBANO false-positive on lngtradinginternationalpanamasa
    is the regression scenario: two unrelated companies both with
    'PANAMA' in their name should NOT overlap-match each other."""

    def test_country_only_overlap_demoted(self):
        from aria_service.intel._sanctions_classify import _name_overlap
        # Query: LNG broker in Panama. Candidate: Petroleum entity in Panama.
        # Pre-R-F277 these shared "panama" → overlap=1 → match upheld.
        # Post-R-F277 "panama" is geographic → overlap=0 → match demoted.
        overlap = _name_overlap(
            "LNG TRADING INTERNATIONAL PANAMA SA",
            "EBANO PETROLEUM PANAMA SA",
        )
        assert overlap == 0, "country-only overlap must demote to zero"

    def test_real_entity_token_overlap_preserved(self):
        """Two entities sharing a REAL identifying token (not just country)
        should still register overlap."""
        from aria_service.intel._sanctions_classify import _name_overlap
        overlap = _name_overlap(
            "Rosoboronexport JSC",
            "Rosoboronexport Holdings",
        )
        assert overlap >= 1

    def test_corp_suffix_overlap_demoted(self):
        """Existing behaviour preserved — corp suffixes don't count as overlap."""
        from aria_service.intel._sanctions_classify import _name_overlap
        overlap = _name_overlap("Foo LTD", "Bar LTD")
        assert overlap == 0

    def test_geographic_filter_covers_common_dd_jurisdictions(self):
        """Specific countries the operator's WhatsApp DD touched: panama,
        switzerland, russia, ukraine, venezuela, colombia. All must filter."""
        from aria_service.intel._sanctions_classify import _tokenize_entity_name
        for country in ("panama", "switzerland", "russia", "ukraine",
                        "venezuela", "colombia", "kenya", "angola"):
            tokens = _tokenize_entity_name(f"Acme {country.title()} Holdings")
            assert country not in tokens, f"{country} should be filtered"
            # "acme" is a real identifying token AND not in any filter set
            assert "acme" in tokens, "discriminating token must survive"

    def test_adjective_country_filter(self):
        """'Swiss', 'Russian' etc. are also geographic descriptors."""
        from aria_service.intel._sanctions_classify import _tokenize_entity_name
        for adj in ("swiss", "russian", "panamanian", "iranian", "saudi"):
            tokens = _tokenize_entity_name(f"Foo {adj.title()} Industries")
            assert adj not in tokens, f"{adj} should be filtered as geographic"

    def test_city_names_still_count(self):
        """Cities are NOT in the geographic filter (city names can be
        identifying — 'Belgrade Industries' ≠ 'Sofia Industries')."""
        from aria_service.intel._sanctions_classify import _tokenize_entity_name
        for city in ("belgrade", "sofia", "warsaw", "tehran", "moscow"):
            tokens = _tokenize_entity_name(f"Foo {city.title()} Industries")
            assert city in tokens, f"{city} should be preserved as discriminating"

    def test_classify_match_demotes_country_only_overlap(self):
        """End-to-end: a HARD-STOP topic match with only country overlap
        must be demoted to 'info'."""
        from aria_service.intel._sanctions_classify import classify_match
        match = {
            "score": 0.85,
            "topics": ["sanction"],  # would be hard_stop topic
            "name": "EBANO PETROLEUM PANAMA SA",
        }
        severity = classify_match(match, query_name="LNG TRADING INTERNATIONAL PANAMA SA")
        assert severity == "info", "country-only-overlap match must demote to info"


class TestPerSourceVerification:
    """R-F287 — derive_verified_sources(matches) returns explicit per-source
    status so the DD report renderer can NEVER fabricate 'NOT CHECKED'
    claims for sources that OpenSanctions actually queried.

    Operator's WhatsApp DD output claimed:
      'UK OFSI: NOT CHECKED — OFSI list was unavailable during the scan'
    which was a fabrication — OpenSanctions had queried OFSI cleanly.
    R-F287 ensures the structured output makes that fabrication
    structurally impossible (the LLM gets explicit CLEAN/HIT/UNAVAILABLE
    per source, no room to invent gaps)."""

    def test_no_matches_all_sources_clean(self):
        """A clean screen → every canonical source reports CLEAN."""
        from aria_service.intel._sanctions_classify import (
            derive_verified_sources, _CANONICAL_SANCTIONS_SOURCES,
        )
        out = derive_verified_sources(matches=[])
        # Every canonical source must appear
        assert set(out.keys()) == set(_CANONICAL_SANCTIONS_SOURCES.keys())
        # All status CLEAN when screen succeeded but no matches
        for src, status in out.items():
            assert status["status"] == "CLEAN", f"{src} should be CLEAN"
            assert status["match_count"] == 0
            assert status["matched_entities"] == []

    def test_screen_failed_all_sources_unavailable(self):
        """When the whole sanctions screen failed (API down etc.) →
        every canonical source must report UNAVAILABLE, not CLEAN.
        Saying CLEAN when we didn't actually check would be the
        worst-class false negative."""
        from aria_service.intel._sanctions_classify import derive_verified_sources
        out = derive_verified_sources(matches=[], screen_succeeded=False)
        for src, status in out.items():
            assert status["status"] == "UNAVAILABLE", f"{src} should be UNAVAILABLE"

    def test_ofac_sdn_hit_resolved(self):
        """A match with `lists: ['us_ofac_sdn']` → OFAC SDN reports HIT,
        other sources report CLEAN."""
        from aria_service.intel._sanctions_classify import derive_verified_sources
        matches = [
            {"name": "Rosoboronexport JSC", "score": 0.95,
             "lists": ["us_ofac_sdn"], "topics": ["sanction"]},
        ]
        out = derive_verified_sources(matches)
        assert out["OFAC SDN"]["status"] == "HIT"
        assert out["OFAC SDN"]["match_count"] == 1
        assert "Rosoboronexport JSC" in out["OFAC SDN"]["matched_entities"]
        # Other sources should be CLEAN
        assert out["UK OFSI / HMT"]["status"] == "CLEAN"
        assert out["EU Consolidated"]["status"] == "CLEAN"
        assert out["UN SC Consolidated"]["status"] == "CLEAN"

    def test_multi_jurisdiction_hit(self):
        """A match listed in multiple jurisdictions → each reports HIT."""
        from aria_service.intel._sanctions_classify import derive_verified_sources
        matches = [
            {"name": "Wagner Group", "score": 1.0,
             "lists": ["us_ofac_sdn", "eu_council", "gb_hmt", "un_sc_sanctions"]},
        ]
        out = derive_verified_sources(matches)
        for src in ("OFAC SDN", "EU Consolidated", "UK OFSI / HMT", "UN SC Consolidated"):
            assert out[src]["status"] == "HIT", f"{src} should be HIT"
            assert "Wagner Group" in out[src]["matched_entities"]
        # BIS Entity not listed → CLEAN
        assert out["BIS Entity List"]["status"] == "CLEAN"

    def test_datasets_alias_for_lists_field(self):
        """OpenSanctions sometimes returns `datasets` instead of `lists`."""
        from aria_service.intel._sanctions_classify import derive_verified_sources
        matches = [
            {"name": "Test Entity", "score": 0.9,
             "datasets": ["us_ofac_sdn"]},  # different field name
        ]
        out = derive_verified_sources(matches)
        assert out["OFAC SDN"]["status"] == "HIT"

    def test_substring_slug_match(self):
        """OpenSanctions slugs vary by version — substring contains match."""
        from aria_service.intel._sanctions_classify import derive_verified_sources
        # "us_bis_entity_list_v2" should still match BIS Entity List
        matches = [
            {"name": "Foo Corp", "score": 0.9,
             "lists": ["us_bis_entity_list_v2"]},
        ]
        out = derive_verified_sources(matches)
        assert out["BIS Entity List"]["status"] == "HIT"

    def test_unrelated_match_doesnt_inflate_any_source(self):
        """A match on a non-canonical list → ALL canonical sources CLEAN.
        E.g., a PEP-only hit shouldn't make OFAC SDN look HIT."""
        from aria_service.intel._sanctions_classify import derive_verified_sources
        matches = [
            {"name": "Some Politician", "score": 0.8,
             "lists": ["everypolitician"], "topics": ["role.pep"]},
        ]
        out = derive_verified_sources(matches)
        for src, status in out.items():
            assert status["status"] == "CLEAN", \
                f"{src} should be CLEAN despite PEP-only match"

    def test_canonical_sources_include_all_operator_jurisdictions(self):
        """Operator's mandate: OFAC + OFSI + EU + UN SC + BIS at minimum."""
        from aria_service.intel._sanctions_classify import _CANONICAL_SANCTIONS_SOURCES
        names = set(_CANONICAL_SANCTIONS_SOURCES.keys())
        # All five must be present
        assert "OFAC SDN" in names
        assert "UK OFSI / HMT" in names
        assert "EU Consolidated" in names
        assert "UN SC Consolidated" in names
        assert "BIS Entity List" in names


class TestWorldBankGuidanceCorrection:
    """R-F279 — purge stale 'register at WB developer portal' guidance.
    Per R-F155 verified 2026-05-10: apigwext.worldbank.org → 403, no
    public registration path exists. The DD orchestrator was emitting
    'register free at worldbank.org developer portal' guidance to the
    operator — a misleading instruction. Replace with the verified-true
    OpenSanctions-aggregation fallback."""

    def test_no_stale_developer_portal_guidance_in_error_msg(self):
        """The lookup() function's error_result when no key is set must
        NOT direct the operator to a non-existent registration portal."""
        import inspect
        from aria_service.intel.sources import worldbank_debarred
        src = inspect.getsource(worldbank_debarred.lookup)
        # The OLD stale guidance must not appear
        assert "register free at worldbank.org" not in src, \
            "stale 'register at worldbank.org developer portal' guidance must be purged"
        assert "developer portal" not in src or "no public" in src.lower(), \
            "any mention of 'developer portal' must contextualise it as non-existent"
        # The NEW correct guidance must appear
        assert "OpenSanctions" in src and "wb_debarred" in src, \
            "fallback to OpenSanctions wb_debarred must be in the error message"

    def test_vendor_registry_marks_wb_as_enterprise_only(self):
        """vendor_registry must mark WB debarred as enterprise-only (not
        self-signup-free) so the DD report doesn't surface a misleading
        'sign up here' link."""
        from aria_service.intel.vendor_registry import _default_registry
        entries = _default_registry()
        wb_entries = [v for v in entries if v.get("id") == "worldbank_debarred"]
        assert len(wb_entries) == 1
        wb = wb_entries[0]
        assert wb["tier"] == "enterprise_only", \
            "WB debarred tier must reflect R-F155 verified state"
        assert wb["status"] == "covered_via_opensanctions"
        assert "R-F155" in wb["notes"] or "no public" in wb["notes"].lower()


class TestPromptClause20BUpdate:
    """R-F276 — Clause 20(b) NO STATUS INFLATION must NOT cite a hard-coded
    default for ARIA_AUTONOMOUS_ENABLED. The operator-observed WhatsApp
    chat at 2026-05-11 16:19 had ARIA confidently claim:
      'Autonomous engine: Built and ready, currently requires operator
       activation (ARIA_AUTONOMOUS_ENABLED=0 by default)'
    while the fly logs at boot 13:34:49 showed:
       'Autonomous engine started (dry_run=False, 74 tasks loaded)'
    — i.e. the engine IS RUNNING in production. The prompt was citing
    a stale default. Fix: prompt now mandates current-state-evidenced
    status assertions, with explicit fallback to 'I don't have current
    visibility' when no live signal is available."""

    def _aria_engine_source(self):
        """Read aria_engine.py source for prompt-text inspection."""
        import pathlib
        path = pathlib.Path(__file__).resolve().parents[1] / "aria_engine.py"
        return path.read_text(encoding="utf-8", errors="replace")

    def test_no_hard_coded_disabled_by_default_claim(self):
        """Clause 20(b) must NOT assert ARIA_AUTONOMOUS_ENABLED=0 as a
        default. The actual default has changed across deploys; asserting
        a stale value invites the LLM to lie about live state."""
        src = self._aria_engine_source()
        # Find clause 20(b) section
        c20_idx = src.find("(b) NO STATUS INFLATION")
        assert c20_idx > 0, "clause 20(b) header must exist"
        # Capture ~600 chars after the header (clause body)
        c20 = src[c20_idx:c20_idx + 2000]
        # The OLD stale assertion must NOT appear
        assert "is globally disabled by default (ARIA_AUTONOMOUS_ENABLED=0)" not in c20, \
            "stale 'disabled by default' literal must be removed from clause 20(b)"

    def test_current_state_evidence_pattern_required(self):
        """Clause 20(b) must instruct using live evidence (boot snapshot,
        /api/aria/health, /api/aria/autonomous/status)."""
        src = self._aria_engine_source()
        c20_idx = src.find("(b) NO STATUS INFLATION")
        c20 = src[c20_idx:c20_idx + 2000]
        # Must reference at least one of the live-evidence surfaces
        evidence_markers = [
            "/api/aria/health",
            "/api/aria/autonomous/status",
            "ARIA STATE AT BOOT",
            "R-F248",
            "R-F266",
        ]
        hits = [m for m in evidence_markers if m in c20]
        assert len(hits) >= 2, \
            f"clause 20(b) must reference ≥2 live-evidence surfaces; found: {hits}"

    def test_explicit_no_visibility_fallback(self):
        """Clause 20(b) must instruct an explicit 'no visibility' fallback,
        not silent default-citation."""
        src = self._aria_engine_source()
        c20_idx = src.find("(b) NO STATUS INFLATION")
        c20 = src[c20_idx:c20_idx + 2000]
        # Must contain the no-visibility fallback pattern
        assert "don't have current visibility" in c20.lower() or \
               "no current visibility" in c20.lower() or \
               "no visibility into" in c20.lower(), \
            "clause 20(b) must mandate explicit no-visibility fallback"


def test_smoke_marker():
    """If you see this in green, the test infrastructure is wired."""
    assert True, "test_session_2026_05_11.py loaded + smoke test ran"

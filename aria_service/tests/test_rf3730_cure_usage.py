"""R-F3730 — Phase 0.3 runtime usage observation.

The census identified 109 DEAD-CANDIDATE modules and NONE is deletable, because
the three-proof rule (Cure Protocol 4.1) needs a runtime proof that did not
exist: no route in either tier recorded that it was called.

These tests hold the properties that make it safe to run on every request to a
single-process brain: no I/O on the request path, never raises, bounded
cardinality, and coalesced flushes.
"""

from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import cure_usage




def _swap_store(monkeypatch, store):
    """R-F3752 — swap the store BOTH ways; the package attribute is the
    load-bearing half.

    `cure_usage.flush()`/`snapshot()` do `from aria_service.intel import
    state_store`. That resolves the PACKAGE ATTRIBUTE once the submodule has been
    imported; it does NOT re-consult sys.modules. So a sys.modules-only swap works
    when this file runs alone (state_store not yet imported) and SILENTLY STOPS
    WORKING once any earlier test imports it — the real store is used and the
    branch under test never executes.

    Measured 2026-08-05: these tests passed alone and five of them failed once
    test_rf3716_3717 ran first, because it imports redis_store, which pulls in
    state_store. A test seam has to be independent of import order.
    """
    import sys as _sys
    import aria_service.intel as _pkg
    monkeypatch.setitem(_sys.modules, "aria_service.intel.state_store", store)
    monkeypatch.setattr(_pkg, "state_store", store, raising=False)

@pytest.fixture(autouse=True)
def _clean():
    cure_usage._reset_for_tests()
    yield
    cure_usage._reset_for_tests()


def test_record_route_is_sync_and_does_no_io():
    """The request path must not touch the store. If record_route ever became
    async or did I/O, every request would pay for observability."""
    assert not asyncio.iscoroutinefunction(cure_usage.record_route)
    cure_usage.record_route("/api/aria/dd/{id}", "get")
    assert cure_usage._buffer["GET /api/aria/dd/{id}"] == 1


def test_counts_accumulate_per_method_and_template():
    for _ in range(3):
        cure_usage.record_route("/health", "GET")
    cure_usage.record_route("/health", "POST")
    assert cure_usage._buffer["GET /health"] == 3
    assert cure_usage._buffer["POST /health"] == 1


def test_record_route_never_raises_on_bad_input():
    """Observability must never be able to break a request."""
    for bad in [None, "", 42, object()]:
        cure_usage.record_route(bad, "GET")  # type: ignore[arg-type]
    for bad_m in [None, 42]:
        cure_usage.record_route("/x", bad_m)  # type: ignore[arg-type]
    # empty template is ignored, not counted
    assert "GET " not in cure_usage._buffer


def test_cardinality_is_capped():
    """Keying on a raw path instead of a template would grow without bound.
    The cap drops rather than grows, and records that it dropped."""
    for i in range(cure_usage.MAX_TRACKED + 50):
        cure_usage.record_route(f"/t/{i}", "GET")
    assert len(cure_usage._buffer) == cure_usage.MAX_TRACKED
    assert cure_usage._dropped == 50
    # an ALREADY-TRACKED field still counts after the cap is hit
    before = cure_usage._buffer["GET /t/0"]
    cure_usage.record_route("/t/0", "GET")
    assert cure_usage._buffer["GET /t/0"] == before + 1


def test_should_flush_respects_interval_and_empty_buffer():
    assert cure_usage.should_flush(now=0.0) is False, "empty buffer must not flush"
    cure_usage.record_route("/health", "GET")
    assert cure_usage.should_flush(now=0.0) is False, "interval not elapsed"
    assert cure_usage.should_flush(now=cure_usage.FLUSH_INTERVAL_S + 1) is True


@pytest.mark.asyncio
async def test_flush_writes_counts_and_clears_buffer(monkeypatch):
    writes: list[tuple[str, str, int]] = []

    class _FakeStore:
        @staticmethod
        async def hincrby(key, field, amount=1, **kw):
            writes.append((key, field, amount))
            return amount

        @staticmethod
        async def set_json(key, obj, ex=None, **kw):
            return True

    _swap_store(monkeypatch, _FakeStore)
    cure_usage.record_route("/health", "GET")
    cure_usage.record_route("/health", "GET")
    cure_usage.record_route("/api/aria/dd/{id}", "POST")

    written = await cure_usage.flush()

    assert written == 2, "one write per distinct field"
    assert ("crucix:cure:usage_routes", "GET /health", 2) in writes
    assert not cure_usage._buffer, "buffer drained after a successful flush"


@pytest.mark.asyncio
async def test_a_failed_write_returns_the_count_to_the_buffer(monkeypatch):
    """A store failure must not silently lose observations — losing them would
    make a live module look unobserved and push it toward deletion."""

    class _BrokenStore:
        @staticmethod
        async def hincrby(key, field, amount=1, **kw):
            raise RuntimeError("store down")

        @staticmethod
        async def set_json(key, obj, ex=None, **kw):
            raise RuntimeError("store down")

    _swap_store(monkeypatch, _BrokenStore)
    cure_usage.record_route("/health", "GET")
    cure_usage.record_route("/health", "GET")

    await cure_usage.flush()

    assert cure_usage._buffer["GET /health"] == 2, "counts restored, not dropped"
    assert cure_usage._flush_failures > 0


@pytest.mark.asyncio
async def test_snapshot_reports_unavailable_rather_than_lying(monkeypatch):
    """A store read failure must not read as 'nothing was observed'. That is the
    CLAUDE.md §1 gate-#4 failure class — a check certified by an absence."""

    class _BrokenStore:
        @staticmethod
        async def get_json(key):
            raise RuntimeError("store down")

    _swap_store(monkeypatch, _BrokenStore)
    snap = await cure_usage.snapshot()
    assert snap["available"] is False
    assert "store read failed" in snap["reason"]


@pytest.mark.asyncio
async def test_snapshot_returns_observed_routes(monkeypatch):
    class _Store:
        @staticmethod
        async def hgetall(key):
            assert key == cure_usage.ROUTES_KEY
            return {"GET /health": "12", "POST /api/aria/dd/{id}": "3"}

        @staticmethod
        async def get_json(key):
            return {"last_flush_epoch": 1.0}

    _swap_store(monkeypatch, _Store)
    snap = await cure_usage.snapshot()
    assert snap["available"] is True
    assert snap["observed_routes"] == 2
    assert snap["total_requests"] == 15


@pytest.mark.asyncio
async def test_counts_are_read_with_the_same_type_they_are_written_with(monkeypatch):
    """Regression for the accessor mismatch that shipped in R-F3730.

    flush() writes with hincrby (a HASH). The first snapshot() read with
    get_json (a JSON blob), which returns None for a hash — so live reported
    observed_routes:0 while flush_failures:0 and last_flush_epoch was valid.
    A write-succeeds/read-blind pair is the worst shape: it is indistinguishable
    from 'nothing was ever observed', which is precisely what would have made
    109 live-or-dead modules look uniformly unobserved and safe to delete.

    This drives a REAL round trip through one fake store: write via flush(),
    read via snapshot(), and require the counts to survive.
    """
    hashes: dict[str, dict[str, int]] = {}
    blobs: dict[str, Any] = {}

    class _RoundTripStore:
        @staticmethod
        async def hincrby(key, field, amount=1, **kw):
            hashes.setdefault(key, {})
            hashes[key][field] = hashes[key].get(field, 0) + amount
            return hashes[key][field]

        @staticmethod
        async def hgetall(key):
            return {k: str(v) for k, v in hashes.get(key, {}).items()}

        @staticmethod
        async def set_json(key, obj, ex=None, **kw):
            blobs[key] = obj
            return True

        @staticmethod
        async def get_json(key):
            return blobs.get(key)

    _swap_store(monkeypatch, _RoundTripStore)
    cure_usage.record_route("/health/live", "GET")
    cure_usage.record_route("/health/live", "GET")
    cure_usage.record_route("/api/aria/dd/{id}", "POST")

    await cure_usage.flush()
    snap = await cure_usage.snapshot()

    assert snap["available"] is True
    assert snap["observed_routes"] == 2, "written counts must be readable back"
    assert snap["total_requests"] == 3
    assert snap["routes"]["GET /health/live"] == 2


def test_maybe_schedule_flush_is_safe_without_a_running_loop():
    """Called from a sync context it must return False, not explode."""
    cure_usage.record_route("/health", "GET")
    cure_usage._last_flush = -10_000.0
    assert cure_usage.maybe_schedule_flush() is False


def test_store_declaration_satisfies_engineering_brief_invariant_10():
    """Invariant 10: no new store without ownership, retention, erasure, backup
    and recovery rules. R-F3730 shipped two keys with NONE of them declared;
    R-F3735 added the declaration after a self-audit against the brief.

    Asserting on the docstring is deliberate — this repo has no store registry,
    so the module docstring IS the declaration (the pattern news_archive.py
    follows). A test keeps it from being quietly deleted.
    """
    doc = cure_usage.__doc__ or ""
    for element in ("Owner.", "Retention class.", "erasure", "Backup / recovery.",
                    "Model context"):
        assert element in doc, f"invariant 10 declaration is missing: {element}"


def test_only_route_templates_are_recorded_never_resolved_paths():
    """The no-personal-data property that invariant 10's erasure exemption rests
    on. If a resolved path were ever recorded, this counter would capture user
    and case identifiers and become a personal-data store."""
    # A resolved path is still stored verbatim if a caller passes one -- the
    # guarantee lives at the CALL SITE, so assert the middleware uses the
    # route template rather than request.url.path.
    import inspect
    from aria_service import main

    src = inspect.getsource(main._observe_route_usage)
    # Strip comments first: the middleware's own comment explains WHY it does not
    # use request.url.path, and a naive substring check matches that explanation.
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )
    assert 'request.scope.get("route")' in code or "scope.get('route')" in code, (
        "the middleware must key on the ROUTE TEMPLATE"
    )
    assert "url.path" not in code, (
        "recording request.url.path would capture ids and create a personal-data store"
    )


def test_no_ttl_is_used_on_the_observation_keys():
    """CLAUDE.md §7: ARIA's memory does not expire, and a 14-day window must
    survive restarts. A TTL here would silently truncate the overlay."""
    import inspect

    src = inspect.getsource(cure_usage.flush)
    assert "ex=" not in src, "observation writes must not set a TTL"

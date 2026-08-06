"""R-F2928 — operator-gated single-key state diagnostic.

Exists to answer one question that no other surface could: DD runs complete and
return a full report, but /dd/report/{run_id} 404s and the index stays empty,
with NO persist error ever logged. That leaves two very different causes — the
write is lost, or the write lands and the read cannot see it — which need
opposite fixes.

The safety property under test is that it NEVER opens a second connection to the
state sqlite file. That is precisely how the 2026-07-02 writer wedge began (3.5h
outage), so a regression here is an outage, not an inconvenience.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from aria_service.routes import aria as aria_routes

# R-F3757/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _run(coro):
    return asyncio.run(coro)


class TestReadOnlyAndSafe:
    def test_it_uses_the_shared_redis_store_helper(self):
        """Must go through redis_store.get (the live pool), never sqlite3/
        aiosqlite directly."""
        src = function_source(aria_routes, "admin_state_key_ep")
        assert "_rs.get(" in src, "does not read through the shared helper"
        for banned in ("sqlite3.connect", "aiosqlite.connect", "_open_cold_store"):
            assert banned not in src, f"opens its own connection: {banned}"

    def test_it_never_writes(self):
        src = function_source(aria_routes, "admin_state_key_ep")
        for banned in ("set_json", "set_key", "_rs.set(", "delete("):
            assert banned not in src, f"diagnostic must be read-only: {banned}"

    def test_it_is_operator_gated(self):
        """A raw state reader must not be reachable with a service token."""
        assert aria_routes._OPERATOR_ONLY_RE.search("/api/aria/admin/state/key")


class TestBehaviour:
    def test_missing_key_reports_absent_not_error(self, monkeypatch):
        """'absent' and 'read failed' are different diagnoses and must never be
        collapsed — that conflation is what hid this bug for so long."""
        async def _get(key):
            return None

        monkeypatch.setattr(aria_routes, "HTTPException", aria_routes.HTTPException)
        from aria_service.intel import redis_store as _rs
        monkeypatch.setattr(_rs, "get", _get)
        out = _run(aria_routes.admin_state_key_ep(key="crucix:dd:report:nope"))
        assert out["exists"] is False
        assert out["error"] is None

    def test_present_key_reports_length_and_bounded_preview(self, monkeypatch):
        from aria_service.intel import redis_store as _rs

        async def _get(key):
            return "x" * 5000

        monkeypatch.setattr(_rs, "get", _get)
        out = _run(aria_routes.admin_state_key_ep(key="crucix:dd:report:abc", peek=100))
        assert out["exists"] is True
        assert out["length"] == 5000
        assert len(out["preview"]) == 100, "preview must be bounded"

    def test_a_read_failure_is_surfaced_not_swallowed(self, monkeypatch):
        from aria_service.intel import redis_store as _rs

        async def _boom(key):
            raise RuntimeError("state_store: no connection")

        monkeypatch.setattr(_rs, "get", _boom)
        out = _run(aria_routes.admin_state_key_ep(key="crucix:dd:report:abc"))
        assert out["exists"] is False
        assert "no connection" in (out["error"] or "")

    def test_it_reports_which_db_the_key_routes_to(self, monkeypatch):
        from aria_service.intel import redis_store as _rs

        async def _get(key):
            return None

        monkeypatch.setattr(_rs, "get", _get)
        hot = _run(aria_routes.admin_state_key_ep(key="crucix:dd:report:abc"))
        assert hot["routed_db"] == "hot", (
            "DD reports must route hot — if this ever flips, the hot/cold split "
            "is a live suspect for lost reports"
        )

    def test_empty_key_is_rejected(self):
        with pytest.raises(Exception):
            _run(aria_routes.admin_state_key_ep(key=""))

"""Capability tests for the DD-remediation fixes owned by Claude:

  R-F2373 C1 — crypto wallet never-false-clean (screen_wallet_checked)
  R-F2374 H4/M3 — operator-gate destructive endpoints (_OPERATOR_ONLY_RE + DELETE)
  R-F2375 H5 — llm/fallback.get_provider_status aggregator + real introspection fns
  R-F2375 H6 — /phase/gates measures gate #4 for real; gate #3 honest-unmeasurable

Each test drives the REAL function that was broken and asserts the user-visible
outcome (per §3c). No destructive path is ever invoked.
"""
import asyncio

import pytest


# ── R-F2373 C1 — crypto wallet never-false-clean ──────────────────────────────
class _FakeRedis:
    def __init__(self, idx):
        self._idx = idx

    async def get_json(self, key):
        if isinstance(self._idx, Exception):
            raise self._idx
        return self._idx


def _patch_redis(monkeypatch, idx):
    from aria_service.intel import crypto_sanctions as cs

    async def _fake_redis():
        return _FakeRedis(idx)

    monkeypatch.setattr(cs, "_redis", _fake_redis)
    return cs


def test_rf2373_crypto_empty_index_is_unavailable_not_clean(monkeypatch):
    """CRITICAL: an empty/unbuilt index must render UNVERIFIED, never matched=false."""
    cs = _patch_redis(monkeypatch, {})  # empty map = never built / failed refresh
    r = asyncio.run(cs.screen_wallet_checked("0xabc123"))
    assert r["source_unavailable"] is True, "empty index must be source_unavailable"
    assert r["screened"] is False, "empty index was not actually screened"
    assert r["matched"] is False
    # the legacy list API must NOT be usable as a clean signal here
    assert asyncio.run(cs.screen_wallet("0xabc123")) == []


def test_rf2373_crypto_redis_error_is_unavailable(monkeypatch):
    cs = _patch_redis(monkeypatch, RuntimeError("redis down"))
    r = asyncio.run(cs.screen_wallet_checked("0xabc123"))
    assert r["source_unavailable"] is True
    assert r["reason"] == "index_read_failed"


def test_rf2373_crypto_loaded_index_genuine_no_match_is_clean(monkeypatch):
    """A LOADED index with the address absent IS a genuine clean no-match."""
    cs = _patch_redis(monkeypatch, {"0xdeadbeef": [{"entity_name": "X", "chain": "eth"}]})
    r = asyncio.run(cs.screen_wallet_checked("0xfeed0000"))
    assert r["screened"] is True
    assert r["source_unavailable"] is False
    assert r["matched"] is False
    assert r["hits"] == []


def test_rf2373_crypto_batch_all_unavailable_not_clean(monkeypatch):
    cs = _patch_redis(monkeypatch, {})
    out = asyncio.run(cs.screen_wallet_batch(["0xabc", "0xdef"]))
    assert out["source_unavailable"] is True
    assert out["screened"] is False
    assert "COULD NOT VERIFY" in out["narrative"]
    assert "none matched" not in out["narrative"].lower()


# ── R-F2374 H4/M3 — operator-gate destructive endpoints ───────────────────────
def test_rf2374_operator_only_regex_covers_destructive_paths():
    from aria_service.routes.aria import _OPERATOR_ONLY_RE, _OPERATOR_ONLY_DELETE_RE

    # POST-only destructive paths must now be operator-gated
    for p in ("/api/aria/dd/admin/reset",
              "/api/aria/admin/reset-brain-stats",
              "/api/aria/neural/conflicts/clear"):
        assert _OPERATOR_ONLY_RE.search(p), f"{p} must be operator-only"

    # Vault DELETE is method-aware (global vault wipe/delete)
    assert _OPERATOR_ONLY_DELETE_RE.search("/api/aria/vault")
    assert _OPERATOR_ONLY_DELETE_RE.search("/api/aria/vault/some-site")

    # The per-tenant DD vault case delete must NOT be operator-gated
    assert not _OPERATOR_ONLY_DELETE_RE.search("/api/aria/dd/vault/case/xyz")
    # and normal reads are untouched by the path regex
    assert not _OPERATOR_ONLY_RE.search("/api/aria/dd/report/abc")
    assert not _OPERATOR_ONLY_RE.search("/api/aria/vault")  # GET vault is not path-gated


# ── R-F2375 H5 — introspection unblinded ──────────────────────────────────────
def test_rf2375_get_provider_status_is_real_dict():
    from aria_service.llm import fallback as fb

    out = fb.get_provider_status()
    assert isinstance(out, dict) and out, "provider status must be a non-empty dict"
    assert "deepseek" in out and "anthropic" in out
    for slot in out.values():
        assert set(slot) >= {"configured", "breaker_state", "available"}
        assert isinstance(slot["configured"], bool)
        assert isinstance(slot["available"], bool)


def test_rf2375_real_introspection_fns_exist_and_shape():
    # The names health/perf now calls must exist with the right sync/async-ness.
    from aria_service.autonomous import engine as eng
    from aria_service.learning import verification_gate as vg

    st = eng.get_engine_status()               # SYNC
    assert isinstance(st, dict)
    vstats = asyncio.run(vg.get_stats())        # ASYNC
    assert isinstance(vstats, dict)


# ── R-F2375 H6 — /phase/gates honest measurement ──────────────────────────────
def test_rf2375_phase_gates_measures_gate4_and_honest_gate3():
    import aria_service.main as _main

    result = asyncio.run(asyncio.wait_for(_main.phase_gates(), timeout=60))
    gates = result["gates"]
    summary = result["summary"]

    # Gate #4 now reads a REAL source (redis list) — value is an int (0+),
    # never the old -1 sentinel, and pass is a real bool.
    g4 = gates["gate_4_quarantine_closed"]
    assert g4.get("value") != -1
    assert isinstance(g4.get("value"), int) or g4.get("measurable") is False

    # Gate #3 — R-F4238 (2026-08-23): this asserted `pass is None` /
    # `measurable is False`, which was correct when R-F2375 was written (there
    # was no windowed ERROR source, so "unmeasurable" was the honest answer).
    # **R-F2622 then BUILT that source** — a durable, TTL-less error-streak
    # anchor written at `record_error()` time — so gate #3 is now genuinely
    # MEASURED, and the test had been standing red in docs/suite_baseline.json
    # ever since, asserting the absence of a capability that now exists.
    #
    # R-F2375's surviving intent is the anti-fabrication half, and that is what
    # is asserted now: gate #3 must never carry the -1 sentinel, and its verdict
    # must be tri-state-honest — a real bool when the streak can be measured,
    # None when it cannot. What it must NOT be is a number invented to fill the
    # field. (§1: R-F2622 replaced a pass that was certified by an EMPTY ledger.)
    g3 = gates["gate_3_zero_errors"]
    assert g3.get("value") != -1
    assert g3.get("pass") in (True, False, None)
    assert g3.get("measurable") is (g3.get("pass") is not None), (
        "`measurable` is derived from `pass` in _gate() — they can never disagree")
    if g3.get("pass") is not None:
        # Measured: R-F2622 requires the streak to come from the durable anchor,
        # and says so on the gate. A measured gate #3 with no basis would be the
        # fabricated pass it exists to prevent.
        assert g3.get("streak_basis"), (
            "a MEASURED gate #3 must name what it measured the streak from")

    # Summary distinguishes measurable from unmeasurable (no silent fail-by-default)
    assert "measurable" in summary and "unmeasurable" in summary
    assert summary["measurable"] + summary["unmeasurable"] == summary["total"], (
        "every gate must be counted exactly once — R-F2375's real point is that "
        "an unmeasurable gate is neither a pass nor a silent failure")

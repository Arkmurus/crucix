"""R-F3707 — CAPABILITY: /health actually DEGRADES. Behaviour, not syntax.

WHY THIS EXISTS
---------------
`test_rf762_state_backend_health` guarded R-F762's guarantee by string-matching
the literal expression `and state_backend_ind["reachable"]` in main.py. That is
a test of SYNTAX:

  * it BROKE on a legitimate refactor (R-F3704 turned the boolean chain into a
    named `_degraded_reasons` list so /health could also degrade on operating
    mode and report WHICH signal failed), and
  * it would have PASSED just as happily if the expression were present but its
    result discarded.

The peer agent flagged the failure as "the same 'surface certifies health by
construction' class we spent the session killing" — correctly, because a
string-match cannot tell a refactor from a regression. The answer is not a
better string: it is to drive the real handler and assert the real output.

This suite calls `aria_service.main.health()` directly with each degradation
condition injected, and asserts the RESPONSE.

Run: python -m pytest aria_service/tests/test_rf3707_health_degrades_behaviourally.py -v
"""
from __future__ import annotations

import asyncio

import pytest

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


def _health(monkeypatch, *, reachable=True, mode_normal=True, chain_resilient=True,
            autonomous_ok=True):
    """Drive the REAL /health handler with the given conditions."""
    from aria_service import main

    monkeypatch.setattr(main.app.state, "state_backend_reachable", reachable,
                        raising=False)

    # LLM chain: resilient or exhausted.
    class _LLM:
        name = "deepseek"
        is_configured = True

        def get_stats(self):
            return {}

        def get_health(self):
            return {"resilient": chain_resilient, "active_providers": ["deepseek"]}

    monkeypatch.setattr(main.app.state, "llm_provider", _LLM(), raising=False)

    # Operating mode.
    from aria_service.intel import operating_modes as om

    async def _mode():
        return om.Mode.NORMAL if mode_normal else om.Mode.DEGRADED

    monkeypatch.setattr(om, "get_mode", _mode)

    # Autonomous engine liveness.
    try:
        from aria_service.autonomous import engine as eng

        def _status():
            return {
                "enabled": True, "running": autonomous_ok, "dry_run": False,
                "autonomy_level": 3, "seconds_since_last_tick": 1 if autonomous_ok else 99999,
                "tasks_loaded": 98,
            }

        monkeypatch.setattr(eng, "get_engine_status", _status, raising=False)
    except Exception:
        pass

    return asyncio.run(main.health())


def test_a_healthy_system_reports_operational(monkeypatch):
    out = _health(monkeypatch)
    assert out["status"] == "operational", (
        f"a healthy system must not be reported degraded: "
        f"{out.get('degraded_reasons')}"
    )
    assert out["degraded_reasons"] == []


def test_an_unreachable_state_backend_degrades(monkeypatch):
    """R-F762's guarantee, asserted on BEHAVIOUR."""
    out = _health(monkeypatch, reachable=False)
    assert out["status"] == "degraded", (
        "a backend-unreachable boot must NOT report operational — knowledge is "
        "not being persisted and any monitor watching /health must see it"
    )
    assert "state_backend_unreachable" in out["degraded_reasons"]
    # The block itself must still be present for the status page.
    assert out["state_backend"]["reachable"] is False
    assert out["state_backend"]["status"] == "red"


def test_a_degraded_operating_mode_degrades(monkeypatch):
    """R-F3704 — DEGRADED suppresses external delivery (operating_modes.py:189),
    so 'operational' while WhatsApp briefs are dropped is a false clean."""
    out = _health(monkeypatch, mode_normal=False)
    assert out["status"] == "degraded"
    assert any(r.startswith("operating_mode_") for r in out["degraded_reasons"]), (
        f"expected an operating_mode reason, got {out['degraded_reasons']}"
    )


def test_an_exhausted_chain_degrades(monkeypatch):
    out = _health(monkeypatch, chain_resilient=False)
    assert out["status"] == "degraded"
    assert "llm_chain_exhausted" in out["degraded_reasons"]


def test_reasons_accumulate_rather_than_masking_each_other(monkeypatch):
    """The operator must see EVERY failing signal, not just the first."""
    out = _health(monkeypatch, reachable=False, mode_normal=False,
                  chain_resilient=False)
    assert out["status"] == "degraded"
    for expected in ("state_backend_unreachable", "llm_chain_exhausted"):
        assert expected in out["degraded_reasons"], (
            f"{expected} missing from {out['degraded_reasons']} — a boolean "
            f"chain short-circuits and hides the rest"
        )
    assert any(r.startswith("operating_mode_") for r in out["degraded_reasons"])


def test_health_never_raises_when_the_mode_is_unreadable(monkeypatch):
    """Unknown is not healthy — but it must not 500 the status page either."""
    from aria_service.intel import operating_modes as om

    async def _boom():
        raise RuntimeError("store down")

    # Build the healthy baseline first (this also binds app.state.llm_provider,
    # which the handler reads unconditionally), then break ONLY the mode.
    _health(monkeypatch)
    monkeypatch.setattr(om, "get_mode", _boom)
    from aria_service import main

    out = asyncio.run(main.health())
    assert out["status"] == "degraded"
    assert "operating_mode_unknown" in out["degraded_reasons"], (
        "could-not-measure must be reported, never silently certified healthy"
    )


def test_the_response_still_carries_its_documented_blocks(monkeypatch):
    """R-F762/R-F2849 consumers read these; the refactor must not drop them."""
    out = _health(monkeypatch)
    for block in ("loop", "service", "llm_chain", "autonomous", "state_backend",
                  "degraded_reasons"):
        assert block in out, f"/health lost the {block!r} block"


# ══════════════════════════════════════════════════════════════════════════
# R-F3707 — the error-log cache that erased errors during bursts
# ══════════════════════════════════════════════════════════════════════════

def test_a_write_invalidates_the_error_log_read_cache(monkeypatch):
    """THE DEFECT: get() cached any "error_log" key for 5s and NOTHING cleared
    it on write. record_error does read → append → write of the whole blob, so
    two errors inside 5 seconds meant the second read the PRE-APPEND snapshot
    and wrote it back — ERASING THE FIRST. Bursts are exactly when errors
    cluster and when the evidence matters."""
    from aria_service.intel import state_store as ss

    key = "crucix:aria:error_log"
    ss._error_log_cache[key] = (__import__("time").monotonic(), '["first"]')
    assert key in ss._error_log_cache

    written: dict = {}

    async def _fake_upsert(k, v, kind, expires_at, keepttl=False):
        written["k"], written["v"] = k, v

    monkeypatch.setattr(ss, "_upsert", _fake_upsert)
    asyncio.run(ss.set_key(key, '["first","second"]'))

    assert key not in ss._error_log_cache, (
        "the write did not invalidate the cached read — the next reader would "
        "get the pre-append snapshot and overwrite this entry away"
    )
    assert written["v"] == '["first","second"]'


def test_the_cache_predicate_is_shared_between_read_and_write():
    """An inline duplicate is how a cache gets populated on read and never
    cleared on write."""
    from aria_service.intel import state_store as ss
    import inspect

    assert ss._is_error_log_key("crucix:aria:error_log") is True
    assert ss._is_error_log_key("crucix:aria:knowledge") is False
    src = module_source(ss)
    assert src.count('"error_log" in (key or "")') == 1, (
        "the predicate must exist exactly once — the read path and the write "
        "invalidation must not be able to drift apart"
    )


def test_a_non_error_log_write_does_not_touch_the_cache(monkeypatch):
    from aria_service.intel import state_store as ss

    ss._error_log_cache["crucix:aria:error_log"] = (0.0, "keep")

    async def _fake_upsert(k, v, kind, expires_at, keepttl=False):
        return None

    monkeypatch.setattr(ss, "_upsert", _fake_upsert)
    asyncio.run(ss.set_key("crucix:aria:some_other_key", "x"))
    assert "crucix:aria:error_log" in ss._error_log_cache, (
        "an unrelated write must not clear the cache"
    )


# ══════════════════════════════════════════════════════════════════════════
# R-F3707 — the hourly AST sweep that blocked the loop
# ══════════════════════════════════════════════════════════════════════════

def test_the_wire_balance_scan_runs_off_the_event_loop():
    """Measured at 2.78s of pure CPU over 360 modules, hourly, ON the loop."""
    import inspect
    from aria_service.intel import wiring_monitor as wm

    assert hasattr(wm, "_audit_wire_balance_sync"), (
        "the CPU-bound scan must be a separate sync function so it can be offloaded"
    )
    src = function_source(wm, "audit_wire_balance")
    assert "asyncio.to_thread(_audit_wire_balance_sync)" in src, (
        "glob + ast.parse over every intel module must not hold the loop — same "
        "class as R-F3475 (HTML extraction) and R-F1890 (encodes)"
    )


def test_the_brain_wiring_stays_on_the_loop():
    """engine_wiring needs a RUNNING loop; emitting from a worker thread would
    trade a stall for a §21a blind spot."""
    import inspect
    from aria_service.intel import wiring_monitor as wm

    sync_src = function_source(wm, "_audit_wire_balance_sync")
    assert "wire_failure(" not in sync_src and "wire_success(" not in sync_src, (
        "the thread half must not emit brain signals — engine_wiring falls back "
        "when there is no running loop (engine_wiring.py:108-111)"
    )
    assert hasattr(wm, "_wire_and_persist_balance")


def test_the_scan_still_produces_a_complete_report():
    """Offloading must not change WHAT is measured."""
    from aria_service.intel import wiring_monitor as wm

    rep = wm._audit_wire_balance_sync()
    for k in ("total_modules", "modules_with_success", "modules_with_failure",
              "total_success_calls", "total_failure_calls", "unbalanced",
              "well_balanced", "timestamp"):
        assert k in rep, f"the offloaded scan lost the {k!r} field"
    assert rep["total_modules"] > 0

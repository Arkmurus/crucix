"""R-F3061 + R-F3062 — the two REDs left on the brain dashboard.

R-F3061: `outcome[dd] success 0% (n=17)` turned the Delivery organ RED. It was
    NOT 17 broken DDs. `_finalize_dd_run` computed
    `layers_errored = layers_total - layers_ok`, and `layers_ok` required each
    layer to expose `.meta.status`. Five of the twelve layers can never do
    that, so `layers_errored >= 5` on EVERY run and `all_layers_ok` could never
    be true. Live proof (Rheinmetall dd_75bc5a5a7e7c): layers_total=12,
    layers_ok=6, recorded "6 layer(s) errored" — while exactly ONE layer had
    actually failed (digital, timeout after 90s). Same family as R-F3036, where
    /brain/stats `fail` could never increment: a metric that cannot succeed by
    construction.

R-F3062: /api/aria/health awaited get_coverage() inline. The first call after
    every deploy re-parses every module (~5.8s), blowing the brain dashboard's
    8s per-panel budget; the panel was dropped and public/aria-brain.html
    rendered `ECOSYSTEM: UNKNOWN`.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import ecosystem_map as em


# ---------------------------------------------------------------------------
# R-F3061 — a layer's honest state
# ---------------------------------------------------------------------------

def _meta(status):
    return types.SimpleNamespace(status=status)


def _report(**layers):
    r = types.SimpleNamespace()
    for k, v in layers.items():
        setattr(r, k, v)
    return r


def test_rf3061_section_with_meta_ok():
    r = _report(identity=types.SimpleNamespace(meta=_meta("ok")))
    assert ddo._dd_layer_state(r, "identity") == "ok"


@pytest.mark.parametrize("status", ["error", "timeout"])
def test_rf3061_section_with_meta_error(status):
    r = _report(digital=types.SimpleNamespace(meta=_meta(status)))
    assert ddo._dd_layer_state(r, "digital") == "error"


def test_rf3061_plain_dict_layer_is_ok_not_error():
    """counter_intelligence / sanctions_divergence are PLAIN DICTS with no
    meta. They produced a payload — scoring them as failures is a fabricated
    negative.

    FAILS BEFORE: no `.meta` ⇒ excluded from layers_ok ⇒ counted as errored.
    """
    r = _report(counter_intelligence={"entity": "X", "n_signals": 0})
    assert ddo._dd_layer_state(r, "counter_intelligence") == "ok"


def test_rf3061_plain_dict_with_explicit_failure_is_error():
    """The guard must not become a way to hide a real failure."""
    r = _report(sanctions_divergence={"name": "X", "ok": False})
    assert ddo._dd_layer_state(r, "sanctions_divergence") == "error"


def test_rf3061_name_alias_is_resolved():
    """`sweep_intelligence` appears in layers_run; the result lives on
    `sweep_data`.

    FAILS BEFORE: getattr(report, "sweep_intelligence") -> None -> errored.
    """
    r = _report(sweep_data=types.SimpleNamespace(meta=_meta("ok")))
    assert ddo._dd_layer_state(r, "sweep_intelligence") == "ok"
    assert ddo._DD_LAYER_ATTR_ALIAS["sweep_intelligence"] == "sweep_data"


def test_rf3061_absent_layer_is_unobservable_not_error():
    """`forensic` / `deterministic_primitives` store no attribute at all. They
    ran but left nothing to inspect — neither proof of success nor evidence of
    failure. Calling that an error is what produced the permanent 0%."""
    r = _report()
    assert ddo._dd_layer_state(r, "forensic") == "unobservable"
    assert ddo._dd_layer_state(r, "deterministic_primitives") == "unobservable"


def test_rf3061_the_live_rheinmetall_shape_scores_one_error_not_six():
    """THE bug, reproduced from the real report dd_75bc5a5a7e7c.

    Exactly one layer failed (digital, timeout 90s). The old counter said six.
    """
    r = _report(
        identity=types.SimpleNamespace(meta=_meta("ok")),
        compliance=types.SimpleNamespace(meta=_meta("ok")),
        network=types.SimpleNamespace(meta=_meta("ok")),
        digital=types.SimpleNamespace(meta=_meta("error")),      # the ONLY real failure
        sweep_data=types.SimpleNamespace(meta=_meta("ok")),      # via alias
        commercial_coherence=types.SimpleNamespace(meta=_meta("ok")),
        counter_intelligence={"entity": "Rheinmetall AG"},       # plain dict
        sanctions_divergence={"name": "Rheinmetall AG", "ok": True},
        verification=types.SimpleNamespace(meta=_meta("ok")),
        synthesis=types.SimpleNamespace(meta=_meta("ok")),
        # forensic + deterministic_primitives intentionally absent
    )
    layers_run = [
        "identity", "compliance", "network", "digital", "sweep_intelligence",
        "commercial_coherence", "counter_intelligence", "sanctions_divergence",
        "forensic", "deterministic_primitives", "verification", "synthesis",
    ]
    states = {n: ddo._dd_layer_state(r, n) for n in layers_run}
    errored = sum(1 for s in states.values() if s == "error")
    unobservable = sum(1 for s in states.values() if s == "unobservable")

    assert errored == 1, (
        f"expected exactly 1 real failure (digital), got {errored}: "
        f"{[n for n, s in states.items() if s == 'error']}"
    )
    assert unobservable == 2, f"forensic + deterministic_primitives, got {unobservable}"
    assert states["digital"] == "error"


def test_rf3061_a_clean_run_can_actually_succeed():
    """The whole point: `all_layers_ok` must be reachable. Before, five layers
    guaranteed at least five 'errors', so no DD could ever be recorded as a
    successful delivery."""
    r = _report(
        identity=types.SimpleNamespace(meta=_meta("ok")),
        sweep_data=types.SimpleNamespace(meta=_meta("ok")),
        counter_intelligence={"entity": "X"},
    )
    layers_run = ["identity", "sweep_intelligence", "counter_intelligence", "forensic"]
    states = {n: ddo._dd_layer_state(r, n) for n in layers_run}
    assert sum(1 for s in states.values() if s == "error") == 0


# ---------------------------------------------------------------------------
# R-F3062 — /health must not block on a cold ecosystem build
# ---------------------------------------------------------------------------

def test_rf3062_nonblocking_helper_exists():
    assert hasattr(em, "get_coverage_nonblocking")


def test_rf3062_health_uses_the_nonblocking_path():
    """The endpoint must not await get_coverage() inline."""
    src = open(
        __import__("aria_service.routes.aria", fromlist=["x"]).__file__,
        encoding="utf-8",
    ).read()
    assert "get_coverage_nonblocking()" in src, (
        "/health no longer uses the non-blocking coverage path"
    )
    assert "await _ecosystem_map.get_coverage()" not in src, (
        "/health still awaits the blocking build — the UNKNOWN banner returns"
    )


def test_rf3062_cold_cache_returns_none_fast_and_starts_a_build(monkeypatch):
    """Cold cache ⇒ return None quickly (caller reports unknown) while a
    background build warms it. FAILS BEFORE: the helper did not exist and the
    caller blocked for ~6s."""
    monkeypatch.setitem(em._CACHE, "data", None)

    started = {"n": 0}

    async def _slow():
        started["n"] += 1
        await asyncio.sleep(5)
        return {"health_sensors": {}}

    monkeypatch.setattr(em, "get_coverage", _slow)
    monkeypatch.setattr(em, "_COVERAGE_TASK", None, raising=False)

    async def _go():
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        out = await em.get_coverage_nonblocking(max_wait_s=0.2)
        elapsed = loop.time() - t0
        # let the shielded background task settle so pytest sees no stray task
        task = em._COVERAGE_TASK
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return out, elapsed

    out, elapsed = asyncio.run(_go())
    assert out is None, "cold cache must report not-measured, never a fake value"
    assert elapsed < 2.0, f"blocked for {elapsed:.2f}s — the panel budget is 8s"
    assert started["n"] == 1, "no background build was started; cache stays cold forever"


def test_rf3062_wait_is_shielded_so_a_timeout_cannot_discard_the_build():
    """`asyncio.wait_for` CANCELS its awaitable. Cancelling a ~6s parse on
    every timeout would leave the cache permanently cold — the same
    'wait_for cancels, so the work was discarded' trap seen in the DD path.
    """
    import inspect

    src = inspect.getsource(em.get_coverage_nonblocking)
    assert "asyncio.shield" in src, (
        "the bounded wait is not shielded — a timeout would cancel the build "
        "and the cache could never warm"
    )

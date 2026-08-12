"""C-37 / R-F3934 — `success_rate: 0.0` meant two different things.

Found by the DD sweep. Live on aria-intel, `GET /api/aria/brain/stats` reported eight
modules at `success_rate: 0.0`:

    health_precompute, deploy, llm_recovery_probe, autonomous_safety,
    llm_deepseek, llm_deepseek_backup, llm_chain_exhausted, search_searxng

`success_rate = success / (total - skip)`. For a module that only ever calls
`wire_failure`, every recorded signal is a failure BY CONSTRUCTION, so the rate is
0.0 whether it fired once or ten thousand times — it can never be anything else,
however healthy the module is. Verified statically: `aria_service/llm/openai_compat.py`,
the emitter for every `llm_*` module, contains **zero** `wire_success` calls, and
`main.py::_health_precompute_loop` likewise wires only failures.

The list is MIXED, which is what makes it dangerous: `search_searxng` *does* wire
success, so its 0.0 is a genuine measurement. An operator scanning the panel saw eight
broken subsystems; several were failure-only reporters doing exactly what they were
built to do.

WHY THIS DOES NOT GUESS. The counters cannot distinguish the two cases — "never
succeeded" and "cannot succeed" are the same numbers. The distinction is a static
property of the module's wiring, and the obvious idea (reuse `wiring_monitor` M1's
wire-balance AST scan) DOES NOT WORK: M1 globs `intel/*.py` only, so it never sees
`llm/openai_compat.py` or `main.py`, and it keys results by FILE NAME while the brain's
module keys are runtime strings — `llm_deepseek` is built as `f"llm_{self.name}"` and
corresponds to no file at all. A mapping from one to the other would be invented.

So the honest move is the one this repo keeps arriving at: when you cannot tell, say
so. `only_failures_recorded` marks the entries whose rate carries no information, and
points the reader at `fail`/`total`, which do.
"""
from __future__ import annotations

import pytest

from aria_service.intel import brain_hook


def _with_stats(monkeypatch, blob: dict):
    """Drive the REAL get_stats() over a controlled stats blob.

    Patches the store read it actually performs and clears the 30s cache, so the
    test exercises the shipped aggregation rather than a reimplementation.
    """
    class _S:
        async def get_json(self, key):
            return blob

    import aria_service.intel.redis_store as _rs
    monkeypatch.setattr(_rs, 'get_json', _S().get_json)
    monkeypatch.setattr(brain_hook, '_stats_cache', None, raising=False)
    monkeypatch.setattr(brain_hook, '_stats_cache_at', 0.0, raising=False)
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(brain_hook, '_flush_stats_pending', _noop, raising=False)


def _module_entry(stats: dict, name: str) -> dict:
    mods = stats.get("modules") or {}
    assert name in mods, f"{name} missing from stats: {sorted(mods)[:10]}"
    return mods[name]


@pytest.mark.asyncio
async def test_a_failure_only_module_is_marked(monkeypatch) -> None:
    """THE SYMPTOM: a module that has only ever reported failures.

    Its `success_rate` is 0.0 and always will be. The entry must say so rather than
    leaving the reader to infer a broken subsystem.
    """
    fake = {
        "only_fails": {"total": 2, "success": 0, "fail": 2, "skip": 0,
                       "last_signal_at": 1_780_000_000},
        "_global": {},
    }
    _with_stats(monkeypatch, fake)

    stats = await brain_hook.get_stats()
    entry = _module_entry(stats, "only_fails")

    assert entry["success_rate"] == 0
    assert entry["only_failures_recorded"] is True, (
        "C-37: a rate that cannot vary was published as a measured rate"
    )


@pytest.mark.asyncio
async def test_a_module_with_any_success_is_not_marked(monkeypatch) -> None:
    """A module that HAS succeeded is genuinely measurable — never flag it."""
    fake = {
        "mixed": {"total": 10, "success": 3, "fail": 7, "skip": 0,
                  "last_signal_at": 1_780_000_000},
        "_global": {},
    }
    _with_stats(monkeypatch, fake)

    entry = _module_entry(await brain_hook.get_stats(), "mixed")
    assert entry["only_failures_recorded"] is False
    assert entry["success_rate"] == 0.3


@pytest.mark.asyncio
async def test_a_quiet_module_is_not_marked(monkeypatch) -> None:
    """Zero failures is not 'only failures'. A module that has recorded nothing must
    not be flagged — that would be inventing a claim about an absence, which is the
    defect this whole sweep is about."""
    fake = {
        "quiet": {"total": 0, "success": 0, "fail": 0, "skip": 0,
                  "last_signal_at": 1_780_000_000},
        "_global": {},
    }
    _with_stats(monkeypatch, fake)

    entry = _module_entry(await brain_hook.get_stats(), "quiet")
    assert entry["only_failures_recorded"] is False


@pytest.mark.asyncio
async def test_the_flag_is_additive_and_breaks_no_existing_field(monkeypatch) -> None:
    """The existing contract must be untouched: `success_rate` stays a number and the
    raw counters stay put, so no consumer changes behaviour."""
    fake = {
        "m": {"total": 4, "success": 0, "fail": 4, "skip": 0,
              "last_signal_at": 1_780_000_000},
        "_global": {},
    }
    _with_stats(monkeypatch, fake)

    entry = _module_entry(await brain_hook.get_stats(), "m")
    for field in ("total", "success", "fail", "skip", "success_rate", "status"):
        assert field in entry, f"{field} disappeared from the module entry"
    assert isinstance(entry["success_rate"], (int, float))


@pytest.mark.asyncio
async def test_a_module_with_only_skips_is_marked_unmeasurable(monkeypatch) -> None:
    """C-37 RESIDUAL (R-F3936) — the same defect at the other end of the expression.

    `success_rate` falls back to `0` when `total - skip == 0`, i.e. when there is
    nothing to divide. Found LIVE after the C-37 deploy: `deploy` reported
    `success_rate: 0.0, fail: 0, total: 1` — neither a failure nor a rate, yet
    indistinguishable from a module that failed every call.

    `only_failures_recorded` does not cover it (there are no failures), so it needs
    its own flag rather than being quietly folded into one that would then be lying.
    """
    fake = {
        "all_skipped": {"total": 3, "success": 0, "fail": 0, "skip": 3,
                        "last_signal_at": 1_780_000_000},
        "_global": {},
    }
    _with_stats(monkeypatch, fake)

    entry = _module_entry(await brain_hook.get_stats(), "all_skipped")
    assert entry["success_rate"] == 0
    assert entry["no_measurable_signals"] is True, (
        "a rate computed from an empty denominator was published as a measurement"
    )
    assert entry["only_failures_recorded"] is False, (
        "there were no failures - do not mislabel skips as failures"
    )


@pytest.mark.asyncio
async def test_a_genuinely_measured_module_is_not_marked_unmeasurable(monkeypatch) -> None:
    """The flag must be able to be False, or it carries no information."""
    fake = {
        "real": {"total": 5, "success": 4, "fail": 1, "skip": 0,
                 "last_signal_at": 1_780_000_000},
        "_global": {},
    }
    _with_stats(monkeypatch, fake)

    entry = _module_entry(await brain_hook.get_stats(), "real")
    assert entry["no_measurable_signals"] is False
    assert entry["success_rate"] == 0.8

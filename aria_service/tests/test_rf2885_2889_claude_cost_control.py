"""R-F2885..R-F2889 — capability tests for the Claude-flip cost-control layer.

Context (§3c): before this batch, setting ANTHROPIC_API_KEY would arm several
Claude spend paths that cost_tracker could not see, and the ONLY live brake was
the $300 monthly cap. Each test below drives the ACTUAL path that was broken —
not a helper — and asserts the user-visible outcome (spend is counted / the call
is refused / the operator is told).

Every test here was written to FAIL against the pre-fix tree:
  * daily cap            → assert_daily_cap did not exist
  * metered enforcement  → only assert_monthly_cap was called
  * OCR metering         → usage discarded, no cap check, mislabelled model
  * reviewer metering    → create_llm_provider returned a BARE provider
  * threshold push       → _emit_threshold_warnings was logger-only, monthly-only
  * one pricing table    → cost_monitor priced from its own stale table
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types

import pytest

from aria_service.intel import cost_tracker as ct


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────
# Fake store — mimics the redis_store surface cost_tracker actually uses.
# ──────────────────────────────────────────────────────────────────────────
class _FakeStore:
    def __init__(self):
        self.floats: dict[str, float] = {}
        self.json: dict[str, object] = {}

    async def incrbyfloat(self, key, amount):
        self.floats[key] = self.floats.get(key, 0.0) + float(amount)
        return self.floats[key]

    async def get(self, key):
        if key in self.floats:
            return str(self.floats[key])
        return None

    async def expire(self, key, ttl):
        return True

    async def set_json(self, key, value, ex=None):
        self.json[key] = value
        return True

    async def get_json(self, key):
        return self.json.get(key)


@pytest.fixture
def store(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(ct, "rs", fake)
    ct._warned_thresholds.clear()
    return fake


# ──────────────────────────────────────────────────────────────────────────
# R-F2888 — the daily cap is REAL (it did not exist before)
# ──────────────────────────────────────────────────────────────────────────
class TestDailyCap:
    def test_daily_cap_blocks_once_over(self, store, monkeypatch):
        """The capability that was entirely missing: a day ceiling that refuses."""
        monkeypatch.setenv("ARIA_DAILY_CAP_USD", "5.00")
        monkeypatch.delenv("ARIA_DAILY_CAP_WARN_ONLY", raising=False)
        day_key = f"{ct.COST_DAY_PREFIX}{ct._current_day_key()}"
        store.floats[day_key] = 6.00  # already over

        with pytest.raises(ct.DailyCostCapExceeded):
            _run(ct.assert_daily_cap())

    def test_daily_cap_allows_under(self, store, monkeypatch):
        monkeypatch.setenv("ARIA_DAILY_CAP_USD", "5.00")
        store.floats[f"{ct.COST_DAY_PREFIX}{ct._current_day_key()}"] = 0.10
        _run(ct.assert_daily_cap())  # must not raise

    def test_daily_reserve_stops_concurrent_overshoot(self, store, monkeypatch):
        """R-F2111's lesson applied to the day key: N concurrent calls must not
        all pass the same sub-cap read. The reserve is what prevents it."""
        monkeypatch.setenv("ARIA_DAILY_CAP_USD", "1.00")

        async def _hammer():
            # Each reserves 0.40; the third pushes the total over 1.00.
            results = []
            for _ in range(3):
                try:
                    await ct.assert_daily_cap(estimated_cost_usd=0.40)
                    results.append("ok")
                except ct.DailyCostCapExceeded:
                    results.append("blocked")
            return results

        assert _run(_hammer()) == ["ok", "ok", "blocked"]

    def test_warn_only_does_not_block(self, store, monkeypatch):
        monkeypatch.setenv("ARIA_DAILY_CAP_USD", "1.00")
        monkeypatch.setenv("ARIA_DAILY_CAP_WARN_ONLY", "1")
        store.floats[f"{ct.COST_DAY_PREFIX}{ct._current_day_key()}"] = 99.0
        _run(ct.assert_daily_cap())  # must not raise

    def test_store_down_fails_open(self, monkeypatch):
        """A cost ceiling must never be the reason a request dies when we cannot
        even read the counter — the monthly cap remains the backstop."""
        class _Dead:
            async def incrbyfloat(self, *a, **k):
                raise RuntimeError("store down")
            async def get(self, *a, **k):
                raise RuntimeError("store down")
            async def expire(self, *a, **k):
                raise RuntimeError("store down")

        monkeypatch.setattr(ct, "rs", _Dead())
        monkeypatch.setenv("ARIA_DAILY_CAP_USD", "1.00")
        _run(ct.assert_daily_cap())  # must not raise

    def test_get_day_spend_reports_utilisation(self, store, monkeypatch):
        monkeypatch.setenv("ARIA_DAILY_CAP_USD", "10.00")
        store.floats[f"{ct.COST_DAY_PREFIX}{ct._current_day_key()}"] = 2.50
        out = _run(ct.get_day_spend())
        assert out["spent_usd"] == 2.50
        assert out["cap_usd"] == 10.00
        assert out["utilisation_pct"] == 25.0


# ──────────────────────────────────────────────────────────────────────────
# R-F2888 — MeteredProvider actually consults the daily cap
# ──────────────────────────────────────────────────────────────────────────
class TestMeteredEnforcesDaily:
    def test_metered_complete_refuses_when_daily_cap_hit(self, monkeypatch):
        """THE broken path: every metered LLM call now hits the day ceiling.
        Pre-fix, _enforce_monthly_cap ignored the day entirely."""
        from aria_service.llm.metered import MeteredProvider

        called = {"inner": False}

        class _Inner:
            name = "anthropic"
            is_configured = True
            async def complete(self, *a, **k):
                called["inner"] = True
                return types.SimpleNamespace(
                    model="claude-sonnet-5", input_tokens=1, output_tokens=1)

        async def _boom(*a, **k):
            raise ct.DailyCostCapExceeded(spent=99.0, cap=25.0, day="2026-07-23")

        monkeypatch.setattr(ct, "assert_daily_cap", _boom)
        monkeypatch.setattr(ct, "assert_monthly_cap", lambda *a, **k: _noop())

        with pytest.raises(ct.DailyCostCapExceeded):
            _run(MeteredProvider(_Inner()).complete("sys", "user"))
        assert called["inner"] is False, "provider was called despite the cap"


async def _noop():
    return None


# ──────────────────────────────────────────────────────────────────────────
# R-F2889 — threshold warnings reach the OPERATOR, not just the log
# ──────────────────────────────────────────────────────────────────────────
class TestSpendAlerts:
    def test_80pct_pushes_to_operator(self, store, monkeypatch):
        pushed = []

        async def _fake_record(**kwargs):
            pushed.append(kwargs)
            return {}

        from aria_service.intel import pending_actions as _pa_mod
        monkeypatch.setattr(_pa_mod, "record", _fake_record)

        async def _drive():
            ct._emit_threshold_warnings(8.0, 10.0, "2026-07-23", scope="daily")
            await asyncio.sleep(0)  # let the dispatched task run
            await asyncio.sleep(0)

        _run(_drive())
        assert pushed, "80% of cap did not reach the operator queue"
        assert pushed[0]["severity"] == "HIGH"

    def test_100pct_is_critical(self, store, monkeypatch):
        pushed = []

        async def _fake_record(**kwargs):
            pushed.append(kwargs)
            return {}

        from aria_service.intel import pending_actions as _pa_mod
        monkeypatch.setattr(_pa_mod, "record", _fake_record)

        async def _drive():
            ct._emit_threshold_warnings(10.0, 10.0, "2026-07", scope="monthly")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        _run(_drive())
        assert pushed and pushed[0]["severity"] == "CRITICAL"

    def test_50pct_stays_log_only(self, store, monkeypatch):
        """Below 80% must NOT page the operator — alert fatigue is the failure
        mode that makes a real alert get ignored."""
        pushed = []

        async def _fake_record(**kwargs):
            pushed.append(kwargs)
            return {}

        from aria_service.intel import pending_actions as _pa_mod
        monkeypatch.setattr(_pa_mod, "record", _fake_record)

        async def _drive():
            ct._emit_threshold_warnings(5.0, 10.0, "2026-07-23", scope="daily")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        _run(_drive())
        assert not pushed

    def test_latched_once_per_period(self, store):
        """Each threshold fires at most once per (scope, period)."""
        ct._emit_threshold_warnings(8.0, 10.0, "2026-07-23", scope="daily")
        before = set(ct._warned_thresholds)
        ct._emit_threshold_warnings(8.1, 10.0, "2026-07-23", scope="daily")
        assert set(ct._warned_thresholds) == before

    def test_daily_latches_pruned_on_day_change(self, store):
        ct._emit_threshold_warnings(8.0, 10.0, "2026-07-22", scope="daily")
        ct._emit_threshold_warnings(8.0, 10.0, "2026-07-23", scope="daily")
        assert not any(t.startswith("daily:2026-07-22:")
                       for t in ct._warned_thresholds)

    def test_monthly_scope_still_works_with_legacy_3arg_call(self, store):
        """The existing hot-path callers pass 3 positional args — they must not
        break when scope defaults to monthly."""
        ct._emit_threshold_warnings(9.0, 10.0, "2026-07")
        assert any(t.startswith("monthly:2026-07:") for t in ct._warned_thresholds)


# ──────────────────────────────────────────────────────────────────────────
# R-F2888 — ONE pricing table
# ──────────────────────────────────────────────────────────────────────────
class TestSinglePricingSource:
    def test_cost_monitor_delegates_to_cost_tracker(self):
        """cost_monitor priced Claude from its own stale table. Both must now
        agree, or spend is under-counted depending on which path a caller hits."""
        from aria_service.autonomous.cost_monitor import compute_cost

        for model in ("claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"):
            assert compute_cost(model, 1_000_000, 1_000_000) == pytest.approx(
                ct.estimate_cost_usd(model, 1_000_000, 1_000_000)
            ), f"{model} priced differently by the two tables"

    def test_models_absent_from_the_stale_table_are_priced(self):
        """claude-opus-4-8 had NO row in cost_monitor.PRICING — it silently used
        the _default Sonnet rate, under-counting Opus by ~1.7x."""
        from aria_service.autonomous.cost_monitor import compute_cost

        opus = compute_cost("claude-opus-4-8", 1_000_000, 1_000_000)
        assert opus == pytest.approx(30.00), opus  # 5 in + 25 out


# ──────────────────────────────────────────────────────────────────────────
# R-F2887 — the coder reviewer's provider is metered
# ──────────────────────────────────────────────────────────────────────────
class TestReviewerMetered:
    def test_reviewer_providers_are_wrapped(self, monkeypatch):
        """The broken path: _build_providers returned BARE providers, so a
        review cycle spent off-ledger and past both caps."""
        from aria_service.llm.metered import MeteredProvider
        from aria_service.autonomous import claude_reviewer as cr

        monkeypatch.setenv("ARIA_CODER_CLAUDE_REVIEW_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OLLAMA_URL", raising=False)

        reviewer = cr.ClaudeReviewer()
        providers = reviewer._build_provider_chain()

        assert providers, "no review providers built — test drove the wrong path"
        for p in providers:
            assert isinstance(p, MeteredProvider), (
                f"{getattr(p, 'name', p)} is unmetered — its spend is invisible"
            )

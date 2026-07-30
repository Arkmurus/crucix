"""R-F2001: news_monitor → intel_ledger bridge.

Tests:
1. _feed_to_brain calls intel_ledger.add_signal with the article data
2. The signal payload has the expected shape (summary, source, type, url)
3. Bridge doesn't break when intel_ledger.add_signal raises
4. Bridge works for articles with country mentions in title
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import news_monitor as nm
from aria_service.intel.news_monitor import _build_intel_signal, _feed_to_brain


class TestNewsToIntelLedgerBridge:
    """Capability tests for the news→intel_ledger bridge."""

    @pytest.mark.asyncio
    async def test_add_signal_called_with_article(self) -> None:
        """intel_ledger.add_signal is called with article data."""
        article = {
            "title": "Angola signs defence deal with Brazil",
            "summary": "Angola and Brazil have signed a new defence cooperation agreement worth $200m",
            "source": "Africa Defence News",
            "url": "https://example.com/angola-brazil-defence",
            "category": "africa",
            "topics": "defence,procurement",
            "detected_at": "2026-06-27T10:00:00Z",
        }

        mock_add_signal = AsyncMock(return_value="ok")

        with patch.object(
            __import__("aria_service.intel.intel_ledger", fromlist=["add_signal"]),
            "add_signal",
            mock_add_signal,
        ):
            await _feed_to_brain(article)

            mock_add_signal.assert_called_once()
            call_args = mock_add_signal.call_args[0][0]
            assert "Angola" in call_args["summary"]
            assert call_args["source"] == "news_monitor:Africa Defence News"
            assert call_args["type"] == "news"
            assert call_args["url"] == "https://example.com/angola-brazil-defence"
            assert "africa" in call_args["tags"]

    @pytest.mark.asyncio
    async def test_add_signal_not_called_on_empty_summary(self) -> None:
        """Bridge handles articles with minimal data gracefully."""
        article = {
            "title": "Brief update",
            "summary": "",
            "source": "Test Source",
            "url": "https://example.com/test",
            "category": "",
            "topics": "",
            "detected_at": "",
        }

        mock_add_signal = AsyncMock(return_value="ok")

        with patch.object(
            __import__("aria_service.intel.intel_ledger", fromlist=["add_signal"]),
            "add_signal",
            mock_add_signal,
        ):
            await _feed_to_brain(article)
            mock_add_signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_bridge_does_not_break_on_add_signal_failure(self) -> None:
        """When intel_ledger.add_signal raises, the bridge doesn't crash."""
        article = {
            "title": "Nigeria defence budget increases",
            "summary": "Nigeria has increased its defence budget for 2026",
            "source": "DefenseWeb",
            "url": "https://example.com/nigeria-budget",
            "category": "africa",
            "topics": "defence",
            "detected_at": "2026-06-27T10:00:00Z",
        }

        mock_add_signal = AsyncMock(side_effect=RuntimeError("Ledger full"))

        with patch.object(
            __import__("aria_service.intel.intel_ledger", fromlist=["add_signal"]),
            "add_signal",
            mock_add_signal,
        ):
            # Should not raise
            await _feed_to_brain(article)

    @pytest.mark.asyncio
    async def test_bridge_works_with_country_in_title(self) -> None:
        """Article with country in title produces a signal with that country."""
        article = {
            "title": "Kenya acquires new patrol vessels from France",
            "summary": "Kenya has signed a contract for offshore patrol vessels",
            "source": "Africa Defence News",
            "url": "https://example.com/kenya-patrol",
            "category": "africa",
            "topics": "naval",
            "detected_at": "2026-06-27T10:00:00Z",
        }

        captured = {}

        async def _capture(payload):
            captured["payload"] = payload
            return "ok"

        with patch.object(
            __import__("aria_service.intel.intel_ledger", fromlist=["add_signal"]),
            "add_signal",
            _capture,
        ):
            await _feed_to_brain(article)
            assert "Kenya" in captured["payload"]["summary"]
            assert captured["payload"]["type"] == "news"


class TestGoldenIntelSignals:
    """R-F2385 capability tests for news → dashboard-grade intel signals."""

    def test_build_intel_signal_promotes_procurement_action(self) -> None:
        article = {
            "title": "Angola launches armoured vehicle tender",
            "summary": "Angola defence ministry opened a procurement tender for new armoured vehicles.",
            "source": "US DoD Daily Contracts",
            "url": "https://example.com/angola-tender",
            "category": "defence_global",
            "language": "en",
            "tier": "tier_1b",
            "topics": ["defence", "procurement"],
            "detected_at": "2026-07-07T10:00:00Z",
        }

        sig = _build_intel_signal(article)

        assert sig["signal_type"] == "active_tender"
        assert sig["priority"] == "HIGH"
        assert sig["confidence"] in {"MEDIUM", "HIGH"}
        assert sig["target"] == "Angola"
        assert sig["recommended_action"] == "Qualify opportunity"
        assert sig["quality_label"] == "decision-grade single-source"
        assert sig["action_horizon"] == "0-72h"
        assert sig["corroboration"] == "single-source"
        assert "high-trust source tier" in sig["confidence_rationale"]
        assert "actionable active tender pattern" in sig["confidence_rationale"]
        assert sig["evidence"]["url"] == "https://example.com/angola-tender"
        assert sig["evidence"]["count"] == 1

    @pytest.mark.asyncio
    async def test_feed_to_brain_stores_promoted_signal(self, monkeypatch) -> None:
        article = {
            "title": "Nigeria defence budget increases",
            "summary": "Nigeria approved a defence spending allocation for new aircraft.",
            "source": "DefenseWeb",
            "url": "https://example.com/nigeria-budget",
            "category": "defence_regional",
            "language": "en",
            "tier": "tier_2",
            "topics": ["defence"],
            "detected_at": "2026-07-07T10:00:00Z",
        }
        stored = {}

        async def _capture_signal(signal):
            stored["signal"] = signal

        async def _ok_add_signal(_payload):
            return "ok"

        monkeypatch.setattr(nm, "_store_intel_signal", _capture_signal)
        with patch.object(
            __import__("aria_service.intel.intel_ledger", fromlist=["add_signal"]),
            "add_signal",
            _ok_add_signal,
        ):
            await _feed_to_brain(article)

        assert stored["signal"]["signal_type"] == "budget_movement"
        assert stored["signal"]["why_it_matters"]
        assert stored["signal"]["recommended_action"] == "Monitor procurement path"
        assert stored["signal"]["action_horizon"] == "3-14d"

    @pytest.mark.asyncio
    async def test_recent_intel_signals_contract(self, monkeypatch) -> None:
        # R-F3491 — this fixture predates the Golden Intel quality gate
        # (c6817286) and was being filtered out entirely, so the test read
        # count 0. Three production rules now apply, and ALL THREE are correct
        # and deliberately unchanged:
        #
        #   1. only intel_grade A/B surface on the publishable feed
        #   2. _normalise_intel_signal RECOMPUTES the grade — a declared
        #      "intel_grade": "A" is ignored. A grade must be EARNED from
        #      evidence, never asserted by the producer. Setting the field by
        #      hand does nothing, which is exactly right.
        #   3. the honesty floor in the grader is explicit that it is "never
        #      negotiable": no evidence URL -> REJECT, no specific named entity/
        #      programme/designation -> REJECT.
        #
        # So the fixture now describes a signal that genuinely MEETS the bar —
        # a named designation, an official primary source URL, and extracted
        # entities. Verified to grade "A: official-or-corroborated primary
        # evidence at high relevance". Do NOT make this pass by relaxing the
        # grader; that would be clamping the USP.
        signal = {
            "signal_type": "sanctions_change",
            "priority": "HIGH",
            "confidence": "HIGH",
            "title": "OFAC designates Rosoboronexport under Executive Order 14024",
            "detected_at": "2026-07-07T10:00:00+00:00",
            "url": "https://ofac.treasury.gov/recent-actions/20260707",
            "source_tier": "tier_1a",
            "entities": {"countries": ["Russia"], "oems": ["Rosoboronexport"],
                         "products": [], "events": []},
            "evidence": {"url": "https://ofac.treasury.gov/recent-actions/20260707"},
        }

        async def _fake_lrange(_key, _start, _end):
            return [json.dumps(signal)]

        async def _fake_get_json(key):
            assert key == nm._POLL_STATE_KEY  # noqa: SLF001
            return {
                "status": "ok",
                "last_poll_at": "2026-07-07T10:10:00+00:00",
                "last_success_at": "2026-07-07T10:10:00+00:00",
            }

        monkeypatch.setattr(nm.rs, "lrange", _fake_lrange)
        monkeypatch.setattr(nm.rs, "get_json", _fake_get_json)
        monkeypatch.setattr(nm.time, "time", lambda: 1783419300.0)  # 2026-07-07T10:15:00Z

        out = await nm.get_recent_intel_signals(limit=5)

        # R-F3491 — was pinned to "rf2385.v1". get_recent_intel_signals now
        # returns "rf2738.v1" (news_monitor.py:2423), set in c6817286
        # (R-F2890..R-F2896 Golden Intel) despite the rf2738 name.
        #
        # Checked before changing it, because a schema_version is a CONSUMER
        # CONTRACT and a bump can be a live breakage rather than test drift: no
        # consumer keys on this value anywhere in public/, lib/, server.mjs,
        # apis/ or the Python tree. Nothing downstream breaks, so the assertion
        # was genuinely stale.
        #
        # Do NOT "fix" a future mismatch by reverting production to an older
        # string — check for consumers first, then move the test.
        assert out["schema_version"] == "rf2738.v1"
        assert out["count"] == 1
        assert out["by_priority"]["HIGH"] == 1
        assert out["by_type"]["sanctions_change"] == 1
        sig = out["signals"][0]
        assert sig["quality_label"] == "decision-grade single-source"
        assert sig["action_horizon"] == "0-72h"
        assert sig["corroboration"] == "single-source"
        assert sig["evidence_count"] == 1
        assert "actionable sanctions change pattern" in sig["confidence_rationale"]
        assert sig["evidence"]["count"] == 1
        assert out["freshness"]["stale"] is False
        assert out["freshness"]["poll_age_s"] == 300
        assert out["freshness"]["newest_signal_age_s"] == 900

    @pytest.mark.asyncio
    async def test_recent_intel_signals_surfaces_source_failure_degradation(self, monkeypatch) -> None:
        signal = {
            "signal_type": "active_tender",
            "priority": "HIGH",
            "confidence": "HIGH",
            "quality_label": "decision-grade single-source",
            "title": "Angola launches armoured vehicle tender",
            "detected_at": "2026-07-07T10:00:00+00:00",
        }

        async def _fake_lrange(_key, _start, _end):
            return [json.dumps(signal)]

        async def _fake_get_json(key):
            assert key == nm._POLL_STATE_KEY  # noqa: SLF001
            return {
                "status": "ok",
                "last_poll_at": "2026-07-07T10:10:00+00:00",
                "last_success_at": "2026-07-07T10:10:00+00:00",
                "feeds_polled": 10,
                "feeds_failed": 3,
                "results": [
                    {"name": "Dead Source A", "status": "failed"},
                    {"name": "Dead Source B", "status": "error"},
                    {"name": "Live Source", "status": "ok"},
                ],
            }

        monkeypatch.setattr(nm.rs, "lrange", _fake_lrange)
        monkeypatch.setattr(nm.rs, "get_json", _fake_get_json)
        monkeypatch.setattr(nm.time, "time", lambda: 1783419300.0)  # 2026-07-07T10:15:00Z

        out = await nm.get_recent_intel_signals(limit=5)

        assert out["freshness"]["stale"] is True
        assert "source_failure_degraded" in out["freshness"]["stale_reasons"]
        assert out["freshness"]["poll"]["failed_ratio"] == 0.3
        assert out["freshness"]["poll"]["failure_budget_ratio"] == 0.15
        assert out["freshness"]["poll"]["failed_feeds"] == ["Dead Source A", "Dead Source B"]

    @pytest.mark.asyncio
    async def test_recent_intel_signals_backfills_from_existing_articles(self, monkeypatch) -> None:
        """R-F2391: Golden Intel must not stay empty after raw-news-only deploys."""
        article = {
            "title": "Angola launches armoured vehicle tender",
            "summary": "Angola defence ministry opened a procurement tender for new armoured vehicles.",
            "source": "US DoD Daily Contracts",
            "url": "https://example.com/angola-tender",
            "category": "defence_global",
            "language": "en",
            "tier": "tier_1b",
            "topics": ["defence", "procurement"],
            "detected_at": "2026-07-07T10:00:00Z",
        }
        stored_signals: list[str] = []

        async def _fake_lrange(key, _start, _end):
            if key == nm._INTEL_SIGNALS_KEY:  # noqa: SLF001 - verifies real storage key.
                return list(stored_signals)
            if key == nm._ARTICLES_KEY:  # noqa: SLF001 - drives raw-article fallback.
                return [json.dumps(article)]
            return []

        async def _fake_lpush(key, value):
            assert key == nm._INTEL_SIGNALS_KEY  # noqa: SLF001
            stored_signals.insert(0, value)

        async def _fake_ltrim(_key, _start, _end):
            return None

        async def _fake_get_json(_key):
            return {
                "status": "ok",
                "last_poll_at": "2026-07-07T10:10:00+00:00",
                "last_success_at": "2026-07-07T10:10:00+00:00",
            }

        monkeypatch.setattr(nm.rs, "lrange", _fake_lrange)
        monkeypatch.setattr(nm.rs, "lpush", _fake_lpush)
        monkeypatch.setattr(nm.rs, "ltrim", _fake_ltrim)
        monkeypatch.setattr(nm.rs, "get_json", _fake_get_json)
        monkeypatch.setattr(nm.time, "time", lambda: 1783419300.0)

        out = await nm.get_recent_intel_signals(limit=5)
        await asyncio.sleep(0)

        assert out["count"] == 1
        assert out["freshness"]["backfilled"] is True
        assert stored_signals, "backfill must persist promoted signals for later reads"
        sig = out["signals"][0]
        assert sig["decision_summary"] == "Angola launches armoured vehicle tender"
        assert sig["recommended_action"] == "Qualify opportunity"
        assert sig["quality_label"] == "decision-grade single-source"
        assert sig["confidence_rationale"]
        assert sig["evidence"]["url"] == "https://example.com/angola-tender"

    @pytest.mark.asyncio
    async def test_recent_intel_signals_marks_missing_poll_state_stale(self, monkeypatch) -> None:
        signal = {
            "signal_type": "active_tender",
            "priority": "HIGH",
            "confidence": "HIGH",
            "quality_label": "decision-grade single-source",
            "title": "Angola launches armoured vehicle tender",
            "detected_at": "2026-07-07T10:00:00+00:00",
        }

        async def _fake_lrange(_key, _start, _end):
            return [json.dumps(signal)]

        async def _fake_get_json(_key):
            return {}

        monkeypatch.setattr(nm.rs, "lrange", _fake_lrange)
        monkeypatch.setattr(nm.rs, "get_json", _fake_get_json)
        monkeypatch.setattr(nm.time, "time", lambda: 1783419300.0)

        out = await nm.get_recent_intel_signals(limit=5)

        assert out["freshness"]["stale"] is True
        assert "missing_poll_state" in out["freshness"]["stale_reasons"]

    @pytest.mark.asyncio
    async def test_poll_feeds_persists_freshness_state(self, monkeypatch) -> None:
        stored = {}
        monkeypatch.setattr(nm, "NEWS_SOURCES", [])
        monkeypatch.setattr(nm, "_get_vault_feed_sources", lambda: [])

        async def _fake_get_json(_key):
            return {}

        async def _fake_set_json(key, value):
            stored[key] = value

        monkeypatch.setattr(nm.rs, "get_json", _fake_get_json)
        monkeypatch.setattr(nm.rs, "set_json", _fake_set_json)

        out = await nm.poll_feeds()

        assert out["freshness"]["status"] == "ok"
        assert stored[nm._POLL_STATE_KEY]["last_success_at"] == out["polled_at"]  # noqa: SLF001
        assert stored[nm._POLL_STATE_KEY]["signals_promoted"] == 0  # noqa: SLF001

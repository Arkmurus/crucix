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
        signal = {
            "signal_type": "sanctions_change",
            "priority": "HIGH",
            "confidence": "HIGH",
            "title": "New sanctions designation",
        }

        async def _fake_lrange(_key, _start, _end):
            return [json.dumps(signal)]

        monkeypatch.setattr(nm.rs, "lrange", _fake_lrange)

        out = await nm.get_recent_intel_signals(limit=5)

        assert out["schema_version"] == "rf2385.v1"
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

        monkeypatch.setattr(nm.rs, "lrange", _fake_lrange)
        monkeypatch.setattr(nm.rs, "lpush", _fake_lpush)
        monkeypatch.setattr(nm.rs, "ltrim", _fake_ltrim)

        out = await nm.get_recent_intel_signals(limit=5)
        await asyncio.sleep(0)

        assert out["count"] == 1
        assert stored_signals, "backfill must persist promoted signals for later reads"
        sig = out["signals"][0]
        assert sig["decision_summary"] == "Angola launches armoured vehicle tender"
        assert sig["recommended_action"] == "Qualify opportunity"
        assert sig["quality_label"] == "decision-grade single-source"
        assert sig["confidence_rationale"]
        assert sig["evidence"]["url"] == "https://example.com/angola-tender"

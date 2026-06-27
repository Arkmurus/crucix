"""R-F2001: news_monitor → intel_ledger bridge.

Tests:
1. _feed_to_brain calls intel_ledger.add_signal with the article data
2. The signal payload has the expected shape (summary, source, type, url)
3. Bridge doesn't break when intel_ledger.add_signal raises
4. Bridge works for articles with country mentions in title
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel.news_monitor import _feed_to_brain


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

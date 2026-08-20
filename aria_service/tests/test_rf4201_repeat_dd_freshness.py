"""R-F4201 — prior global reads cannot starve a repeat entity DD."""

import asyncio
from unittest.mock import AsyncMock, patch

from aria_service.intel import deep_researcher as DR


class _LLM:
    is_configured = True

    async def complete(self, *args, **kwargs):
        class _Result:
            text = '{"key_findings": ["fresh finding"], "risks": []}'

        return _Result()


def test_repeat_dd_reads_prior_seen_url_and_retains_fact():
    """Drive investigate with the exact stale-global-read shape seen live."""
    url = "https://registry.example/vigilo"

    async def _search(*args, **kwargs):
        return [{"title": "Vigilo filing", "link": url}]

    async def _analyse(*args, **kwargs):
        return {"facts": [{
            "topic": "Ownership",
            "content": "A current filing identifies the owner.",
            "confidence": "PROBABLE",
        }]}

    async def _run():
        with patch.object(DR, "_web_search", new=_search), \
             patch.object(DR, "_fetch_article_text", new=AsyncMock(return_value="filing " * 40)), \
             patch.object(DR, "_analyse_article", new=_analyse), \
             patch.object(DR, "_load_hypotheses", new=AsyncMock(return_value=[])), \
             patch.object(DR, "_save_hypotheses", new=AsyncMock(return_value=None)), \
             patch.object(DR, "_get_read_urls", new=AsyncMock(return_value={url})), \
             patch.object(DR, "_mark_read", new=AsyncMock(return_value=None)), \
             patch.object(DR, "_process_analysis", new=AsyncMock(return_value=(1, 0))), \
             patch.object(DR, "search_knowledge", return_value=""):
            return await DR.investigate(
                _LLM(), "Vigilo Solutions Limited due diligence",
                depth="quick", investigate_people=0,
            )

    result = asyncio.run(_run())

    assert result["articles_read"] >= 1
    assert result["facts_learned"] >= 1
    assert all(fact["source_url"] == url for fact in result["facts"])


def test_non_dd_research_still_respects_prior_read_cache():
    """The freshness exception is scoped; general discovery remains deduplicated."""
    url = "https://news.example/already-read"

    async def _search(*args, **kwargs):
        return [{"title": "Prior article", "link": url}]

    async def _run():
        with patch.object(DR, "_web_search", new=_search), \
             patch.object(DR, "_load_hypotheses", new=AsyncMock(return_value=[])), \
             patch.object(DR, "_save_hypotheses", new=AsyncMock(return_value=None)), \
             patch.object(DR, "_get_read_urls", new=AsyncMock(return_value={url})), \
             patch.object(DR, "search_knowledge", return_value=""):
            return await DR.investigate(
                _LLM(), "regional security developments",
                depth="quick", investigate_people=0,
            )

    result = asyncio.run(_run())
    assert result["articles_read"] == 0
    assert result["facts_learned"] == 0

"""Capability test for R-F2190 — vault-curated sources feed CONTENT into the brain.

Closes Pipeline 2 of the vault business review: a website added via vault.html
"Add Site" must become searchable by chat + intelligence (RAG/knowledge), not just
the dashboard ledger. This drives the REAL broken path: news_monitor._feed_to_brain.

Asserts:
  1. a vault-curated article (source "vault:…" / category "vault_curated") triggers
     brain_hook.absorb(module="news_monitor", entity_name=<site>, extra_topics incl
     "vault_source") — i.e. content reaches RAG/knowledge.
  2. a normal news article does NOT absorb (scoping preserved — no firehose into RAG).

Run: python -m pytest aria_service/tests/test_vault_brain_ingest_rf2190.py -q
"""
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from aria_service.intel import news_monitor as nm


def _feed(article):
    async def _run():
        with patch.object(nm, "wire_success", MagicMock()), \
             patch("aria_service.intel.intel_ledger.add_signal", AsyncMock()), \
             patch("aria_service.intel.brain_hook.absorb", AsyncMock()) as mock_absorb:
            await nm._feed_to_brain(article)
            return mock_absorb
    return asyncio.run(_run())


def test_vault_source_content_absorbed_into_brain():
    m = _feed({
        "title": "Acme wins $40M tender",
        "summary": "Acme Corp secured a defense contract.",
        "source": "vault:Acme Intel",
        "url": "https://acme.example.com/news/1",
        "category": "vault_curated",
    })
    assert m.await_count == 1, "vault source must be absorbed into the brain (Pipeline 2)"
    kw = m.await_args.kwargs
    assert kw["module"] == "news_monitor"
    assert kw["entity_name"] == "Acme Intel", "entity keyed off the vault site name"
    assert "vault_source" in (kw.get("extra_topics") or [])
    assert kw["source_id"].startswith("vault_source:")


def test_vault_category_without_prefix_also_absorbed():
    m = _feed({
        "title": "Sector update", "summary": "Body.",
        "source": "Custom Feed", "url": "https://c.example.com/2",
        "category": "vault_curated",
    })
    assert m.await_count == 1


def test_regular_news_not_absorbed():
    m = _feed({
        "title": "Global markets dip", "summary": "Markets fell.",
        "source": "Reuters", "url": "https://reuters.example.com/3",
        "category": "markets",
    })
    assert m.await_count == 0, "non-vault news must NOT flood RAG/knowledge"


if __name__ == "__main__":
    test_vault_source_content_absorbed_into_brain()
    test_vault_category_without_prefix_also_absorbed()
    test_regular_news_not_absorbed()
    print("ALL PASS")

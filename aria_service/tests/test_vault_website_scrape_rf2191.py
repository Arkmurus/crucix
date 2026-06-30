"""Capability test for R-F2191 — manually-added WEBSITES reliably bring value.

Closes Pipeline 1 robustness of the vault review: a vault site of type "website" that
is NOT an RSS/Atom feed used to be silently dropped (`unknown_format`). It is now scraped
(researcher.extract_url_text) and ingested through the same store→ledger→brain path, so a
manually-inserted website flows to the dashboard (data output) AND the brain (intel).

Drives the REAL new path: news_monitor._scrape_vault_website.

Run: python -m pytest aria_service/tests/test_vault_website_scrape_rf2191.py -q
"""
import asyncio
from unittest.mock import patch, AsyncMock

from aria_service.intel import news_monitor as nm


def _scrape(seen=False, ok=True, text="Extracted page content about Acme Corp defense contracts."):
    async def _run():
        with patch("aria_service.intel.researcher.extract_url_text",
                   AsyncMock(return_value={"extraction_ok": ok, "text": text, "title": "Acme Page"})), \
             patch.object(nm, "_is_seen", AsyncMock(return_value=seen)), \
             patch.object(nm, "_mark_seen", AsyncMock()), \
             patch.object(nm, "_store_article", AsyncMock()) as store, \
             patch.object(nm, "_feed_to_brain", AsyncMock()) as brain:
            res = await nm._scrape_vault_website(
                "vault:Acme", "https://acme.example.com", "vault_curated", "en", "tier_2", ["custom"])
            return res, store, brain
    return asyncio.run(_run())


def test_vault_website_scraped_and_ingested():
    res, store, brain = _scrape()
    assert res == {"fetched": 1, "new": 1}
    assert store.await_count == 1, "scraped website must be stored (data output)"
    art = store.await_args.args[0]
    assert art["source"] == "vault:Acme"
    assert art["category"] == "vault_curated"      # → triggers brain absorb (Pipeline 2)
    assert art["title"] == "Acme Page"
    assert "Acme" in art["full_text"]
    assert brain.await_count == 1, "must feed brain (ledger + RAG absorb)"


def test_unchanged_website_not_reingested():
    res, store, brain = _scrape(seen=True)
    assert res == {"fetched": 1, "new": 0}
    assert store.await_count == 0
    assert brain.await_count == 0


def test_failed_extraction_no_ingest():
    res, store, brain = _scrape(ok=False, text="")
    assert res == {"fetched": 0, "new": 0}
    assert store.await_count == 0
    assert brain.await_count == 0


if __name__ == "__main__":
    test_vault_website_scraped_and_ingested()
    test_unchanged_website_not_reingested()
    test_failed_extraction_no_ingest()
    print("ALL PASS")

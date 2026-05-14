"""runner — background crawl loop for ARIA's search engine.

R-F501 (2026-05-14). Lifespan-wired in R-F504 once the indexer +
internal_search are in place; for now this module exposes:

  async def crawl_seed_homepages(limit=None) -> dict
      Walk SEEDS, fetch each domain's home page, hand to indexer.
      One-shot operation; safe to call from a /admin route or CLI.

  async def crawl_loop(interval_sec=3600) -> None
      Long-running loop that re-crawls each enabled domain at most once
      per `interval_sec`. Sleep between iterations.

Phase 1 keeps this simple: one URL per seed (the home page), depth 0.
Phase 2 will add the frontier with link discovery.
"""
from __future__ import annotations

import asyncio
import logging
import time

from aria_service.search_index import db
from . import fetcher

logger = logging.getLogger("aria.crawler.runner")


async def crawl_seed_homepages(limit: int | None = None) -> dict:
    """Fetch the home page of every enabled seed domain and index it.

    Returns:
      {fetched: N, indexed: M, skipped: K, errors: E, duration_sec: T}

    Each domain is fetched serially in this MVP to keep the politeness
    semantics simple — the per-domain rate limit is already enforced
    inside politeness.acquire(). Phase 2 will parallelise across
    distinct domains with a domain-aware semaphore.
    """
    t0 = time.time()
    domains = await db.list_domains(enabled_only=True)
    if limit is not None:
        domains = domains[:limit]

    # Late import — indexer hasn't shipped yet at R-F501; degrade to
    # storing raw fetch results in `documents`. Once R-F502 ships,
    # `indexer.index_fetch_result` does proper extraction + dedup.
    try:
        from aria_service.search_index import indexer  # noqa: F401
        has_indexer = True
    except Exception:
        has_indexer = False

    fetched = indexed = skipped = errors = 0

    for d in domains:
        url = f"https://{d['domain']}/"
        try:
            result = await fetcher.fetch_for_index(url)
        except Exception as e:
            errors += 1
            logger.warning("runner: fetch raised for %s: %s",
                           d["domain"], e)
            continue

        if result is None:
            skipped += 1
            continue
        fetched += 1

        if has_indexer:
            try:
                from aria_service.search_index import indexer
                doc_id = await indexer.index_fetch_result(result)
                if doc_id:
                    indexed += 1
            except Exception as e:
                errors += 1
                logger.warning("runner: index raised for %s: %s",
                               d["domain"], e)
        else:
            # MVP fallback: upsert directly. Stops being the path in R-F502.
            try:
                await db.upsert_document(
                    url=result["url"], domain=result["domain"],
                    title=result.get("title"),
                    headings=result.get("headings"),
                    body=result.get("body"),
                    language=result.get("language"),
                    source_tier=result.get("source_tier"),
                    http_status=result.get("http_status"),
                    fetched_at=result.get("fetched_at"),
                    canonical_url=result.get("canonical_url"),
                )
                indexed += 1
            except Exception as e:
                errors += 1
                logger.warning("runner: direct upsert raised for %s: %s",
                               d["domain"], e)

    return {
        "fetched": fetched, "indexed": indexed, "skipped": skipped,
        "errors": errors, "duration_sec": round(time.time() - t0, 2),
        "domains_total": len(domains),
    }


async def crawl_loop(interval_sec: int = 3600,
                     stop_event: asyncio.Event | None = None) -> None:
    """Long-running loop. Crawls the seed home pages every `interval_sec`.
    Designed to be wired into lifespan as a background task. Stops when
    `stop_event` is set."""
    logger.info("crawler.runner: starting loop with interval=%ds", interval_sec)
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            summary = await crawl_seed_homepages()
            logger.info("crawler.runner: cycle done — %s", summary)
        except Exception as e:
            logger.warning("crawler.runner: cycle failed: %s", e)
        try:
            await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            break

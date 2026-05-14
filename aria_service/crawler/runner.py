"""runner — background crawl loop for ARIA's search engine.

R-F501 (2026-05-14). One-shot + long-running crawl orchestrators.

R-F508 (2026-05-14) — three operational fixes after the first live
deploy (07:42 BST) showed:
  - Tier-1 sites (afdb.org, consilium.europa.eu, bis.doc.gov) blocked
    the chat-rotation Chrome UA with 403s
  - Each 403 chained into a Wayback fallback that tripped archive.is
    rate limit and OPENed the breaker after 3 fetches
  - Crawler started DURING lifespan init, contesting event-loop time
    with RAG init + OCR prewarm; healthcheck briefly failed at 07:43:34
    (recovered 17s later)

Fix:
  - crawl_seed_homepages now uses fetcher.fetch_for_crawl (R-F508
    polite single-attempt fetcher, identified UA, no Wayback chain).
  - crawl_loop accepts startup_delay_sec (default 60s) so the first
    cycle fires AFTER the lifespan-init burst has settled.
  - Cycle summary breaks results down by status_class so coverage is
    visible (`ok=12 4xx=8 5xx=2 timeout=3 robots=1 thin=4`).
"""
from __future__ import annotations

import asyncio
import logging
import time

from aria_service.search_index import db
from . import fetcher

logger = logging.getLogger("aria.crawler.runner")


_DEFAULT_STARTUP_DELAY_SEC = 60


async def crawl_seed_homepages(limit: int | None = None,
                                 use_polite_crawler: bool = True) -> dict:
    """Fetch the home page of every enabled seed domain and index it.

    Args:
      limit: optional cap on number of seeds visited this cycle.
      use_polite_crawler: when True (default since R-F508), uses
        fetcher.fetch_for_crawl — own httpx, identified UA, no Wayback
        fallback. When False, uses the legacy fetcher.fetch_for_index
        (preserved for explicit test paths and the on-demand chat-fill).

    Returns:
      {fetched, indexed, skipped, errors, duration_sec, domains_total,
       by_status: {ok: N, 4xx: N, 5xx: N, timeout: N, robots_blocked: N,
                   thin: N, error: N, none: N}}
    """
    t0 = time.time()
    domains = await db.list_domains(enabled_only=True)
    if limit is not None:
        domains = domains[:limit]

    try:
        from aria_service.search_index import indexer  # noqa: F401
        has_indexer = True
    except Exception:
        has_indexer = False

    fetched = indexed = skipped = errors = 0
    by_status: dict[str, int] = {}

    for d in domains:
        url = f"https://{d['domain']}/"
        try:
            if use_polite_crawler:
                result = await fetcher.fetch_for_crawl(url)
            else:
                result = await fetcher.fetch_for_index(url)
        except Exception as e:
            errors += 1
            by_status["error"] = by_status.get("error", 0) + 1
            logger.warning("runner: fetch raised for %s: %s",
                           d["domain"], e)
            continue

        if result is None:
            skipped += 1
            by_status["none"] = by_status.get("none", 0) + 1
            continue

        status_class = result.get("status_class") or (
            "ok" if result.get("extraction_ok") else "none"
        )
        by_status[status_class] = by_status.get(status_class, 0) + 1

        if not result.get("extraction_ok"):
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
        "by_status": by_status,
    }


async def crawl_loop(interval_sec: int = 3600,
                     stop_event: asyncio.Event | None = None,
                     startup_delay_sec: int | None = None) -> None:
    """Long-running crawl loop with R-F508 startup-delay quieting.

    Args:
      interval_sec: pause between cycles. Default 1h; R-F507 lifespan
        passes 6h (21600s).
      stop_event: when set, the loop exits at the next checkpoint.
      startup_delay_sec: seconds to wait BEFORE the first cycle. Lets
        lifespan-init bursts (RAG prewarm, OCR prewarm, reasoning lib
        purge) settle before the crawler starts contesting the event
        loop. Default 60s; pass 0 to disable; tests can pass 0.
    """
    if startup_delay_sec is None:
        startup_delay_sec = _DEFAULT_STARTUP_DELAY_SEC
    logger.info(
        "crawler.runner: starting loop with interval=%ds startup_delay=%ds",
        interval_sec, startup_delay_sec,
    )
    if startup_delay_sec > 0:
        try:
            await asyncio.sleep(startup_delay_sec)
        except asyncio.CancelledError:
            return
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

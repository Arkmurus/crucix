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


#: R-F3821 — how many UNVETTED tier-4 discovery domains one cycle may fetch.
#: Operator-tunable without a deploy; the right number is operational, not structural.
_DEFAULT_DISCOVERY_PER_CYCLE = 500


def _discovery_budget() -> int:
    """Per-cycle ration for tier-4 discovery. Never returns <= 0 on a bad value.

    A typo in an env var must not stop ARIA crawling — that would be a config error
    silently becoming an outage, which is the failure shape §1 keeps recording.
    """
    import os
    raw = (os.getenv("ARIA_CRAWL_DISCOVERY_PER_CYCLE") or "").strip()
    if not raw:
        return _DEFAULT_DISCOVERY_PER_CYCLE
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "[R-F3821] ARIA_CRAWL_DISCOVERY_PER_CYCLE=%r is not an integer — "
            "using the %d default rather than stopping the sweep",
            raw, _DEFAULT_DISCOVERY_PER_CYCLE)
        return _DEFAULT_DISCOVERY_PER_CYCLE
    return max(0, n)


def _prioritise_sweep(domains: list[dict], discovery_budget: int) -> list[dict]:
    """Curated seeds first, then a rationed, ROTATING slice of tier-4 discovery.

    R-F3821. Measured live 2026-08-09: of 22,115 registry rows, **21,953 were tier-4
    `discovered` (99.3%)**, all enabled, and `crawl_loop` fetched every one of them
    every 6 hours. The 147 curated tier-1..3 seeds — OFAC, BIS, the EU Commission,
    defence media — were interleaved with twenty thousand speculative rows admitted
    before R-F3820's ingress gate existed. The fly logs showed the result as a plain
    alphabetical march: `investors.xpinc → investors.yeti → invoicefly.com`.

    Two properties, and the second is as important as the first:

    * CURATED IS NEVER RATIONED. The ration applies to discovery only, so a budget of
      zero still sweeps every seed. Those are the product's backbone.
    * DISCOVERY ROTATES, oldest-crawled first. `list_domains` orders by
      `tier ASC, domain ASC`, which is STABLE — so a naive `[:budget]` would re-fetch
      the same alphabetical prefix every cycle and the tail would never be crawled at
      all. Sorting by `last_crawled_at` (never-crawled first) makes the ration fair
      instead of merely smaller.

    WHAT THIS DOES NOT DO. It does not disable, demote or delete anything (§7). The
    tempting alternative — judge each crawled page and switch off the off-mission
    ones — was tested against ARIA's own live index and **13 of 14 curated tier-1
    sources came back off_topic**, including `ofac.treasury.gov` ("Home | Office of
    Foreign Assets Control") and `bis.doc.gov`. It would have disabled OFAC. A
    homepage title is navigational, not topical; that is why R-F3820 judges a SERP
    title+snippet and never a bare domain.
    """
    curated = [d for d in domains if (d.get("tier") or 4) <= 3]
    discovery = [d for d in domains if (d.get("tier") or 4) >= 4]
    # None (never crawled) sorts first — it is owed a turn most.
    discovery.sort(key=lambda d: (d.get("last_crawled_at") is not None,
                                  d.get("last_crawled_at") or 0))
    return curated + discovery[:max(0, discovery_budget)]


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
    # R-F687 (2026-05-18) — drop auto-registered hallucination garbage
    # (tier=4 + sector="discovered" + common-noun label) from the sweep.
    # R-F676 closed the upstream generator; this filter stops the
    # existing polluted registry rows from being re-crawled every cycle
    # and tripping the web_atlas brain_hook breaker. Rows stay in the
    # registry as historical artifacts (no destructive delete).
    from . import on_demand as _on_demand
    before_count = len(domains)
    domains = [
        d for d in domains
        if not _on_demand.is_auto_registered_garbage(d)
    ]
    skipped_garbage = before_count - len(domains)
    if skipped_garbage:
        logger.info(
            "R-F687: crawl sweep skipped %d auto-registered garbage domain(s) "
            "from %d total", skipped_garbage, before_count,
        )
    # R-F3821 — curated seeds first, discovery rationed and rotated. Before this the
    # sweep fetched all ~20,700 enabled domains every cycle, 99.3% of them unvetted
    # tier-4 rows, with the 147 curated seeds interleaved among them.
    _budget = _discovery_budget()
    _curated_n = sum(1 for d in domains if (d.get("tier") or 4) <= 3)
    _discovery_n = len(domains) - _curated_n
    domains = _prioritise_sweep(domains, _budget)
    _deferred = (_curated_n + _discovery_n) - len(domains)
    logger.info(
        "[R-F3821] sweep plan: %d curated + %d discovery (of %d, budget %d); "
        "%d discovery deferred to a later cycle (oldest-crawled first)",
        _curated_n, len(domains) - _curated_n, _discovery_n, _budget, _deferred,
    )
    # §21a — the sweep's SHAPE is a real signal: if curated ever drops to 0 the
    # product is crawling only speculation, and nothing else would say so.
    try:
        from aria_service.intel.engine_wiring import wire_success
        wire_success(
            module="crawler.runner",
            summary=(f"sweep plan: {_curated_n} curated + "
                     f"{len(domains) - _curated_n} discovery, {_deferred} deferred"),
            source_id="crawler.runner:crawl_seed_homepages",
        )
    except Exception:      # pragma: no cover - observability never blocks the crawl
        pass

    if limit is not None:
        domains = domains[:limit]

    try:
        from aria_service.search_index import indexer  # noqa: F401
        has_indexer = True
    except Exception:
        has_indexer = False

    fetched = indexed = skipped = errors = 0
    by_status: dict[str, int] = {}
    # R-F1257: batch absorbs — collect all indexed pages and fire ONE
    # absorb per batch instead of one per page (which saturated the
    # brain_hook background tier with 50+ concurrent tasks).
    _absorb_batch: list[dict[str, str]] = []

    # R-F3008 — FOREGROUND PRIORITY. This sweep is a 6-hourly BACKGROUND job; an
    # interactive user DD must not be starved of the event loop by it. While any DD
    # is executing in this process, pause between domains so the DD gets scheduled.
    # We SLOW the crawl, never stop it (it completes its sweep later). Lazy import so
    # a wiring problem can never break the crawler.
    try:
        from aria_service.intel.dd_orchestrator import dd_inflight_count as _dd_inflight
    except Exception:  # pragma: no cover
        def _dd_inflight() -> int:
            return 0

    for d in domains:
        if _dd_inflight() > 0:
            await asyncio.sleep(2.0)  # R-F3008 — let the interactive DD run
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

        _indexed_this_iter = False
        if has_indexer:
            try:
                from aria_service.search_index import indexer
                doc_id = await indexer.index_fetch_result(result)
                if doc_id:
                    indexed += 1
                    _indexed_this_iter = True
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
                _indexed_this_iter = True
            except Exception as e:
                errors += 1
                logger.warning("runner: direct upsert raised for %s: %s",
                               d["domain"], e)

        # R-F667 (2026-05-17): close the 6h autonomous-crawl knowledge
        # leak per CLAUDE.md §15 (pay-once-remember-forever). Audit
        # 2026-05-17 found the crawl loop wrote 100+ pages to
        # search_index.db (FTS) but emitted ZERO signals to brain_hook
        # / mastery / neural_memory — so ARIA could search the pages
        # via /search but didn't *know* them. Every equivalent customer
        # question still paid an LLM call.
        #
        # R-F1257 (2026-06-01): BATCH absorbs instead of one per page.
        # Pre-R-F1257 every indexed page fired a separate brain_hook.absorb
        # as a background task. With 16 concurrent background tiers, 50+
        # pages per cycle created 50+ background tasks all competing for
        # the semaphore — saturating the brain_hook pipeline and tripping
        # the circuit breaker. Now we collect all indexed pages and fire
        # ONE absorb per batch with a consolidated summary.
        if _indexed_this_iter:
            _absorb_batch.append({
                "domain": (result.get("domain") or d["domain"]).strip(),
                "title": (result.get("title") or "").strip(),
                "url": result.get("canonical_url") or result.get("url") or "",
                "body": (result.get("body") or ""),
            })

    # R-F1257: fire ONE batch absorb instead of N per-page absorbs.
    # This prevents saturating the brain_hook background tier when the
    # crawl indexes 50+ pages in a single cycle.
    if _absorb_batch:
        try:
            from aria_service.intel import brain_hook as _bh
            _domains = list({p["domain"] for p in _absorb_batch})
            _titles = [p["title"] for p in _absorb_batch if p["title"]]
            _summary = (
                f"Web atlas crawl indexed {len(_absorb_batch)} pages "
                f"across {len(_domains)} domains: "
                f"{', '.join(_titles[:5])}"
                f"{'...' if len(_titles) > 5 else ''}"
            )
            # Concatenate first 2000 chars of each page body for neural
            _detail = "\n\n---\n\n".join(
                f"[{p['domain']}] {p['title']}: {p['body'][:2000]}"
                for p in _absorb_batch[:10]  # cap at 10 pages for detail
            )
            _t = asyncio.create_task(_bh.absorb(
                module="web_atlas",
                summary=_summary,
                detail=_detail,
                entity_name=",".join(_domains[:5]),
                success=True,
                source_id=f"crawl_batch:{len(_absorb_batch)}_pages",
                confidence="ASSESSED",
            ))
            _t.add_done_callback(
                lambda t: t.result() if not t.cancelled() and t.exception() is None else None
            )
        except Exception as e:
            logger.debug(
                "R-F1257: batch brain_hook.absorb failed (non-fatal): %s", e,
            )

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

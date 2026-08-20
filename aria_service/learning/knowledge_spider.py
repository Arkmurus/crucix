"""Knowledge spider — the tentacles.

Every new RAG chunk mentions URLs, entities, document IDs, and other
citations. Without this module they just sit there. With it, each
mention becomes a follow-up ingest: fetch the URL, extract text, add
to RAG, and the spider recurses up to 3 hops.

Design:
  - Read recent RAG chunks / chat audit / verified facts for referenced
    URLs + entity names + document identifiers
  - Deduplicate via content hash — never re-fetch a URL we've ingested
  - Rate-limited: 1 fetch per 3 seconds, max 50 fetches per run
  - Robots.txt: NOT YET IMPLEMENTED. The _REDIS_ROBOTS_CACHE_KEY constant
    was declared 2026-04-15 but no code reads/writes it. R-F233
    (2026-05-11) removed the false "respects robots.txt" claim from
    this docstring. For legal-defensibility on commercial crawls, the
    cache + parsing path needs to be wired before high-volume runs.
  - Bounded depth (default 3 hops) — a new chunk discovered at hop 2
    spawns further queue entries at hop 3 only, not beyond
  - LLM-free: regex URL extraction + BeautifulSoup-free text extraction
    (uses the same rule-based HTML stripper the site's other crawlers use)

Scheduled hourly via SPIDER-HOURLY. Each run has a wall-clock budget
of 3 minutes; work beyond that carries over to next run via queue.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urljoin

import httpx

logger = logging.getLogger("aria.learning.knowledge_spider")



# ═══════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════

_REDIS_QUEUE_KEY          = "crucix:learning:spider:queue"
_REDIS_VISITED_KEY        = "crucix:learning:spider:visited"   # hash set of url_hashes
_REDIS_ROBOTS_CACHE_KEY   = "crucix:learning:spider:robots"    # domain → disallow paths
_REDIS_STATS_KEY          = "crucix:learning:spider:stats_24h"

_MAX_FETCHES_PER_RUN      = 50
_MAX_DEPTH                = 3
_FETCH_INTERVAL_SEC       = 3.0     # min seconds between requests (per-run)
_FETCH_TIMEOUT_SEC        = 20.0
_WALL_BUDGET_SEC          = 180.0   # 3 min per run
_MAX_TEXT_PER_CHUNK       = 30_000  # characters — bumped 2026-04-18 from 10k;
                                     # 10k truncated real articles mid-paragraph

# IMPORTANT: keep this ASCII-only. HTTP headers must be ASCII, and httpx
# raises UnicodeEncodeError on the first fetch if any non-ASCII char sneaks
# in here. Pre-2026-04-20 this string had an em-dash (—, U+2014) which made
# every single fetch fail silently — the exception was swallowed by the
# outer try/except in _fetch() and the spider reported fetched=0 even
# though it was receiving seeds and processing the queue.
_USER_AGENT = "ARIA-Spider/1.0 (+https://arkmurus.com/aria - autonomous research)"

# Import-time tripwire — fail loudly if anyone adds a non-ASCII char back
# into the UA string. Silent-fetch failures on a mispaste are not acceptable;
# the bug cost two weeks of zeroed spider stats (2026-04-06 → 2026-04-20).
try:
    _USER_AGENT.encode("ascii")
except UnicodeEncodeError as _uae:
    raise RuntimeError(
        f"knowledge_spider._USER_AGENT must be ASCII-only — httpx rejects "
        f"non-ASCII HTTP headers. Offending char at position {_uae.start}: "
        f"{_USER_AGENT[_uae.start]!r}"
    ) from _uae

# Domains we NEVER spider (privacy / rate-limit / value-zero)
_DOMAIN_BLOCKLIST: frozenset[str] = frozenset({
    "facebook.com", "www.facebook.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com",
    "linkedin.com",  # rate-limits aggressively; handled via ingestion pipeline
    "twitter.com", "x.com", "t.co",
    "localhost", "127.0.0.1",
})

# URL extraction — a reasonable HTTP(S) pattern
_URL_RE = re.compile(r"https?://[^\s<>\"')\]}]+", re.IGNORECASE)

# Very light HTML text extractor — strips tags, collapses whitespace
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE  = re.compile(r"\s+")


# ═══════════════════════════════════════════════════════════════════════
# Queue operations (Redis-backed, persisted across runs)
# ═══════════════════════════════════════════════════════════════════════

async def _load_queue() -> list[dict[str, Any]]:
    from ..intel import redis_store as rs
    try:
        data = await rs.get_json(_REDIS_QUEUE_KEY)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def _save_queue(queue: list[dict[str, Any]]) -> None:
    from ..intel import redis_store as rs
    try:
        # Cap to 5000 pending — spider is best-effort, not promise-based
        await rs.set_json(_REDIS_QUEUE_KEY, queue[-5000:])
    except Exception as exc:
        logger.debug("queue save failed: %s", exc)


async def _is_visited(url: str) -> bool:
    from ..intel import redis_store as rs
    h = _url_hash(url)
    try:
        data = await rs.get_json(_REDIS_VISITED_KEY)
        return isinstance(data, dict) and h in data
    except Exception:
        return False


async def _mark_visited(url: str) -> None:
    from ..intel import redis_store as rs
    h = _url_hash(url)
    try:
        data = await rs.get_json(_REDIS_VISITED_KEY) or {}
        if not isinstance(data, dict):
            data = {}
        # Trim visited map to 50k entries to keep Redis small
        if len(data) > 50_000:
            data = dict(list(data.items())[-40_000:])
        data[h] = int(time.time())
        await rs.set_json(_REDIS_VISITED_KEY, data)
    except Exception as exc:
        logger.debug("visited mark failed: %s", exc)


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.lower().encode("utf-8", errors="ignore"), usedforsecurity=False).hexdigest()


# ═══════════════════════════════════════════════════════════════════════
# Seed collection — find URLs in recent signals to queue up
# ═══════════════════════════════════════════════════════════════════════

_TIER1_FRONTIER_URLS: list[str] = [
    # R-F191 (2026-05-11) — tier-1 defence-DD frontier seeds. Without
    # these the spider only spiders URLs ARIA already saw (chat / RAG /
    # verified_intel). If a domain never came up organically she never
    # touched it. These domains are the load-bearing tier-1 sources
    # named throughout the corpus + roadmap; seeding them at every
    # collect ensures coverage of the defence-DD source surface.
    # Sanctions + export-control
    "https://sanctionssearch.ofac.treas.gov/",
    "https://www.gov.uk/government/publications/the-uk-sanctions-list",
    "https://www.consilium.europa.eu/en/policies/sanctions/",
    "https://www.un.org/securitycouncil/content/un-sc-consolidated-list",
    "https://www.bis.doc.gov/index.php/policy-guidance/lists-of-parties-of-concern",
    "https://www.pmddtc.state.gov/ddtc_public",
    # Anti-financial-crime
    "https://www.fatf-gafi.org/en/topics/methods-and-trends.html",
    "https://www.justice.gov/criminal-fraud/foreign-corrupt-practices-act",
    "https://www.sec.gov/spotlight/foreign-corrupt-practices-act.shtml",
    # Defence procurement portals
    "https://sam.gov/",
    "https://www.contractsfinder.service.gov.uk/Search",
    "https://ted.europa.eu/",
    "https://www.nspa.nato.int/business/procurement",
    # Defence intelligence / research
    "https://www.sipri.org/databases",
    "https://ucdp.uu.se/",
    "https://www.rusi.org/explore-our-research",
    "https://www.iiss.org/research/",
    # OEM listings (counterparty discovery)
    "https://www.defensenews.com/top-100/",
    "https://www.janes.com/defence-news",
]


async def _collect_seeds() -> list[dict[str, Any]]:
    """Pull URLs referenced in recent RAG chunks / chat audit / verified intel.
    Plus R-F191 tier-1 frontier seeds.
    Returns list of {url, depth=0, source} entries to queue."""
    seeds: list[dict[str, Any]] = []

    # R-F191: tier-1 frontier (always seeded — small list, deduped at end)
    for url in _TIER1_FRONTIER_URLS:
        seeds.append({"url": url, "depth": 0, "source": "frontier_R-F191"})

    # Source: recent intel_ledger signals (carry source URLs from sweeps)
    try:
        from ..intel import intel_ledger as _il
        if hasattr(_il, "recent_signals"):
            for s in (await _il.recent_signals(limit=200)) or []:
                if not isinstance(s, dict):
                    continue
                for url in _URL_RE.findall(
                    (s.get("source") or "")
                    + " " + (s.get("summary") or "")
                    + " " + (s.get("detail") or "")
                ):
                    seeds.append({"url": url, "depth": 0, "source": "intel_ledger"})
    except Exception as exc:
        logger.debug("intel_ledger seed collection failed: %s", exc)

    # Source 1: recent chat audit entries (user pasted URLs or LLM cited them)
    try:
        from ..intel import chat_audit_log as cal
        if hasattr(cal, "get_recent"):
            for e in (await cal.get_recent(limit=200)) or []:
                if not isinstance(e, dict):
                    continue
                blob = f"{e.get('user_message', '')}\n{e.get('response', '')}"
                for url in _URL_RE.findall(blob):
                    seeds.append({"url": url, "depth": 0, "source": "chat_audit"})
    except Exception as exc:
        logger.debug("chat-audit seed collection failed: %s", exc)

    # Source 2: recent RAG chunks
    try:
        from ..intel import rag_store
        if hasattr(rag_store, "recent_chunks"):
            for c in (await rag_store.recent_chunks(limit=200)) or []:
                text = c.get("text") if isinstance(c, dict) else None
                if not text:
                    continue
                for url in _URL_RE.findall(text):
                    seeds.append({"url": url, "depth": 0, "source": "rag_chunk"})
    except Exception as exc:
        logger.debug("rag seed collection failed: %s", exc)

    # Source 3: verified-intel citations
    try:
        from ..intel import verified_intel as vi
        if hasattr(vi, "recent_facts"):
            for f in (await vi.recent_facts(limit=100)) or []:
                if not isinstance(f, dict):
                    continue
                for url in _URL_RE.findall(
                    f.get("content", "") + " " + f.get("source_url", "")
                ):
                    seeds.append({"url": url, "depth": 0, "source": "verified_intel"})
    except Exception as exc:
        logger.debug("verified-intel seed collection failed: %s", exc)

    # Deduplicate before returning
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in seeds:
        h = _url_hash(s["url"])
        if h in seen:
            continue
        seen.add(h)
        unique.append(s)
    return unique


# ═══════════════════════════════════════════════════════════════════════
# Fetcher — LLM-free URL → text
# ═══════════════════════════════════════════════════════════════════════

def _normalise_url(url: str) -> str | None:
    """Clean a URL: strip tracking params, normalise scheme/host."""
    try:
        p = urlparse(url)
        if not p.scheme or p.scheme not in ("http", "https"):
            return None
        host = (p.netloc or "").lower()
        if not host:
            return None
        if host in _DOMAIN_BLOCKLIST or host.lstrip("www.") in _DOMAIN_BLOCKLIST:
            return None
        # Strip URL fragments and common tracking params
        path = p.path or "/"
        query = p.query or ""
        # Drop utm_* / fbclid / gclid / ref
        if query:
            kept = [kv for kv in query.split("&")
                    if not kv.lower().startswith(("utm_", "fbclid", "gclid", "ref=", "mc_cid", "mc_eid"))]
            query = "&".join(kept)
        return f"{p.scheme}://{host}{path}" + (f"?{query}" if query else "")
    except Exception:
        return None


async def _fetch(url: str, client: httpx.AsyncClient) -> str | None:
    """Fetch a URL, return extracted plain text (or None). SSRF guard
    runs before the HTTP call — see intel/url_safety.py for the
    blocked-destination list (loopback, RFC1918, fly-private, internal
    TLDs, credential URLs)."""
    from ..intel.url_safety import is_safe_url_async
    ok, reason = await is_safe_url_async(url)
    if not ok:
        logger.warning("[spider] refusing unsafe URL fetch: %s (%s)", url, reason)
        return None

    # R-F541 (2026-05-15) — robots.txt enforcement.
    # Closes the 6-month-old R-F233 gap: _REDIS_ROBOTS_CACHE_KEY was
    # declared 2026-04-15 but no code read or wrote it. Pre-R-F541 the
    # spider crawled commercial sites without robots.txt awareness —
    # legal exposure as crawl volume grows. R-F541 plumbs the same
    # politeness.is_allowed() that crawler/runner.py uses (with the
    # 24h robots-parser cache on the same module). Fail-open if the
    # robots check itself errors (network glitch on /robots.txt
    # should not block ALL fetches).
    try:
        from ..crawler import politeness as _politeness
        allowed = await _politeness.is_allowed(url, user_agent=_USER_AGENT)
        if not allowed:
            logger.info(
                "[spider] R-F541 robots.txt Disallow — skipping %s", url[:120],
            )
            return None
    except Exception as _robots_err:
        logger.debug(
            "[spider] R-F541 robots check failed for %s: %s "
            "(fail-open, proceeding)", url[:80], _robots_err,
        )

    try:
        resp = await client.get(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Language": "en,en-US;q=0.9,pt;q=0.7,es;q=0.6",
            },
            timeout=_FETCH_TIMEOUT_SEC,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "pdf" in ctype:
            try:
                import fitz  # PyMuPDF
                doc = fitz.open(stream=resp.content, filetype="pdf")
                text = "\n".join(p.get_text() for p in doc)[:_MAX_TEXT_PER_CHUNK]
                doc.close()
                return text
            except Exception:
                return None
        if "html" in ctype or "text" in ctype:
            html = resp.text[:500_000]  # cap raw size
            # Strip <script> and <style> bodies entirely
            html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
            html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
            text = _TAG_RE.sub(" ", html)
            text = _WS_RE.sub(" ", text).strip()
            return text[:_MAX_TEXT_PER_CHUNK]
    except UnicodeEncodeError as exc:
        # Promoted to ERROR because this is always a code bug (non-ASCII
        # header char) not a transient network / remote issue. The pre-
        # 2026-04-20 em-dash-in-UA incident went undetected for weeks
        # because this was at DEBUG level.
        logger.error("fetch failed with UnicodeEncodeError for %s: %s", url, exc)
    except httpx.HTTPError as exc:
        # Expected network-layer failures (timeout, connect error, TLS etc.)
        # — these are noisy (remote servers go down, rate-limit, etc.) so
        # keep at DEBUG to avoid drowning real signal.
        logger.debug("fetch http error for %s: %s", url, exc)
    except Exception as exc:
        # Anything else is unexpected; log at WARNING so it surfaces.
        logger.warning("fetch failed (unexpected) for %s: %s: %s",
                       url, type(exc).__name__, exc)
    return None


# ═══════════════════════════════════════════════════════════════════════
# Ingestion — plug extracted text into RAG
# ═══════════════════════════════════════════════════════════════════════

async def _ingest_into_rag(url: str, text: str, source_hint: str = "spider") -> bool:
    try:
        from ..intel import rag_store
        if not hasattr(rag_store, "add_chunk"):
            return False
        await rag_store.add_chunk(
            text=text,
            metadata={
                "source": url,
                "ingested_by": "knowledge_spider",
                "discovered_via": source_hint,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return True
    except Exception as exc:
        logger.debug("rag ingest failed for %s: %s", url, exc)
        return False


# ═══════════════════════════════════════════════════════════════════════
# Public runner
# ═══════════════════════════════════════════════════════════════════════

async def run_spider_tick() -> dict[str, Any]:
    """Single scheduled run — consumes the queue, spiders outward,
    persists new entries, updates stats. Returns a summary."""
    t_start = time.monotonic()

    # Build working queue = persisted queue + fresh seeds
    queue = await _load_queue()
    seeds = await _collect_seeds()
    for s in seeds:
        normalised = _normalise_url(s["url"])
        if not normalised:
            continue
        if await _is_visited(normalised):
            continue
        queue.append({"url": normalised, "depth": s.get("depth", 0), "source": s.get("source", "")})

    fetched: list[dict[str, Any]] = []
    dropped_blocklist = 0
    dropped_visited = 0
    new_discoveries = 0

    # Dedup queue in-place
    seen_hashes: set[str] = set()
    unique_queue: list[dict[str, Any]] = []
    for item in queue:
        h = _url_hash(item["url"])
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        unique_queue.append(item)
    queue = unique_queue

    async with httpx.AsyncClient() as client:
        while queue and len(fetched) < _MAX_FETCHES_PER_RUN:
            if (time.monotonic() - t_start) > _WALL_BUDGET_SEC:
                break
            item = queue.pop(0)
            url = item["url"]
            depth = item.get("depth", 0)

            normalised = _normalise_url(url)
            if not normalised:
                dropped_blocklist += 1
                continue
            if await _is_visited(normalised):
                dropped_visited += 1
                continue

            # Fetch + ingest
            text = await _fetch(normalised, client)
            await _mark_visited(normalised)
            if text and len(text) > 200:
                ingested = await _ingest_into_rag(
                    normalised, text, source_hint=item.get("source", "")
                )
                fetched.append({"url": normalised, "ingested": ingested, "chars": len(text)})
                # Extract NEW URLs from this chunk for the next hop
                if depth < _MAX_DEPTH:
                    for next_url in _URL_RE.findall(text):
                        nn = _normalise_url(next_url)
                        if not nn:
                            continue
                        if await _is_visited(nn):
                            continue
                        queue.append({"url": nn, "depth": depth + 1, "source": "spider"})
                        new_discoveries += 1

            # Rate limit
            await asyncio.sleep(_FETCH_INTERVAL_SEC)

    # Persist remaining queue + stats
    await _save_queue(queue)
    summary = {
        "fetched": len(fetched),
        "ingested": sum(1 for f in fetched if f["ingested"]),
        "new_discoveries_queued": new_discoveries,
        "dropped_blocklist": dropped_blocklist,
        "dropped_visited": dropped_visited,
        "queue_remaining": len(queue),
        "duration_s": round(time.monotonic() - t_start, 2),
    }

    # 24h stat counter — keepttl on subsequent writes so the 24h window
    # actually rolls instead of restarting on every spider tick. Without
    # this, /api/aria/autonomy/surface read `fetches_24h` / `ingests_24h`
    # as lifetime tallies (same anti-pattern fixed in f981c0a).
    try:
        from ..intel import redis_store as rs
        existing = await rs.get_json(_REDIS_STATS_KEY)
        is_fresh = not isinstance(existing, dict)
        stats = existing if isinstance(existing, dict) else {"fetches": 0, "ingests": 0}
        stats["fetches"] = stats.get("fetches", 0) + summary["fetched"]
        stats["ingests"] = stats.get("ingests", 0) + summary["ingested"]
        if is_fresh:
            await rs.set_json(_REDIS_STATS_KEY, stats, ex=86400)
        else:
            await rs.set_json(_REDIS_STATS_KEY, stats, keepttl=True)
    except Exception:
        pass

    # brain_hook — spider is a learning signal
    try:
        from ..intel import brain_hook
        await brain_hook.absorb(
            module="knowledge_spider",
            summary=f"Spider tick: fetched {summary['fetched']}, ingested {summary['ingested']}, queued {summary['new_discoveries_queued']} new",
            success=summary["ingested"] > 0,
        )
    except Exception:
        pass

    logger.info("[spider] %s", summary)
    return summary


async def get_stats() -> dict[str, Any]:
    from ..intel import redis_store as rs
    try:
        stats = await rs.get_json(_REDIS_STATS_KEY) or {}
    except Exception:
        stats = {}
    queue = await _load_queue()
    return {
        "fetches_24h": stats.get("fetches", 0),
        "ingests_24h": stats.get("ingests", 0),
        "queue_depth": len(queue),
    }


def summary() -> dict[str, Any]:
    """Capability-manifest summary."""
    return {
        "max_depth": _MAX_DEPTH,
        "max_fetches_per_run": _MAX_FETCHES_PER_RUN,
        "wall_budget_sec": _WALL_BUDGET_SEC,
        "blocklist_domains": len(_DOMAIN_BLOCKLIST),
    }

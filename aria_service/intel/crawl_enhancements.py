"""Professional-researcher crawl enhancements — PDF, Wayback, robots, sitemap.

What this closes
────────────────
Earlier session's honest audit put ARIA at ~80% of a professional
researcher's crawl capability. This module closes the quick-win 15%:

  1. PDF auto-extract — when URL serves application/pdf or ends in .pdf,
     route to PyMuPDF instead of the HTML scraper (no more "extracted:
     binary content, 0 chars")
  2. Wayback Machine fallback — when primary fetch returns 403/404/429
     or looks like bot-detection, query Archive.org for the latest
     snapshot and fetch from there. Archive.org is a legitimate public
     crawler whose cached content is publicly citable.
  3. robots.txt respect — parse + cache per-domain robots.txt, honour
     Disallow + Crawl-delay. Professional courtesy + reduces IP bans.
  4. Sitemap-aware crawl — when crawling a domain, check /sitemap.xml
     first. Sitemaps give you the site's own list of what's important
     and up-to-date, far better than link-walking from the homepage.
  5. Content-type routing — CSV/XLSX/JSON/XML endpoints get their own
     parsers (openpyxl + csv + stdlib json/ET), not the HTML scraper.

What we explicitly DON'T do
───────────────────────────
  - CAPTCHA bypass — ethically, legally, and reputationally out of
    bounds for a regulated-sector DD tool. See memory/session notes.
  - Login-gated content evasion — either register legitimately or skip.
  - Residential proxy rotation for anti-bot evasion — same reasoning.

Public API
──────────
  async fetch_with_fallbacks(url) -> dict   # main entry, handles all cases
  async fetch_pdf(url) -> dict              # direct PDF path
  async fetch_via_wayback(url) -> dict      # direct archive lookup
  async check_robots(url) -> dict           # policy for a given URL
  async discover_via_sitemap(domain) -> list[str]
  async detect_content_type(url) -> str
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse, urljoin

logger = logging.getLogger("aria.crawl_enhancements")

_UA = "ARIA-Research-Crawler/1.0 (+research@arkmurus.com)"
_ROBOTS_CACHE: dict[str, dict] = {}
_ROBOTS_TTL_S = 3600  # 1h


# ── Content-type detection ────────────────────────────────────────────────

async def detect_content_type(url: str, timeout: float = 8.0) -> dict:
    """HEAD request to learn the content-type before committing to a
    fetcher. Returns {content_type, status, length, final_url}."""
    import httpx
    out: dict[str, Any] = {
        "url": url,
        "content_type": "",
        "status": 0,
        "length": None,
        "final_url": url,
        "error": None,
    }
    # R-F1851 (DD stage 2) — SSRF guard. `url` is user/discovery-supplied; validate
    # it (DNS-resolved) and disable redirect-following so a HEAD/stream probe cannot
    # reach an internal host. This mirrors the safe_get posture used by fetch_pdf.
    from . import url_safety as _us
    _ok_ct, _reason_ct = await _us.is_safe_url_async(url)
    if not _ok_ct:
        out["error"] = f"ssrf_blocked:{_reason_ct}"
        return out
    try:
        async with httpx.AsyncClient(  # no-breaker: crawl enhancements are best-effort
            timeout=timeout, follow_redirects=False,
        ) as client:
            try:
                r = await client.head(url, headers={"User-Agent": _UA})
                # Some servers 405 HEAD — fall back to streaming GET
                if r.status_code == 405 or r.status_code >= 500:
                    async with client.stream("GET", url, headers={"User-Agent": _UA}) as s:
                        out["content_type"] = s.headers.get("content-type", "")
                        out["status"] = s.status_code
                        out["length"] = int(s.headers.get("content-length", 0) or 0) or None
                        out["final_url"] = str(s.url)
                else:
                    out["content_type"] = r.headers.get("content-type", "")
                    out["status"] = r.status_code
                    cl = r.headers.get("content-length", "")
                    out["length"] = int(cl) if cl.isdigit() else None
                    out["final_url"] = str(r.url)
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    # Strip ";charset=..." noise
    ct = out["content_type"].split(";")[0].strip().lower()
    out["content_type"] = ct
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="crawl_enhancements",
        summary="Detect Content Type",
        source_id="crawl_enhancements:R-F996",
    )

    return out


# ── robots.txt policy ─────────────────────────────────────────────────────

async def check_robots(url: str) -> dict:
    """Fetch (and cache) the domain's robots.txt, evaluate whether OUR
    user-agent is allowed to fetch the given URL, and read the
    Crawl-delay directive.

    Returns {allowed: bool, crawl_delay: float, sitemaps: list[str]}.
    Fail-open — if robots.txt is unreachable or unparseable, we assume
    allowed=True (most sites without robots.txt are open by convention).
    """
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return {"allowed": True, "crawl_delay": 0.0, "sitemaps": [],
                "error": "invalid url"}

    now = time.time()
    cached = _ROBOTS_CACHE.get(base)
    if cached and (now - cached.get("fetched_at", 0)) < _ROBOTS_TTL_S:
        policy = cached
    else:
        import httpx
        robots_url = f"{base}/robots.txt"
        text = ""
        try:
            # R-F522 (2026-05-14) — cap redirects at 5. Live 2026-05-14
            # 12:22:56: imo.org/robots.txt 302→www.imo.org/robots.txt which
            # then 301-loops on itself ~60 times in 0.5s (each hop ~6ms),
            # all logged at INFO via httpx. Default httpx max_redirects=20
            # let the loop run to completion before failing. Server bug
            # (Location header points back to same URL) — we can't fix
            # imo.org but we can cap our patience. Same pattern observed
            # earlier on email.net.
            from . import url_safety as _us  # R-F1851 (DD stage 2): SSRF guard
            async with httpx.AsyncClient(  # no-breaker: crawl enhancements are best-effort
                timeout=8.0, follow_redirects=False,
            ) as client:
                # safe_get revalidates every redirect hop (cap 5, matching the
                # historical max_redirects) so a robots.txt fetch on a
                # user-derived base host cannot reach an internal service.
                r = await _us.safe_get(client, robots_url, headers={"User-Agent": _UA}, max_redirects=5)
                if r.status_code == 200 and len(r.text) < 200_000:
                    text = r.text
        except Exception as e:
            logger.debug("[robots] fetch %s failed: %s", robots_url, e)
        policy = _parse_robots(text)
        policy["fetched_at"] = now
        _ROBOTS_CACHE[base] = policy

    # Evaluate path against Disallow rules
    path = parsed.path or "/"
    allowed = True
    for rule in policy.get("disallow", []):
        if _path_matches(path, rule):
            allowed = False
            break
    # Allow rules can override
    for rule in policy.get("allow", []):
        if _path_matches(path, rule):
            allowed = True
            break

    return {
        "allowed": allowed,
        "crawl_delay": float(policy.get("crawl_delay", 0.0) or 0.0),
        "sitemaps": policy.get("sitemaps", []),
    }


def _parse_robots(text: str) -> dict:
    """Minimal robots.txt parser. Honours User-agent: * and any UA
    whose name includes 'ARIA' (future-proofing). Ignores other UA
    blocks — same behaviour as google's lenient parser."""
    if not text:
        return {"disallow": [], "allow": [], "crawl_delay": 0, "sitemaps": []}

    disallow: list[str] = []
    allow: list[str] = []
    crawl_delay = 0.0
    sitemaps: list[str] = []

    current_ua = ""
    applies_to_us = False
    for raw_line in text.splitlines():
        line = raw_line.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            current_ua = value.lower()
            # We apply rules for '*' or any UA that mentions aria/bot
            applies_to_us = current_ua in ("*",) or "aria" in current_ua
            continue
        if key == "sitemap":
            sitemaps.append(value)
            continue
        if not applies_to_us:
            continue
        if key == "disallow" and value:
            disallow.append(value)
        elif key == "allow" and value:
            allow.append(value)
        elif key == "crawl-delay":
            try:
                crawl_delay = max(crawl_delay, float(value))
            except Exception:
                pass

    return {
        "disallow": disallow,
        "allow": allow,
        "crawl_delay": crawl_delay,
        "sitemaps": sitemaps,
    }


def _path_matches(path: str, rule: str) -> bool:
    """Simple path-prefix / wildcard match per robots.txt spec."""
    if not rule:
        return False
    if rule == "/":
        return True
    if "*" not in rule and "$" not in rule:
        return path.startswith(rule)
    # Translate wildcard rule to regex
    pattern = "^" + re.escape(rule).replace(r"\*", ".*").replace(r"\$", "$")
    try:
        return bool(re.match(pattern, path))
    except Exception:
        return path.startswith(rule.rstrip("*$"))


# ── PDF fetch ─────────────────────────────────────────────────────────────

def _extract_pdf_sync(content: bytes) -> dict:
    """R-F2056 — fitz parse + text extraction, run OFF the event loop via
    asyncio.to_thread. PyMuPDF is a C extension that RELEASES the GIL during
    extraction, so threading genuinely frees the loop (unlike pure-Python work) —
    a concurrent DD crawling a big PDF no longer stalls every other user.
    Returns {text, pages, title, ok, error}."""
    res = {"text": "", "pages": 0, "title": "", "ok": False, "error": None}
    try:
        import fitz  # pymupdf
    except ImportError:
        res["error"] = "PyMuPDF not installed — add pymupdf to requirements"
        return res
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as e:
        res["error"] = f"PDF parse failed: {str(e)[:200]}"
        return res
    try:
        text_parts: list[str] = []
        for page in doc:
            try:
                text_parts.append(page.get_text())
            except Exception:
                continue
        text = "\n\n".join(text_parts)
        meta = doc.metadata or {}
        res["text"] = text[:50000]
        res["pages"] = len(doc)
        res["title"] = (meta.get("title") or "").strip()
        res["ok"] = bool(text.strip())
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return res


async def fetch_pdf(url: str, timeout: float = 30.0) -> dict:
    """Fetch a PDF and extract text via PyMuPDF (already in requirements).

    Returns {ok, text, pages, title, url, error}. Text capped at 50k
    chars for downstream RAG ingest; full PDF is not retained in memory.
    """
    import httpx
    out: dict[str, Any] = {
        "ok": False, "text": "", "pages": 0, "title": "", "url": url,
        "error": None, "source": "pdf_fetch",
    }
    try:
        from . import url_safety as _us
        async with httpx.AsyncClient(  # no-breaker: crawl enhancements are best-effort
            timeout=timeout, follow_redirects=False,  # R-F1825: safe_get revalidates each hop
        ) as client:
            r = await _us.safe_get(client, url, headers={"User-Agent": _UA})  # R-F1825 (C2-broaden): SSRF guard on user/discovery PDF URL
            if r.status_code != 200:
                out["error"] = f"HTTP {r.status_code}"
                return out
            content = r.content
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return out

    if not content or len(content) < 100:
        out["error"] = "empty or too-short PDF content"
        return out
    if not content.startswith(b"%PDF"):
        out["error"] = "content is not a valid PDF (wrong magic header)"
        return out

    # R-F2056 — extract via PyMuPDF OFF the event loop (fitz releases the GIL, so
    # to_thread genuinely frees the loop) so a PDF-crawling DD can't stall others.
    pdf = await asyncio.to_thread(_extract_pdf_sync, content)
    if pdf.get("error"):
        out["error"] = pdf["error"]
        # R-F2056/§21a — a failed PDF source fetch is a real signal the brain
        # should see (so the coder/self-heal can act on recurring bad sources).
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="crawl_enhancements",
                detail=f"PDF fetch/extract failed for {url}: {pdf['error']}",
                gap_type="pdf_fetch_failure",
                source=f"crawl_enhancements:fetch_pdf",
            )
        except Exception:
            pass
        return out
    out["text"] = pdf["text"]
    out["pages"] = pdf["pages"]
    out["title"] = pdf["title"]
    out["ok"] = pdf["ok"]
    return out


# ── Wayback Machine fallback ──────────────────────────────────────────────

async def fetch_via_wayback(
    url: str,
    timeout: float = 15.0,
    *,
    timestamp: str | None = None,
) -> dict:
    """When the primary fetch fails, try Archive.org's latest snapshot.

    Uses the /wayback/available endpoint to find the most recent
    snapshot, then fetches from web.archive.org. Archive.org is a
    legitimate public crawler whose cached content is publicly citable
    — this is NOT evasion, it's using the canonical third-party
    archive.

    R-F437 (2026-05-13) — `timestamp` (YYYYMMDD) selects the snapshot
    closest to that date instead of the latest. Used by
    fetch_historical_snapshots to pivot 1y / 3y back on the same URL
    and detect officers / contacts who appeared in the past but are
    missing from the current site (rebrand / shell-flip detection).
    """
    import httpx
    from .circuit_breaker import get_breaker
    out: dict[str, Any] = {
        "ok": False, "url": url, "snapshot_url": "",
        "snapshot_timestamp": "", "html": "", "error": None,
        "source": "wayback",
        "requested_timestamp": timestamp or "",
    }
    # R-F1790: circuit breaker for archive.org — this page-fetch wayback
    # fallback had none, so archive.org rate-limits could cascade across DD ops.
    _wb_cb = get_breaker("fetch:wayback", failure_threshold=3, cooldown_seconds=600)
    if _wb_cb.is_open():
        out["error"] = "wayback circuit OPEN (recent archive.org failures) — skipping"
        return out
    availability_url = f"https://archive.org/wayback/available?url={url}"
    if timestamp:
        availability_url += f"&timestamp={timestamp}"
    try:
        async with httpx.AsyncClient(  # no-breaker: crawl enhancements are best-effort
            timeout=timeout, follow_redirects=True,
        ) as client:
            r = await client.get(availability_url, headers={"User-Agent": _UA})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        _wb_cb.record_failure(reason="timeout")  # R-F1790
        out["error"] = f"availability lookup failed: {str(e)[:200]}"
        return out

    snap = ((data.get("archived_snapshots") or {}).get("closest") or {})
    if not snap.get("available") or not snap.get("url"):
        out["error"] = "no archived snapshot available for this URL"
        return out

    snap_url = snap["url"]
    out["snapshot_url"] = snap_url
    out["snapshot_timestamp"] = snap.get("timestamp", "")

    try:
        async with httpx.AsyncClient(  # no-breaker: crawl enhancements are best-effort
            timeout=timeout, follow_redirects=True,
        ) as client:
            r = await client.get(snap_url, headers={"User-Agent": _UA})
            if r.status_code != 200:
                _wb_cb.record_failure(reason="server")  # R-F1790
                out["error"] = f"snapshot fetch HTTP {r.status_code}"
                return out
            out["html"] = r.text
            out["ok"] = True
            _wb_cb.record_success()  # R-F1790
    except Exception as e:
        _wb_cb.record_failure(reason="timeout")  # R-F1790
        out["error"] = f"snapshot fetch failed: {str(e)[:200]}"

    return out


# ── R-F437 — Wayback historical snapshot pivot ─────────────────────────────


async def fetch_historical_snapshots(
    url: str,
    *,
    years_back: tuple[int, ...] = (1, 3),
    timeout: float = 15.0,
) -> list[dict]:
    """R-F437 — fetch Wayback snapshots at several historical timestamps.

    Live-only crawl misses the case where a now-clean entity was a
    sanctioned shell five years ago, or where a director resigned to
    cover an embarrassing affiliation. Returns one result dict per
    requested year (same shape as fetch_via_wayback), in chronological
    order. Failed snapshots are returned with ok=False so the caller
    can decide whether the gap is meaningful or just an archive miss.

    Args:
        url:          The seed URL (use the site's canonical home, not
                      a deep page — Wayback's coverage is best on home).
        years_back:   Tuple of years-back integers. Default (1, 3)
                      keeps cost to 2 HTTP requests per DD.
        timeout:      Per-snapshot timeout in seconds.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    results: list[dict] = []
    for years in sorted(set(years_back)):
        try:
            target = now - timedelta(days=int(years * 365.25))
            ts = target.strftime("%Y%m%d")
            snap = await fetch_via_wayback(url, timeout=timeout, timestamp=ts)
            snap["years_back"] = years
            results.append(snap)
        except Exception as e:  # never let one snapshot break the batch
            results.append({
                "ok": False,
                "url": url,
                "years_back": years,
                "error": f"historical fetch failed: {str(e)[:200]}",
                "source": "wayback",
            })
    return results


# ── Sitemap discovery ─────────────────────────────────────────────────────

async def discover_via_sitemap(domain_or_url: str, limit: int = 200) -> list[str]:
    """Return the list of URLs the site publishes via sitemap.xml.
    Far better than link-walking for systematic crawls — the site
    operator tells you directly what they want crawled."""
    parsed = urlparse(
        domain_or_url if "://" in domain_or_url else f"https://{domain_or_url}"
    )
    base = f"{parsed.scheme}://{parsed.netloc}"

    # 1. Check robots.txt for sitemap declarations
    policy = await check_robots(f"{base}/")
    sitemap_candidates = list(policy.get("sitemaps") or [])

    # 2. Fall back to standard locations
    for default in (f"{base}/sitemap.xml", f"{base}/sitemap_index.xml",
                    f"{base}/sitemaps.xml"):
        if default not in sitemap_candidates:
            sitemap_candidates.append(default)

    import httpx
    urls: list[str] = []
    seen: set[str] = set()
    for sm_url in sitemap_candidates[:5]:
        if len(urls) >= limit:
            break
        try:
            async with httpx.AsyncClient(  # no-breaker: crawl enhancements are best-effort
                timeout=15.0, follow_redirects=True,
            ) as client:
                r = await client.get(sm_url, headers={"User-Agent": _UA})
                if r.status_code != 200 or not r.text:
                    continue
                xml = r.text
        except Exception:
            continue

        # Sitemap-index vs sitemap. Both have <loc> tags.
        # Index entries point at sub-sitemaps; regular entries point at pages.
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml, re.IGNORECASE)
        is_index = "<sitemapindex" in xml.lower()

        if is_index:
            # One level of recursion — add sub-sitemap URLs to the queue
            for sub in locs[:10]:
                if sub not in sitemap_candidates:
                    sitemap_candidates.append(sub)
            continue

        for loc in locs:
            if loc in seen:
                continue
            seen.add(loc)
            urls.append(loc)
            if len(urls) >= limit:
                break

    return urls


# ── Main entry — fetch_with_fallbacks ─────────────────────────────────────

async def fetch_with_fallbacks(
    url: str,
    *,
    allow_pdf: bool = True,
    allow_wayback: bool = True,
    respect_robots: bool = True,
    timeout: float = 20.0,
) -> dict:
    """Professional-researcher fetch with all fallbacks.

    Order of attempts:
      1. robots.txt check — if disallowed, return immediately with
         allowed=False (don't fetch).
      2. Detect content-type via HEAD.
      3. If PDF: route to fetch_pdf (binary-safe, PyMuPDF extraction).
      4. Primary fetch of HTML.
      5. If primary fails (403/404/429/timeout) AND allow_wayback:
         try Archive.org's snapshot.

    Returns a unified shape:
      {
        source: "primary" | "pdf" | "wayback" | "blocked_by_robots",
        ok: bool,
        url: str,
        final_url: str,
        content_type: str,
        text: str,        # for HTML + PDF
        html: str,        # for HTML only
        status: int,
        error: str | None,
        robots_allowed: bool,
        wayback_timestamp: str,  # only if source=wayback
      }
    """
    result: dict[str, Any] = {
        "source": "primary",
        "ok": False,
        "url": url,
        "final_url": url,
        "content_type": "",
        "text": "",
        "html": "",
        "status": 0,
        "error": None,
        "robots_allowed": True,
        "wayback_timestamp": "",
    }

    # R-F1851 (DD stage 2) — single upfront SSRF guard. `url` is user/discovery-
    # supplied; one entry check (DNS-resolved) covers every downstream path here:
    # robots.txt, content-type probe, primary fetch, Wayback, and the Playwright
    # headless fallback (which would otherwise navigate to an internal host).
    # Per-hop redirect revalidation is additionally enforced by safe_get below.
    from . import url_safety as _us_entry
    _ok_entry, _reason_entry = await _us_entry.is_safe_url_async(url)
    if not _ok_entry:
        result["error"] = f"ssrf_blocked:{_reason_entry}"
        result["source"] = "blocked_unsafe_url"
        _emit_crawl_signal(result)
        return result

    # 1. robots.txt
    if respect_robots:
        try:
            policy = await check_robots(url)
            result["robots_allowed"] = policy.get("allowed", True)
            if not policy.get("allowed", True):
                result["source"] = "blocked_by_robots"
                result["error"] = "disallowed by robots.txt"
                _emit_crawl_signal(result)
                return result
            # Politeness — sleep the crawl-delay if any
            delay = float(policy.get("crawl_delay", 0.0))
            if 0 < delay <= 10:
                await asyncio.sleep(delay)
        except Exception as e:
            logger.debug("[crawl_enhancements] robots check failed: %s", e)

    # 2. Detect content type
    try:
        ct_info = await detect_content_type(url)
        result["content_type"] = ct_info.get("content_type", "")
        result["status"] = ct_info.get("status", 0)
        result["final_url"] = ct_info.get("final_url", url)
    except Exception as e:
        logger.debug("[crawl_enhancements] content-type detect failed: %s", e)

    # 3. PDF path
    if allow_pdf and (
        "application/pdf" in (result["content_type"] or "")
        or url.lower().endswith(".pdf")
        or result["final_url"].lower().endswith(".pdf")
    ):
        pdf_result = await fetch_pdf(url, timeout=timeout * 2)
        result["source"] = "pdf"
        result["ok"] = pdf_result.get("ok", False)
        result["text"] = pdf_result.get("text", "")
        if pdf_result.get("error"):
            result["error"] = pdf_result["error"]
        _emit_crawl_signal(result)
        return result

    # 4. Primary HTML fetch
    import httpx
    from . import url_safety as _us  # R-F1851 (DD stage 2): SSRF guard
    primary_failed = False
    primary_error = ""
    try:
        async with httpx.AsyncClient(  # no-breaker: crawl enhancements are best-effort
            timeout=timeout, follow_redirects=False,  # R-F1851: safe_get revalidates each hop
        ) as client:
            r = await _us.safe_get(client, url, headers={"User-Agent": _UA})  # R-F1851: SSRF guard on primary HTML fetch
            result["status"] = r.status_code
            result["final_url"] = str(r.url)
            if r.status_code == 200:
                result["html"] = r.text
                result["ok"] = True
                # Also extract plain text via the existing structured
                # extractor so the return shape is uniform
                try:
                    from .researcher import extract_structured_html_async
                    extracted = await extract_structured_html_async(r.text)
                    result["text"] = extracted.get("text", "") or ""
                except Exception:
                    result["text"] = re.sub(r"<[^>]+>", " ", r.text)[:50000]
                # R-F1101 — check for thin JS-shell content and fall through
                # to Playwright if the page is likely a JS SPA
                from . import headless as _h1101
                if _h1101.is_thin_content(r.text):
                    logger.info("Thin 200 from %s (%d chars) — will try Playwright",
                                url[:80], len(r.text))
                    primary_failed = True
                    primary_error = "thin JS shell (200 but no content)"
                else:
                    return result
            else:
                primary_failed = True
                primary_error = f"HTTP {r.status_code}"
    except Exception as e:
        primary_failed = True
        primary_error = f"{type(e).__name__}: {str(e)[:200]}"

    # 5. Wayback fallback
    if primary_failed and allow_wayback:
        wb = await fetch_via_wayback(url)
        if wb.get("ok"):
            result["source"] = "wayback"
            result["ok"] = True
            result["html"] = wb.get("html", "")
            result["wayback_timestamp"] = wb.get("snapshot_timestamp", "")
            result["error"] = (
                f"primary failed ({primary_error}), served from Wayback "
                f"snapshot {wb.get('snapshot_timestamp','?')}"
            )
            try:
                from .researcher import extract_structured_html_async
                extracted = await extract_structured_html_async(result["html"])
                result["text"] = extracted.get("text", "") or ""
            except Exception:
                result["text"] = re.sub(r"<[^>]+>", " ", result["html"])[:50000]
            return result

    # 6. Playwright fallback — final attempt for JS-heavy sites that
    # Lightpanda couldn't render AND Wayback doesn't have. Honest
    # browser, no stealth — so if the site blocks us we report
    # blocked=True rather than circumventing.
    if primary_failed:
        try:
            from .scraper import orchestrator as _scraper
            pw_result = await _scraper.fetch(url, timeout=timeout * 2)
            if pw_result.get("ok"):
                result["source"] = pw_result.get("adapter") or "playwright"
                result["ok"] = True
                result["html"] = pw_result.get("html", "")
                result["text"] = pw_result.get("text", "")
                result["status"] = pw_result.get("status", result["status"])
                return result
            if pw_result.get("blocked"):
                result["error"] = (
                    f"blocked by bot-detection: "
                    f"{pw_result.get('block_reason','unknown')}"
                )
                return result
        except ImportError:
            logger.debug("Playwright scraper unavailable — skipping fallback")
        except Exception as _e:
            logger.debug("Playwright fallback raised: %s", _e)

    result["error"] = primary_error or "fetch failed"
    _emit_crawl_signal(result)
    return result


def _emit_crawl_signal(result: dict) -> None:
    """Fire-and-forget brain absorb for crawl_enhancements outcomes."""
    try:
        import asyncio as _aio
        from . import brain_hook as _bh

        async def _emit():
            await _bh.absorb(
                module="crawl_enhancements",
                summary=(
                    f"Crawl {result.get('source','?')} "
                    f"({'ok' if result.get('ok') else 'fail'}): "
                    f"{(result.get('url') or '')[:120]} "
                    f"ct={result.get('content_type','?')[:40]}"
                ),
                detail=(result.get('error') or "")[:300],
                entity_name=(result.get("final_url") or result.get("url") or "")[:120],
                success=bool(result.get("ok")),
                gap_type=(
                    "blocked_by_robots" if result.get("source") == "blocked_by_robots"
                    else "crawl_failed" if not result.get("ok") else None
                ),
                gap_detail=(result.get('error') or "")[:200] if not result.get("ok") else None,
                confidence="ASSESSED",
            )

        try:
            loop = _aio.get_running_loop()
            loop.create_task(_emit())
        except RuntimeError:
            pass
    except Exception:
        pass


# ── Content-type-aware parsers for non-HTML endpoints ─────────────────────

async def fetch_structured_endpoint(url: str, timeout: float = 20.0) -> dict:
    """Handle CSV / XLSX / JSON / XML endpoints with appropriate parsers
    instead of the HTML scraper. Returns {kind, records, headers, error}.
    """
    ct_info = await detect_content_type(url, timeout=timeout)
    ct = ct_info.get("content_type", "")
    out: dict[str, Any] = {"kind": "unknown", "records": [], "headers": [], "error": None}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:  # no-breaker: crawl enhancements are best-effort
            r = await client.get(url, headers={"User-Agent": _UA})
            if r.status_code != 200:
                out["error"] = f"HTTP {r.status_code}"
                return out
            body = r.content
            text = r.text if r.content else ""
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return out

    if "json" in ct or url.lower().endswith(".json"):
        import json
        try:
            data = json.loads(text)
            if isinstance(data, list):
                out["kind"] = "json_array"
                out["records"] = data[:500]
            elif isinstance(data, dict):
                out["kind"] = "json_object"
                out["records"] = [data]
        except Exception as e:
            out["error"] = f"JSON parse: {str(e)[:150]}"

    elif "csv" in ct or url.lower().endswith(".csv"):
        import csv
        import io
        try:
            reader = csv.reader(io.StringIO(text))
            rows = list(reader)
            if rows:
                out["kind"] = "csv"
                out["headers"] = rows[0]
                out["records"] = [
                    {h: v for h, v in zip(rows[0], row)}
                    for row in rows[1:501]
                ]
        except Exception as e:
            out["error"] = f"CSV parse: {str(e)[:150]}"

    elif "spreadsheetml" in ct or url.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
            import io as _io
            wb = openpyxl.load_workbook(
                _io.BytesIO(body), read_only=True, data_only=True,
            )
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if rows:
                headers = [str(h) if h is not None else "" for h in rows[0]]
                out["kind"] = "xlsx"
                out["headers"] = headers
                out["records"] = [
                    {h: (v if v is not None else "") for h, v in zip(headers, row)}
                    for row in rows[1:501]
                ]
            wb.close()
        except Exception as e:
            out["error"] = f"XLSX parse: {str(e)[:150]}"

    elif "xml" in ct or url.lower().endswith(".xml"):
        try:
            from defusedxml import ElementTree as ET
            root = ET.fromstring(text)
            out["kind"] = "xml"
            out["records"] = [{
                "root_tag": root.tag.rsplit("}", 1)[-1],
                "child_count": len(list(root)),
                "sample_children": [
                    c.tag.rsplit("}", 1)[-1] for c in list(root)[:10]
                ],
            }]
        except Exception as e:
            out["error"] = f"XML parse: {str(e)[:150]}"

    else:
        out["error"] = f"unsupported content-type: {ct}"

    return out

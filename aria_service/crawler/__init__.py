"""ARIA Crawler — own engine, no third-party search API.

Modules:
  seed_list  — 150+ curated domains tagged by sector / region / tier
  politeness — per-domain token bucket + robots.txt cache
  fetcher    — thin wrapper around researcher.extract_url_text + politeness
  runner     — background loop (lifespan-wired in R-F504 phase)

The crawler does NOT do extraction — it delegates to the existing
researcher.extract_url_text primitive (R-F126 fast path, SSRF-guarded,
Wayback + Lightpanda fallback). This module only adds:
  * curated seed registry
  * per-domain rate limiting
  * robots.txt enforcement
  * persistence into aria_service/search_index/db.py
"""

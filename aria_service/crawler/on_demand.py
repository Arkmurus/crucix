"""on_demand — bounded synchronous gap-fill crawler.

R-F507 (2026-05-14). The watershed move: ARIA's search engine fills
itself on demand. Before R-F507 the engine existed but stayed empty;
now every chat query that hits a thin internal result triggers a
bounded mini-crawl in the background — pay once, remember forever.

The crawler does NOT depend on any third-party search API. Candidate
URLs come from:
  1. Entity → domain-name guesses (modirum gespi → modirumgespi.com,
     gespi.ao, modirum-gespi.com, etc.). Deterministic heuristic; no
     network round-trip to discover them.
  2. Curated seed domains' search endpoints where they expose one
     (Phase 2 — for now we hit home pages only; the indexer extracts
     links, which feeds Phase 2 frontier expansion).
  3. Already-indexed pages whose outbound links the indexer collected
     (also Phase 2).

Public surface:
    async def ensure_indexed(query, *, time_budget_s=20.0,
                              max_pages=15) -> dict
        Fetch + index candidate URLs for `query` up to the budget;
        returns {indexed, skipped, errors, candidates_tried,
        duration_sec, query}.

    async def background_ensure(query) -> None
        Fire-and-forget wrapper called from chat. Caps budget at 25s
        so it can't outlive even a slow LLM turn, and never raises.

The synchronous variant is also exposed for an admin endpoint and the
capability test.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from aria_service.search_index import db, indexer
from . import fetcher

logger = logging.getLogger("aria.crawler.on_demand")


# ─────────────────────────────────────────────────────────────────
# Candidate URL generation
# ─────────────────────────────────────────────────────────────────

# Stripped at token level: drop punctuation; keep letters + digits + hyphen.
_TOKEN_RX = re.compile(r"[A-Za-z0-9-]+")

# Common ccTLDs / generic TLDs to try when guessing a corporate site
# from an entity name. Ordered by hit-rate: .com first; then country
# codes for ARIA's main operating regions (Lusophone Africa, EU, US).
_GUESS_TLDS = (
    ".com", ".co", ".net", ".org",
    ".pt", ".com.br", ".com.ao", ".co.ao", ".co.mz",
    ".eu", ".fr", ".es", ".de", ".it", ".co.uk", ".uk",
    ".za", ".co.za", ".com.tr", ".ru",
)


# R-F654 (2026-05-17): tokens that produce useless candidate domains.
# Live evidence 2026-05-17 08:11-08:13 — chat queries containing 2-char
# numeric or placeholder tokens fed guess_entity_urls and produced URLs
# like 283.com, 2b.org, 2026.co, acme-widgets.com — each auto-registered
# at tier 4 then re-crawled by the daily loop forever.
#
# Reject:
#   - tokens that are PURE DIGITS (e.g., "283", "2026") — never a real
#     org domain, just years / version numbers / generic counts
#   - tokens shorter than 3 chars (e.g., "2b", "ai") — too short to
#     uniquely identify an org; combine with another token if needed
#   - well-known RFC 2606 / tutorial placeholder words — "acme", "widgets",
#     "example", "foo", "bar", "baz", "test", "sample", "todo"
_PLACEHOLDER_TOKENS = frozenset({
    "acme", "widgets", "widget", "example", "foo", "bar", "baz",
    "qux", "test", "tests", "sample", "samples", "todo", "tbd",
    "placeholder", "dummy", "mock", "demo",
})


def _tokens(query: str) -> list[str]:
    """Tokenise + drop low-quality tokens that would produce parked /
    placeholder / numeric guess URLs. R-F654 raises the floor from
    len>=2 to len>=3 AND rejects pure-digit + placeholder tokens."""
    out: list[str] = []
    for t in _TOKEN_RX.findall(query or ""):
        if len(t) < 3:
            continue
        low = t.lower()
        if low.isdigit():
            continue
        if low in _PLACEHOLDER_TOKENS:
            continue
        out.append(low)
    return out


def guess_entity_urls(query: str, limit: int = 30) -> list[str]:
    """Generate plausible corporate / institutional URLs for an entity
    query without hitting any external service.

    Heuristic: for tokens [a, b, c] we try
        ab.tld           — concatenated
        a-b.tld          — hyphenated
        a.tld            — head-only (parent-org sites like "modirum.com")
        b.tld            — second-only (subsidiary-as-brand like "gespi.ao")

    R-F676 (2026-05-18): single-token shapes (head-only, second-only)
    are ONLY emitted when the query has ≤2 tokens — i.e., the original
    "Modirum Gespi" design case. Live evidence 2026-05-18 07:14-07:19
    showed autonomous-research sentence queries ("Indonesia Philippines
    defence modernisation 2026", "DSCA FMS notification …") going
    through `background_ensure` and feeding the head-only shape, which
    happily produced `indonesia.com`, `philippines.com`, `dsca.com`,
    `koreas.com`, `britain.net`, `news.co`, `article.org` — all real
    parking / unrelated domains that got Lightpanda-rendered and
    indexed as if they were intel. 3+ token queries now only emit
    combined + hyphenated shapes, which DNS-fail harmlessly for
    sentence inputs.
    """
    toks = _tokens(query)
    if not toks:
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(host: str) -> None:
        host = host.lower()
        if host and host not in seen:
            seen.add(host)
            candidates.append(f"https://{host}/")

    # R-F676: only emit head-only / second-only shapes when the query
    # is short enough to plausibly BE an entity name (≤2 tokens).
    emit_single_token_shapes = len(toks) <= 2

    # TLD-outer / shape-inner iteration so the limit cap doesn't cut
    # before high-value shapes reach the list for the common TLDs.
    for tld in _GUESS_TLDS:
        if len(toks) >= 2:
            _add(f"{toks[0]}{toks[1]}{tld}")   # combined (modirumgespi.com)
            _add(f"{toks[0]}-{toks[1]}{tld}")  # hyphenated (modirum-gespi.com)
        if emit_single_token_shapes:
            _add(f"{toks[0]}{tld}")            # head-only (modirum.com parent)
            if len(toks) >= 2:
                _add(f"{toks[1]}{tld}")        # second-only (gespi.ao brand)
    return candidates[:limit]


# ─────────────────────────────────────────────────────────────────
# Auto-register from external search
# ─────────────────────────────────────────────────────────────────

def _safe_domain_for_register(domain: str) -> bool:
    """Reject obviously unsafe / nonsensical domains before registering.
    The fetch-time SSRF guard catches anything that slips through, but
    we don't want garbage in the domains table either."""
    if not domain or len(domain) < 4 or len(domain) > 253:
        return False
    if "." not in domain or domain.startswith("."):
        return False
    if domain in ("localhost", "ip6-localhost"):
        return False
    parts = domain.split(".")
    if not all(parts):
        return False
    # Numeric IPv4 / IPv6 — reject; we only crawl named hosts.
    if all(p.isdigit() for p in parts):
        return False
    # R-F654 (2026-05-17): the LABEL (everything before the public TLD)
    # must not be purely numeric and must be at least 3 chars. Catches
    # the live-evidence cases 283.com, 2026.org, 2b.com that slipped past
    # the all-parts-numeric check (those have alphabetic TLDs).
    label = parts[0]
    if label.isdigit():
        return False
    if len(label) < 3:
        return False
    # Reject RFC 2606 / placeholder labels even if length passes.
    label_low = label.lower()
    if label_low in _PLACEHOLDER_TOKENS:
        return False
    # Hyphenated placeholder labels (e.g., "acme-widgets") — if every
    # non-empty hyphen segment is a known placeholder, the whole label
    # is junk. (Mixed labels like "bae-systems" survive because "bae"
    # isn't a placeholder.)
    if "-" in label_low:
        segments = [s for s in label_low.split("-") if s]
        if segments and all(s in _PLACEHOLDER_TOKENS for s in segments):
            return False
    return True


async def auto_register_domain(
    domain: str, *, tier: int = 4, sector: str = "discovered",
    rate_limit_per_sec: float = 0.5,
) -> bool:
    """Register a discovered domain at tier 4 if we don't know it yet.
    Returns True iff a new row was created. Idempotent — repeated calls
    for the same domain are no-ops."""
    if not _safe_domain_for_register(domain):
        return False
    existing = await db.get_domain(domain)
    if existing is not None:
        return False
    await db.register_domain(
        domain=domain, tier=tier, sector=sector,
        rate_limit_per_sec=rate_limit_per_sec,
        notes=(f"auto-registered (R-F507) at "
               f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"),
    )
    return True


# ─────────────────────────────────────────────────────────────────
# Synchronous bounded fill
# ─────────────────────────────────────────────────────────────────

async def ensure_indexed(
    query: str,
    *,
    time_budget_s: float = 20.0,
    max_pages: int = 15,
    fetch_fn=None,
) -> dict:
    """Fetch + index candidate URLs for `query` within bounds.

    Args:
      query: free-text entity / topic query.
      time_budget_s: total wall-clock cap. Returns whatever has been
        indexed so far when the budget elapses.
      max_pages: hard cap on the number of pages indexed regardless of
        time budget.
      fetch_fn: optional override for tests — defaults to
        crawler.fetcher.fetch_for_index.

    Returns:
      {query, candidates_tried, fetched, indexed, skipped, errors,
       duration_sec}
    """
    t0 = time.time()
    fetch = fetch_fn or fetcher.fetch_for_index

    candidates = guess_entity_urls(query, limit=min(max_pages * 3, 30))
    fetched = indexed = skipped = errors = 0
    new_domains_registered = 0

    # For each candidate domain we may need to auto-register it before
    # the fetcher's registered-only gate will let us crawl.
    for url in candidates:
        if (time.time() - t0) >= time_budget_s:
            break
        if indexed >= max_pages:
            break

        # Auto-register the guessed domain so the fetcher's "must be
        # in registry" gate doesn't block us. New rows land at tier 4.
        try:
            from .politeness import domain_of
            d = domain_of(url)
            if d:
                created = await auto_register_domain(d)
                if created:
                    new_domains_registered += 1
        except Exception:
            pass

        try:
            result = await fetch(url, timeout=8.0)
        except TypeError:
            # Tests sometimes pass a 1-arg fake.
            try:
                result = await fetch(url)
            except Exception as e:
                errors += 1
                logger.debug("on_demand: fetch %s raised: %s", url[:120], e)
                continue
        except Exception as e:
            errors += 1
            logger.debug("on_demand: fetch %s raised: %s", url[:120], e)
            continue

        if result is None:
            skipped += 1
            continue
        fetched += 1

        try:
            doc_id = await indexer.index_fetch_result(result)
            if doc_id:
                indexed += 1
            else:
                skipped += 1
        except Exception as e:
            errors += 1
            logger.debug("on_demand: index %s raised: %s", url[:120], e)

    duration = round(time.time() - t0, 3)
    summary = {
        "query": query,
        "candidates_tried": len(candidates),
        "fetched": fetched,
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "new_domains_registered": new_domains_registered,
        "duration_sec": duration,
        "budget_exhausted": duration >= time_budget_s,
    }
    logger.info("on_demand.ensure_indexed(%r) → %s", query[:60], summary)
    return summary


# ─────────────────────────────────────────────────────────────────
# Fire-and-forget wrapper for chat
# ─────────────────────────────────────────────────────────────────

# Per-query lockout — don't trigger a second background crawl for the
# same query within this window. Prevents WhatsApp typing storms from
# fanning out 10 simultaneous crawls.
_BACKGROUND_LOCKOUT_SEC = 600.0  # 10 minutes
_recent_bg_queries: dict[str, float] = {}


def _background_lock_check(query: str) -> bool:
    """True if we should fire; False if a recent background crawl for
    this query is still inside the lockout window."""
    q = (query or "").strip().lower()
    if not q:
        return False
    now = time.time()
    last = _recent_bg_queries.get(q)
    if last is not None and (now - last) < _BACKGROUND_LOCKOUT_SEC:
        return False
    _recent_bg_queries[q] = now
    # Best-effort GC — keep the dict small.
    if len(_recent_bg_queries) > 1024:
        cutoff = now - _BACKGROUND_LOCKOUT_SEC
        for k in [k for k, t in _recent_bg_queries.items() if t < cutoff]:
            _recent_bg_queries.pop(k, None)
    return True


async def background_ensure(query: str,
                              time_budget_s: float = 25.0,
                              max_pages: int = 10) -> None:
    """Fire-and-forget wrapper. Never raises. Logs the outcome."""
    # R-F676 (2026-05-18) defensive double-check: refuse sentence /
    # topic queries even if a caller forgot to gate via
    # looks_like_entity_query. Production caller researcher.py:1481
    # already checks; this is belt-and-braces against future callers.
    if not looks_like_entity_query(query):
        logger.debug(
            "on_demand.background_ensure(%r): rejected — not entity-like",
            (query or "")[:60],
        )
        return
    if not _background_lock_check(query):
        logger.debug("on_demand.background_ensure(%r): lockout active",
                     query[:60])
        return
    try:
        summary = await ensure_indexed(
            query, time_budget_s=time_budget_s, max_pages=max_pages,
        )
        logger.info("background_ensure done: %s", summary)
    except Exception as e:
        logger.warning("background_ensure raised: %s", e)


# Heuristic: chat queries that look like entity research benefit
# from on-demand fill. Generic noun phrases ("how do I…", "what is…")
# don't — they'd waste the budget on guessed domains that don't exist.
_ENTITY_HINT_RX = re.compile(
    r"\b([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){1,4})\b"
)

# R-F676 (2026-05-18): words that signal a topic / sentence query
# rather than an entity name. Live evidence 2026-05-18 07:14-07:19
# showed autonomous-research queries like "Indonesia Philippines
# defence modernisation 2026" passing the old _ENTITY_HINT_RX
# regex (it matches any 2-word capitalised run anywhere), reaching
# `background_ensure`, and producing hallucinated URLs like
# philippines.com / dsca.com / news.co / article.org.
_SENTENCE_STOPWORDS = frozenset({
    # Generic English sentence connectors
    "the", "a", "an", "and", "or", "of", "to", "for", "by", "in", "on",
    "with", "from", "is", "are", "was", "were", "be", "been", "being",
    "as", "at", "but", "if", "then", "than", "this", "that", "these",
    "those", "may", "might", "could", "should", "would", "have", "has",
    "had", "do", "does", "did", "into", "over", "under", "about",
    # Research / topic phrasing
    "evidence", "summary", "article", "news", "report", "analysis",
    "modernisation", "modernization", "strategy", "spending",
    "acquisition", "tender", "notification", "establishment",
    "regional", "shift", "package", "export", "licence", "license",
    "used", "open", "source", "public", "records", "given",
    "recipients", "role", "partner", "lead", "contract", "next", "gen",
    "signal", "coordinated", "aircraft", "platforms", "consultancy",
    "security", "defence", "defense", "industry", "industries",
    "stakeholders", "tender", "review", "regulation", "regulations",
    "aggregating",
})


def looks_like_entity_query(query: str) -> bool:
    """True iff the WHOLE query reads like an entity NAME, not a
    sentence containing entity names.

    R-F676 (2026-05-18): the previous heuristic matched any 2-5
    consecutive capitalised words anywhere in the string. That made
    sentence queries like "Indonesia Philippines defence
    modernisation 2026" trigger background_ensure via the "Indonesia
    Philippines" prefix, which then produced hallucinated URLs.
    The tightened heuristic requires:
      1. ≤4 total words (entities are short)
      2. No sentence/topic stopwords ("evidence", "modernisation", ...)
      3. Most words are Capitalised (entity names are)
    Conservative: false negatives are fine (we just don't fire);
    false positives waste budget AND pollute the index.
    """
    if not query:
        return False
    raw_words = query.split()
    # Drop trailing 4-digit years ("2026") — common in autonomous
    # research queries but not part of an entity name.
    words = [w for w in raw_words if not (w.isdigit() and len(w) == 4)]
    if not (2 <= len(words) <= 4):
        return False
    # Reject if any word is a sentence/topic stopword.
    for w in words:
        if w.lower().strip(",.;:!?()'\"") in _SENTENCE_STOPWORDS:
            return False
    # Most words must start with an uppercase letter — entity names
    # almost always do. Allow one non-capitalised token (e.g., "de"
    # in "Banco de Brasil") by requiring cap_count >= len-1, with a
    # floor of 2 (two-token entities must have both capitalised).
    cap_count = sum(
        1 for w in words if w[:1].isalpha() and w[:1].isupper()
    )
    if cap_count < max(2, len(words) - 1):
        return False
    return True

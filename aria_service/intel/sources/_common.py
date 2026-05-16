"""Shared helpers for DD source adapters.

Each adapter uses these so we get consistent shape, timing, error
handling, and similarity scoring without rewriting the boilerplate.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aria.sources._common")


def now_iso() -> str:
    """ISO-8601 UTC timestamp — every adapter stamps its result."""
    return datetime.now(timezone.utc).isoformat()


def empty_result(source: str, query: dict, auth: str = "none", citation_url: str = "") -> dict:
    """Return the canonical empty-but-successful shape. Callers populate
    hits/hit_count as they go."""
    return {
        "source": source,
        "ok": True,
        "query": query,
        "hits": [],
        "hit_count": 0,
        "query_time_ms": 0,
        "fetched_at": now_iso(),
        "auth": auth,
        "error": None,
        "citation_url": citation_url,
    }


def error_result(
    source: str,
    query: dict,
    error: str,
    auth: str = "none",
    citation_url: str = "",
    started_at: float | None = None,
) -> dict:
    """Canonical error shape. `ok=False` signals the fetch failed; the
    orchestrator surfaces this as a data_gap, not a clean screen."""
    return {
        "source": source,
        "ok": False,
        "query": query,
        "hits": [],
        "hit_count": 0,
        "query_time_ms": int((time.time() - started_at) * 1000) if started_at else 0,
        "fetched_at": now_iso(),
        "auth": auth,
        "error": (error or "")[:300],
        "citation_url": citation_url,
    }


def finalise(result: dict, started_at: float) -> dict:
    """Fill in hit_count + query_time_ms once the adapter has assembled
    its hits list. Returns the same dict for chaining."""
    result["hit_count"] = len(result.get("hits") or [])
    result["query_time_ms"] = int((time.time() - started_at) * 1000)
    return result


# ── Name normalisation ─────────────────────────────────────────────────────
# Sanctions-list names are messy ("BAYKAR DEFENSE INDUSTRIES AND AEROSPACE
# TECHNOLOGIES JOINT-STOCK COMPANY LTD."), query names are short ("Baykar").
# Fuzzy matching needs both sides stripped of corporate suffixes and
# punctuation before comparison.

_SUFFIX_TOKENS = {
    # English
    "ltd", "limited", "llc", "inc", "incorporated", "corp", "corporation",
    "plc", "lp", "llp", "co", "company", "holdings", "group", "pvt",
    "private",
    # German
    "gmbh", "ag", "kg", "ohg", "se",
    # French
    "sa", "sas", "sarl", "eurl",
    # Italian / Spanish / Portuguese
    "srl", "spa", "sl", "sa", "lda", "ltda",
    # Nordic
    "oy", "ab", "as", "asa",
    # Slavic
    "spol", "sro", "zrt", "sp", "z", "o",
    # Turkish
    "as", "ltd", "sti",
    # Arabic-Latinised
    "llc", "co", "company",
    # Defence-industry specifics often appended
    "defense", "defence", "systems", "industries", "industrie",
    "technologies", "technology", "aerospace", "aviation",
}


def normalise_name(name: str) -> str:
    """Lowercase, strip punctuation, drop corporate suffixes. Used for
    fuzzy comparison and as a dedupe key within result sets."""
    if not name:
        return ""
    # Lowercase and collapse whitespace, strip most punctuation
    n = re.sub(r"[^\w\s&-]", " ", name.lower())
    n = re.sub(r"\s+", " ", n).strip()
    # Drop corporate suffix tokens
    tokens = [t for t in n.split() if t not in _SUFFIX_TOKENS and len(t) > 1]
    return " ".join(tokens)


def similarity(a: str, b: str) -> float:
    """Return 0.0-1.0 similarity after normalisation. Uses difflib's
    SequenceMatcher ratio — deterministic, no external deps, good enough
    for the narrow "fuzzy match a cleaned name" job we need.

    R-F569 (2026-05-16) — substring boost is now gated. Pre-R-F569 any
    a-in-b or b-in-a got at least 0.85 even when the shared substring
    was 2-3 chars. That made "ES" in "ES SECURITIES" boost an Embraer
    query to 0.78+, false-flagging a clean Brazilian aerospace giant.
    Guard: the shorter string must be ≥5 chars AND constitute ≥40% of
    the longer one. Otherwise we fall through to plain SequenceMatcher.
    """
    a_n = normalise_name(a)
    b_n = normalise_name(b)
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    # If one is a substring of the other, boost — but only if the
    # substring is long enough to be discriminating.
    if a_n in b_n or b_n in a_n:
        shorter = a_n if len(a_n) <= len(b_n) else b_n
        longer = b_n if shorter is a_n else a_n
        if len(shorter) >= 5 and len(shorter) / max(len(longer), 1) >= 0.4:
            return max(0.85, difflib.SequenceMatcher(None, a_n, b_n).ratio())
    return difflib.SequenceMatcher(None, a_n, b_n).ratio()


def fuzzy_filter(
    hits: list[dict],
    query_name: str,
    name_key: str = "name",
    threshold: float = 0.62,
    max_hits: int = 25,
) -> list[dict]:
    """Score each hit by name similarity and keep those above threshold.
    Adds a `_match_score` key to each surviving hit so the caller can
    sort or display the best matches first."""
    if not query_name or not hits:
        return []
    scored: list[tuple[float, dict]] = []
    for h in hits:
        cand = (h.get(name_key) or "").strip()
        if not cand:
            # Check alias fields if the primary is empty
            cand = (h.get("primary_name") or h.get("full_name") or "").strip()
        s = similarity(query_name, cand)
        # Also try any alias / a.k.a. fields
        aliases = h.get("aliases") or h.get("aka") or []
        if isinstance(aliases, list):
            for a in aliases:
                if isinstance(a, str):
                    s = max(s, similarity(query_name, a))
                elif isinstance(a, dict):
                    s = max(s, similarity(query_name, a.get("name") or ""))
        if s >= threshold:
            h["_match_score"] = round(s, 3)
            scored.append((s, h))
    scored.sort(key=lambda p: p[0], reverse=True)
    return [h for (_s, h) in scored[:max_hits]]


# ── Cached HTTP client helper ──────────────────────────────────────────────
# Every adapter needs the same pattern: short per-request timeout,
# user-agent header, retry once on connection error. This lives here so
# we don't copy-paste it 6 times.

_DEFAULT_TIMEOUT_S = 12.0
_DEFAULT_UA = "ARIA-DD-Orchestrator/1.0 (contact via /api/aria/health)"


async def http_get_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> Any:
    """Fetch a URL and parse JSON. Returns None on any error. Adapters
    that need finer control (auth headers, XML parsing, retries) build
    their own client — this is the happy-path helper."""
    import httpx

    h = {"User-Agent": _DEFAULT_UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url, params=params, headers=h)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.debug("http_get_json %s failed: %s", url, e)
        return None


async def http_get_text(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> str | None:
    """Fetch a URL and return text. For XML/CSV/HTML sources."""
    import httpx

    h = {"User-Agent": _DEFAULT_UA}
    if headers:
        h.update(headers)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url, params=params, headers=h)
            r.raise_for_status()
            return r.text
    except Exception as e:
        logger.debug("http_get_text %s failed: %s", url, e)
        return None

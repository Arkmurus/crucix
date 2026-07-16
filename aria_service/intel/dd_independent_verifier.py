"""R-F2669 — C-3 v2 independent-verification classifier.

Upgrades C-3 v1's conservative label-based corroboration (R-F2662) to a REAL
independent-origin model. A claim is independently corroborated only when >=2 of its
sources resolve to DISTINCT INDEPENDENT origins, where an origin is:

  - the underlying STORY (content level) when known — the DD re-fetch supplies a content
    fingerprint per source, so wire-syndicated republications of ONE story collapse to
    ONE origin (the case raw domain-family cannot catch);
  - else the publisher FAMILY (registrable domain + verified_intel.SOURCE_FAMILIES), so
    bbc.com/bbc.co.uk and uk.reuters.com/reuters.com collapse correctly;
  - ARIA's OWN compute / memory (ghost_scorer, network_walker, aria_knowledge, …) is
    'internal' and NEVER an independent witness.

This is the model behind the R-F2413 flag (`independent_source_verification_run`). It is
validated OFFLINE against the golden set; the flag flips ONLY when the eval shows
false_positive_rate == 0 AND recall improved, and ONLY after operator review. In LIVE
mode the re-fetch provides the per-source `story` fingerprint; WITHOUT it the classifier
falls back to publisher-family (which cannot detect random-republisher syndication) — so
the flag must never be set for a claim whose sources were not re-fetched.
"""

from __future__ import annotations

from typing import Any

# ARIA's OWN compute / memory / RAG — never an independent external witness.
_INTERNAL = frozenset({
    "aria_knowledge", "neural_memory", "memory", "rag", "internal",
    "ghost_scorer", "network_walker", "tech_classifier", "risk_indices", "press",
})
# Named distinct external authorities — each a genuinely independent origin.
_AUTHORITIES = frozenset({
    "companies_house", "sec_edgar", "gleif", "opencorporates",
    "transparency_intl_cpi", "basel_aml_index", "fatf", "worldbank_wgi", "oecd_crc",
})
# Registrable-domain suffixes that take 3 labels (best-effort; not a full public-suffix list).
_TWO_PART_SUFFIXES = frozenset({
    "co.uk", "com.au", "co.jp", "co.nz", "org.uk", "gov.uk", "ac.uk",
    "com.br", "co.za", "com.sg", "co.in", "com.tr",
})


def _is_internal(s: str) -> bool:
    return (
        s in _INTERNAL
        or s.startswith(("rag:", "neural", "aria_", "ghost", "network_", "tech_classifier"))
    )


def registrable_domain(host_or_url: str) -> str:
    """Best-effort registrable domain: strip scheme/path/www + subdomains.

    uk.reuters.com -> reuters.com ; www.bbc.co.uk -> bbc.co.uk ; theguardian.com -> theguardian.com
    """
    h = (host_or_url or "").strip().lower()
    if "://" in h:
        h = h.split("://", 1)[1]
    h = h.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if h.startswith("www."):
        h = h[4:]
    parts = [p for p in h.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in _TWO_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def publisher_family(host_or_url: str) -> str:
    """Registrable domain -> verified_intel.SOURCE_FAMILIES family, else the domain."""
    dom = registrable_domain(host_or_url)
    if not dom:
        return "external_unclassified"
    try:
        from .verified_intel import SOURCE_FAMILIES
        for fam, domains in SOURCE_FAMILIES.items():
            if dom in domains or any(registrable_domain(d) == dom for d in domains):
                return f"pub:{fam}"
    except Exception:
        pass
    return f"pub:{dom}"


def origin_key(source: Any) -> str:
    """Map a source to its INDEPENDENT-ORIGIN key. Same key => not independent.

    `source` may be a label string ("companies_house", "sanctions:ofac"), a domain/url
    string ("bbc.co.uk"), or a dict {"url"|"domain": ..., "story": <content fingerprint>}.
    """
    # A dict source is always an external re-fetchable location (never an internal label):
    # content-story fingerprint takes precedence, else publisher family.
    if isinstance(source, dict):
        story = (str(source.get("story") or "")).strip() or None
        if story:
            return f"story:{story}"  # one underlying story = one origin
        loc = str(source.get("url") or source.get("domain") or source.get("source") or "").strip().lower()
        return publisher_family(loc) if loc else "external_unclassified"
    s = str(source).strip().lower()
    # A domain/url is external — resolve to its publisher family. Do this BEFORE the
    # internal-LABEL check so a real domain like 'ghostblog.com' or 'network-news.com' is
    # never misread as ARIA's internal 'ghost_scorer'/'network_walker' compute.
    if "." in s or "/" in s:
        return publisher_family(s)
    if _is_internal(s):
        return "internal"
    if s.startswith("sanctions:") or s in _AUTHORITIES:
        return s
    return "external_unclassified"


def count_independent_origins(sources: list) -> int:
    keys = {origin_key(x) for x in (sources or [])}
    keys.discard("internal")
    return len(keys)


def is_independently_corroborated(sources: list, *, min_origins: int = 2) -> bool:
    """A claim is independently corroborated iff its sources span >= min_origins
    distinct independent origins (internal echo excluded)."""
    return count_independent_origins(sources) >= min_origins


# =============================================================================
# LIVE RE-FETCH — compute each cited source's CONTENT-STORY fingerprint so that
# wire-syndicated republications of ONE story collapse to ONE independent origin.
# This is the piece R-F2413 names ("re-fetch the cited sources") and the reason the
# offline eval used a golden `story` field: live, we compute it here.
# =============================================================================

def content_shingles(text: str, *, shingle: int = 5, min_words: int = 20) -> frozenset:
    """Word-shingle SET of an article body (for near-duplicate detection via Jaccard).

    Returns an empty set for too-little content — such a source cannot be fingerprinted
    and MUST NOT be treated as an independent origin (conservative: no over-count).
    """
    import re
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    if len(words) < max(shingle, min_words):
        return frozenset()
    return frozenset(" ".join(words[i:i + shingle]) for i in range(len(words) - shingle + 1))


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return (len(a & b) / union) if union else 0.0


def cluster_stories(url_shingles: dict, *, threshold: float = 0.6) -> dict:
    """Cluster URLs whose content is near-duplicate (Jaccard >= threshold) → same story id.

    Jaccard is robust to a site's differing header/footer (theunion barely grows), unlike
    an exact hash — so wire-syndicated republications of ONE story land in ONE cluster =
    ONE independent origin. URLs with empty shingles (too little content / failed fetch)
    get NO story id (excluded — never counted). Deterministic (insertion order).
    """
    stories: dict = {}
    reps: list = []  # (story_id, representative shingle set)
    next_id = 0
    for url, sh in url_shingles.items():
        if not sh:
            continue
        assigned = None
        for sid, rep in reps:
            if _jaccard(sh, rep) >= threshold:
                assigned = sid
                break
        if assigned is None:
            assigned = f"story_{next_id}"
            next_id += 1
            reps.append((assigned, sh))
        stories[url] = assigned
    return stories


async def refetch_story_ids(
    urls: list, *, deadline_s: float = 60.0, fetcher=None, threshold: float = 0.6
) -> dict:
    """Re-fetch each URL independently, shingle its content, and CLUSTER near-duplicates →
    {url: story_id | None}. Same story id => same underlying story => one independent
    origin. None means the re-fetch failed or the page had too little content (excluded —
    never counted, so it cannot create a false positive).

    Best-effort + bounded by deadline_s (re-fetching is slow — the caller runs this
    out-of-band). `fetcher(url) -> (status, text)` is injectable for testing; live it
    defaults to citation_audit._fetch_text.
    """
    import time as _t
    if fetcher is None:
        from .citation_audit import _fetch_text as fetcher  # noqa: N806
    url_shingles: dict = {}
    _start = _t.time()
    for url in list(dict.fromkeys(u for u in (urls or []) if u)):  # dedupe, keep order
        if (_t.time() - _start) >= deadline_s:
            break
        try:
            _status, _text = await fetcher(url)
            url_shingles[url] = content_shingles(_text)
        except Exception:
            url_shingles[url] = frozenset()
    stories = cluster_stories(url_shingles, threshold=threshold)
    return {u: stories.get(u) for u in url_shingles}  # None where content was too little


async def assess_independent_verification(
    report: dict, *, deadline_s: float = 60.0, fetcher=None
) -> dict:
    """LIVE report-level independent verification of the PRESS evidence (where wire
    syndication / echo actually happens): re-fetch each cited press URL, fingerprint its
    content, and count DISTINCT independent origins with same-story republications
    collapsed. Returns the metric + per-URL detail for the LIVE eval / operator review.

    SAFETY (FP-rate 0): a source counts as an independent origin ONLY when it was
    successfully re-fetched AND yielded a content fingerprint — a failed re-fetch is
    DROPPED, never counted, so an unverifiable source can never create a false positive.

    Does NOT set independent_source_verification_run — that flip stays operator-gated on
    a reviewed live eval.
    """
    dig = (report or {}).get("digital") or {}
    press = dig.get("press_coverage") or []
    items: list[dict] = []
    for p in press:
        url = p.get("url") if isinstance(p, dict) else getattr(p, "url", None)
        if url:
            items.append({"url": url})
    story_ids = await refetch_story_ids(
        [it["url"] for it in items], deadline_s=deadline_s, fetcher=fetcher,
    )
    verified_sources: list = []
    per_url: list[dict] = []
    for it in items:
        sid = story_ids.get(it["url"])
        if sid:  # only re-fetched-and-clustered sources count as an origin
            verified_sources.append({"domain": it["url"], "story": sid})
        per_url.append({
            "url": it["url"],
            "refetched": bool(sid),
            "origin": (origin_key({"domain": it["url"], "story": sid}) if sid else None),
        })
    origins = count_independent_origins(verified_sources)
    return {
        "press_items": len(items),
        "refetched_ok": sum(1 for v in story_ids.values() if v),
        "independent_press_origins": origins,
        "press_independently_corroborated": origins >= 2,
        "per_url": per_url,
    }


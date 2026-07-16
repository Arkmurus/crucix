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

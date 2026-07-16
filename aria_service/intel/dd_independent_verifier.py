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
        loc = str(source.get("url") or source.get("domain") or source.get("source") or "").strip().lower()
        # Defence-in-depth (Pass-2): a dict-wrapped internal label stays internal.
        if loc and "." not in loc and "/" not in loc and _is_internal(loc):
            return "internal"
        if story:
            return f"story:{story}"  # one underlying story = one origin
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


def independent_verify_mode() -> str:
    """R-F2671 — rollout gate for the C-3 v2 out-of-band re-fetch verification:
      'off'     (default) — do not re-fetch; independent_source_verification_run stays False.
      'measure' — re-fetch + surface the metric for the LIVE EVAL; flag stays False.
      'enforce' — re-fetch + surface + SET independent_source_verification_run=True.
    Operator flips measure→enforce ONLY after reviewing the measure-mode live data
    (R-F2413: the flag must be EARNED, never flipped blind).
    """
    import os
    v = (os.getenv("ARIA_DD_INDEPENDENT_VERIFY") or "").strip().lower()
    if v in ("enforce", "2"):
        return "enforce"
    if v in ("measure", "1", "on", "true", "yes"):
        return "measure"
    return "off"


def _source_facets(source: Any) -> tuple:
    """(publisher_key, story_key) for a source — the two independence axes.

    publisher_key is always set ('internal' marks exclusion; an authority/sanctions label
    is its own publisher; a domain resolves to its publisher family). story_key is the
    content-cluster id when known, else None. Used by the connected-components origin count.
    """
    if isinstance(source, dict):
        story = (str(source.get("story") or "")).strip() or None
        loc = str(source.get("url") or source.get("domain") or source.get("source") or "").strip().lower()
        # Defence-in-depth (Pass-2): an internal provenance label wrapped in a dict
        # ({"source": "aria_knowledge"}) must still be excluded, not read as a publisher.
        if loc and "." not in loc and "/" not in loc and _is_internal(loc):
            return "internal", None
        pub = publisher_family(loc) if loc else "external_unclassified"
        return pub, (f"story:{story}" if story else None)
    s = str(source).strip().lower()
    if "." in s or "/" in s:
        return publisher_family(s), None
    if _is_internal(s):
        return "internal", None
    if s.startswith("sanctions:") or s in _AUTHORITIES:
        return s, None
    return "external_unclassified", None


def count_independent_origins(sources: list) -> int:
    """R-F2677 — count distinct INDEPENDENT origins as connected components under the
    equivalence: two sources are the SAME origin if they share a PUBLISHER *or* a STORY.

      - same STORY, different publishers  → wire syndication / verbatim PR → ONE origin.
      - same PUBLISHER, different stories  → one editorial voice reporting N facts → ONE
        origin (a publisher is not N independent witnesses — R-F2677 live-eval fix; the old
        story-only count made 3 turdef.com articles read as 3 independent sources).
      - internal compute/memory is excluded (never an independent witness).

    Conservative by construction: the publisher∪story transitive merge can only REDUCE the
    count (bias to 'not corroborated' — the safe error for a DD tool), never inflate it.
    """
    facets = [f for f in (_source_facets(s) for s in (sources or [])) if f[0] != "internal"]
    n = len(facets)
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        parent[_find(a)] = _find(b)

    pub_seen: dict = {}
    story_seen: dict = {}
    for i, (pub, story) in enumerate(facets):
        if pub in pub_seen:
            _union(i, pub_seen[pub])
        else:
            pub_seen[pub] = i
        if story is not None:
            if story in story_seen:
                _union(i, story_seen[story])
            else:
                story_seen[story] = i
    return len({_find(i) for i in range(n)})


def is_independently_corroborated(sources: list, *, min_origins: int = 2) -> bool:
    """A claim is independently corroborated iff its sources span >= min_origins
    distinct independent origins (internal echo excluded)."""
    return count_independent_origins(sources) >= min_origins


# =============================================================================
# R-F2674 — SELF-SOURCE exclusion. The R-F2671 live eval found the model counting the
# SUBJECT's OWN websites as independent origins (Modirum: 4 of 5 counted origins were
# modirum*.com; Assan: its own site + assanpanel.com). A company's own site is NEVER an
# independent witness to claims about itself. With no authoritative "official domain" field
# in the report, recognise own-domains by NAME: distinctive tokens of the subject name,
# generic corporate/industry words dropped, matched against the domain's core label.
# =============================================================================

# Generic corporate / industry words that are NOT distinctive of a subject (dropped so a
# token like "defence" never makes every defence outlet look like the subject's own site).
_CORP_STOPWORDS = frozenset({
    "group", "holding", "holdings", "plc", "ltd", "limited", "llc", "inc", "incorporated",
    "corp", "corporation", "company", "sa", "srl", "gmbh", "bv", "nv", "spa", "oyj", "oy",
    "aps", "sarl", "pte", "pty", "defence", "defense", "systems", "system", "international",
    "global", "industries", "industry", "technologies", "technology", "solutions",
    "services", "service", "capital", "partners", "ventures", "the", "and", "for",
    # Publisher-root nouns: if a subject's ONLY distinctive token is one of these, a
    # prefix match would still collide with real outlets (e.g. subject "Times Group" →
    # timesofmalta.com; "Post Holdings" → postmedia.com). Dropping them biases to the SAFE
    # direction (miss a rare self-source rather than over-exclude a genuine third party —
    # a false "not corroborated" is the dangerous error for a DD tool). Pass-2 finding.
    "news", "times", "post", "press", "star", "sun", "mail", "media", "daily", "herald",
    "tribune", "journal", "wire", "gazette", "chronicle", "observer", "mirror", "record",
})


def subject_domain_tokens(entity_name: str, *, min_len: int = 4) -> frozenset:
    """Distinctive lowercased tokens of a subject entity name (generic corporate/industry
    words + short tokens dropped). "Modirum Gespi" -> {modirum, gespi}; "Assan Group" ->
    {assan}. Used to recognise the subject's OWN web domains as self-sources."""
    import re
    toks = re.findall(r"[a-z0-9]+", (entity_name or "").lower())
    return frozenset(t for t in toks if len(t) >= min_len and t not in _CORP_STOPWORDS)


def is_self_source(host_or_url: str, subject_tokens) -> bool:
    """True when the registrable domain is (very likely) the SUBJECT'S OWN property — its
    core label STARTS WITH a distinctive subject token. Self-sources are excluded from the
    independent-origin count: a company's own site cannot independently corroborate itself.

    PREFIX (not substring) match — Pass-2 fix: own-domains are name+descriptor and so START
    with the name (`modirumplatforms`, `assangroup`, `assanpanel`), whereas the dangerous
    over-exclusions were SUFFIX collisions (`washingtonpost`/`nypost` for subject token
    `post`, `foxnews` for `news`, `nytimes` for `times`) — prefix matching rejects those.
    Conservative: no subject tokens (unknown name) → False (never over-excludes).
    """
    if not subject_tokens:
        return False
    dom = registrable_domain(host_or_url)
    if not dom:
        return False
    core = dom.split(".", 1)[0]  # label before the registrable suffix, e.g. 'modirumplatforms'
    return any(core.startswith(t) for t in subject_tokens)


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
    # R-F2674 — subject name → distinctive tokens, so the subject's OWN domains are
    # recognised and excluded from the independent-origin count (self-sources).
    _tgt = (report or {}).get("target")
    _tgt = _tgt if isinstance(_tgt, dict) else {}
    _idn = (report or {}).get("identity")
    _idn = _idn if isinstance(_idn, dict) else {}
    subject_name = (
        (report or {}).get("entity")
        or _tgt.get("name") or _tgt.get("entity")
        or _idn.get("entity_name")
        or ""
    )
    if not isinstance(subject_name, str):
        subject_name = str(subject_name or "")
    subject_tokens = subject_domain_tokens(subject_name)
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
    self_excluded = 0
    for it in items:
        sid = story_ids.get(it["url"])
        self_src = is_self_source(it["url"], subject_tokens)
        # An origin counts ONLY if it re-fetched AND is not the subject's own site.
        counts = bool(sid) and not self_src
        if counts:
            verified_sources.append({"domain": it["url"], "story": sid})
        elif sid and self_src:
            self_excluded += 1
        per_url.append({
            "url": it["url"],
            "refetched": bool(sid),
            "self_source": self_src,
            "origin": (origin_key({"domain": it["url"], "story": sid}) if counts else None),
        })
    origins = count_independent_origins(verified_sources)
    return {
        "press_items": len(items),
        "refetched_ok": sum(1 for v in story_ids.values() if v),
        "self_sources_excluded": self_excluded,
        "subject_name": subject_name,
        "independent_press_origins": origins,
        "press_independently_corroborated": origins >= 2,
        "per_url": per_url,
    }


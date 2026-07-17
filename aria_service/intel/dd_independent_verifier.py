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


# =============================================================================
# R-F2687 — PR-ECHO detection (C-3 v3). The residual R-F2677 left open before
# `enforce`: a company press release REWORDED by >=2 trade outlets escapes the
# Jaccard-0.6 lexical gate (each outlet writes its own prose → shingles diverge)
# and counts as 2 INDEPENDENT origins. That is a fabrication: both are echoes of
# ONE origin — the subject's own announcement — so the report would claim
# "independently corroborated" for a claim nobody independently checked.
#
# WHY NOT PLAIN SEMANTIC SIMILARITY (the obvious answer, and a trap):
# embedding cosine measures TOPIC, not ORIGIN. Two genuinely INDEPENDENT
# investigations of the same event are also highly similar — clustering on
# cosine alone would collapse real corroboration and destroy recall (the very
# thing the C-3 eval measures). Semantics alone cannot separate "same story"
# from "same subject".
#
# THE SIGNAL THAT ACTUALLY SEPARATES THEM: journalists reword PROSE but copy
# QUOTES verbatim. A PR's quoted spokesperson line survives the rewrite intact
# in every outlet that ran it, while two independent reporters almost never
# produce identical >=8-word quoted spans. So:
#   - a shared verbatim QUOTE  → same origin (PR echo)   [primary, precise]
#   - high cosine AND a PR marker on BOTH sides → same origin  [secondary]
# Both signals only ever MERGE clusters → origins can only go DOWN → the
# "never over-claim independence" (FP-rate 0) property is preserved by
# construction. The cost is recall, which is why the secondary signal is
# deliberately narrow and why the thresholds below are stated as UNPROVEN
# until the C-3 measure-mode eval scores them on the golden set.
# =============================================================================

import re as _re

# Quoted spans. CRITICAL: match quote PAIRS with no length bound, then filter by
# length afterwards. A length-bounded class (["“”]([^"“”]{40,400})["“”]) has no
# open/close pairing: on `"short" ... "the real PR quote"` it fails at the short
# quote's opener, restarts at its CLOSER, captures the intervening PROSE as a
# "quote", and swallows the real quote's opening delimiter — so the PR echo is
# MISSED and the narrative prose is fingerprinted instead. Pairing straight quotes
# in sequence (1st-2nd, 3rd-4th) is what the alternation below does naturally.
_QUOTE_RE = _re.compile(r'"([^"]*)"|“([^”]*)”')

# "This came from an announcement, not from reporting." Deliberately narrow —
# these phrases are how outlets attribute PR-derived copy.
# NOTE the \s+ between words: real article bodies WRAP, so "according to a\nstatement"
# is the normal shape. A single-space pattern silently misses it (caught by a fixture
# that happened to wrap at exactly that point) — and a marker that never fires makes
# the whole secondary signal dead code.
_PR_MARKER_RE = _re.compile(
    r"press\s+release|in\s+a\s+statement|said\s+in\s+a\s+statement"
    r"|according\s+to\s+a\s+statement|announced\s+today|prnewswire|businesswire"
    r"|globenewswire|pr\s+newswire|company\s+statement|issued\s+a\s+statement",
    _re.I,
)

_MIN_QUOTE_WORDS = 8          # shorter spans collide by chance ("we are delighted")
_SEMANTIC_ECHO_THRESHOLD = 0.90  # measured — see the R-F2692 note below; do NOT lower

# R-F2692 — WHO is speaking decides whether a shared quote means a shared ORIGIN.
# R-F2687 treated ANY shared verbatim quote as a PR echo. The C-3 v3 eval (R-F2690)
# measured the cost: `independent_reports_sharing_one_official_quote` — Reuters and the
# Guardian BOTH quoting the same regulator's published decision verbatim — got merged,
# so genuine corroboration was LOST (fn=1, recall 0.857). A shared quote is NOT always a
# shared origin: two newsrooms independently reporting an AUTHORITY's statement are two
# real witnesses; two outlets reprinting the SUBJECT's spokesperson are one echo of a
# self-interested source. So attribute the quote instead of dropping the signal.
_AUTHORITY_RE = _re.compile(
    r"regulator|watchdog|authority|commission|tribunal|ombudsman|inspectorate"
    r"|ministry|prosecut|court|judge|police|central\s+bank|agency"
    r"|decision\s+notice|published\s+decision|court\s+filing|indictment",
    _re.I,
)
_SUBJECT_ROLE_RE = _re.compile(
    r"chief\s+executive|\bceo\b|\bcfo\b|\bcoo\b|chairman|chairwoman|chair\b"
    r"|\bboss\b|founder|managing\s+director|company\s+statement|the\s+company\s+said"
    r"|the\s+firm\s+said|spokesperson|spokesman|spokeswoman|a\s+company\s+representative",
    _re.I,
)


def quote_fingerprints(text: str, *, min_words: int = _MIN_QUOTE_WORDS) -> frozenset:
    """Normalised verbatim QUOTED spans in an article body.

    The PR-echo tell: outlets reword the surrounding prose but paste the quote
    verbatim. Spans shorter than `min_words` are dropped — boilerplate like
    "we are pleased to announce" would collide across unrelated articles and
    manufacture false echoes (i.e. would UNDER-count independence).
    """
    out = set()
    for m in _QUOTE_RE.finditer(text or ""):
        span = m.group(1) if m.group(1) is not None else (m.group(2) or "")
        words = _re.findall(r"[a-z0-9]+", span.lower())
        if len(words) >= min_words:
            out.add(" ".join(words))
    return frozenset(out)


def has_pr_marker(text: str) -> bool:
    """Does the body attribute itself to an announcement/statement (not reporting)?"""
    return bool(_PR_MARKER_RE.search(text or ""))


def shares_verbatim_quote(a: frozenset, b: frozenset) -> bool:
    """True when two articles carry the SAME quoted statement.

    NOTE (R-F2692): shared != same origin. Ask `quote_attribution` WHO said it before
    concluding echo — see `detect_pr_echo`. This helper is the set test only.
    """
    return bool(a and b and (a & b))


# Attribution verbs. The speaker sits immediately either side of one of these.
_ATTRIB_VERB = r"said|says|added|commented|told|noted|wrote|stated|announced|confirmed"
# `"Q," the regulator said in its decision.`  → speaker precedes the verb
_SPEAKER_BEFORE_VERB = _re.compile(
    r"^[\s,\"“”]*(?P<who>[^.,;:]{1,80}?)\s+(?:" + _ATTRIB_VERB + r")\b", _re.I
)
# `"Q," said Acme chief executive Jane Roe.`  → speaker follows the verb
_SPEAKER_AFTER_VERB = _re.compile(
    r"^[\s,\"“”]*(?:" + _ATTRIB_VERB + r")\s+(?P<who>[^.,;:]{1,80})", _re.I
)
# `Acme Corp said in a statement: "Q."`      → leading attribution, before the quote
_SPEAKER_LEADING = _re.compile(
    r"(?P<who>[^.!?;]{1,90}?)\s+(?:" + _ATTRIB_VERB + r")\b[^\"“”]{0,40}$", _re.I
)
# The speaker phrase ends where the sentence moves on to circumstance. Without this,
# `said chief executive Jane Doe after the competition regulator cleared the deal`
# swallows "regulator" and flips a subject quote to "authority".
_SPEAKER_STOP = _re.compile(
    r"\b(?:after|when|while|as|following|amid|during|before|because|since)\b", _re.I
)


def _speaker_phrase(after_raw: str, before_raw: str) -> str:
    """Extract WHO is credited with an adjacent quote. "" when not confidently found.

    Tries the trailing attribution first (`," the regulator said` / `," said the CEO`),
    then the leading one (`Acme said in a statement: "`). Returning "" (→ "unknown" →
    treated as an echo) is the SAFE default: it removes claimed independence rather
    than inventing it.
    """
    for rx in (_SPEAKER_BEFORE_VERB, _SPEAKER_AFTER_VERB):
        m = rx.match(after_raw or "")
        if m:
            who = m.group("who").strip()
            return _SPEAKER_STOP.split(who)[0].strip()
    m = _SPEAKER_LEADING.search(before_raw or "")
    if m:
        who = m.group("who").strip()
        return _SPEAKER_STOP.split(who)[0].strip()
    return ""


def quote_attribution(
    text: str, quote_norm: str, *, subject_tokens=frozenset(), window: int = 160
) -> str:
    """WHO is credited with `quote_norm` in `text`? → "authority" | "subject" | "unknown".

    R-F2692. Reads the attribution clause AROUND the quote — "…," the regulator said in
    its published decision' vs '…," said chief executive Marta Oliveira'. The clause that
    FOLLOWS the quote is checked first (that is where attribution conventionally sits);
    the preceding sentence is the fallback. Authority is checked before subject because a
    regulator also has a "spokesperson" — the authority reading must win that collision.
    """
    body = text or ""
    for m in _QUOTE_RE.finditer(body):
        span = m.group(1) if m.group(1) is not None else (m.group(2) or "")
        words = _re.findall(r"[a-z0-9]+", span.lower())
        if " ".join(words) != quote_norm:
            continue
        # Identify the SPEAKER, do not classify a region. Scanning a window/clause for
        # an authority word is unsound in both directions, and a Pass-2 review proved
        # it re-enabled the fabrication: in the common LEADING-attribution shape
        # (`Acme said in a statement: "Q." The regulator approved the deal.`) the
        # sentence terminator sits INSIDE the quote, so a `(?<=[.!?])\s` clip cannot
        # match at offset 0 and the whole NEXT sentence became the "attribution" —
        # reading "authority" and letting a real PR echo escape as 2 origins.
        # So: extract the speaker phrase around the attribution verb, and classify
        # ONLY that phrase.
        speaker = _speaker_phrase(
            body[m.end(): m.end() + window], body[max(0, m.start() - window): m.start()]
        )
        if not speaker:
            return "unknown"
        # AUTHORITY requires positive evidence that the authority IS the speaker.
        if _AUTHORITY_RE.search(speaker):
            return "authority"
        if _SUBJECT_ROLE_RE.search(speaker):
            return "subject"
        low = speaker.lower()
        if subject_tokens and any(t in low for t in subject_tokens):
            return "subject"
        return "unknown"
    return "unknown"


async def semantic_similarity(a_text: str, b_text: str) -> float | None:
    """Cosine similarity of two article bodies — DELEGATES to the existing helper.

    §6-native: ARIA's own local all-MiniLM-L6-v2 (no paid API, no new dependency).
    Reuses `consistency_suite._similarity`, which is the same function and already
    does it correctly: `to_thread(_safe_encode, ...)` honours the R-F530/R-F789
    process-wide encode lock and the R-F1890 offload, and it uses numpy for the
    cosine. Calling `embedder.encode(...)` directly here would re-introduce exactly
    the loop-stalling shape R-F703's regression test was written to kill
    (to_thread does NOT release the GIL — that is why the offload exists).

    Returns None only on error. NOTE the degradation contract: when the embedder is
    unavailable, `_similarity` falls back to token-overlap Jaccard — a LEXICAL proxy
    — so a reworded PR scores low and the SECONDARY signal effectively disappears,
    leaving the quote signal alone (i.e. pre-R-F2687 behaviour). That direction is
    over-counting independence, never under-counting, so a missing model can never
    manufacture a new echo — but it also cannot be claimed as coverage.
    """
    try:
        from .consistency_suite import _similarity
        return await _similarity(a_text[:2000], b_text[:2000])
    except Exception:
        return None


async def detect_pr_echo(
    a_text: str, b_text: str, *, subject_tokens=frozenset()
) -> tuple[bool, str]:
    """Are these two article bodies echoes of ONE origin (the subject's announcement)?

    Returns (is_echo, reason). Reason is recorded per-pair so the operator can
    audit WHY two sources collapsed — an unexplained collapse is indistinguishable
    from a bug (§22).
    """
    qa, qb = quote_fingerprints(a_text), quote_fingerprints(b_text)
    shared = qa & qb
    if shared:
        # R-F2692 — a shared quote is an ECHO only when the SUBJECT is the speaker.
        # If BOTH articles credit an independent AUTHORITY (regulator/court), they are
        # two newsrooms reporting the same official statement = two real witnesses, and
        # merging them destroys genuine corroboration (the measured fn=1 in C-3 v3).
        for q in sorted(shared):
            attr_a = quote_attribution(a_text, q, subject_tokens=subject_tokens)
            attr_b = quote_attribution(b_text, q, subject_tokens=subject_tokens)
            if attr_a == "authority" and attr_b == "authority":
                continue  # this quote is an authority's — keep looking
            # Subject-attributed, or unattributable: treat as an echo. UNKNOWN defaults
            # to echo deliberately — an unattributable shared quote is more likely
            # syndicated copy than two reporters producing an identical 8+ word span,
            # and this direction removes claimed independence (never invents it).
            return True, f"shared_quote_{attr_a}_{attr_b}"
        # every shared quote was an authority's → not an echo; fall through.
    # Secondary: reworded PR with no shared quote. Requires BOTH a very high
    # cosine AND a PR marker on BOTH sides — cosine alone would collapse two
    # independent investigations of the same event.
    if has_pr_marker(a_text) and has_pr_marker(b_text):
        sim = await semantic_similarity(a_text, b_text)
        if sim is not None and sim >= _SEMANTIC_ECHO_THRESHOLD:
            return True, f"pr_marker_and_semantic_{sim:.2f}"
    return False, ""


async def cluster_stories_with_echo(
    url_texts: dict,
    *,
    threshold: float = 0.6,
    deadline_s: float | None = None,
    subject_tokens=frozenset(),
) -> tuple[dict, list]:
    """Lexical clustering (R-F2669) PLUS the R-F2687 PR-echo merge.

    {url: text} → ({url: story_id}, [echo_detail]). Two URLs share a story when their
    content is a near-duplicate (Jaccard >= threshold — the wire-syndication case) OR
    when one is a PR echo of the other (the reworded case). Empty/failed content gets
    NO story id — excluded, never counted (unchanged from R-F2669).

    SINGLE-LINKAGE over ALL PAIRS via union-find — deliberately NOT the
    first-match-against-a-representative loop R-F2669 used. That shape is UNSAFE once
    echo edges exist, and a Pass-2 counterexample proved it: when an echo merge
    absorbs B into A, B never becomes a representative, so a later C that matches only
    B loses its link and FRAGMENTS into a new story. Jaccard-to-rep is not transitive,
    so merging one cluster could SPLIT two others and RAISE the origin count — flipping
    `press_independently_corroborated` False→True, the exact fabrication this detector
    exists to prevent.

    Union-find restores the safety property honestly: clustering is a graph, an origin
    is a connected component, and ADDING an edge can only MERGE components, never split
    one. So origins(lexical+echo) <= origins(lexical) always — the echo signal can only
    ever REMOVE claimed independence. That is what "conservative by construction" has to
    mean; the old loop only appeared to have it.

    `deadline_s` bounds the pairwise pass (echo detection can load the embedding model),
    so a slow model can never blow the caller's out-of-band budget. On timeout the
    remaining pairs are simply not compared → fewer merges → MORE origins, so a partial
    pass is reported honestly by `echo_pass_complete=False` rather than silently trusted.
    """
    import time as _t
    _start = _t.time()
    urls = [u for u, t in url_texts.items() if content_shingles(t)]
    shingles = {u: content_shingles(url_texts[u]) for u in urls}
    parent = {u: u for u in urls}

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    echoes: list = []
    complete = True
    for i in range(len(urls)):
        for j in range(i + 1, len(urls)):
            a, b = urls[i], urls[j]
            if _find(a) == _find(b):
                continue  # already one story — no need to pay for the comparison
            if _jaccard(shingles[a], shingles[b]) >= threshold:
                _union(a, b)
                continue
            if deadline_s is not None and (_t.time() - _start) >= deadline_s:
                complete = False
                break
            is_echo, why = await detect_pr_echo(
                url_texts[a], url_texts[b], subject_tokens=subject_tokens,
            )
            if is_echo:
                _union(a, b)
                echoes.append({"url": b, "echo_of": a, "reason": why})
        if not complete:
            break

    # Deterministic ids: components numbered by first-seen URL (insertion order).
    story_of: dict = {}
    stories: dict = {}
    for u in urls:
        root = _find(u)
        if root not in story_of:
            story_of[root] = f"story_{len(story_of)}"
        stories[u] = story_of[root]
    for e in echoes:
        e["story"] = stories.get(e["url"])
    if not complete:
        echoes.append({"echo_pass_complete": False})
    return stories, echoes


async def refetch_story_ids_detailed(
    urls: list,
    *,
    deadline_s: float = 60.0,
    fetcher=None,
    threshold: float = 0.6,
    detect_echo: bool = True,
    subject_tokens=frozenset(),
) -> tuple[dict, list]:
    """R-F2687 — as refetch_story_ids, but also returns the PR-echo audit trail.

    ({url: story_id | None}, [{url, story, reason}]). `detect_echo=False` restores the
    pure R-F2669 lexical behaviour (used to A/B the echo signal in the C-3 eval).
    """
    import time as _t
    if fetcher is None:
        from .citation_audit import _fetch_text as fetcher  # noqa: N806
    url_texts: dict = {}
    _start = _t.time()
    for url in list(dict.fromkeys(u for u in (urls or []) if u)):  # dedupe, keep order
        if (_t.time() - _start) >= deadline_s:
            break
        try:
            _status, _text = await fetcher(url)
            url_texts[url] = _text or ""
        except Exception:
            url_texts[url] = ""
    if detect_echo:
        # R-F2687 — the echo pass shares the caller's budget: deadline_s used to bound
        # ONLY the fetch loop above, so the clustering (which can trigger a ~32s cold
        # model load + O(n^2) pair encodes) ran unbounded past a deadline the caller
        # believed it had set. Hand it whatever is LEFT.
        _left = deadline_s - (_t.time() - _start)
        stories, echoes = await cluster_stories_with_echo(
            url_texts, threshold=threshold, deadline_s=max(_left, 0.0),
            subject_tokens=subject_tokens,
        )
    else:
        stories = cluster_stories(
            {u: content_shingles(t) for u, t in url_texts.items()}, threshold=threshold
        )
        echoes = []
    return {u: stories.get(u) for u in url_texts}, echoes


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

    R-F2687: now also collapses PR echoes (reworded press releases), not just lexical
    near-duplicates. Thin wrapper over refetch_story_ids_detailed — kept for the
    existing callers that do not need the echo audit trail.
    """
    ids, _echoes = await refetch_story_ids_detailed(
        urls, deadline_s=deadline_s, fetcher=fetcher, threshold=threshold,
    )
    return ids


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
    # R-F2687 — echo-aware clustering + the audit trail of WHY sources collapsed.
    # R-F2692 — hand the detector the subject tokens (already derived above for the
    # R-F2674 self-source check) so it can tell "the SUBJECT's spokesperson said" (echo)
    # from "the REGULATOR said" (two independent witnesses).
    story_ids, pr_echoes = await refetch_story_ids_detailed(
        [it["url"] for it in items], deadline_s=deadline_s, fetcher=fetcher,
        subject_tokens=subject_tokens,
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
        # R-F2687 — every PR-echo collapse, with its reason. An unexplained drop in
        # origins is indistinguishable from a bug; this is the operator's audit trail
        # and the C-3 measure-mode eval's input for scoring the echo signal.
        "pr_echoes": pr_echoes,
        "pr_echoes_collapsed": len(pr_echoes),
    }


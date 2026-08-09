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
from ..intel.engine_wiring import wire_success, wire_failure  # R-F2489 §21a success+failure

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


# R-F687 (2026-05-18) — common-noun labels that match the
# auto-registered hallucination pattern from the pre-R-F676 era.
# Live fly logs 2026-05-18 10:23-10:37 showed the daily crawler
# alphabetically polling these domains and the web_atlas brain_hook
# absorbing each as intel, tripping the brain_hook breaker (3500ms
# threshold) repeatedly: 10:32:32 (p95=5573ms), 10:33:47 (p95=4560ms),
# 10:37:46 (p95=5195ms). Each trip = ~70s of degraded absorb for real
# intel arriving in that window.
#
# Curation rule: defence-industry / intel queries virtually NEVER
# produce these single-word labels as legitimate entity names. Real
# defence orgs are compound ("baykar", "modirumgespi", "leonardo",
# "embraer", "lockheedmartin") or acronyms with specific meaning
# ("baykar", "drdo", "hal", "sami"). Generic English nouns and
# adjectives that landed in the registry came from the pre-R-F676
# head-only / second-only shape generator processing sentence queries.
_GARBAGE_COMMON_NOUN_LABELS = frozenset({
    # From live fly logs 2026-05-18 (alphabetical pollution batches):
    "application", "applications", "approval", "arabia", "arabian",
    "armes", "armoured", "article", "articles", "articlenews",
    "august", "billion", "boycott", "britain", "broil", "cancellation",
    "capital", "chapter", "cia", "cisco", "com", "competitive",
    "competitiveintelligence", "compliance", "compliancerequirements",
    "conditionality", "contain", "contract", "counter-intelligence",
    "cve", "dedicated", "deeply", "defence", "defense", "directors",
    "dispute", "drone", "dynamics", "east", "email", "embedded",
    # R-F689 (2026-05-18) expansion — labels observed in fly logs
    # 10:35-10:50 alphabetical sweep that R-F687 didn't catch.
    # All single-word common-noun labels at tier-4 + sector=discovered.
    "establishment", "european", "executive", "expedite", "export",
    "failed", "financial", "finnish", "fire", "fiscal",
    "fujimoto", "funding", "further", "general", "generic",
    "geopolitical", "government", "hypothesis",
    "ieee", "india", "indo", "informa", "intelligence",
    "interpols", "japan", "key", "killing", "korea",
    # R-F691 (2026-05-18) further expansion — fly logs 10:50-10:53:
    "lacks", "layer", "likely", "mhi", "microsoft",
    "middle", "middleeast", "military", "million", "model",
    # Bare top-level English nouns/adjectives commonly hallucinated:
    "news", "article", "south", "north", "west",
    "philippines", "indonesia", "koreas", "fms", "dsca", "ecju",
    "quakers",
})


# R-F692 (2026-05-18) — algorithmic detection of auto-registered
# garbage labels, replacing whack-a-mole blocklist expansion.
#
# Live fly logs 2026-05-18 10:23-10:53 showed every alphabetical batch
# revealing new single-word common-noun labels (R-F687: 24 labels,
# R-F689: 27, R-F691: 10 — and counting). The pre-R-F676 hallucination
# pollution left HUNDREDS of such rows in the registry. Reactive
# blocklist expansion keeps the breaker tripping until the next labels
# arrive.
#
# Pattern check: ANY label matching ^[a-z]{3,10}$ (lowercase ASCII, no
# hyphens, no digits, 3-10 chars) at tier-4 + sector=discovered is
# almost certainly the head-only/second-only hallucination shape.
# Real defence-industry compound labels are either ≥11 chars
# (rheinmetall, lockheedmartin, defencenews, modirumgespi) or contain
# hyphens / digits / subdomains (baykar.com.tr, kongsberg-defence.no).
#
# Allow-list below catches the SHORT legitimate single-word brand
# names that match the regex but should NEVER be filtered.
_LEGITIMATE_SINGLE_WORD_ENTITY_LABELS = frozenset({
    # Defence OEMs (Tier 1 / Tier 2 primes + major subsystem suppliers)
    "baykar",     # Turkish drone OEM
    "embraer",    # Brazilian aerospace
    "leonardo",   # Italian conglomerate (Helicopters / Electronics)
    "lockheed",   # US prime (label only — full is "lockheedmartin")
    "raytheon",   # US prime (RTX legacy brand)
    "northrop",   # US prime
    "boeing",     # US prime
    "airbus",     # European prime
    "thales",     # French electronics/defence
    "saab",       # Swedish aerospace
    "dassault",   # French aerospace
    "qinetiq",    # UK defence sci/tech
    "nammo",      # Norwegian munitions
    "patria",     # Finnish defence
    "otokar",     # Turkish armoured vehicles
    "aselsan",    # Turkish electronics
    "roketsan",   # Turkish missiles
    "havelsan",   # Turkish C4I
    "nexter",     # French land systems
    "hanwha",     # Korean defence
    "hyundai",    # Korean conglomerate (also defence)
    "doosan",     # Korean machinery / defence
    "modirum",    # operator-curated example (Lusophone integration)
    "fnss",       # Turkish armoured (acronym)
    "drdo",       # Indian DRDO (acronym)
    "iai",        # Israel Aerospace Industries (acronym)
    "kai",        # Korea Aerospace Industries (acronym)
    # Research / strategic studies / intel hubs (single-word labels)
    "arxiv",      # preprint archive
    "rusi",       # UK Royal United Services Institute
    "sipri",      # Stockholm International Peace Research Institute
    "iiss",       # Int. Institute for Strategic Studies
    "csis",       # Center for Strategic & International Studies
    "rand",       # RAND Corporation
    "uscc",       # US-China Economic & Security Review Commission
    # Useful generic-but-legit sources
    "janes",      # Janes (defence intelligence)
    "wapo",       # Washington Post (acronym sometimes used)
    "nikkei",     # Japanese business / Asia
    "reuters",    # global wire
})

# Pattern: lowercase ASCII letters only, 3-8 chars, no hyphen/digit.
# This is the shape head-only/second-only hallucination produces for
# single English common nouns. The 8-char upper bound is calibrated
# to avoid catching legitimate compound labels — `defenceweb` (10),
# `executivegov` (12), `armyrecognition` (15), `eastafricanpolicyobserver`
# (24) all pass through. Specific 9-12 char garbage nouns
# (`executive`, `intelligence`, etc.) still get caught by the
# explicit blocklist above.
_GARBAGE_LABEL_RX = re.compile(r"^[a-z]{3,8}$")

# R-F717 (2026-05-19) — categorical garbage: domain labels that can
# NEVER be a legitimate entity name regardless of tier or sector.
# Live fly logs 2026-05-19 08:52:41-08:52:57 showed 2026.co / 2026.com
# / 2026.net / 2026.org / 283.co / 23227526.fs1.hubspotusercontent...
# all being re-crawled every sweep. R-F687's _GARBAGE_LABEL_RX requires
# lowercase letters → pure-digit labels slip past. Pure-digit first
# labels (years like 2026, IDs like 283, tracking codes like 23227526)
# are never a real entity — apply this filter ahead of the tier check.
_DIGIT_ONLY_LABEL_RX = re.compile(r"^[0-9]+$")

# R-F698 (2026-05-18) — hyphenated common-noun compound garbage filter.
# Live fly logs 2026-05-18 17:59:40 showed `competitive-intelligence.com`
# slipping past R-F687/F692 (regex rejected hyphens; explicit blocklist
# only had `counter-intelligence`). Tripped web_atlas breaker (p95=5533ms)
# → PR04 cascade at 18:00:43.
#
# Algorithm: split on `-`. Hyphenated label is garbage iff EVERY
# token is itself either (a) in the explicit blocklist OR (b) matches
# the single-word garbage shape AND not in allow-list. ANY token
# being a known legitimate brand (allow-list) immediately rescues
# the whole compound — `baykar-savunma`, `thales-uk` etc. pass.
_HYPHEN_TOKEN_RX = re.compile(r"^[a-z]{3,}$")


def _looks_like_garbage_label_algorithmic(label: str) -> bool:
    """R-F692 + R-F698 — return True if `label` matches the
    auto-registered hallucination pattern AND is not on the
    legitimate-entity allow-list. Caller guarantees `label` is
    lowercase already.

    Two pattern shapes:
      - R-F692: single word, 3-8 lowercase ASCII letters
      - R-F698: hyphen-joined compound where every token is either
                a known garbage common-noun (explicit blocklist) OR
                matches the short single-word shape — and NO token
                is on the legitimate-brand allow-list.
    """
    # R-F692 single-word path
    if _GARBAGE_LABEL_RX.match(label):
        return label not in _LEGITIMATE_SINGLE_WORD_ENTITY_LABELS
    # R-F698 hyphenated path — CONSERVATIVE: every token must be
    # in the EXPLICIT blocklist. Pure shape-match would false-positive
    # on legitimate thinktanks like `east-african-policy.org` (token
    # `east` is blocklisted but `african` + `policy` are not).
    if "-" in label:
        # Whole-label allow-list (e.g. operator-trusted compounds)
        if label in _LEGITIMATE_SINGLE_WORD_ENTITY_LABELS:
            return False
        tokens = label.split("-")
        # Need at least 2 non-empty tokens, all lowercase ASCII
        if len(tokens) < 2 or any(not _HYPHEN_TOKEN_RX.match(t) for t in tokens):
            return False
        # ANY token on the legit-brand allow-list rescues the whole label
        # (`baykar-savunma`, `thales-uk`, `modirum-systems` etc.)
        if any(t in _LEGITIMATE_SINGLE_WORD_ENTITY_LABELS for t in tokens):
            return False
        # ALL tokens must be in the explicit blocklist for flag.
        # Catches `competitive-intelligence` (both blocklisted),
        # `general-dynamics` (both blocklisted), `defence-news` (both
        # blocklisted) — without false-positiving on legit compounds
        # where only some tokens look common-noun-ish.
        return all(t in _GARBAGE_COMMON_NOUN_LABELS for t in tokens)
    return False


def is_auto_registered_garbage(domain_row: dict) -> bool:
    """Return True if a domain row matches the auto-registered
    hallucination pattern that R-F676 closed upstream but left behind
    in the registry.

    R-F687 conditions still apply (conservative — false positives waste
    crawl budget AND pollute corpus, false negatives are fine):
      1. tier == 4 (auto-registered, not operator-curated)
      2. sector == "discovered" (auto-register marker)

    R-F692 (2026-05-18) added a SECOND match path on top of the
    explicit blocklist (R-F687/689/691):

      EITHER: leftmost label is in `_GARBAGE_COMMON_NOUN_LABELS`
        (operator-curated hard block — wins regardless of allow-list)
      OR    : label matches the algorithmic garbage pattern AND is
        NOT on `_LEGITIMATE_SINGLE_WORD_ENTITY_LABELS` (the
        catch-all that stopped the whack-a-mole expansion at 60+
        labels by 10:53)

    Operator-curated tier-1/2/3 domains are NEVER filtered, even if
    their label matches — a `cisco.com` entry at tier-1 survives
    (only the auto-register path lands at tier-4).
    """
    if not domain_row:
        return False
    domain = (domain_row.get("domain") or "").strip().lower()
    if not domain or "." not in domain:
        return False
    label = domain.split(".", 1)[0]
    # R-F717 (2026-05-19) — categorical garbage: pure-digit first label
    # is never a legitimate entity. Fire BEFORE the tier check because
    # `2026.co` / `283.com` / `23227526.fs1.hubspot...` can never be a
    # real operator-curated source regardless of tier/sector metadata.
    if _DIGIT_ONLY_LABEL_RX.match(label):
        return True
    if domain_row.get("tier") != 4:
        return False
    if (domain_row.get("sector") or "") != "discovered":
        return False
    # Explicit blocklist — operator-curated, hard block.
    if label in _GARBAGE_COMMON_NOUN_LABELS:
        return True
    # R-F692 algorithmic catch — single-word common-noun pattern,
    # allow-list overrides for legitimate short brand names.
    return _looks_like_garbage_label_algorithmic(label)


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


def _registration_is_on_mission(domain: str, evidence: str | None) -> tuple[bool, str]:
    """R-F3820 — may this domain earn a PERMANENT registry row?

    Returns (ok, reason). The reason is always populated on a refusal so a drop can
    be explained rather than asserted (§22).

    WHY RELEVANCE AND NOT A BLOCKLIST. ARIA was crawling porn (live, 2026-08-09:
    `GET https://jerk-porn.com/` as ARIA-Intel/1.0), and the pages reached the brain
    as `reading_region:market_intel:lusophone` facts grading Phase A gate #2. The
    obvious fix — an adult/gambling word blocklist — was measured against ARIA's own
    data first and flagged `internationaldefenceanalysis.com`, `stockanalysis.com`
    ("anal" inside ANALysis), `repository.essex.ac.uk` ("sex" inside esSEX), "The
    Defense Post", "ASPI Strategist" and the German word "Fersensporn" (heel spur).
    Those are core defence sources. A blocklist is also unbounded: every new porn
    farm is a new string.

    Asking "is this on mission" instead excludes porn, gambling, Amazon and consumer
    noise BY CONSTRUCTION, because none of them are ever about defence, and it needs
    no maintenance as the spam changes.

    The judge is `news_monitor._topical_relevance`, which already exists and is
    already calibrated for exactly this question ("fit for a security / defence /
    procurement / compliance feed"), rather than a second, divergent classifier —
    §1 records what happens when one measure gets forked in two.

    FAILS CLOSED, including on absent evidence. §7 forbids eviction, so a wrongly
    ADMITTED domain is permanent while a wrongly REJECTED one is simply re-registered
    the next time it appears with better evidence. Those costs are not symmetric, so
    forgetting to pass evidence must DENY — otherwise the gate is bypassable by
    omission, which is precisely how the original hole worked.
    """
    text = (evidence or "").strip()
    if not text:
        return False, "no_evidence"
    try:
        from ..intel.news_monitor import _topical_relevance
    except Exception as exc:                      # pragma: no cover - import guard
        # Cannot judge → cannot admit. An unavailable judge must not become a pass;
        # that is the "absence read as a measurement" shape §1 keeps recording.
        return False, f"judge_unavailable:{type(exc).__name__}"
    try:
        verdict = _topical_relevance({"title": text, "summary": "",
                                      "category": "", "topics": []})
    except Exception as exc:                      # pragma: no cover
        return False, f"judge_error:{type(exc).__name__}"
    if verdict.get("on_topic"):
        return True, f"on_topic:{verdict.get('score')}"
    return False, f"off_mission:{verdict.get('reason') or 'no_domain_terms'}"


async def auto_register_domain(
    domain: str, *, evidence: str = "", requested_entity: str = "",
    tier: int = 4, sector: str = "discovered",
    rate_limit_per_sec: float = 0.5,
) -> bool:
    """Register a discovered domain at tier 4 if we don't know it yet.

    Returns True iff a new row was created. Idempotent — repeated calls
    for the same domain are no-ops.

    R-F3820 — a domain must be JUSTIFIED, and there are exactly two justifications:

    `evidence`  — the text of an unsolicited search result (title+snippet). Judged on
                  topical relevance, because nobody asked for this domain; it merely
                  appeared in a SERP. This is the path that admitted 163 adult and 41
                  gambling domains.
    `requested_entity` — ARIA was ASKED to research this entity and derived the
                  candidate URL from its NAME (`guess_entity_urls`). Justified by
                  INTENT, not by lexicon.

    THE SECOND ONE EXISTS BECAUSE THE FIRST WOULD BREAK DUE DILIGENCE. Measured:
    `Rheinmetall AG` scores 0.65 and `BAE Systems plc` 0.25, but `Acme Ventures Ltd`,
    `Gazprom` and `Modirum Gespi` all score ZERO — an arbitrary counterparty's name
    contains no defence vocabulary, by construction. DD is precisely the business of
    investigating names nobody has heard of, so relevance-gating that path would
    disable the product's primary function while looking like a safety improvement.

    The distinction is provenance, and it is the honest one: the porn arrived as
    UNSOLICITED SERP domains, never as a requested entity. `guess_entity_urls` derives
    candidates from the query string itself, so this path cannot admit `jerk-porn.com`
    unless somebody explicitly asks ARIA to research it.

    Neither supplied → REFUSED. See `_registration_is_on_mission` for why deny is the
    default when nothing justifies the row.
    """
    if not _safe_domain_for_register(domain):
        return False
    if (requested_entity or "").strip():
        on_mission, why = True, f"entity_request:{requested_entity.strip()[:60]}"
    else:
        on_mission, why = _registration_is_on_mission(domain, evidence)
    if not on_mission:
        # §21a — a refusal is a real decision and must not be silent, or the registry
        # simply appears to stop growing with no way to tell a working gate from a
        # broken crawler.
        logger.info("[R-F3820] registration refused: %s (%s)", domain, why)
        try:
            from ..intel.engine_wiring import wire_failure
            wire_failure(module="crawler.on_demand",
                         detail=f"domain registration refused: {domain} ({why})",
                         gap_type="source_validator_rejected",
                         source="on_demand:auto_register_domain")
        except Exception:      # pragma: no cover - observability never blocks
            pass
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
                # R-F3820 — justified by INTENT, not by lexicon. These candidates come
                # from `guess_entity_urls(query)`, i.e. an entity ARIA was asked to
                # research, and the URL is derived from the NAME. Relevance-gating this
                # path would break due diligence outright: measured, `Acme Ventures
                # Ltd`, `Gazprom` and `Modirum Gespi` all score zero on the defence
                # lexicon, because an unknown counterparty's name is exactly that.
                created = await auto_register_domain(d, requested_entity=query)
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
    # R-F2489 §21a — both branches reach the brain: a fill that indexed 0 pages
    # while hitting errors is a soft-failure worth a gap; anything else is success.
    if indexed == 0 and errors > 0:
        wire_failure(module="crawler_on_demand",
                     detail=f"ensure_indexed({query[:80]!r}) indexed 0 with {errors} errors",
                     gap_type="engine_failure", source="crawler_on_demand")
    else:
        wire_success(module="crawler_on_demand",
                     summary=f"ensure_indexed({query[:40]!r}) indexed {indexed}")
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
        # R-F2489 §21a — background crawl failure (was log-only/dark) → brain.
        wire_failure(module="crawler_on_demand",
                     detail=f"background_ensure({query[:80]!r}) raised: {e}",
                     gap_type="engine_failure", source="crawler_on_demand")


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

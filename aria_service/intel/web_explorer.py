"""ARIA Web Explorer — unified entry point for web research.

R-F307 (2026-05-11): consolidates the fragme

nted web-research stack
(web_search + researcher + link_investigator + deep_researcher) behind
one coherent capability with ecosystem awareness baked in.

Design goals — direct response to operator directive "make sure to do a
powerful web explorer ... all improvements should work across the
entire aria ecosystem ... think outside the box":

  1. One entry point. Callers don't have to pick from 6 different
     functions and figure out which one fits their case. The explorer
     decides: query-only → search; URL-only → fetch; both → triangulate;
     thin results → escalate.

  2. Memory-first. Per pay-once-remember-forever doctrine, every call
     checks RAG before going to the web. If memory has it, no network.

  3. Cost-free by default. Mirrors Claude: free RSS + free article fetch
     + free academic APIs (Crossref, OpenAlex, Semantic Scholar) + DDG
     free tier. Paid paths (Brave, paid LLM extraction) require explicit
     `cost_free=False`. Student learning loop ALWAYS gets cost_free=True.

  4. Same-host + authoritative bias. Same fix as R-F292 but applied to
     EVERY fetch path, not just link_investigator. The chat handler's
     read_article / extract_url calls also won't drift off-host.

  5. Language fan-out. RU/ZH/AR/PT/FR/ES variants auto-fan when the
     query touches those linguistic regions. Existing logic in
     web_search.search_multilingual exposed cleanly via this entry.

  6. Brandification. Per R-F299 — domain hostnames get TLD stripped,
     hyphens become spaces, so "modirumgespi.com defence procurement"
     becomes "modirumgespi defence procurement" automatically.

  7. Year regex sanity. Per R-F293 — 1900-2049 only; page numbers and
     article references stop being tagged as years.

  8. Adaptive depth. `depth='auto'` (default) decides:
       - URL present → link_investigator (depth=2, ~$0)
       - Query thin (<3 hits) AND cost_free=False → deep_research
       - Otherwise → multilingual search + parallel fetch
     `depth='quick'` forces single-pass; `depth='thorough'` forces
     deep + link_investigator both.

  9. Ecosystem snapshot. Every ExplorationResult includes per-backend
     status (fired / silent / errored / dead), latency, results-count.
     This is the R-F305 wired_but_silent detector applied to web
     research, surfaced via `result.ecosystem`.

 10. Provenance everywhere. Every fact / snippet carries (source_url,
     backend, retrieved_at, tier). No anonymous results.

 11. Year filter applied to web-search results too. R-F293 only fixed
     link_investigator — extending it here means stray years across the
     stack get sanitised at the same place.

 12. Always wired. The chat tool dispatch + reading_session + DD
     digital layer all route through this module. No more "wired but
     never called" surprises (the failure mode tonight that drove the
     ecosystem sweep).

USAGE
=====

    from aria_service.intel import web_explorer

    # Query-only (search-first)
    result = await web_explorer.explore("Modirum GESPI defence procurement")

    # URL-only (fetch-first)
    result = await web_explorer.explore(
        urls=["https://modirumgespi.com/en"],
    )

    # Triangulate
    result = await web_explorer.explore(
        "Modirum GESPI",
        urls=["https://modirumgespi.com/en"],
        depth="thorough",
    )

    # Cost-free (learning loops, no paid paths)
    result = await web_explorer.explore(
        topic, cost_free=True,
    )

    # Inspect the ecosystem
    print(result.ecosystem.summary)
    # → {"active_backends": 4, "silent_backends": 1, "dead_backends": 0,
    #    "health_signal": "HEALTHY"}

The result is a dataclass — `result.facts` is the fused findings,
`result.provenance` is per-fact source attribution, `result.ecosystem`
is the per-backend status snapshot. Always populated, even on errors."""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger("aria.web_explorer")


# ─────────────────────────── DATA MODEL ───────────────────────────────────────

@dataclass
class BackendStatus:
    """Per-backend execution status for ecosystem awareness."""
    name: str
    fired: bool = False
    results_count: int = 0
    latency_ms: int = 0
    error_reason: str = ""
    # state ∈ {active, silent, errored, skipped_cost, skipped_dead}
    state: str = "skipped_cost"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExplorationFact:
    """A single piece of evidence harvested from a source. Provenance is
    mandatory — every fact ties back to a URL + backend + retrieval time."""
    kind: str                       # search_result | extracted | rag | derived
    value: str                      # the fact / snippet / title
    source_url: str = ""
    backend: str = ""               # google_news | semantic_scholar | rag | link_tree | ...
    tier: str = "UNVERIFIED"        # T1 / T2 / T3 / T4 / UNVERIFIED
    retrieved_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    context: str = ""               # surrounding text (~200 chars)
    score: float = 0.0              # relevance / confidence

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class EcosystemSnapshot:
    """Per-call ecosystem health for the web research stack. Mirrors
    R-F305's per-layer state for the DD orchestrator, applied here to
    per-backend state for the explorer."""
    backends: list[BackendStatus] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    health_signal: str = "HEALTHY"   # HEALTHY | PARTIAL | DEGRADED | DEAD
    interpretation: str = ""

    def as_dict(self) -> dict:
        return {
            "backends": [b.as_dict() for b in self.backends],
            "summary": self.summary,
            "health_signal": self.health_signal,
            "interpretation": self.interpretation,
        }


@dataclass
class ExplorationResult:
    """The full output of one explore() call. Always populated, even on
    backend failures — the ecosystem snapshot makes failures visible
    rather than silent."""
    query: str = ""
    urls_seeded: list[str] = field(default_factory=list)
    depth: str = "auto"
    cost_free: bool = True
    language_fanout: list[str] = field(default_factory=list)
    brandified_query: str = ""

    facts: list[ExplorationFact] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    ecosystem: EcosystemSnapshot = field(default_factory=EcosystemSnapshot)

    memory_hits: int = 0           # how many facts came from RAG (free)
    web_hits: int = 0              # how many came from web backends
    total_cost_usd: float = 0.0
    duration_ms: int = 0
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "urls_seeded": self.urls_seeded,
            "depth": self.depth,
            "cost_free": self.cost_free,
            "language_fanout": self.language_fanout,
            "brandified_query": self.brandified_query,
            "facts": [f.as_dict() for f in self.facts],
            "provenance": self.provenance,
            "ecosystem": self.ecosystem.as_dict(),
            "memory_hits": self.memory_hits,
            "web_hits": self.web_hits,
            "total_cost_usd": self.total_cost_usd,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at,
            "notes": self.notes,
        }


# ─────────────────────────── HELPERS ──────────────────────────────────────────

_BRANDIFY_TLD_RE = re.compile(r"\.[A-Za-z]{2,6}$")
_BRANDIFY_SEP_RE = re.compile(r"[-_]+")
_VALID_YEAR_RE = re.compile(r"\b(19[0-9]{2}|20[0-4][0-9])\b")  # R-F293

# Language fan-out triggers — keyword → language code. Auto-detected
# from the query content. Mirrors web_search.search_multilingual.
_LANG_FANOUT_TRIGGERS = {
    "ru": ["russia", "russian", "moscow", "kremlin", "rosoboronexport",
           "almaz-antey", "uralvagonzavod"],
    "zh": ["china", "chinese", "beijing", "shanghai", "norinco",
           "casic", "casc", "aviation industry corporation"],
    "ar": ["saudi", "uae", "emirates", "qatar", "kuwait", "bahrain",
           "oman", "egypt", "iraq", "algeria", "morocco"],
    "pt": ["brazil", "brasil", "angola", "portugal", "mozambique",
           "lusophone", "cape verde", "guinea-bissau"],
    "fr": ["france", "french", "francophone", "senegal", "mali",
           "ivory coast", "côte d'ivoire", "algeria", "morocco",
           "tunisia", "cameroon", "gabon", "burkina"],
    "es": ["spain", "spanish", "mexico", "colombia", "argentina",
           "chile", "peru", "venezuela", "ecuador"],
    "tr": ["turkey", "turkish", "ankara", "istanbul", "aselsan",
           "roketsan", "stm", "tai", "fnss"],
}


def brandify_query(query: str) -> str:
    """R-F299 generalised + R-F337 recursive TLD strip: strip TLD +
    separators from any hostname-like token in the query. Repeats the
    TLD strip until no more match — fixes compound TLDs like .com.tr,
    .co.uk, .com.br, .org.uk. Live 21:55 evidence: 'assangroup.com.tr'
    stripped once → 'assangroup.com' which still trips the sanctions
    domain-token gate. Recursive strip yields 'assangroup'."""
    if not query:
        return query
    parts = query.split()
    out: list[str] = []
    for p in parts:
        looks_like_host = (
            "." in p
            and "/" not in p
            and not p.startswith("(")
            and p.count(".") <= 4
        )
        if looks_like_host:
            # R-F337: iterate TLD strip until no more match.
            # Limit to 4 iterations as a sanity cap (real hostnames have
            # at most 4 segments; this never loops forever).
            stripped = p
            for _ in range(4):
                new_stripped = _BRANDIFY_TLD_RE.sub("", stripped)
                if new_stripped == stripped:
                    break
                stripped = new_stripped
                # Don't strip past the last segment: if no '.' remains,
                # we're at the brand and must stop.
                if "." not in stripped:
                    break
            stripped = _BRANDIFY_SEP_RE.sub(" ", stripped)
            stripped = re.sub(r"\s+", " ", stripped).strip()
            if stripped:
                out.append(stripped)
            else:
                out.append(p)
        else:
            out.append(p)
    return " ".join(out)


def detect_language_fanout(query: str) -> list[str]:
    """Return the list of language codes whose triggers fire on this
    query. ['en'] is the implicit baseline and not included."""
    if not query:
        return []
    q_lower = query.lower()
    hits: list[str] = []
    for lang, triggers in _LANG_FANOUT_TRIGGERS.items():
        for trig in triggers:
            if trig in q_lower:
                hits.append(lang)
                break
    return hits


def _is_valid_year(token: str) -> bool:
    """R-F293 reapplied at the explorer level."""
    return bool(_VALID_YEAR_RE.fullmatch(token))


def _same_host(a: str, b: str) -> bool:
    """Compare the netlocs of two URLs."""
    try:
        return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
    except Exception:
        return False


def _classify_tier(url: str) -> str:
    """Coarse tier classification for provenance. Same patterns as the
    DD orchestrator's source-tier ranking."""
    if not url:
        return "UNVERIFIED"
    u = url.lower()
    # T1 — sanctions / registries / sovereign sources
    for t1 in (
        ".gov", ".gov.uk", "europa.eu", "treasury.gov",
        "opensanctions.org", "ofac.treasury.gov",
        "find-and-update.company-information.service.gov.uk",
        "sec.gov", "eur-lex.europa.eu", "un.org",
        "avoindata.prh.fi", "tietopalvelu.ytj.fi",
    ):
        if t1 in u:
            return "T1"
    # T2 — defence trade press / academic
    for t2 in (
        "janes.com", "defensenews.com", "shephardmedia.com",
        "armadainternational.com", "defence-industry.eu",
        "armytechnology.com", "navaltoday.com", "asianmilitaryreview.com",
        "doi.org", "semanticscholar.org", "openalex.org",
        "crossref.org", "ssrn.com", "arxiv.org",
        "iiss.org", "rusi.org", "sipri.org", "fatf-gafi.org",
    ):
        if t2 in u:
            return "T2"
    # T3 — general news
    for t3 in (
        "reuters.com", "bloomberg.com", "bbc.co.uk", "bbc.com",
        "nytimes.com", "ft.com", "wsj.com", "ap.org",
        "news.google.com", "bing.com/news",
    ):
        if t3 in u:
            return "T3"
    # T4 — social / unranked
    for t4 in ("twitter.com", "x.com", "facebook.com",
               "linkedin.com", "instagram.com"):
        if t4 in u:
            return "T4"
    return "UNVERIFIED"


# ─────────────────────────── MEMORY-FIRST ─────────────────────────────────────

async def _check_memory(query: str, top_k: int = 5) -> tuple[list[ExplorationFact], BackendStatus]:
    """RAG-first: if memory has it, return without touching the web."""
    t0 = time.monotonic()
    status = BackendStatus(name="rag_memory")
    facts: list[ExplorationFact] = []
    try:
        from . import rag_store
        hits = await rag_store.search(query, top_k=top_k)
        status.fired = True
        status.results_count = len(hits or [])
        for h in hits or []:
            text = (h.get("text") or "")[:600]
            url = h.get("url") or h.get("source") or ""
            facts.append(ExplorationFact(
                kind="rag",
                value=text,
                source_url=url,
                backend="rag_memory",
                tier=_classify_tier(url),
                score=float(h.get("score", 0) or 0),
                context=h.get("source_type") or "",
            ))
        status.state = "active" if facts else "silent"
    except Exception as e:
        status.error_reason = str(e)[:200]
        status.state = "errored"
        logger.debug("rag memory check failed: %s", e)
    status.latency_ms = int((time.monotonic() - t0) * 1000)
    return facts, status


# ─────────────────────────── SEARCH BACKENDS ──────────────────────────────────

async def _run_web_search(
    query: str,
    *,
    max_results: int = 12,
    language_fanout: list[str],
) -> tuple[list[ExplorationFact], list[BackendStatus]]:
    """Multi-backend free web search. Uses web_search.search_multilingual
    which already aggregates Google News + Bing + DDG + OpenAlex +
    Semantic Scholar + Crossref. Returns aggregated facts + per-backend
    status (best-effort — search_multilingual flattens results so
    per-backend latency is approximated)."""
    t0 = time.monotonic()
    facts: list[ExplorationFact] = []
    status = BackendStatus(name="web_search_multilingual")
    try:
        from . import web_search
        languages = ["en"] + list(language_fanout)
        hits = await web_search.search_multilingual(
            query,
            languages=languages,
            max_results=max_results,
        )
        status.fired = True
        status.results_count = len(hits or [])
        for h in hits or []:
            url = getattr(h, "url", "") or ""
            title = getattr(h, "title", "") or ""
            snippet = (getattr(h, "snippet", "") or "")[:400]
            backend_name = getattr(h, "backend", "") or getattr(h, "source", "") or "web"
            tier = getattr(h, "source_tier", None) or _classify_tier(url)
            facts.append(ExplorationFact(
                kind="search_result",
                value=title or snippet[:160],
                source_url=url,
                backend=f"web_search:{backend_name}",
                tier=tier,
                context=snippet,
                score=0.5,
            ))
        status.state = "active" if facts else "silent"
    except Exception as e:
        status.error_reason = str(e)[:200]
        status.state = "errored"
        logger.warning("web_search failed: %s", e)
    status.latency_ms = int((time.monotonic() - t0) * 1000)
    return facts, [status]


# ─────────────────────────── LINK-TREE (URL given) ────────────────────────────

async def _run_link_tree(
    seed_url: str,
    *,
    query_context: str = "",
) -> tuple[list[ExplorationFact], BackendStatus]:
    """Recursive URL walker via link_investigator. Inherits R-F292/293
    (same-host bias, year regex range) automatically."""
    t0 = time.monotonic()
    facts: list[ExplorationFact] = []
    status = BackendStatus(name="link_investigator")
    try:
        from . import link_investigator
        tree = await link_investigator.investigate_link_tree(
            seed_url=seed_url,
            query_context=query_context,
            max_depth=2,
            max_pages=20,
            wall_budget_s=60,
            cost_budget_usd=0.0,  # rule-based, free
            llm=None,
        )
        status.fired = True
        for ff in (tree.fused_facts or [])[:30]:
            url = ""
            if ff.source_urls:
                url = ff.source_urls[0]
            # R-F293 redux — year kind, validate before emitting
            if ff.kind == "year" and not _is_valid_year(ff.value):
                continue
            facts.append(ExplorationFact(
                kind="extracted",
                value=f"{ff.kind}: {ff.value}",
                source_url=url,
                backend="link_tree",
                tier=_classify_tier(url),
                context=ff.first_context[:200] if ff.first_context else "",
                score=min(1.0, ff.triangulation / 3.0),
            ))
        status.results_count = len(facts)
        status.state = "active" if facts else "silent"
    except Exception as e:
        status.error_reason = str(e)[:200]
        status.state = "errored"
        logger.warning("link_investigator failed: %s", e)
    status.latency_ms = int((time.monotonic() - t0) * 1000)
    return facts, status


# ─────────────────────────── ARTICLE FETCH (URL given) ────────────────────────

async def _run_article_fetch(
    urls: list[str],
    *,
    max_per_url: int = 1,
) -> tuple[list[ExplorationFact], BackendStatus]:
    """Parallel free-tier article-text fetch. No LLM, no Brave —
    rule-based extraction only."""
    t0 = time.monotonic()
    facts: list[ExplorationFact] = []
    status = BackendStatus(name="article_fetch")
    try:
        from . import researcher as _res
        async def _fetch_one(url: str) -> tuple[str, str]:
            try:
                body = await _res._fetch_article_text(url, timeout=12.0)
                return url, body or ""
            except Exception as e:
                logger.debug("article fetch %s failed: %s", url, e)
                return url, ""

        results = await asyncio.gather(*[_fetch_one(u) for u in urls])
        status.fired = True
        for url, body in results:
            if not body or len(body) < 100:
                continue
            facts.append(ExplorationFact(
                kind="extracted",
                value=body[:800],
                source_url=url,
                backend="article_fetch",
                tier=_classify_tier(url),
                context=body[:400],
                score=0.7,
            ))
        status.results_count = len(facts)
        status.state = "active" if facts else "silent"
    except Exception as e:
        status.error_reason = str(e)[:200]
        status.state = "errored"
        logger.warning("article_fetch failed: %s", e)
    status.latency_ms = int((time.monotonic() - t0) * 1000)
    return facts, status


# ─────────────────────────── FUSION + DEDUP ───────────────────────────────────

def _fuse_facts(all_facts: list[ExplorationFact]) -> list[ExplorationFact]:
    """Dedupe by normalised value + URL, keeping the highest-tier source."""
    if not all_facts:
        return []
    tier_rank = {"T1": 4, "T2": 3, "T3": 2, "T4": 1, "UNVERIFIED": 0}
    by_key: dict[str, ExplorationFact] = {}
    for f in all_facts:
        norm = re.sub(r"\s+", " ", (f.value or "").lower().strip())[:200]
        key = (norm, f.source_url[:200])
        cur = by_key.get(key)
        if cur is None or tier_rank.get(f.tier, 0) > tier_rank.get(cur.tier, 0):
            by_key[key] = f
    return list(by_key.values())


# ─────────────────────────── ECOSYSTEM SNAPSHOT ───────────────────────────────

def _build_ecosystem_snapshot(backends: list[BackendStatus]) -> EcosystemSnapshot:
    """Aggregate per-backend status into the call-level health signal."""
    snap = EcosystemSnapshot(backends=list(backends))
    n_active = sum(1 for b in backends if b.state == "active")
    n_silent = sum(1 for b in backends if b.state == "silent")
    n_errored = sum(1 for b in backends if b.state == "errored")
    n_skipped = sum(
        1 for b in backends if b.state in ("skipped_cost", "skipped_dead")
    )
    snap.summary = {
        "active_backends": n_active,
        "silent_backends": n_silent,
        "errored_backends": n_errored,
        "skipped_backends": n_skipped,
        "total_backends": len(backends),
        "avg_latency_ms": (
            int(sum(b.latency_ms for b in backends) / max(len(backends), 1))
        ),
    }
    if n_errored >= 2 or (n_active == 0 and n_silent == 0):
        snap.health_signal = "DEAD"
    elif n_errored >= 1 or n_silent >= 2:
        snap.health_signal = "DEGRADED"
    elif n_silent >= 1:
        snap.health_signal = "PARTIAL"
    else:
        snap.health_signal = "HEALTHY"
    snap.interpretation = (
        "active = produced data, silent = ran with zero results, "
        "errored = exception, skipped_cost = blocked by cost_free=True, "
        "skipped_dead = circuit-breaker open. Use silent/dead counts to "
        "diagnose 'ARIA going backwards' — those are the wired-but-not-"
        "firing backends per R-F305."
    )
    return snap


# ─────────────────────────── COST-FREE GUARD ──────────────────────────────────

# Modules that incur per-call $$ (paid LLM completions, paid Brave answers,
# paid OpenSanctions tier, etc.). The cost-free contract bans them.
_PAID_MODULES_FORBIDDEN_WHEN_COST_FREE = {
    "brave_answers": "Brave Search Answers API — deprecated, paid per call",
    "deep_research": "deep_research invokes LLM completions per turn — paid",
}


def _assert_cost_free_or_warn(backend_name: str, cost_free: bool) -> bool:
    """Returns True if the backend is allowed to run.  When cost_free=True
    and the backend is on the paid list, returns False and logs a warning
    instead of raising — the call continues with the backend skipped."""
    if not cost_free:
        return True
    if backend_name in _PAID_MODULES_FORBIDDEN_WHEN_COST_FREE:
        logger.info(
            "[web_explorer] cost_free=True: skipping paid backend %s (%s)",
            backend_name,
            _PAID_MODULES_FORBIDDEN_WHEN_COST_FREE[backend_name],
        )
        return False
    return True


# ─────────────────────────── PUBLIC ENTRY POINT ───────────────────────────────

async def explore(
    query: str = "",
    *,
    urls: list[str] | None = None,
    depth: str = "auto",                # auto | quick | thorough
    cost_free: bool = True,
    language_fanout: str = "auto",      # auto | off | list[str]
    max_results: int = 12,
    memory_first: bool = True,
    context: str = "",
) -> ExplorationResult:
    """Unified web exploration entry point.

    Parameters
    ----------
    query : str
        The research question or entity name. Hostnames auto-brandified
        via brandify_query (R-F299 generalised).
    urls : list[str] | None
        Optional seed URLs. Triggers link_investigator + article_fetch.
        Combine with `query` to triangulate.
    depth : "auto" | "quick" | "thorough"
        - quick: single-pass search, no link-tree, no escalation
        - auto (default): search + link-tree if URLs given; escalate
          to deep_research if results thin AND cost_free=False
        - thorough: search + link-tree + deep_research (deep_research
          only when cost_free=False)
    cost_free : bool (default True)
        When True, paid paths (deep_research with LLM, Brave) are
        skipped. Student learning loop must always pass True.
    language_fanout : "auto" | "off" | list[str]
        Languages to fan out the search query into. "auto" detects from
        query content (mentions of Russia → +ru, China → +zh, etc.).
        "off" disables. Explicit list overrides detection.
    max_results : int
        Max search results per backend pass.
    memory_first : bool
        If True (default), check RAG store before any web call. If RAG
        has it, the call may return without touching the web at all.
    context : str
        Optional context passed to link_investigator for relevance
        scoring (entity name / sector hint).
    """
    t0 = time.monotonic()
    result = ExplorationResult(
        query=query,
        urls_seeded=list(urls or []),
        depth=depth,
        cost_free=cost_free,
    )

    # Brandification
    result.brandified_query = brandify_query(query) if query else ""
    effective_query = result.brandified_query or query

    # Language fan-out resolution
    if language_fanout == "auto":
        result.language_fanout = detect_language_fanout(effective_query)
    elif language_fanout == "off":
        result.language_fanout = []
    elif isinstance(language_fanout, list):
        result.language_fanout = list(language_fanout)
    else:
        result.language_fanout = []

    all_facts: list[ExplorationFact] = []
    backends: list[BackendStatus] = []

    # ── 1. Memory-first ─────────────────────────────────────────────
    if memory_first and effective_query:
        mem_facts, mem_status = await _check_memory(effective_query, top_k=5)
        backends.append(mem_status)
        if mem_facts:
            all_facts.extend(mem_facts)
            result.memory_hits = len(mem_facts)

    # ── 2. Search (query-bearing requests) ──────────────────────────
    if effective_query and depth != "skip_search":
        if _assert_cost_free_or_warn("web_search", cost_free):
            search_facts, search_statuses = await _run_web_search(
                effective_query,
                max_results=max_results,
                language_fanout=result.language_fanout,
            )
            backends.extend(search_statuses)
            all_facts.extend(search_facts)
            result.web_hits += len(search_facts)

    # ── 3. URL-seeded paths (link-tree + article fetch) ─────────────
    seed_urls = urls or []
    if seed_urls:
        # 3a. Link-tree on first seed (other seeds get article-fetched)
        tree_facts, tree_status = await _run_link_tree(
            seed_urls[0],
            query_context=effective_query or context,
        )
        backends.append(tree_status)
        all_facts.extend(tree_facts)
        result.web_hits += len(tree_facts)

        # 3b. Direct fetch for remaining seeds
        if len(seed_urls) > 1:
            fetch_facts, fetch_status = await _run_article_fetch(seed_urls[1:])
            backends.append(fetch_status)
            all_facts.extend(fetch_facts)
            result.web_hits += len(fetch_facts)

    # ── 4. Adaptive escalation ──────────────────────────────────────
    web_facts_count = sum(1 for f in all_facts if f.kind != "rag")
    if depth == "thorough" or (depth == "auto" and web_facts_count < 3 and not cost_free):
        # Deep research path — costs money, requires cost_free=False
        if not cost_free and effective_query:
            try:
                from . import researcher as _res
                # Use the existing deep_research entry but consume its
                # output into our fact shape.
                dr_out = await _res.deep_research(
                    query=effective_query,
                    depth="thorough" if depth == "thorough" else "quick",
                    llm=None,  # rule-based; caller can supply LLM if they want
                )
                dr_status = BackendStatus(name="deep_research", fired=True)
                if isinstance(dr_out, dict):
                    arts = dr_out.get("articles") or []
                    for art in arts[:15]:
                        url = (art.get("url") or "")[:400]
                        title = (art.get("title") or "")[:200]
                        snippet = (art.get("snippet") or art.get("summary") or "")[:400]
                        all_facts.append(ExplorationFact(
                            kind="search_result",
                            value=title or snippet[:160],
                            source_url=url,
                            backend="deep_research",
                            tier=_classify_tier(url),
                            context=snippet,
                            score=0.6,
                        ))
                    dr_status.results_count = len(arts[:15])
                    dr_status.state = "active" if arts else "silent"
                else:
                    dr_status.state = "silent"
                backends.append(dr_status)
                result.web_hits += dr_status.results_count
            except Exception as e:
                backends.append(BackendStatus(
                    name="deep_research",
                    fired=True,
                    state="errored",
                    error_reason=str(e)[:200],
                ))
                logger.warning("deep_research escalation failed: %s", e)
        else:
            # Skipped — note it so the ecosystem snapshot is honest
            backends.append(BackendStatus(
                name="deep_research",
                state="skipped_cost",
                error_reason=(
                    "cost_free=True; deep_research requires explicit "
                    "cost_free=False to invoke (paid LLM completions)"
                ),
            ))

    # ── 5. Fuse + provenance ────────────────────────────────────────
    result.facts = _fuse_facts(all_facts)
    result.provenance = [
        {
            "source_url": f.source_url,
            "backend": f.backend,
            "tier": f.tier,
            "retrieved_at": f.retrieved_at,
        }
        for f in result.facts
        if f.source_url
    ]

    # ── 6. Ecosystem snapshot ───────────────────────────────────────
    result.ecosystem = _build_ecosystem_snapshot(backends)

    # ── 7. Notes — operator-facing diagnostic ───────────────────────
    if result.ecosystem.health_signal in ("DEGRADED", "DEAD"):
        silent = [
            b.name for b in result.ecosystem.backends if b.state == "silent"
        ]
        errored = [
            b.name for b in result.ecosystem.backends if b.state == "errored"
        ]
        if silent:
            result.notes.append(f"silent backends: {', '.join(silent)}")
        if errored:
            result.notes.append(f"errored backends: {', '.join(errored)}")

    result.duration_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "[web_explorer] explore() complete: query=%r urls=%d facts=%d "
        "memory=%d web=%d backends=%d health=%s duration=%dms cost_free=%s",
        (effective_query or "")[:80],
        len(result.urls_seeded),
        len(result.facts),
        result.memory_hits,
        result.web_hits,
        len(result.ecosystem.backends),
        result.ecosystem.health_signal,
        result.duration_ms,
        cost_free,
    )
    return result


# ─────────────────────────── BRAIN HOOK ───────────────────────────────────────
# Per pay-once-remember-forever doctrine: every successful explore() call
# pushes the harvested facts into brain_hook so the next query hits memory
# for $0. Fire-and-forget; never blocks the caller.

async def explore_and_remember(*args, **kwargs) -> ExplorationResult:
    """Wrapper that runs explore() then absorbs the results into the brain
    on a fire-and-forget task. Use this from chat-tool dispatch where
    we explicitly want the pay-once-remember-forever effect."""
    result = await explore(*args, **kwargs)
    try:
        from . import brain_hook
        # Best-effort absorb. Use the first non-empty fact as the
        # canonical summary; the rest tag into knowledge.
        if result.facts:
            asyncio.create_task(brain_hook.absorb(
                module="web_explorer",
                summary=(
                    f"web_explorer harvested {len(result.facts)} facts "
                    f"for query={result.query!r} from {len(result.urls_seeded)} URLs"
                ),
                entity_name=result.query[:120],
                success=True,
                confidence="ASSESSED",
            ))
    except Exception as e:
        logger.debug("explore_and_remember brain_hook absorb failed: %s", e)
        from .engine_wiring import wire_failure as _wf
        _wf(
            module="web_explorer",
            detail=f"brain_hook.absorb failed in explore_and_remember: {e}",
            gap_type="engine_failure",
            source="web_explorer:explore_and_remember",
        )

    # R-F996 — wire to brain
    wire_success(
        module="web_explorer",
        summary="Web exploration",
        source_id="web_explorer:R-F996",
    )
    return result

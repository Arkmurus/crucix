"""ARIA Web Search — independent multi-backend search engine.

INDEPENDENCE PRINCIPLE: ARIA owns her search infrastructure. No single
vendor dependency. If Brave is down, use SearXNG. If SearXNG is down,
use Google News RSS. If all paid APIs fail, scrape directly. ARIA
always returns results.

Backends (priority order):
  1. Brave Search API    — best quality, 2000 queries/month free tier
  2. SearXNG instances   — free meta-search (aggregates Google, Bing, DDG)
  3. Google News RSS     — free, news-focused, 30 results max
  4. Bing News RSS       — free fallback
  5. Direct scraping     — last resort, trafilatura extraction

Usage:
  from aria_service.intel.web_search import search, search_news, search_entity

  results = await search("Angola defence procurement 2026", max_results=10)
  results = await search_news("ECJU export licence update", language="en")
  results = await search_entity("Duma Engineering Ltd", country="UK")
"""

from __future__ import annotations

from .engine_wiring import wire_success, wire_failure

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from .ua_rotation import random_ua
from .wire import fail_wire  # R-F1789 §21 brain-wiring
from .circuit_breaker import classify_status  # R-F2071: canonical HTTP-status classifier

logger = logging.getLogger("aria.web_search")

# ── R-F2071: import-time name-resolution guard ──────────────────────────────
# Every bare-name function call in this module must resolve to an imported
# name, a locally defined name, or a builtin. This assertion runs at import
# time (not at runtime on a specific code path), so a NameError like the
# `classify_status` bug (called but never imported) is caught immediately
# on deploy rather than silently lurking until a non-200 HTTP response.
# The guard is intentionally simple: it only checks public lower-case names
# (not _-prefixed internals, not dunder methods). It does NOT check method
# calls (obj.method()) or attribute access — those are resolved at runtime
# by the object's type and are not susceptible to this failure class.
if os.getenv("ARIA_SKIP_IMPORT_GUARD", "").lower() not in ("1", "true", "yes"):
    import ast as _import_guard_ast
    import sys as _import_guard_sys
    _guard_frame = _import_guard_sys._getframe()
    _guard_src = _guard_frame.f_code.co_filename
    try:
        with open(_guard_src, encoding="utf-8") as _guard_f:
            _guard_tree = _import_guard_ast.parse(_guard_f.read())
    except Exception:
        pass
    else:
        _guard_imports: set[str] = set()
        _guard_defs: set[str] = set()
        for _guard_node in _import_guard_ast.walk(_guard_tree):
            if isinstance(_guard_node, _import_guard_ast.Import):
                for _guard_alias in _guard_node.names:
                    _guard_name = _guard_alias.asname or _guard_alias.name
                    _guard_imports.add(_guard_name)
                    if "." in _guard_name:
                        _guard_imports.add(_guard_name.split(".")[-1])
            elif isinstance(_guard_node, _import_guard_ast.ImportFrom):
                for _guard_alias in _guard_node.names:
                    _guard_imports.add(_guard_alias.asname or _guard_alias.name)
            elif isinstance(_guard_node, (_import_guard_ast.FunctionDef,
                                          _import_guard_ast.AsyncFunctionDef)):
                _guard_defs.add(_guard_node.name)
            elif isinstance(_guard_node, _import_guard_ast.Assign):
                for _guard_t in _guard_node.targets:
                    if isinstance(_guard_t, _import_guard_ast.Name):
                        _guard_defs.add(_guard_t.id)
            elif isinstance(_guard_node, _import_guard_ast.AnnAssign):
                if isinstance(_guard_node.target, _import_guard_ast.Name):
                    _guard_defs.add(_guard_node.target.id)
        _guard_builtins: set[str] = set()
        try:
            import builtins as _guard_builtins_mod
            _guard_builtins = set(dir(_guard_builtins_mod))
        except Exception:
            _guard_builtins = set()
        _guard_calls: set[str] = set()
        for _guard_node in _import_guard_ast.walk(_guard_tree):
            if isinstance(_guard_node, _import_guard_ast.Call):
                if isinstance(_guard_node.func, _import_guard_ast.Name):
                    _guard_name = _guard_node.func.id
                    if _guard_name[0].islower() and not _guard_name.startswith("_"):
                        _guard_calls.add(_guard_name)
        _guard_unresolved = _guard_calls - _guard_imports - _guard_defs - _guard_builtins
        if _guard_unresolved:
            raise NameError(
                f"R-F2071 import guard: function(s) called but not imported/defined/builtin "
                f"in {_guard_src}: {sorted(_guard_unresolved)}. "
                f"Add the missing import or define the function locally."
            )

# R-W5 (2026-05-11): per-call ecosystem snapshot for the most-recent
# search() invocation. Operator / chat layer / dashboard reads via
# get_last_search_ecosystem() to see which backends fired vs silent
# vs errored — the wired-but-silent detector applied at the search
# layer, mirroring R-F305's pattern for the DD orchestrator.
_LAST_SEARCH_ECOSYSTEM: dict = {}


@fail_wire(module="web_search", gap_type="source_failure")
def get_last_search_ecosystem() -> dict:
    """R-W5: read the per-backend ecosystem snapshot of the most-recent
    search() call. Returns:
        {
          "query": str, "language": str,
          "backends": [{name, state, results_count, error_reason?}, ...],
          "summary": {active_backends, silent_backends, errored_backends, total_backends},
          "health_signal": "HEALTHY" | "PARTIAL" | "DEGRADED" | "DEAD",
          "total_duration_ms": int,
        }
    Empty dict if no search has run since boot."""
    return dict(_LAST_SEARCH_ECOSYSTEM)


# ── Configuration ───────────────────────────────────────────────────────────

BRAVE_API_KEY = (os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("BRAVE_API_KEY") or "").strip()
GNEWS_API_KEY = (os.getenv("GNEWS_API_KEY") or os.getenv("GNEWS_KEY") or "").strip()

# R-F2318 — Brave routing. Brave is the PRIMARY backend for USER-FACING DD +
# deep-research + chat search ONLY; the continuous/autonomous researcher stays on the
# free stack (so its learning transfers to SearXNG and the paid quota isn't burned by
# the high-volume loop). Callers opt in either explicitly (search(..., use_brave=True))
# or by setting the context flag at a user-facing entry point (enable_brave_for_scope()),
# which propagates through asyncio to every downstream search() in that request — while
# the autonomous loop, running in its own background task tree, never sets it.
import contextvars as _contextvars
_BRAVE_CTX: "_contextvars.ContextVar[bool]" = _contextvars.ContextVar("aria_brave_enabled", default=False)
# Global hard kill-switch (ops): ARIA_BRAVE_DISABLED=1 forces the free stack everywhere.
_BRAVE_GLOBALLY_OFF = (os.getenv("ARIA_BRAVE_DISABLED") or "").lower() in ("1", "true", "yes")


def enable_brave_for_scope(on: bool = True) -> _contextvars.Token:
    """Enable Brave as the primary search backend for the CURRENT async context and
    everything it awaits/spawns. Call at user-facing entry points (DD / deep-research /
    chat search). The continuous researcher never calls this → stays on the free stack.

    R-F3087: return the ContextVar token so long-lived caller tasks can restore their
    previous state.  Without restoration, an autonomous task that ran one DD kept the
    paid Brave path enabled for unrelated searches later in the same task."""
    return _BRAVE_CTX.set(bool(on))


def reset_brave_scope(token: _contextvars.Token) -> None:
    """Restore the Brave context to the value captured by ``enable_brave_for_scope``."""
    _BRAVE_CTX.reset(token)


def _dd_brave_only() -> bool:
    """R-F3847 — on the DD path, Brave is THE search engine. Nothing substitutes.

    Operator directive 2026-08-11: Brave (and Anthropic) are the designated tools for
    DD reports, "and nothing else".

    This turns OFF the R-F3122 SearXNG fallback for Brave-scoped (DD) searches. That
    fallback was a reasonable design — it kept coverage when Brave yielded nothing,
    and it was honest about it (`brave_fallback_used` surfaced per §14). It is removed
    because it is the measured path by which noise reached a customer-facing report:

        dd_92f9d77b8886 "Silverbrook Capital Management"
          .digital.press_coverage[3].url = support.google.com/chrome/answer/95346?hl=fr

    a French Chrome cookies help page cited as press coverage. Chain: DD enables Brave
    exclusively → Brave yields nothing → fallback runs SearXNG → SearXNG was returning
    query-independent noise (R-F3844). R-F3844 fixed that from the backend side; this
    fixes it from the policy side, so DD no longer depends on a degraded backend
    behaving itself.

    Default ON, because a designation that only holds when an env var is set is not a
    designation. Reversible without a deploy via ARIA_DD_BRAVE_ONLY=0, and ONLY the
    explicit falsey words count — a typo must not silently restore substitution.

    The free/autonomous stack is untouched: it still uses SearXNG, produces no
    customer-facing DD, and must not burn paid quota (R-F2318).
    """
    raw = (os.getenv("ARIA_DD_BRAVE_ONLY") or "").strip().lower()
    return raw not in ("0", "false", "no")


def brave_is_enabled() -> bool:
    """True when Brave should be used for the current call: key present, not globally
    disabled, and the context flag is set (or an explicit use_brave=True is passed)."""
    if _BRAVE_GLOBALLY_OFF or not BRAVE_API_KEY:
        return False
    try:
        return bool(_BRAVE_CTX.get())
    except Exception:
        return False


def mask_brave_source(results: list) -> None:
    """R-F2318 — relabel the internal 'brave' backend source to 'aria_search' on results
    LEAVING search(), so no user-facing surface (DD report, citations, triangulation)
    reveals Brave. In-place; internal consumers upstream already used the real label.
    Operator directive 2026-07-02: "the brave branding must not appear on user DDs —
    all under Aria"."""
    for r in results:
        src = getattr(r, "source", "") or ""
        if src == "brave" or src.startswith("brave:"):
            try:
                r.source = "aria_search"
            except Exception:
                pass


SEARXNG_INSTANCES: list[str] = [
    # Removed 2026-04-20 — all 5 previously-listed public instances
    # (search.sapti.me, searxng.world, search.bus-hit.me, searx.tiekoetter.com,
    # search.ononoki.org) were sitting in circuit_breaker.open permanently,
    # polluting the open-circuit-breakers metric. Per the 2026-04-19 audit
    # triage: Brave is primary and the academic-API direct integrations
    # (Semantic Scholar / OpenAlex / CrossRef) capture the fallback value.
    # SearXNG self-host is deferred; re-populate this list or gate the
    # backend behind an env flag if it ever comes back. `_search_searxng`
    # returns [] safely when this list is empty.
]
REQUEST_TIMEOUT = 12.0
MAX_RESULTS_PER_BACKEND = 15
# R-F2226 — search gather budget (quorum+grace early-return). The gather used to
# block on the SLOWEST backend for the full per-backend REQUEST_TIMEOUT (12s), so
# one slow supplementary backend (news-RSS redirect chains, the 3-registry
# academic fan-out) pinned EVERY search at 12s even though the fast primary
# backend (SearXNG self-host, ~2s) had already returned. Now: once QUORUM
# backends return a useful (non-empty) result, only GRACE more seconds are given
# before stragglers are cancelled. BUDGET is the hard backstop and equals the old
# 12s worst case — when quorum is NEVER reached (degraded: only a slow backend
# has anything) the full budget is still waited, so recall is preserved.
SEARCH_GATHER_BUDGET = float(os.getenv("ARIA_SEARCH_GATHER_BUDGET", "12.0"))
SEARCH_GATHER_QUORUM = int(os.getenv("ARIA_SEARCH_GATHER_QUORUM", "2"))
SEARCH_GATHER_GRACE = float(os.getenv("ARIA_SEARCH_GATHER_GRACE", "2.5"))


async def _gather_search_backends(tasks: list, *, budget: float, quorum: int, grace: float) -> list:
    """R-F2226 — collect backend results in TASK ORDER without blocking on the
    slowest backend.

    Waits incrementally. Once `quorum` tasks have returned a non-empty,
    non-exception result, the remaining tasks get only `grace` more seconds
    before they are cancelled and whatever finished is salvaged. If quorum is
    never reached, the full `budget` is waited (recall-safe degraded path).
    Order-preserving so the caller's per-backend snapshot (index→name) stays
    correct; mirrors the R-F1657 salvage semantics. Never raises.
    """
    loop = asyncio.get_event_loop()
    start = loop.time()
    pending = set(tasks)
    useful = 0
    quorum_at: float | None = None
    while pending:
        now = loop.time()
        remaining = budget - (now - start)          # hard backstop
        if remaining <= 0:
            break
        if quorum_at is not None:                    # quorum met → only grace left
            remaining = min(remaining, grace - (now - quorum_at))
            if remaining <= 0:
                break
        try:
            done, pending = await asyncio.wait(
                pending, timeout=max(0.01, remaining),
                return_when=asyncio.FIRST_COMPLETED,
            )
        except Exception:
            break
        if not done:                                 # deadline hit, nothing new
            break
        for _d in done:
            try:
                _r = _d.result()
                if _r and not isinstance(_r, Exception):
                    useful += 1
            except Exception:
                pass
        if quorum_at is None and useful >= quorum:
            quorum_at = loop.time()
    # Cancel stragglers so they don't linger (matches the prior R-F1657 behaviour).
    for _t in tasks:
        if not _t.done():
            _t.cancel()
    # Collect in ORDER; unfinished/cancelled → TimeoutError sentinel.
    out: list = []
    for _t in tasks:
        if _t.done() and not _t.cancelled():
            try:
                out.append(_t.result())
            except Exception:
                out.append(TimeoutError("gather timeout"))
        else:
            out.append(TimeoutError("gather timeout"))
    return out


# Brave's `search_lang` rejects bare codes for languages with regional
# variants (Portuguese, Chinese, Japanese) with HTTP 422. Live evidence
# 2026-05-01 06:34:08: a Mozambique sweep sent `search_lang=pt` and got
# 422; the Lusophone backend silently dropped to 1/3 backends. Map
# unsupported bare codes to a regional default. `pt-pt` covers
# Angola/Mozambique; Brazil callers should pass `pt-BR` explicitly.
_BRAVE_LANG_NORMALISE = {
    "pt": "pt-pt",
    "zh": "zh-hans",
    "ja": "jp",
}


def _normalise_brave_lang(language: str) -> str:
    if not language:
        return "en"
    code = language.strip().lower()
    return _BRAVE_LANG_NORMALISE.get(code, code)

# Source credibility tiers (from v3_prompts SOURCE_HIERARCHY)
TIER_1_DOMAINS = {
    "gov.uk", "nato.int", "un.org", "sipri.org", "wassenaar.org",
    "eur-lex.europa.eu", "ofac.treasury.gov", "opensanctions.org",
    "ted.europa.eu", "sam.gov", "icrc.org", "icc-cpi.int",
    "defense.gov",  # R-F2248: US DoD official (primary-source contracts feed, R-F2247)
}
TIER_2_DOMAINS = {
    "rand.org", "rusi.org", "iiss.org", "csis.org", "cfr.org",
    "chathamhouse.org", "issafrica.org", "carnegieendowment.org",
    "sipri.org", "worldbank.org",
    "reliefweb.int",  # R-F2248: UN OCHA (conflict/humanitarian feed, R-F2247)
}
TIER_3_DOMAINS = {
    "janes.com", "armyrecognition.com", "defensenews.com",
    "breakingdefense.com", "defenceweb.co.za", "shephard.co.uk",
}
TIER_4_DOMAINS = {
    "reuters.com", "bbc.com", "ft.com", "aljazeera.com",
    "france24.com", "lusa.pt", "dw.com", "rfi.fr",
}
DISINFORMATION_DOMAINS = {
    "rt.com", "sputniknews.com", "tass.com", "cgtn.com",
    "xinhuanet.com", "presstv.ir",
}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""           # backend that found it
    credibility_tier: int = 5  # 1=official, 2=institution, 3=industry, 4=quality press, 5=general
    language: str = "en"
    timestamp: str = ""
    relevance_score: float = 0.0

    @fail_wire(module="web_search", gap_type="source_failure")
    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "credibility_tier": self.credibility_tier,
            "language": self.language,
            "relevance_score": round(self.relevance_score, 2),
        }


def _get_domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).hostname.replace("www.", "").lower()
    except Exception:
        return ""


def _apply_domain_diversity_cap(results: list, max_results: int) -> list:
    """R-F2245 — stop any single domain from monopolising the top-N results (the
    source-collusion / echo-chamber risk: e.g. janes.com across 9 feeds, or one
    site ranking on many paths). Walk the ALREADY-RANKED list and keep at most
    ARIA_SEARCH_MAX_PER_DOMAIN (default 3) per registrable domain, then backfill
    from the capped overflow so we NEVER return fewer than the plain slice. This is
    a diversity re-ordering WITHIN the winners — it never lifts a low-relevance
    result above the cut, it just spreads perspective across domains. Cap<=0 or a
    short list → plain slice (no-op)."""
    try:
        cap = int(os.getenv("ARIA_SEARCH_MAX_PER_DOMAIN", "3") or "3")
    except Exception:
        cap = 3
    if cap <= 0 or len(results) <= max_results:
        return results[:max_results]
    seen: dict[str, int] = {}
    primary: list = []
    overflow: list = []
    for r in results:
        d = _get_domain(getattr(r, "url", "") or "")
        if d and seen.get(d, 0) >= cap:
            overflow.append(r)
        else:
            seen[d] = seen.get(d, 0) + 1
            primary.append(r)
    return (primary + overflow)[:max_results]


def _domain_matches(domain: str, tier_set: set) -> bool:
    """R-F2248 — SUFFIX (registrable-domain) match, not substring. Substring let a
    hostile domain that merely CONTAINS a trusted one ('notun.org', 'un.org.evil.com')
    inherit its credibility tier. Match the domain itself or a genuine subdomain."""
    return any(domain == d or domain.endswith("." + d) for d in tier_set)


def _score_credibility(url: str) -> int:
    domain = _get_domain(url)
    if not domain:
        return 5
    if _domain_matches(domain, DISINFORMATION_DOMAINS):
        return 6  # quarantine tier
    if _domain_matches(domain, TIER_1_DOMAINS):
        return 1
    if _domain_matches(domain, TIER_2_DOMAINS):
        return 2
    if _domain_matches(domain, TIER_3_DOMAINS):
        return 3
    if _domain_matches(domain, TIER_4_DOMAINS):
        return 4
    return 5


def _score_relevance(result: SearchResult, query: str) -> float:
    """Score result relevance based on query term overlap + credibility."""
    query_terms = set(query.lower().split())
    text = (result.title + " " + result.snippet).lower()
    overlap = sum(1 for t in query_terms if t in text and len(t) > 2)
    term_score = overlap / max(len(query_terms), 1)

    # Credibility boost: tier 1 = 1.5x, tier 2 = 1.3x, tier 3 = 1.15x
    cred_mult = {1: 1.5, 2: 1.3, 3: 1.15, 4: 1.05, 5: 1.0, 6: 0.3}

    # R-F888 (2026-05-25) — SOURCE-TYPE weighting. The academic registries
    # (Crossref / Semantic Scholar / OpenAlex) are the FALLBACK tier (per this
    # module's header: "academic-API integrations capture the fallback value").
    # But their high credibility multiplier (×1.3-1.5) + keyword-dense titles
    # let them OUT-RANK live-web/news results for general + current-affairs
    # queries. Live 2026-05-25 (operator "zero confidence"): "who is the
    # current US president" returned Crossref's "Who Was Who 2007" (stops at
    # G.W. Bush) over DuckDuckGo's live "President Donald Trump 2025-2029".
    # Backends individually return the CORRECT answer — the ranking buried it.
    # Demote academic to a true fallback (only wins when nothing else returns);
    # boost live-web/news so current-affairs + entity lookups surface.
    src = (result.source or "").lower()
    if any(a in src for a in ("crossref", "semantic_scholar", "semanticscholar",
                              "openalex", "academic", "doi.org", "ssrn")):
        source_mult = 0.45
    elif any(w in src for w in ("google_news", "bing_news", "duckduckgo", "ddg",
                                "searxng", "brave", "defence_event")):
        source_mult = 1.25
    else:
        source_mult = 1.0
    return term_score * cred_mult.get(result.credibility_tier, 1.0) * source_mult


# ── Backend: Brave Search API ───────────────────────────────────────────────

# ── Backend: Brave Search API (R-F2318, restored 2026-07-02) ────────────────
#
# Operator directive 2026-07-02: Brave is the PRIMARY search backend for
# user-facing DD + deep-research + chat search. It is NOT used by ARIA's
# continuous/autonomous researcher (that stays on the free stack so its learning
# transfers to SearXNG and the paid quota isn't burned by the high-volume loop).
# Routing is via the `use_brave` flag / _BRAVE_CTX contextvar in search().
# Every Brave call is captured (query → results) into the distillation corpus so
# ARIA can learn to MATCH Brave with SearXNG and eventually drop the dependency (§6).
#
# Rate-limit throttle: Brave's free tier is ~1 query/sec; a DD multi-query fan-out
# would otherwise 429-storm. _BRAVE_MIN_INTERVAL spaces call STARTS (env-tunable
# via ARIA_BRAVE_MAX_QPS; raise it on a paid data tier). We hold the lock only for
# the spacing sleep, not the HTTP request, so requests still overlap.
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_BRAVE_MIN_INTERVAL = 1.0 / max(0.1, float(os.getenv("ARIA_BRAVE_MAX_QPS", "1") or "1"))
_brave_last_call = 0.0
_brave_lock = asyncio.Lock()


#: R-F3051 — a Brave query that returns HTTP 200 with ZERO results is not an error
#: and not a rate limit: the query is simply over-constrained for Brave's engine.
#: Proven live 2026-07-25 against the paid key, on the exact query a DD generates:
#:
#:   "SUPACAT LIMITED"                                                    -> 5
#:   "SUPACAT LIMITED" United Kingdom                                     -> 5
#:   "SUPACAT LIMITED" fine OR penalty OR enforcement                     -> 5
#:   "SUPACAT LIMITED" regulator OR licence OR ... OR enforcement         -> 5
#:   "SUPACAT LIMITED" United Kingdom fine OR penalty                     -> 0  ←
#:   "SUPACAT LIMITED" United Kingdom regulator OR ... OR enforcement     -> 0  ←
#:
#: A quoted phrase PLUS a bare qualifier PLUS an OR-block is the combination that
#: kills it — dropping just the qualifier restores results. The DD adverse-media
#: templates build exactly that shape, so the PAID PRIMARY backend was contributing
#: nothing to real reports while SearXNG carried them (live: primary_user_search
#: state=silent, 0 results; searxng 10). The report said so honestly, which is why
#: this was visible at all — but honest silence is still a wasted capability.
_BRAVE_QUOTED_RE = re.compile(r'"[^"]+"')


def _relax_brave_query(query: str) -> str:
    """R-F3051 — fall back to the QUOTED ENTITY PHRASE alone.

    The relaxation direction matters, and the obvious one is wrong. Measured against
    the live paid key on the DD's own query (count = results / how many NAME the
    subject):

      "SUPACAT LIMITED"                                    5 / 4   ← on-subject
      "SUPACAT LIMITED" fine OR penalty                    5 / 0   ← junk
      "SUPACAT LIMITED" regulator OR licence OR ...        5 / 0   ← junk
      "SUPACAT LIMITED" United Kingdom fine OR penalty     0 / 0

    Brave SILENTLY DROPS the quoted phrase whenever an OR-block is present and
    answers the OR terms generically — returning "Penalties | FinCEN.gov" and
    "FIA Super Licence - Wikipedia" for a Devon vehicle manufacturer. So keeping the
    OR-block (the intuitive relaxation) buys results that R-F2745's subject-name
    filter then discards — which is exactly what the live reports showed
    ("29 search result(s) excluded as not referencing 'SUPACAT LIMITED'"). The
    entity phrase is the anchor; the OR terms are the part Brave cannot honour.

    Returns "" when the query is already just the phrase, so no retry is spent."""
    q = str(query or "").strip()
    if not q:
        return ""
    quoted = _BRAVE_QUOTED_RE.findall(q)
    if not quoted:
        return ""                     # no phrase anchor → relaxing could change intent
    relaxed = " ".join(quoted).strip()
    return relaxed if relaxed and relaxed != q else ""


def _safe_body(resp: Any) -> str:
    """The response body, or "" — reading it must never turn a 429 into a crash.

    R-F3868: the body is load-bearing (it is the only signal separating a pacing
    429 from a spent plan, §18), but it is also untrusted input on an error path.
    """
    try:
        return (resp.text or "")[:500]
    except Exception:
        return ""


async def _brave_meter(outcome: str, *, status: int | None = None,
                       body: str = "", headers: Any = None) -> None:
    """Count one Brave call. Metering must NEVER break the search it observes.

    R-F3868 — Brave is DD's designated engine and nothing was counting its calls:
    `/api/aria/cost/external` reported `by_service: {}, total_calls: 0`. An
    unmeasured dependency reads exactly like a healthy one, right up to the 429.
    """
    try:
        from . import brave_usage as _bu
        await _bu.record_call(outcome, status=status, body=body, headers=headers)
    except Exception:      # pragma: no cover - observability never blocks search
        logger.debug("[R-F3868] brave metering unavailable", exc_info=True)


async def _search_brave(query: str, max_results: int = 10, language: str = "en",
                        *, _is_relaxed_retry: bool = False) -> list[SearchResult]:
    """Brave Web Search API. Returns [] when unconfigured, cooled, rate-limited, or
    on error (never raises) so the parallel gather degrades gracefully to the free
    stack (§14 fallback transparency). Gated upstream by the use_brave flag; this
    function additionally no-ops without BRAVE_API_KEY."""
    if not BRAVE_API_KEY:
        return []
    from .circuit_breaker import get_breaker as _get_cb_brave
    _cb = _get_cb_brave("search:brave", failure_threshold=5, cooldown_seconds=300)
    if _cb.is_open():
        return []
    # Respect Brave's rate limit: space call starts by _BRAVE_MIN_INTERVAL.
    global _brave_last_call
    async with _brave_lock:
        _wait = _BRAVE_MIN_INTERVAL - (time.monotonic() - _brave_last_call)
        if _wait > 0:
            await asyncio.sleep(_wait)
        _brave_last_call = time.monotonic()
    count = max(1, min(int(max_results or 10), 20))   # Brave caps count at 20
    params = {
        "q": query,
        "count": count,
        "search_lang": _normalise_brave_lang(language),
        "safesearch": "off",
        "text_decorations": "false",
    }
    headers = {
        "X-Subscription-Token": BRAVE_API_KEY,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "User-Agent": random_ua(),
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(_BRAVE_ENDPOINT, params=params, headers=headers)
            if resp.status_code == 429:
                _cb.record_failure(reason="rate_limited")
                # R-F3868 — the 429 BODY is the only thing that distinguishes "we
                # are going too fast" from "the plan is spent", and §18 records the
                # OpenSanctions defect of reporting the second as the first: a wrong
                # cause pointing at a wrong fix. Retrying cannot clear a spent plan.
                await _brave_meter("rate_limited", status=429,
                                   body=_safe_body(resp), headers=resp.headers)
                logger.info("Brave search rate-limited (429) for %r — falling back to free stack", query[:60])
                return []
            if resp.status_code in (401, 403):
                _cb.record_failure(reason="auth")
                await _brave_meter("auth_failed", status=resp.status_code)
                logger.warning("Brave search auth failed (%s) — check BRAVE_SEARCH_API_KEY secret", resp.status_code)
                return []
            if resp.status_code != 200:
                _cb.record_failure(reason=classify_status(resp.status_code))
                await _brave_meter("http_error", status=resp.status_code)
                return []
            data = resp.json()
            web = (data.get("web") or {}).get("results") or []
            results: list[SearchResult] = []
            for item in web[:max_results]:
                title = (item.get("title") or "").strip()
                url = (item.get("url") or "").strip()
                snippet = re.sub(r"<[^>]+>", "", (item.get("description") or ""))[:300]
                if not (title and url):
                    continue
                results.append(SearchResult(
                    title=title, url=url, snippet=snippet, source="brave",
                    credibility_tier=_score_credibility(url),
                    language=language,
                    timestamp=(item.get("age") or item.get("page_age") or ""),
                ))
            _cb.record_success()
            # R-F3868 — count the call that SUCCEEDED too. A meter that only counts
            # failures cannot answer "how much of the plan have we used", which is
            # the question that matters BEFORE the plan is spent. `empty` is tracked
            # separately from `ok` because a 200-with-zero still consumes quota.
            await _brave_meter("ok" if results else "empty", status=200)
            # R-F3051 — 200-with-zero means over-constrained, not unavailable. Retry
            # ONCE with the qualifier terms dropped (quoted phrase + OR-block kept).
            # Bounded to a single extra call so a genuinely zero-result subject costs
            # at most one more request, and only when relaxing actually changes the
            # query. `_relaxed` is not recursive: the retry cannot itself retry.
            if not results and not _is_relaxed_retry:
                relaxed = _relax_brave_query(query)
                if relaxed:
                    logger.info(
                        "[R-F3051] Brave returned 0 for an over-constrained query — "
                        "retrying relaxed: %r -> %r", query[:70], relaxed[:70])
                    retry = await _search_brave(
                        relaxed, max_results=max_results, language=language,
                        _is_relaxed_retry=True)
                    if retry:
                        for r in retry:
                            # Honest provenance: this answered a RELAXED query, so a
                            # consumer weighing precision knows the qualifier was dropped.
                            try:
                                r.query_relaxed = True
                            except Exception:
                                pass
                        return retry
            logger.info("Brave search %r → %d results (lang=%s)", query[:60], len(results), language)
            return results
    except Exception as e:
        _cb.record_failure(reason="timeout")
        await _brave_meter("timeout")
        logger.warning("Brave search failed: %s", e)
        wire_failure(
            module="web_search._search_brave",
            detail=f"brave backend failed (returned [] — fell back to free stack): {e}",
            gap_type="search_backend_failure",
            source="web_search",
        )
        return []


# ── Backend: SearXNG (free meta-search) ─────────────────────────────────────

def _zero_results_is_expected(query: object) -> bool:
    """R-F3361 — True when a zero-result response says nothing about engine health.

    A `site:`-restricted query narrows the result space to a single domain, and
    for the domains ARIA actually restricts to (x/twitter, linkedin, facebook)
    the upstream engines hold no index at all — so zero is the ORDINARY outcome,
    not evidence that search is blocked. Matches the `site:` OPERATOR only, so
    the word "site" in ordinary prose does not disarm the real alarm.
    """
    if not isinstance(query, str):
        return False
    return "site:" in query.lower()


async def _search_searxng(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """SearXNG — free meta-search engine, tries multiple instances.
    Circuit breaker per instance — skips instances that are DOWN.

    R-F178 (2026-05-11): instance list has been empty since 2026-04-20 (5
    public instances all dead). Short-circuit immediately so the parallel
    backend gather doesn't waste a coroutine + log line every search.

    R-F183 (2026-05-11): when the R-F86 self-host adapter is configured
    (SEARXNG_URL env set, search_searxng.is_configured()=True) use it
    instead. That adapter is the independence-roadmap path: a Fly.io
    machine running the SearXNG Docker image returns free search results
    at $0 marginal cost AND doesn't share an IP with public instances
    that rate-limit aggressively. Operator activates by deploying the
    SearXNG container and setting SEARXNG_URL.
    """
    # R-F183: prefer the self-host adapter when configured.
    # R-F1790: guard the self-host path with a circuit breaker — it is the
    # PRIMARY backend (aria-searxng) and had none (the breaker further down
    # only guards the dead public-instance loop).
    from .circuit_breaker import get_breaker as _get_cb
    _sx_cb = _get_cb("search:searxng-selfhost", failure_threshold=5, cooldown_seconds=300)
    try:
        from . import search_searxng as _sx
        if _sx.is_configured() and not _sx_cb.is_open():
            res = await _sx.search(query, count=max_results, lang=language or "en")
            if res.get("ok") and res.get("results"):
                _sx_cb.record_success()  # R-F1790
                out: list[SearchResult] = []
                for item in res["results"]:
                    url = (item.get("url") or "").strip()
                    if not url:
                        continue
                    out.append(SearchResult(
                        title=(item.get("title") or "")[:300],
                        url=url,
                        snippet=(item.get("snippet") or "")[:500],
                        source=f"searxng:{item.get('engine') or 'self-host'}",
                        credibility_tier=_score_credibility(url),
                    ))
                if out:
                    logger.debug("SearXNG (self-host R-F183): %d results for %r", len(out), query[:60])
                    return out
            # R-F1657: make-loud on empty SearXNG results when the adapter
            # returned ok but no results. A 200 with 0 results from a
            # configured self-host instance means search is BLOCKED (all
            # upstream engines CAPTCHA/rate-limited), not that the world
            # has no answer. Wire a failure so the brain knows.
            if res.get("ok") and res.get("configured") and not res.get("results"):
                # R-F3361 — zero results is only EVIDENCE about engine health
                # when the query could plausibly have matched something. A
                # `site:`-restricted query against a crawler-hostile domain
                # (twitter/x, linkedin — which ARIA generates itself in
                # company_investigator.py:733-734 and deep_researcher.py:707)
                # returns 0 every time, by construction. Treating that as proof
                # that "all upstream engines are blocked" did three harmful
                # things: recorded a false failure on the breaker for ARIA'S
                # PRIMARY SEARCH BACKEND, asserted a cause never established
                # (§22), and wired a false `search_all_engines_blocked` gap to
                # the brain and the coder. Six of nine zero-result events in the
                # live 2026-07-28 ledger window were exactly this. Open-web
                # queries returning zero still raise all three, loudly — that is
                # the real signal R-F1657 was built for and it is untouched.
                if _zero_results_is_expected(query):
                    logger.debug(
                        "SearXNG 0 results for a site:-restricted query — expected, "
                        "not an engine-health signal (query=%r)", str(query)[:60],
                    )
                else:
                    _sx_cb.record_failure(reason="rate_limit")  # R-F1790: 0 results = upstream blocked
                    logger.warning(
                        "SearXNG returned 0 results — all upstream engines likely blocked "
                        "(query=%r, backend=%s)",
                        query[:60], res.get("backend", "searxng"),
                    )
                    try:
                        from .engine_wiring import wire_failure as _wf1657
                        _wf1657(
                            module="web_search",
                            detail=f"SearXNG 0 results: configured instance returned empty "
                                   f"for query={query[:80]} — all upstream engines blocked",
                            gap_type="search_all_engines_blocked",
                            source="web_search:_search_searxng",
                        )
                    except Exception:
                        pass
            # If self-host returned no results / error, fall through to
            # the legacy public-instance loop (currently empty); the path
            # is harmless and lets us add public instances later.
    except Exception as _sx_e:
        _sx_cb.record_failure(reason="timeout")  # R-F1790
        logger.debug("R-F183 searxng self-host probe failed: %s", _sx_e)
        wire_failure(
            module="web_search._search_searxng",
            detail=f"searxng self-host probe failed: {_sx_e}",
            gap_type="search_backend_failure",
            source="web_search",
        )
    if not SEARXNG_INSTANCES:
        return []
    from .circuit_breaker import get_breaker
    for instance in SEARXNG_INSTANCES:
        cb = get_breaker(f"searx:{instance.split('//')[1].split('/')[0]}", cooldown_seconds=600)
        if cb.is_open():
            continue
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(
                    f"{instance}/search",
                    params={
                        "q": query, "format": "json",
                        "language": language, "pageno": 1,
                        "categories": "general",
                    },
                    headers={"User-Agent": random_ua()},
                )
                if resp.status_code != 200:
                    cb.record_failure()
                    continue
                cb.record_success()
                data = resp.json()
                results = []
                for item in (data.get("results", []))[:max_results]:
                    r = SearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        snippet=item.get("content", ""),
                        source=f"searxng:{instance.split('//')[1].split('/')[0]}",
                        credibility_tier=_score_credibility(item.get("url", "")),
                    )
                    results.append(r)
                if results:
                    logger.debug("SearXNG (%s): %d results for %r", instance, len(results), query[:60])
                    return results
        except Exception:
            cb.record_failure()
            continue
    return []


# ── Backend: Academic APIs (Semantic Scholar + OpenAlex + CrossRef) ─────────

async def _search_academic(
    query: str,
    max_results: int = 10,
    language: str = "en",
) -> list[SearchResult]:
    """Tier-2 fan-out across three academic registries. Not Tier-1
    (those are official gov / IGO) but stronger signal than general-web
    press for technical, research, compliance, and programme-history
    questions. Added 2026-04-20 to fill the gap left by the dropped
    SearXNG backend.

    `language` is currently ignored — academic APIs default to English
    and translation-aware search is out of scope for this integration.
    """
    # R-F193 (2026-05-11): no longer skip non-English. OpenAlex /
    # CrossRef / Semantic Scholar index plenty of non-English titles
    # with English metadata. Pre-R-F193 the 3-extra-langs branch in
    # the multilingual fan-out wasted 3 coroutine slots on early-
    # return; now the academic backend contributes English-language
    # papers about foreign-language entity queries (e.g. searching
    # "savunma sanayii baskanligi" still surfaces English papers
    # about the SSB).
    from .circuit_breaker import get_breaker as _get_cb_ac
    _cb = _get_cb_ac("search:academic", failure_threshold=5, cooldown_seconds=300)
    if _cb.is_open():
        return []
    from .sources import academic as _ac
    try:
        raw = await _ac.search_all(query, max_results_per_api=max_results)
    except Exception as exc:
        _cb.record_failure(reason="timeout")
        # R-F1614 make-loud: a backend error here returns [] which is
        # indistinguishable from "no results" — a provider failure must
        # not masquerade as confidently-ungrounded "nothing found".
        logger.warning("_search_academic fan-out failed: %s", exc)
        wire_failure(
            module="web_search._search_academic",
            detail=f"academic backend fan-out failed (returned [] as if no results): {exc}",
            gap_type="search_backend_failure",
            source="web_search",
        )
        return []
    _cb.record_success()
    out: list[SearchResult] = []
    for r in raw:
        # Convert the dict shape academic.py returns into the dataclass
        # web_search uses internally. credibility_tier is set by the
        # adapter (tier 2 for all academic sources).
        out.append(SearchResult(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("snippet", ""),
            source=r.get("source", "academic"),
            credibility_tier=int(r.get("credibility_tier", 2)),
            language=r.get("language", "en"),
        ))
    return out


# ── Backend: Google News RSS (free, news-focused) ───────────────────────────

async def _search_gnews_api(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """GNews.io API backend, enabled by GNEWS_API_KEY.

    This is distinct from Google News RSS. It consumes the operator-provided
    gnews.io key server-side only, returns [] when unconfigured, and wires
    provider failures so a broken paid source is not mistaken for no news.
    """
    if not GNEWS_API_KEY:
        return []
    from .circuit_breaker import get_breaker as _get_cb_gnews
    _cb = _get_cb_gnews("search:gnews_api", failure_threshold=5, cooldown_seconds=300)
    if _cb.is_open():
        return []

    params = {
        "q": query,
        "lang": (language or "en")[:2],
        "max": max(1, min(int(max_results or 10), 10)),
        "apikey": GNEWS_API_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get("https://gnews.io/api/v4/search", params=params)
            if resp.status_code != 200:
                _cb.record_failure(reason=classify_status(resp.status_code))
                wire_failure(
                    module="web_search._search_gnews_api",
                    detail=f"gnews_api returned HTTP {resp.status_code}",
                    gap_type="search_backend_failure",
                    source="web_search",
                )
                return []
            data = resp.json()
            results: list[SearchResult] = []
            for item in (data.get("articles") or [])[:max_results]:
                url = (item.get("url") or "").strip()
                title = (item.get("title") or "").strip()
                if not url or not title:
                    continue
                results.append(SearchResult(
                    title=title[:300],
                    url=url,
                    snippet=(item.get("description") or item.get("content") or "")[:500],
                    source="gnews_api",
                    credibility_tier=_score_credibility(url),
                    language=(language or "en")[:2],
                    timestamp=(item.get("publishedAt") or ""),
                ))
            _cb.record_success()
            return results
    except Exception as e:
        _cb.record_failure(reason="timeout")
        logger.warning("GNews API search failed: %s", e)
        wire_failure(
            module="web_search._search_gnews_api",
            detail=f"gnews_api backend failed (returned [] as if no results): {e}",
            gap_type="search_backend_failure",
            source="web_search",
        )
        return []


async def _search_google_news(query: str, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """Google News RSS — free, news-specific, ~30 results max.
    
    R-F2059: circuit breaker + wire_failure on error. Pre-R-F2059 this
    backend had NO circuit breaker — a persistent Google News outage
    (rate-limit, DNS failure, IP block) would burn a request on EVERY
    search call with no cooldown, silently returning [] each time.
    """
    from .circuit_breaker import get_breaker as _get_cb_gn
    _cb = _get_cb_gn("search:google_news", failure_threshold=5, cooldown_seconds=300)
    if _cb.is_open():
        return []
    lang_map = {"en": "en", "pt": "pt-PT", "fr": "fr", "ar": "ar", "es": "es", "tr": "tr"}
    hl = lang_map.get(language, "en")
    encoded = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl={hl}&gl=US&ceid=US:en"

    try:
        # F78c 2026-04-29: news.google.com/rss/search returns 302 to
        # the actual feed URL (cluster-specific). httpx does NOT follow
        # redirects by default, so we silently dropped Google News from
        # every parallel-gather web_search call ("1 backends" in the
        # live log when only Crossref returned). follow_redirects=True
        # restores the second backend.
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": random_ua()})
            if resp.status_code != 200:
                _cb.record_failure(reason=classify_status(resp.status_code))
                return []
            # Parse RSS XML
            text = resp.text
            results = []
            items = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
            for item_xml in items[:max_results]:
                title_m = re.search(r"<title>(.*?)</title>", item_xml)
                link_m = re.search(r"<link>(.*?)</link>", item_xml)
                desc_m = re.search(r"<description>(.*?)</description>", item_xml)
                pub_m = re.search(r"<pubDate>(.*?)</pubDate>", item_xml)
                title = (title_m.group(1) if title_m else "").strip()
                link = (link_m.group(1) if link_m else "").strip()
                snippet = (desc_m.group(1) if desc_m else "").strip()
                snippet = re.sub(r"<[^>]+>", "", snippet)[:300]
                pub = (pub_m.group(1) if pub_m else "").strip()
                if title and link:
                    results.append(SearchResult(
                        title=title, url=link, snippet=snippet,
                        source="google_news", timestamp=pub,
                        credibility_tier=_score_credibility(link),
                    ))
            _cb.record_success()
            logger.debug("Google News: %d results for %r", len(results), query[:60])
            return results
    except Exception as e:
        _cb.record_failure(reason="timeout")
        # R-F1614 make-loud: backend error → [] looks like "no results".
        logger.warning("Google News search failed: %s", e)
        wire_failure(
            module="web_search._search_google_news",
            detail=f"google_news backend failed (returned [] as if no results): {e}",
            gap_type="search_backend_failure",
            source="web_search",
        )
        return []


# ── Backend: GDELT Project global news (free API, no key, datacenter-TOLERANT) ──
_GDELT_LAST_CALL: float = 0.0  # R-F2257: respect GDELT's ~1-request-per-5s rate limit
_GDELT_CACHE: dict = {}  # R-F2272: query(lower) -> (monotonic_ts, results) — §15 pay-once
_GDELT_CACHE_TTL: float = 1800.0  # 30 min — so the rate-limited leg reliably contributes


async def _search_gdelt(query: str, max_results: int = 10) -> list[SearchResult]:
    """GDELT DOC 2.0 — free, no-key, GLOBAL news/events on any entity in any country.

    R-F2257 — the robustness pivot: GDELT is an API SOURCE, not a scraper, so it SERVES
    a datacenter IP (HTTP 200) instead of CAPTCHA-ing it like Google/Bing/SearXNG do.
    That is the whole point — API-first coverage that doesn't collapse from a datacenter
    egress. It rate-limits to ~1 req/5s (429, NOT a block), so we throttle + circuit-break
    rather than hammer it. Gives real coverage on foreign entities where registries return nothing.
    """
    global _GDELT_LAST_CALL
    q = (query or "").strip()
    if len(q) < 3:  # GDELT rejects ultra-short queries
        return []
    _ck = q.lower()
    # R-F2272 — §15 pay-once: GDELT returns real coverage (verified: 200 + 10 articles for a
    # single query) but rate-limits to 1/5s. A DD fires many queries; without this cache its
    # articles are lost to the throttle on every query after the first. Serve the cache so the
    # promised leg reliably lands in the results instead of silently returning [].
    _cached = _GDELT_CACHE.get(_ck)
    if _cached and (time.monotonic() - _cached[0] < _GDELT_CACHE_TTL):
        return _cached[1][:max_results]
    from .circuit_breaker import get_breaker as _get_cb_g
    _cb = _get_cb_g("search:gdelt", failure_threshold=5, cooldown_seconds=300)
    if _cb.is_open():
        return _cached[1][:max_results] if _cached else []
    # Respect the 1-req/5s limit — but return any (stale) cache rather than nothing.
    _now = time.monotonic()
    if _now - _GDELT_LAST_CALL < 5.0:
        return _cached[1][:max_results] if _cached else []
    _GDELT_LAST_CALL = _now
    encoded = urllib.parse.quote(f'"{q}"' if " " in q else q)  # phrase-match the entity
    url = (f"https://api.gdeltproject.org/api/v2/doc/doc?query={encoded}"
           f"&mode=artlist&maxrecords={min(max_results, 25)}&format=json&sort=datedesc")
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": random_ua()})
            if resp.status_code == 429:
                # rate-limited (not a source failure) — no breaker trip, just skip this cycle
                logger.debug("GDELT rate-limited (429) for %r", q[:60])
                return []
            if resp.status_code != 200:
                _cb.record_failure(reason=classify_status(resp.status_code))
                return []
            try:
                data = resp.json()
            except Exception:
                return []  # GDELT returns a plain-text rate-limit notice on overuse
            arts = (data or {}).get("articles", []) or []
            results = []
            for a in arts[:max_results]:
                title = (a.get("title") or "").strip()
                link = (a.get("url") or "").strip()
                if not (title and link):
                    continue
                results.append(SearchResult(
                    title=title, url=link,
                    snippet=f"[GDELT global-news · {a.get('domain','')} · {a.get('seendate','')}]",
                    source="gdelt", timestamp=a.get("seendate", ""),
                    credibility_tier=_score_credibility(link),
                ))
            _cb.record_success()
            if results:  # R-F2272 — cache a real hit so the throttle can serve it later
                _GDELT_CACHE[_ck] = (time.monotonic(), results)
            logger.debug("GDELT: %d results for %r", len(results), q[:60])
            return results
    except Exception as e:
        _cb.record_failure(reason="timeout")
        logger.warning("GDELT search failed: %s", e)
        wire_failure(
            module="web_search._search_gdelt",
            detail=f"gdelt backend failed (returned [] as if no results): {e}",
            gap_type="search_backend_failure", source="web_search",
        )
        return []


# ── Backend: DuckDuckGo HTML scrape (free, no auth, no API) ────────────────

async def _search_duckduckgo(query: str, max_results: int = 10) -> list[SearchResult]:
    """R-F120 (2026-05-09): DuckDuckGo HTML scrape — free, no API key,
    no rate limit hard cap, hits the same general-web index Brave does.
    Critical fallback when Brave billing exhausts (circuit OPEN) and no
    SearXNG instance is configured. Live coverage of trade shows, contract
    signings, defence press releases that academic backends miss.

    R-F150 (2026-05-10): per-host circuit breaker. Live log 2026-05-10
    11:28:01 showed every DDG POST returning 202 (queued/rate-limited)
    rather than 200. The function correctly returned [] but burned 5
    requests per chat-turn against a backend that wasn't going to answer.
    Add an explicit 202 path that records a failure and an early-return
    when the breaker is open. Cooldown 10 min = enough for DDG to clear
    its rate-limit window without burning more probes than needed.
    """
    from .circuit_breaker import get_breaker
    cb = get_breaker("search:duckduckgo", failure_threshold=5, cooldown_seconds=600)
    if cb.is_open():
        return []
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.post(
                url,
                headers={
                    "User-Agent": random_ua(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            if resp.status_code == 202:
                # R-F150: DDG returns 202 Accepted when queued or rate-
                # limited. Surface it explicitly so the production log
                # shows the breaker reason (was being silently swallowed
                # at debug level before).
                logger.info("DuckDuckGo returned 202 (rate-limited/queued) for %r", query[:60])
                cb.record_failure()
                return []
            if resp.status_code != 200:
                cb.record_failure()
                return []
            html = resp.text
            results: list[SearchResult] = []
            # DDG HTML format: <a class="result__a" href="...">Title</a>
            #                  <a class="result__snippet">Snippet</a>
            pattern = re.compile(
                r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.+?)</a>'
                r'.*?<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.+?)</a>',
                re.DOTALL | re.IGNORECASE,
            )
            for m in pattern.finditer(html):
                u = m.group("url")
                title = re.sub(r"<[^>]+>", "", m.group("title")).strip()
                snippet = re.sub(r"<[^>]+>", "", m.group("snippet")).strip()[:300]
                # DDG redirects through /l/?uddg=<url>; unwrap
                if "/l/?uddg=" in u:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(u).query)
                    if "uddg" in qs:
                        u = qs["uddg"][0]
                if not title or not u or not u.startswith("http"):
                    continue
                results.append(SearchResult(
                    title=title, url=u, snippet=snippet,
                    source="duckduckgo",
                    credibility_tier=_score_credibility(u),
                ))
                if len(results) >= max_results:
                    break
            cb.record_success()
            logger.debug("DuckDuckGo: %d results for %r", len(results), query[:60])
            return results
    except Exception as e:
        cb.record_failure()
        # R-F1614 make-loud: breaker records it internally but the brain
        # never sees the backend error — wire it so a provider failure
        # isn't silently read as "no results".
        logger.warning("DuckDuckGo search failed: %s", e)
        wire_failure(
            module="web_search._search_duckduckgo",
            detail=f"duckduckgo backend failed (returned [] as if no results): {e}",
            gap_type="search_backend_failure",
            source="web_search",
        )
        return []


# ── Backend: Bing News RSS (free fallback) ──────────────────────────────────

async def _search_bing_news(query: str, max_results: int = 10) -> list[SearchResult]:
    """Bing News RSS — free fallback.
    
    R-F2059: circuit breaker + wire_failure on error. Pre-R-F2059 this
    backend had NO circuit breaker — a persistent Bing outage would burn
    a request on EVERY search call with no cooldown.
    """
    from .circuit_breaker import get_breaker as _get_cb_bn
    _cb = _get_cb_bn("search:bing_news", failure_threshold=5, cooldown_seconds=300)
    if _cb.is_open():
        return []
    encoded = urllib.parse.quote_plus(query)
    url = f"https://www.bing.com/news/search?q={encoded}&format=rss"
    try:
        # Same redirect issue as Google News (F78c) — Bing routes RSS
        # requests through interstitial 302s for region/locale.
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": random_ua()})
            if resp.status_code != 200:
                _cb.record_failure(reason=classify_status(resp.status_code))
                return []
            text = resp.text
            results = []
            items = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
            for item_xml in items[:max_results]:
                title_m = re.search(r"<title>(.*?)</title>", item_xml)
                link_m = re.search(r"<link>(.*?)</link>", item_xml)
                desc_m = re.search(r"<description>(.*?)</description>", item_xml)
                title = (title_m.group(1) if title_m else "").strip()
                link = (link_m.group(1) if link_m else "").strip()
                snippet = re.sub(r"<[^>]+>", "", (desc_m.group(1) if desc_m else ""))[:300]
                if title and link:
                    results.append(SearchResult(
                        title=title, url=link, snippet=snippet,
                        source="bing_news",
                        credibility_tier=_score_credibility(link),
                    ))
            _cb.record_success()
            return results
    except Exception as e:
        _cb.record_failure(reason="timeout")
        # R-F1614 make-loud: this was a fully-silent swallow — a Bing
        # backend error returned [] with no log at all, indistinguishable
        # from "no results" (confidently ungrounded).
        logger.warning("Bing News search failed: %s", e)
        wire_failure(
            module="web_search._search_bing_news",
            detail=f"bing_news backend failed (returned [] as if no results): {e}",
            gap_type="search_backend_failure",
            source="web_search",
        )
        return []


# ── Core search functions ───────────────────────────────────────────────────

# ── R-F124 — Memory-first inversion (2026-05-10) ─────────────────────────────
# Pay-once-remember-forever doctrine: every paid API call is absorbed
# into rag_store + intel_ledger, so a repeat query should hit memory
# first and skip the web entirely if the corpus is already strong on
# the topic. Cuts 40-60% of repeat-query LLM/Brave spend.
async def _query_memory(
    query: str, *, max_results: int = 10
) -> list[SearchResult]:
    """Pull recent corpus hits for the query as SearchResult-shaped items.

    Hits rag_store.search() (chromadb-backed semantic) + tags each
    result with credibility_tier=2 (institutional memory) and a
    relevance_score that already reflects the embedding similarity.
    Fail-open — returns [] on any error so live web search still runs.
    """
    out: list[SearchResult] = []
    try:
        from . import rag_store
        hits = await asyncio.wait_for(
            rag_store.search(query, top_k=max_results),
            timeout=4.0,
        )
        for h in hits or []:
            if not isinstance(h, dict):
                continue
            url = (h.get("url") or "").strip()
            if not url:
                # No URL — synthesise an opaque memory:// pointer so the
                # dedupe key stays stable across repeated retrievals
                _id = hashlib.sha1(
                    (h.get("source") or h.get("title") or h.get("text") or "")[:200]
                    .encode("utf-8")
                , usedforsecurity=False).hexdigest()[:12]
                url = f"memory://{_id}"
            out.append(SearchResult(
                title=(h.get("title") or h.get("source") or "memory")[:200],
                url=url,
                snippet=(h.get("text") or "")[:500],
                source=f"memory:{h.get('collection') or h.get('source_type') or 'rag'}",
                credibility_tier=2,
                language=h.get("language") or "en",
                timestamp=h.get("ingested_at") or "",
                relevance_score=float(h.get("score") or 0.0) + 0.5,  # memory-bonus
            ))
    except Exception as _me:
        logger.debug("memory-first query failed (non-fatal): %s", _me)
    return out


# ── R-F125 — Auto-language fan-out (2026-05-10) ──────────────────────────────
# Detect non-English script + entity-name suffix heuristics, then run
# the same query in those languages so e.g. "SAHA 2026 contracts"
# (Turkish defence trade show) also probes tr-TR sources where the
# actual contract press releases live.
def _detect_query_languages(query: str, base_lang: str = "en") -> list[str]:
    """Return additional language codes to fan out to (excludes base_lang)."""
    extras: set[str] = set()
    if not query:
        return []
    # Unicode-script detection
    script_map = [
        ("Ѐ-ӿ", "ru"),   # Cyrillic
        ("؀-ۿ", "ar"),   # Arabic
        ("֐-׿", "he"),   # Hebrew
        ("一-鿿", "zh"),   # CJK Unified
        ("぀-ゟ", "ja"),   # Hiragana
        ("가-힯", "ko"),   # Hangul
        ("ऀ-ॿ", "hi"),   # Devanagari
    ]
    for rng, lang in script_map:
        try:
            if re.search(f"[{rng}]", query):
                extras.add(lang)
        except Exception:
            continue
    # Entity-name + locale heuristics on the Latin-script portion.
    # R-F135 (2026-05-10): expanded to include English country names
    # ("Turkey", "Brazil", "UAE", etc) so queries like "Assan Group Turkey"
    # trigger Turkish fan-out. Without this, the only Turkish-language
    # signal that activates fan-out is a native marker the operator
    # rarely types in English DD prep. Live evidence: SAHA 2026 was
    # caught by R-F125 (which detects "saha "), but "Assan Group Turkey"
    # produced 12 Google-News results from English-only fan-out and
    # missed Türkiye-press indexing of the espionage probe.
    q_lower = query.lower()
    # Word-boundary helper for short English country names so "uae"
    # doesn't false-match "uaeu" / "guam" etc.
    def _has_word(word: str, text: str) -> bool:
        import re as _re_dl
        return bool(_re_dl.search(rf"(^|[^a-z0-9]){_re_dl.escape(word)}($|[^a-z0-9])", text))

    tr_markers_native = ("aş.", " a.ş.", " a.s.", " sti.", " ş.", " ltd.şti", "türk",
                         "türkiye", "istanbul", "ankara", "saha ", "idef",
                         "tusaş", "asisguard", "aselsan", "roketsan", "stm ")
    tr_words = ("turkey", "turkish", "turkiye")
    if any(m in q_lower for m in tr_markers_native) or any(_has_word(w, q_lower) for w in tr_words):
        extras.add("tr")

    pt_markers_native = (" lda", " ltda", " s.a.", "brasil", "portugal", "lisboa",
                         "moçambique", "moçamb", "lusofon", "embraer", "luanda", "maputo")
    pt_words = ("brazil", "brazilian", "portuguese", "angola", "angolan",
                "mozambique", "mozambican", "lusophone")
    if any(m in q_lower for m in pt_markers_native) or any(_has_word(w, q_lower) for w in pt_words):
        extras.add("pt")

    es_markers_native = (" s.l.", "españa", "españ", "méxico", "indra ", "navantia",
                         "buenos aires", "bogotá", "lima ", "caracas")
    es_words = ("spain", "spanish", "mexico", "mexican", "argentina", "argentinian",
                "colombia", "colombian", "peru", "peruvian", "venezuela", "venezuelan",
                "chile", "chilean")
    if any(m in q_lower for m in es_markers_native) or any(_has_word(w, q_lower) for w in es_words):
        extras.add("es")

    fr_markers_native = (" s.a.r.l", " sarl", "société", "française", "côte d'ivoire",
                         "sénégal", "burkina", "thales", "naval group", "dassault",
                         "dakar", "abidjan")
    fr_words = ("france", "french", "senegal", "ivory coast", "ivorian", "moroccan",
                "tunisian", "algerian")
    if any(m in q_lower for m in fr_markers_native) or any(_has_word(w, q_lower) for w in fr_words):
        extras.add("fr")

    de_markers_native = (" gmbh", " ag ", " kg ", "deutschland", "rheinmetall",
                         "diehl", "krauss-maffei", "hensoldt", "münchen", "berlin")
    de_words = ("germany", "german", "austrian", "austria", "swiss")
    if any(m in q_lower for m in de_markers_native) or any(_has_word(w, q_lower) for w in de_words):
        extras.add("de")

    ar_markers_native = ("idex", "navdex", "edge group", "abu dhabi", "riyadh", "doha",
                         "manama", "kuwait city", "amman ")
    ar_words = ("uae", "saudi", "qatar", "qatari", "bahrain", "bahraini",
                "kuwait", "kuwaiti", "oman", "omani", "jordan", "jordanian",
                "iraq", "iraqi", "lebanon", "lebanese", "egyptian", "egypt",
                "emirates", "emirati")
    if any(m in q_lower for m in ar_markers_native) or any(_has_word(w, q_lower) for w in ar_words):
        extras.add("ar")

    # Asia-Pacific defence-supplier markets — Korean / Japanese / Chinese /
    # Indian / Indonesian. English country names trigger fan-out so e.g.
    # "Hanwha Aerospace Korea" probes ko-KR press alongside en-US.
    ko_markers = ("hanwha", "kai aerospace", "lig nex1", "poongsan",
                  "hyundai rotem", "seoul", "busan")
    if any(m in q_lower for m in ko_markers) or _has_word("korea", q_lower) or _has_word("korean", q_lower):
        extras.add("ko")
    ja_markers = ("mitsubishi heavy", "kawasaki heavy", "ihi corp",
                  "nec corp", "tokyo", "osaka", "subaru")
    if any(m in q_lower for m in ja_markers) or _has_word("japan", q_lower) or _has_word("japanese", q_lower):
        extras.add("ja")
    zh_markers = ("norinco", "avic", "casc ", "casic", "cssc", "beijing",
                  "shanghai", "shenzhen", "hikvision", "dahua",
                  "cnsig", "hangzhou", "huawei")
    if any(m in q_lower for m in zh_markers) or _has_word("china", q_lower) or _has_word("chinese", q_lower):
        extras.add("zh")
    # Russian — Wagner / Rosoboronexport / etc. Cyrillic script
    # detection (above) catches native spellings; this catches Latin.
    ru_markers = ("rosoboronexport", "almaz-antey", "kalashnikov", "moscow",
                  "rostec", "rosatom", "wagner group", "wagner pmc",
                  "uralvagonzavod", "sukhoi")
    if any(m in q_lower for m in ru_markers) or _has_word("russia", q_lower) or _has_word("russian", q_lower):
        extras.add("ru")
    hi_markers = ("hal aerospace", "drdo", "tata defence", "bharat dynamics",
                  "mumbai", "delhi", "bengaluru", "hyderabad")
    if any(m in q_lower for m in hi_markers) or _has_word("india", q_lower) or _has_word("indian", q_lower):
        extras.add("hi")
    # Strip the base language so we only return the fan-out additions
    extras.discard((base_lang or "en").lower())
    return sorted(extras)


# ── R-F126 — Defence-show calendar (2026-05-10) ──────────────────────────────
# When an operator asks about a known defence event the post-event press
# releases live on the official site + organiser PR + trade press —
# rarely indexed in time by general news engines (the SAHA 2026 case).
# Map known events to their official site + a curated query enrichment.
DEFENCE_EVENTS: dict[str, dict[str, str]] = {
    "saha":        {"site": "sahaexpo.com",       "lang": "tr",
                    "enrich": "SAHA EXPO İstanbul defence industry summit"},
    "idex":        {"site": "idexuae.ae",         "lang": "ar",
                    "enrich": "IDEX Abu Dhabi defence exhibition contracts"},
    "navdex":      {"site": "navdex.ae",          "lang": "ar",
                    "enrich": "NAVDEX naval defence exhibition Abu Dhabi"},
    "eurosatory":  {"site": "eurosatory.com",     "lang": "fr",
                    "enrich": "Eurosatory Paris défense salon contrats"},
    "dsei":        {"site": "dsei.co.uk",         "lang": "en",
                    "enrich": "DSEI ExCeL London defence equipment contracts"},
    "ausa":        {"site": "ausa.org",           "lang": "en",
                    "enrich": "AUSA Annual Meeting US Army contracts"},
    "dubai airshow": {"site": "dubaiairshow.aero", "lang": "ar",
                      "enrich": "Dubai Airshow contracts orders 2026"},
    "le bourget":  {"site": "siae.fr",            "lang": "fr",
                    "enrich": "Salon du Bourget Paris Air Show contrats"},
    "indo defence": {"site": "indodefence.com",   "lang": "id",
                     "enrich": "Indo Defence Jakarta procurement"},
    "lima":        {"site": "limaexhibition.com", "lang": "en",
                    "enrich": "LIMA Langkawi maritime aerospace exhibition"},
    "dx korea":    {"site": "dxkorea.com",        "lang": "ko",
                    "enrich": "DX Korea Seoul defence procurement"},
    "africa aerospace": {"site": "aadexpo.co.za", "lang": "en",
                         "enrich": "Africa Aerospace Defence AAD Pretoria"},
    "expodefensa": {"site": "expodefensa.com.co", "lang": "es",
                    "enrich": "Expodefensa Colombia defensa contratos"},
    "lima maritime": {"site": "limaexhibition.com", "lang": "en",
                      "enrich": "LIMA Langkawi maritime"},
    "world defense show": {"site": "worlddefenseshow.com", "lang": "ar",
                           "enrich": "World Defense Show Riyadh contracts"},
    "ila berlin":  {"site": "ila-berlin.com",     "lang": "de",
                    "enrich": "ILA Berlin air space defence trade fair"},
    "balt military": {"site": "baltmilitary.com", "lang": "en",
                      "enrich": "Balt Military Expo Gdańsk procurement"},
    "milipol":     {"site": "milipol.com",        "lang": "fr",
                    "enrich": "Milipol homeland security exhibition"},
}


# R-F192 (2026-05-11) — aliases per event so the full name + common
# rephrasings match. Pre-R-F192 the matcher was substring-only on the
# abbreviation key, so "Defence & Security Equipment International"
# didn't match the "dsei" key (no overlap). Now full names + organiser
# names + variant spellings all route to the right event.
DEFENCE_EVENT_ALIASES: dict[str, list[str]] = {
    "saha":               ["saha expo", "sahaexpo", "saha istanbul"],
    "idex":               ["international defence exhibition", "abu dhabi defence"],
    "navdex":             ["naval defence and security"],
    "eurosatory":         ["salon eurosatory", "paris defence exhibition", "défense paris"],
    "dsei":               ["defence and security equipment international",
                           "defence security equipment international",
                           "excel london defence"],
    "ausa":               ["association of the united states army", "ausa meeting"],
    "dubai airshow":      ["dubai air show"],
    "le bourget":         ["paris air show", "siae paris", "salon du bourget"],
    "indo defence":       ["indo defence jakarta"],
    "lima":               ["langkawi international maritime aerospace"],
    "dx korea":           ["defence expo korea"],
    "africa aerospace":   ["aad expo", "africa aerospace and defence"],
    "expodefensa":        ["expodefensa colombia"],
    "lima maritime":      ["langkawi maritime"],
    "world defense show": ["wds riyadh"],
    "ila berlin":         ["ila aerospace berlin", "internationale luftfahrtausstellung"],
}


def _detect_defence_event(query: str) -> dict[str, str] | None:
    """Return the calendar entry for any known defence event mentioned.

    R-F192: matches the canonical key, every alias, AND uses word-
    boundary regex so 'dsei' doesn't false-match the middle of an
    unrelated identifier. Pre-R-F192 was substring-only on the key.
    """
    if not query:
        return None
    q = query.lower()
    import re as _re_d
    for key, entry in DEFENCE_EVENTS.items():
        candidates = [key] + DEFENCE_EVENT_ALIASES.get(key, [])
        for c in candidates:
            # Word-boundary match. Escape since names contain spaces/&
            try:
                if _re_d.search(rf"\b{_re_d.escape(c)}\b", q):
                    return {"key": key, **entry}
            except Exception:
                continue
    return None


async def _search_defence_event(query: str, max_results: int = 10) -> list[SearchResult]:
    """If the query mentions a known defence event, run a site:-scoped
    Brave/DDG search against the official site so post-event press +
    contract-signing pages surface even when general news is thin.
    
    R-F2059: circuit breaker on the DDG sub-call. Pre-R-F2059 a
    persistent DDG outage would silently return [] every time with
    no cooldown.
    """
    from .circuit_breaker import get_breaker as _get_cb_de
    _cb = _get_cb_de("search:defence_event", failure_threshold=5, cooldown_seconds=300)
    if _cb.is_open():
        return []
    entry = _detect_defence_event(query)
    if not entry:
        return []
    site = entry.get("site") or ""
    if not site:
        return []
    enriched = f"{entry.get('enrich', query)} site:{site}"
    out: list[SearchResult] = []
    try:
        # R-F320: Brave removed; run DDG only against the site-scoped query
        ddg_results = await _search_duckduckgo(enriched, max_results)
        for r in ddg_results or []:
            # Tag with defence-event source and tier-2 (institutional)
            r.source = f"defence_event:{entry['key']}"
            r.credibility_tier = 2
            out.append(r)
        _cb.record_success()
    except Exception as _ee:
        _cb.record_failure(reason="timeout")
        logger.debug("defence-event search failed (non-fatal): %s", _ee)
        wire_failure(
            module="web_search._search_defence_event",
            detail=f"defence_event backend failed: {_ee}",
            gap_type="search_backend_failure",
            source="web_search",
        )
    return out


# ── R-F2059: search result cache (pay-once-remember-forever) ──────────────
# Cache search results by (query_hash, language) so repeated queries
# (common in DD workflows) hit cache instead of the wire. LRU with TTL.
# The cache is in-process only (not Redis) — resets on deploy, which is
# fine because the memory-first path (_query_memory) provides the durable
# cache across restarts.
_SEARCH_CACHE: dict[str, tuple[float, list[SearchResult]]] = {}
_SEARCH_CACHE_MAX = 200
_SEARCH_CACHE_TTL = 300  # 5 minutes — short enough for fresh data, long enough for repeat DD queries


def _search_cache_key(query: str, language: str) -> str:
    """Generate a cache key from query + language."""
    import hashlib
    raw = f"{query.lower().strip()}|{language}"
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()


def _search_cache_get(key: str) -> list[SearchResult] | None:
    """Get cached results if fresh. Returns None on miss/expiry."""
    entry = _SEARCH_CACHE.get(key)
    if entry is None:
        return None
    ts, results = entry
    if time.time() - ts > _SEARCH_CACHE_TTL:
        del _SEARCH_CACHE[key]
        return None
    return results


def _search_cache_set(key: str, results: list[SearchResult]) -> None:
    """Store results in cache. Evicts oldest entry if at capacity."""
    if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX:
        # Evict the oldest entry
        oldest = min(_SEARCH_CACHE.items(), key=lambda kv: kv[1][0])
        del _SEARCH_CACHE[oldest[0]]
    _SEARCH_CACHE[key] = (time.time(), results)


@fail_wire(module="web_search", gap_type="source_failure")
async def search(
    query: str,
    *,
    max_results: int = 10,
    language: str = "en",
    screening: bool = False,
    min_credibility: int = 6,
    require_triangulation: bool = False,
    use_brave: bool | None = None,
) -> list[SearchResult]:
    """ARIA's primary search — queries all backends, deduplicates, ranks.

    Args:
        query: search query
        max_results: max results to return
        language: search language (en, pt, fr, ar, es, tr)
        min_credibility: filter out results below this tier (1=best, 6=disinfo)
        require_triangulation: if True, only return results found by 2+ backends

    Returns:
        Deduplicated, credibility-scored, relevance-ranked results.
    """
    # R-F2318 — resolve the Brave gate FIRST so it can namespace the cache: a
    # free/autonomous-populated entry must NOT serve a Brave-on user (that would skip
    # the Brave call + miss the distillation capture), and a Brave-populated entry must
    # NOT bleed into the free-stack autonomous loop. Explicit param > context flag.
    _brave_on = (use_brave if use_brave is not None else brave_is_enabled()) \
        and bool(BRAVE_API_KEY) and not _BRAVE_GLOBALLY_OFF

    # R-F2059: search result cache — pay-once-remember-forever.
    # Check cache before fanning out to backends. Repeated queries
    # (common in DD workflows) skip the wire entirely. R-F2318: brave-namespaced.
    _cache_key = _search_cache_key(query, language) + ("|brave" if _brave_on else "")
    cached = _search_cache_get(_cache_key)
    if cached is not None:
        logger.debug("Search cache HIT for %r (language=%s, brave=%s)", query[:60], language, _brave_on)
        return cached[:max_results]

    # R-F120 (2026-05-09): added DuckDuckGo + Bing News to the main
    # parallel-gather. Live evidence: operator searched SAHA 2026
    # (Turkish defence trade show) when Brave was OPEN — Google News
    # alone returned thin coverage; academic backends had nothing
    # because the topic is industry news not papers; SearXNG not yet
    # deployed. DDG covers general web (substitutes for Brave), Bing
    # News covers news (mirrors Google News for redundancy).
    # R-F125 (2026-05-10): auto-language fan-out — detect non-English
    # script + Turkish/Portuguese/Arabic/etc entity-name markers and
    # add same query in those languages. Solves SAHA 2026 thin-coverage
    # case directly: Turkish defence press indexes the contract data
    # that Google-News-en doesn't surface.
    extra_langs = _detect_query_languages(query, base_lang=language)
    # R-F2318 — Brave is the PRIMARY backend when _brave_on (resolved above, before the
    # cache check): prepended so it leads; the free backends still run and provide
    # fallback (§14). Enabled ONLY for user-facing DD/research/search; the continuous
    # researcher never sets the flag → free stack.
    backend_tasks = ([_search_brave(query, MAX_RESULTS_PER_BACKEND, language)] if _brave_on else []) + [
        # R-F1630 (2026-06-17): _search_brave removed (R-F320 permanent stub).
        # R-F2318 (2026-07-02): restored as the PRIMARY backend for user-facing
        # DD/research/search (prepended above when _brave_on).
        # SearXNG self-host (R-F183) + DuckDuckGo cover general web.
        _search_searxng(query, MAX_RESULTS_PER_BACKEND, language),
        _search_duckduckgo(query, MAX_RESULTS_PER_BACKEND),
        _search_gnews_api(query, MAX_RESULTS_PER_BACKEND, language),
        _search_google_news(query, MAX_RESULTS_PER_BACKEND, language),
        _search_bing_news(query, MAX_RESULTS_PER_BACKEND),
        _search_academic(query, MAX_RESULTS_PER_BACKEND, language),
        _search_defence_event(query, MAX_RESULTS_PER_BACKEND),  # R-F126
        _search_gdelt(query, MAX_RESULTS_PER_BACKEND),  # R-F2257: free API, datacenter-tolerant global news
    ]
    for _xl in extra_langs[:3]:  # cap fan-out at 3 extra langs
        backend_tasks.append(_search_google_news(query, MAX_RESULTS_PER_BACKEND, _xl))

    # ── R-F3119 — IN DD SCOPE, BRAVE IS THE SEARCH TIER. NOT ONE OF TEN. ────
    #
    # OPERATOR DIRECTIVE (2026-07-26): "the entire DD tools should be using only
    # Claude API and Brave API". R-F2318 made Brave the PRIMARY for DD and left the
    # nine free backends running "as fallback (§14)" — but they do not run after
    # Brave, they run WITH it, sharing one `SEARCH_GATHER_BUDGET` under a
    # quorum+grace gather (R-F2226). Nine slow or silent backends therefore consume
    # the budget the primary needs.
    #
    # R-F3122 — RATIONALE CORRECTED. R-F3119 shipped claiming the free stack had
    # STARVED the primary by contention. That does not survive its own evidence: on
    # the measured run ALL TEN backends failed, INCLUDING Brave, while a direct Brave
    # call from the same machine answered in 1.2s. A contended gather would have let
    # the 1.2s backend win; ten-for-ten failure with a fast primary indicates
    # event-loop starvation in that cold local process, NOT budget contention. The
    # observation was real; the causal story was not, and it is withdrawn rather than
    # left standing as justification. This is a POLICY change (the routing
    # directive) — it was never a performance fix.
    #
    # OPERATOR (2026-07-26): "if brave fails utilise aria searxng". So a policy change
    # must not quietly cost resilience. Brave runs ALONE first; ARIA's OWN self-hosted
    # SearXNG (R-F183) runs only if Brave yields nothing. NOT the third-party free
    # stack — DuckDuckGo/GNews/Google/Bing/academic/defence-event/GDELT are absent
    # from the DD path in both phases, which is the directive and also §6.
    #
    # Index verified, not assumed: `backend_tasks` is
    #   [0] brave (prepended when _brave_on), [1] searxng, [2] duckduckgo, [3] gnews,
    #   [4] google_news, [5] bing_news, [6] academic, [7] defence_event, [8] gdelt
    # so SearXNG is [1]. The other seven coroutines are already CONSTRUCTED by the
    # list literal above, so they must be closed here — an un-awaited coroutine leaks
    # and emits a RuntimeWarning on every search.
    #
    # `memory` is always kept: our own local cache, $0, cannot time out on a network
    # (R-F185/§15 pay-once). The continuous researcher never sets the Brave flag, so
    # its free stack is untouched.
    _brave_exclusive = _brave_on and (
        os.getenv("ARIA_DD_BRAVE_EXCLUSIVE", "1") or "1"
    ).strip().lower() not in ("0", "false", "no")
    _brave_fallback_tasks: list = []
    _brave_fallback_suppressed = False
    if _brave_exclusive:
        # R-F3847 — operator designation: Brave is THE DD search engine, nothing
        # substitutes. The R-F3122 SearXNG fallback is the measured path by which a
        # French Chrome help page became "press coverage" in dd_92f9d77b8886. When
        # Brave has nothing, DD reports nothing — and SAYS so — rather than quietly
        # asking a degraded backend instead.
        if _dd_brave_only():
            _brave_fallback_suppressed = True
            for _unused in backend_tasks[1:2]:
                try:
                    _unused.close()
                except Exception:
                    pass
        else:
            _brave_fallback_tasks = backend_tasks[1:2]  # SearXNG only
        for _unused in backend_tasks[2:]:
            try:
                _unused.close()
            except Exception:
                pass
        backend_tasks = backend_tasks[:1]               # Brave was prepended above
        extra_langs = []
    if extra_langs:
        logger.info(
            "Search %r: language fan-out → %s (base=%s)",
            query[:60], extra_langs, language,
        )
    _ev = _detect_defence_event(query)
    if _ev:
        logger.info(
            "Search %r: defence-event match → %s (site:%s, lang:%s)",
            query[:60], _ev["key"], _ev["site"], _ev["lang"],
        )

    # R-F124 — memory-first inversion: query the corpus before fanning
    # out to the web. If memory carries strong recent hits we still run
    # web (fresh data), but memory gets a relevance bonus so repeated
    # queries on the same topic surface cached intel first.
    #
    # R-F185 (2026-05-11): when ARIA_MEMORY_FIRST_SHORTCUT=1, query
    # memory FIRST as a separate await. If it returns ≥max_results
    # strong hits, return immediately without firing any web backends
    # (full pay-once-remember-forever — $0 marginal cost per repeat
    # query). Default behaviour unchanged for safety; flip the env
    # when memory is dense enough.
    _shortcut = (os.getenv("ARIA_MEMORY_FIRST_SHORTCUT") or "").lower() in ("1", "true", "yes")
    if _shortcut:
        try:
            mem_first = await _query_memory(query, max_results=max_results)
            if mem_first and len(mem_first) >= max_results:
                logger.info(
                    "R-F185 memory-first shortcut: %d strong hits for %r — "
                    "skipping all web backends ($0 cost)",
                    len(mem_first), query[:60],
                )
                # Skip the parallel gather entirely. Stamp the source
                # so dashboards can count shortcut hits.
                for r in mem_first:
                    if not r.source.startswith("memory_shortcut:"):
                        r.source = f"memory_shortcut:{r.source}"
                return mem_first[:max_results]
        except Exception as _se:
            logger.debug("R-F185 shortcut probe failed: %s", _se)
    # R-W5 (2026-05-11): per-backend ecosystem snapshot. Track which
    # backends fired, errored, returned 0, or returned data — operator
    # / chat layer / dashboard can read this via get_last_search_ecosystem()
    # to see ACTUAL backend health for the most-recent search.
    _backend_names = (
        ["memory"]
        # R-F2318: "brave" restored as PRIMARY when _brave_on — prepended to match
        # the backend_tasks prepend above. Order must match _all_tasks (= memory +
        # backend_tasks): [brave?], searxng, ddg, gnews_api, google_news, bing, academic,
        # defence_event, gdelt, gnews[langs]...
        + (["brave"] if _brave_on else [])
        # R-F3119 — the names MUST mirror `backend_tasks` exactly or the per-backend
        # ecosystem snapshot mislabels every result (the off-by-one R-F2318 fixed).
        # In DD scope the free stack is not launched, so it must not be named here.
        + ([] if _brave_exclusive else
           ["searxng", "duckduckgo", "gnews_api", "google_news", "bing_news",
            "academic", "defence_event", "gdelt"])
        + [f"google_news[{l}]" for l in extra_langs[:3]]
    )
    import time as _t_ws
    _ws_t0 = _t_ws.monotonic()
    # R-F1649: wrap the parallel gather in a timeout so a single hanging
    # backend can't block the entire search pipeline. REQUEST_TIMEOUT (12s)
    # is the per-backend HTTP timeout; the gather should complete within
    # that window. If it doesn't, return partial results rather than
    # blocking the caller (deep researcher, chat, etc.).
    # R-F1657: create tasks individually so we can read .result() from
    # completed ones on timeout instead of discarding everything.
    _all_tasks = [
        asyncio.create_task(_query_memory(query, max_results=max_results))
    ]
    for _bt in backend_tasks:
        _all_tasks.append(asyncio.create_task(_bt))
    # R-F2226: quorum+grace early-return instead of blocking on the SLOWEST
    # backend for the full REQUEST_TIMEOUT. Returns as soon as a quorum of
    # backends has useful results (+ a short grace for stragglers), but waits the
    # full budget when quorum isn't reached (recall-safe). Order-preserving, so
    # the per-backend snapshot below is unchanged. Subsumes the R-F1649 wall-clock
    # cap and the R-F1657 partial-result salvage.
    raw_results_list = await _gather_search_backends(
        _all_tasks,
        budget=SEARCH_GATHER_BUDGET,
        quorum=SEARCH_GATHER_QUORUM,
        grace=SEARCH_GATHER_GRACE,
    )
    raw_results = list(raw_results_list)

    # ── R-F3122 — BRAVE FAILED? FALL BACK TO ARIA'S SEARXNG, AND SAY SO. ────
    #
    # Brave ran alone above. If it produced nothing — errored, timed out, or genuinely
    # empty — the DD would otherwise have NO web tier at all, the resilience gap
    # R-F3119 opened by dropping the free stack outright. ARIA's own SearXNG now runs
    # as an explicit SECOND phase: it never contends with the primary and exists only
    # on the failure path.
    #
    # §14: cooling is not breaking, but it must be VISIBLE. `brave_fallback_used` is
    # surfaced on the ecosystem snapshot so a report can state that the pinned primary
    # did not serve — never a silent substitution.
    _brave_fallback_used = False
    # R-F3847 — a suppressed substitution must never be silent. "Brave found nothing"
    # and "nobody looked" are different facts, and a report that omits a section reads
    # identically to one where the section was clean — the §22 failure this whole
    # incident was. So it is logged and §21a-wired as a data gap.
    if _brave_fallback_suppressed:
        _primary_yield_chk = sum(len(r) for r in raw_results[1:] if isinstance(r, list))
        if _primary_yield_chk == 0:
            logger.warning(
                "[R-F3847] Brave (the designated DD engine) returned nothing for %r "
                "and substitution is OFF — reporting a DATA GAP, not a clean empty.",
                query[:60],
            )
            try:
                from .engine_wiring import wire_failure
                wire_failure(
                    module="web_search",
                    detail=(f"DD search data gap: Brave returned 0 for {query[:80]!r}; "
                            "SearXNG substitution suppressed by operator designation "
                            "(R-F3847)"),
                    gap_type="search_zero_results",
                    source="web_search:search",
                )
            except Exception:      # pragma: no cover - observability never blocks
                pass
    if _brave_fallback_tasks:
        _primary_yield = sum(len(r) for r in raw_results[1:] if isinstance(r, list))
        if _primary_yield == 0:
            _brave_fallback_used = True
            logger.warning(
                "[R-F3122] Brave (DD primary) returned nothing for %r — falling back "
                "to ARIA's own SearXNG; results will be marked degraded.", query[:60])
            _fb_tasks = [asyncio.create_task(_t) for _t in _brave_fallback_tasks]
            raw_results.extend(await _gather_search_backends(
                _fb_tasks,
                budget=SEARCH_GATHER_BUDGET,
                quorum=1,                # one sovereign backend; quorum 2 is unreachable
                grace=SEARCH_GATHER_GRACE,
            ))
            _backend_names = _backend_names + ["searxng"]
        else:
            # Primary served — close the unused coroutine so it cannot leak.
            for _t in _brave_fallback_tasks:
                try:
                    _t.close()
                except Exception:
                    pass

    _ws_elapsed_ms = int((_t_ws.monotonic() - _ws_t0) * 1000)

    # R-F2318 — distillation capture. When Brave participated, record the per-backend
    # ranked URLs (Brave = teacher, SearXNG/free = student) so ARIA can learn to MATCH
    # Brave with the free stack and eventually drop the paid dependency (§6). Best-effort,
    # never blocks/raises. Internal only — the "brave" label never reaches the user.
    if _brave_on:
        try:
            from . import brave_distill
            brave_distill.capture(query, language, _backend_names, raw_results)
        except Exception:
            pass

    # R-W5: build the per-backend snapshot
    _backend_snapshot: list[dict] = []
    for _i, _batch in enumerate(raw_results):
        _name = _backend_names[_i] if _i < len(_backend_names) else f"backend_{_i}"
        if isinstance(_batch, Exception):
            _backend_snapshot.append({
                "name": _name,
                "state": "errored",
                "results_count": 0,
                "error_reason": str(_batch)[:200],
            })
        elif _batch:
            _backend_snapshot.append({
                "name": _name,
                "state": "active",
                "results_count": len(_batch),
            })
        else:
            _backend_snapshot.append({
                "name": _name,
                "state": "silent",
                "results_count": 0,
            })
    _n_active = sum(1 for b in _backend_snapshot if b["state"] == "active")
    _n_silent = sum(1 for b in _backend_snapshot if b["state"] == "silent")
    _n_errored = sum(1 for b in _backend_snapshot if b["state"] == "errored")
    _health = (
        "DEAD" if _n_active == 0
        else "DEGRADED" if _n_errored >= 2
        else "PARTIAL" if _n_silent >= 2
        else "HEALTHY"
    )
    _LAST_SEARCH_ECOSYSTEM.clear()
    _LAST_SEARCH_ECOSYSTEM.update({
        "query": query[:200],
        "language": language,
        "backends": _backend_snapshot,
        "summary": {
            "active_backends": _n_active,
            "silent_backends": _n_silent,
            "errored_backends": _n_errored,
            "total_backends": len(_backend_snapshot),
        },
        "health_signal": _health,
        "total_duration_ms": _ws_elapsed_ms,
        # ── R-F3122 — §14 fallback transparency ─────────────────────────────
        # In DD scope Brave IS the web tier. When it answers, that is the whole
        # story. When it did not and ARIA's SearXNG served instead, the consumer
        # must be able to say so — a silent substitution would let a report present
        # degraded coverage as the pinned primary's output.
        "brave_primary": bool(_brave_exclusive),
        "brave_fallback_used": bool(_brave_fallback_used),
    })

    # Flatten and deduplicate by URL
    seen_urls: dict[str, SearchResult] = {}
    url_sources: dict[str, set] = {}  # track which backends found each URL
    # R-F2221: Reciprocal Rank Fusion — accumulate 1/(k+rank) across backends so
    # a result several engines rank HIGH beats one a single engine ranked low.
    # This is the rank-aware successor to the binary +0.3 triangulation bonus.
    # Env-tunable, default ON; disabling falls back to the legacy bonus.
    _rrf_enabled = os.getenv("ARIA_RRF_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
    _rrf_k = max(1, int(os.getenv("ARIA_RRF_K", "60")))
    _rrf_weight = float(os.getenv("ARIA_RRF_WEIGHT", "10.0"))
    rrf_raw: dict[str, float] = {}

    for batch in raw_results:
        if isinstance(batch, Exception):
            continue
        for rank, r in enumerate(batch):
            url_key = _get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/")
            # RRF contribution from THIS backend's ranking of this URL.
            rrf_raw[url_key] = rrf_raw.get(url_key, 0.0) + 1.0 / (_rrf_k + rank)
            if url_key not in seen_urls:
                seen_urls[url_key] = r
                url_sources[url_key] = {r.source.split(":")[0]}
            else:
                url_sources[url_key].add(r.source.split(":")[0])
                # Keep the version with the better snippet
                if len(r.snippet) > len(seen_urls[url_key].snippet):
                    seen_urls[url_key].snippet = r.snippet

    results = list(seen_urls.values())

    # Reward cross-backend agreement. R-F2221: rank-aware RRF when enabled,
    # else the legacy binary triangulation bonus (found by 2+ backends).
    for r in results:
        url_key = _get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/")
        sources = url_sources.get(url_key, set())
        if _rrf_enabled:
            # Scaled so a top-ranked, multi-backend result gets a boost on the
            # order of the old +0.3 bonus, but rank-weighted (see R-F2221).
            r.relevance_score += _rrf_weight * rrf_raw.get(url_key, 0.0)
        elif len(sources) >= 2:
            r.relevance_score += 0.3  # legacy triangulation bonus

    # Filter by credibility
    results = [r for r in results if r.credibility_tier <= min_credibility]

    # Filter: require triangulation (2+ backends found it)
    if require_triangulation:
        results = [r for r in results if
                   len(url_sources.get(_get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/"), set())) >= 2]

    # Score relevance
    for r in results:
        r.relevance_score += _score_relevance(r, query)

    # Quarantine disinformation (don't remove, tag for analyst awareness)
    for r in results:
        if r.credibility_tier == 6:
            r.snippet = f"[QUARANTINED — suspected disinformation source] {r.snippet}"

    # Sort by relevance (credibility-weighted)
    results.sort(key=lambda r: r.relevance_score, reverse=True)

    # R-F2205 — optional local cross-encoder re-rank (ENV-gated, default OFF; lazy + offloaded).
    # Surfaces relevant-but-lexically-different sources that credibility/keyword scoring buries.
    # Safe no-op when disabled or unavailable.
    # R-F2846 — SCREENING SKIPS THE RE-RANK.
    # Phase-timed on the brain: rerank_results was 94.40s of a 119.92s search — 79%
    # of every search in the product. Disabling it globally took the same probe to
    # 12.88s (9.3x). That cost made adverse media impossible: each template gets a
    # 10s budget inside a 180s deadline, so the live SOCAR run recorded
    # templates_run 18 / templates_searched 0 / backends_answered False — zero
    # evidence, on 20% of the decision scorecard.
    #
    # But a global off-switch is the wrong end state: re-ranking is a real relevance
    # feature (main.py pre-warms it, R-F2259). The honest split is by PURPOSE.
    # Screening asks "does this entity appear in adverse press?" — titles and
    # snippets answer that, and the ORDER of candidates is irrelevant because every
    # one is inspected downstream. Deep research and chat, where a human reads the
    # top few, are where re-rank earns its cost.
    #
    # SAFETY: this may only change the ORDER of results, never WHICH are found. A
    # screen that quietly searched less would be a recall cut wearing a perf fix.
    try:
        from . import reranker as _rr
        if not screening and _rr.is_enabled() and len(results) > 1:
            results = await _rr.rerank_results(query, results)
    except Exception:
        pass

    # R-F2339 — Brave STUDENT re-rank: when the FREE stack served (Brave did NOT contribute),
    # re-order by the learned Brave-preference so ARIA's own search imitates Brave's
    # source-selection methodology. ENV-gated (ARIA_BRAVE_STUDENT_ENABLED, default OFF —
    # staged rollout after eval agreement). Safe no-op when disabled / model unlearned.
    try:
        _brave_contributed = False
        if "brave" in _backend_names:
            _bi = _backend_names.index("brave")
            _brave_contributed = (_bi < len(raw_results)
                                  and not isinstance(raw_results[_bi], BaseException)
                                  and bool(raw_results[_bi]))
        if not _brave_contributed and len(results) > 1:
            from . import brave_student as _bs
            if _bs.apply_enabled():
                results = _bs.rerank(results)
    except Exception:
        pass

    logger.info("Search %r: %d results from %d backends (deduped from %d)",
                query[:60], min(len(results), max_results),
                sum(1 for b in raw_results if not isinstance(b, Exception) and b),
                sum(len(b) for b in raw_results if not isinstance(b, Exception)))

    # R-F190 (2026-05-11) — per-backend success-rate telemetry.
    # Pre-R-F190 there was no per-backend counter for ok-vs-fail, so
    # operators could only grep logs to see which backend was contri-
    # buting. Now each search round writes ok/fail counters per backend
    # per day under crucix:search:backend:<name>:<ok|fail>:YYYY-MM-DD.
    # Dashboard surface comes via /api/aria/search/backends/stats.
    try:
        from . import redis_store as _rs_t
        import datetime as _dt_t
        _today = _dt_t.datetime.now(_dt_t.timezone.utc).strftime("%Y-%m-%d")
        # raw_results[0] is _query_memory; backends are [1:].
        for backend_idx, batch in enumerate(raw_results):
            # Identify backend by position: 0=memory then the
            # backend_tasks list in order. The names map mirrors the
            # asyncio.gather order at the call site.
            # R-F2318: use the single in-scope name list (brave-aware + includes
            # gdelt + lang fan-out) instead of a second, stale hardcoded map. The
            # prior list omitted gdelt AND didn't know about the brave prepend, so
            # on the Brave path every backend's ok/fail counter was shifted by one
            # (brave→"searxng", searxng→"ddg", …) — Brave's health was invisible.
            bname = _backend_names[backend_idx] if backend_idx < len(_backend_names) else f"backend_{backend_idx}"
            ok = (not isinstance(batch, Exception)) and bool(batch)
            metric = "ok" if ok else "fail"
            try:
                key = f"crucix:search:backend:{bname}:{metric}:{_today}"
                cur = await _rs_t.get(key)
                nxt = int(cur or 0) + 1
                await _rs_t.set(key, str(nxt), ex=14 * 86400)
            except Exception:
                pass
    except Exception:
        pass

    # R-F2245 — per-domain diversity cap so one prolific domain can't own the top-N
    final = _apply_domain_diversity_cap(results, max_results)

    # ── R-F184 (2026-05-11) — pay-once-remember-forever ingest ──
    # Every credibility-tier-1/2/3 result is embedded into the RAG store
    # so the next identical-or-similar query hits memory at $0. Pre-R-F184
    # only brave_answers ingested. Now web_search results (Brave + DDG +
    # Bing News + Google News + Academic + SearXNG) all flow into RAG.
    # Tier 4+ (low credibility, suspected disinfo) deliberately excluded
    # so we don't poison the embedding space.
    try:
        from . import rag_store as _rs_rag
        # R-F859 (2026-05-24) — collect all results and ingest in ONE batched
        # encode pass. Pre-R-F859 this looped ingest_document per result, firing
        # ~25 separate GIL-holding sentence-transformer encodes per burst that
        # starved the asyncio event loop (finding #1 wedge). add_search_results_batch
        # upserts the whole batch in a single model pass.
        _rag_batch = []
        for r in final[:max_results]:
            if r.credibility_tier >= 4:
                continue
            body_for_rag = ((r.title or "") + "\n\n" + (r.snippet or "")).strip()
            if len(body_for_rag) < 40:
                continue
            _rag_batch.append({
                "text": body_for_rag,
                # R-F2318 — mask the internal "brave" label in the RAG store too
                # (defense-in-depth: a citation surface that ever renders a RAG hit's
                # raw source must never reveal Brave). brain_hook.absorb keeps the real
                # label (internal learning only, never user-facing).
                "source": f"web_search:{'aria_search' if (r.source == 'brave' or r.source.startswith('brave:')) else r.source}",
                "source_type": "search_result",
                "title": (r.title or "")[:200],
                "url": (r.url or "")[:500],
                "metadata": {
                    "search_query": query[:200],
                    "credibility_tier": r.credibility_tier,
                    "language": language,
                },
            })
        if _rag_batch:
            await _rs_rag.add_search_results_batch(_rag_batch)
    except Exception as _re:
        logger.debug("R-F184/R-F859 RAG batch ingest pass failed: %s", _re)

    # ── R-F189 (2026-05-11) — all-general-web-dead capability gap ──
    # When Brave is sticky-disabled (R-F171 24h sentinel) AND DuckDuckGo
    # breaker is open AND SearXNG isn't configured, the general-web
    # layer is silently dead and search degrades to news-only + academic.
    # Surface this as a capability_gap so operator sees it before users
    # start asking why entity searches return only news headlines.
    try:
        from .circuit_breaker import peek_breaker
        from . import search_searxng as _sx_h
        # R-F320 (2026-05-11): Brave removed. General-web dead-state
        # now checks DDG breaker + searxng config only.
        # R-F3458 — PEEK, do not get. `get_breaker(name)` with no thresholds REGISTERS
        # the breaker with the defaults if it does not exist yet, and first registration
        # wins — so this read-only dead-state check could permanently define DuckDuckGo's
        # breaker as 3/300s and silently discard the 5/600s that _search_duckduckgo
        # intends. An unregistered breaker has recorded no failures and is not open.
        _ddg_cb = peek_breaker("search:duckduckgo")
        _ddg_breaker_open = bool(_ddg_cb and _ddg_cb.is_open())
        _searxng_configured = _sx_h.is_configured()
        if (
            not final
            and _ddg_breaker_open
            and not _searxng_configured
        ):
            from . import brain_hook as _bh_h
            await _bh_h.absorb(
                module="web_search",
                summary="R-F189: ALL general-web backends down AND search returned 0",
                detail=(
                    "Search is degraded — ddg breaker open AND searxng "
                    "not configured AND academic/news returned nothing for "
                    f"'{query[:80]}'. Operator action: set SEARXNG_URL to a "
                    "self-hosted instance. Memory-first hits unaffected."
                ),
                success=False,
                gap_type="all_general_web_dead",
                gap_detail="ddg+searxng unavailable, zero results",
            )
    except Exception:
        pass

    # ── Brain hook: feed search outcomes to learning ──
    try:
        from . import brain_hook as _bh
        backends_hit = sorted({r.source.split(":")[0] for r in final}) if final else []
        # F72 fix 2026-04-28: include language code in the gap detail so
        # the F66 (gap_type, detail) dedupe distinguishes per-language
        # variants. Without it, a Portuguese query that returns 0 would
        # block the English variant of the same query string from
        # recording its own (genuinely different) outcome for the next
        # hour, and the ledger would carry "no results for X" while
        # crossref+openalex actually serve hl=en X with 12 results
        # seconds later.
        await _bh.absorb(
            module="web_search",
            summary=f"web_search '{query[:80]}': {len(final)} results from {len(backends_hit)} backend(s) [{', '.join(backends_hit) or 'none'}]",
            detail="; ".join(f"{r.title[:80]} → {r.url[:120]}" for r in final[:5]),
            entity_name=query[:80],
            # Backend execution succeeded as long as we returned without
            # raising. Zero results is a valid outcome (rare query, long
            # tail, niche entity) — not a backend failure. Conflating the
            # two produced a 26% "success rate" on prod which masked real
            # failures and triggered noise alerts.
            success=True,
            gap_type=None if final else "search_zero_results",
            gap_detail=(None if final else
                        f"All {len(backend_tasks)} backends returned 0 for "
                        f"[lang={language}] {query[:120]}"),
            confidence="ASSESSED",
        )
    except Exception:
        pass

    # R-F2318 — USER-FACING BRANDING: mask the internal "brave" backend label on the
    # RETURNED results so NO downstream user-facing surface (DD report, citations,
    # source triangulation) ever reveals Brave — all search is presented as ARIA's own.
    # Every INTERNAL consumer (distillation capture, ecosystem snapshot via _backend_names,
    # the brain_hook/RAG absorb above) already ran with the real "brave" label; this only
    # affects what leaves the function. The cache stores the masked copy (consistent).
    mask_brave_source(final)

    # R-F2059: write to search result cache
    _search_cache_set(_cache_key, final)

    return final


@fail_wire(module="web_search", gap_type="source_failure")
async def search_news(query: str, *, max_results: int = 10, language: str = "en") -> list[SearchResult]:
    """News-specific search — GNews API + Google News + Bing News in parallel."""
    tasks = [
        _search_gnews_api(query, MAX_RESULTS_PER_BACKEND, language),
        _search_google_news(query, MAX_RESULTS_PER_BACKEND, language),
        _search_bing_news(query, MAX_RESULTS_PER_BACKEND),
    ]
    raw = await asyncio.gather(*tasks, return_exceptions=True)

    seen = {}
    for batch in raw:
        if isinstance(batch, Exception):
            continue
        for r in batch:
            key = r.title[:50].lower()
            if key not in seen:
                seen[key] = r
                r.relevance_score = _score_relevance(r, query)

    results = sorted(seen.values(), key=lambda r: r.relevance_score, reverse=True)
    final = results[:max_results]

    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="web_search",
            summary=f"search_news '{query[:80]}': {len(final)} results",
            detail="; ".join(f"{r.title[:80]}" for r in final[:5]),
            entity_name=query[:80],
            success=bool(final),
            extra_topics=["osint"],
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return final


@fail_wire(module="web_search", gap_type="source_failure")
async def search_entity(
    entity: str,
    *,
    country: str = "",
    max_results: int = 15,
) -> list[SearchResult]:
    """Entity-focused search — fires 5 query angles for triangulation.

    For counterparty due diligence: searches the entity name across
    multiple formulations to build a complete intelligence picture.
    """
    queries = [
        f"{entity} {country}".strip(),
        f"{entity} directors officers shareholders",
        f"{entity} sanctions litigation court",
        f"{entity} contract award procurement",
        f'"{entity}" site:opencorporates.com OR site:companieshouse.gov.uk',
    ]

    all_results = []
    for q in queries:
        batch = await search(q, max_results=5, min_credibility=5)
        all_results.extend(batch)

    # Deduplicate across all angles
    seen = {}
    for r in all_results:
        key = _get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/")
        if key not in seen:
            seen[key] = r
        else:
            seen[key].relevance_score += 0.2  # found across multiple angles = more relevant

    results = sorted(seen.values(), key=lambda r: r.relevance_score, reverse=True)
    final = results[:max_results]

    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="web_search",
            summary=f"search_entity '{entity}' ({country or 'no country'}): {len(final)} results across {len(queries)} angles",
            detail="; ".join(f"{r.title[:80]} ({r.url[:80]})" for r in final[:5]),
            entity_name=entity,
            success=bool(final),
            extra_topics=["osint", "compliance"],
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return final


# ── P6: Native-language query expansion ──────────────────────────────────
#
# search_multilingual previously only switched the language LOCALE on the
# same English query. A query like "Angola defence tender" run against
# Portuguese locale still matched English words — missing actual
# Portuguese-language terms like "concurso defesa" or "licitação forças
# armadas". The fix is to translate the defence-BD core vocabulary into
# the target language and run the translated query alongside the English
# one. ARIA is a GLOBAL advisor, so she must be able to find
# Portuguese/French/Arabic/Spanish/Russian/Turkish/Chinese procurement
# portals even when the user asks in English.
#
# The dictionary below covers the high-frequency defence-BD terms that
# dominate procurement and compliance queries. It is NOT a full machine-
# translation layer — it is a targeted substitution map that captures
# 80% of the language-barrier value for 5% of the complexity.

_TERM_TRANSLATIONS: dict[str, dict[str, list[str]]] = {
    # Core procurement vocabulary
    "tender":       {"pt": ["concurso", "licitação"], "fr": ["appel d'offres"], "es": ["licitación"], "ar": ["مناقصة"], "ru": ["тендер"], "tr": ["ihale"], "zh": ["招标"], "ro": ["licitație"]},
    "tenders":      {"pt": ["concursos", "licitações"], "fr": ["appels d'offres"], "es": ["licitaciones"], "ar": ["مناقصات"], "ru": ["тендеры"], "tr": ["ihaleler"], "zh": ["招标"], "ro": ["licitații"]},
    "procurement":  {"pt": ["aquisição", "compra pública"], "fr": ["marché public", "acquisition"], "es": ["adquisición", "contratación pública"], "ar": ["مشتريات", "شراء"], "ru": ["закупки"], "tr": ["tedarik", "satın alma"], "zh": ["采购"], "ro": ["achiziție publică"]},
    "contract":     {"pt": ["contrato"], "fr": ["contrat", "marché"], "es": ["contrato"], "ar": ["عقد"], "ru": ["контракт"], "tr": ["sözleşme"], "zh": ["合同"]},
    "contract award":{"pt": ["adjudicação"], "fr": ["attribution de marché"], "es": ["adjudicación"], "ar": ["ترسية"], "ru": ["присуждение контракта"], "tr": ["ihale tahsisi"], "zh": ["合同授予"]},
    "bid":          {"pt": ["proposta"], "fr": ["offre"], "es": ["oferta"], "ar": ["عرض"], "ru": ["заявка"], "tr": ["teklif"], "zh": ["投标"]},

    # Defence vocabulary
    "defence":      {"pt": ["defesa"], "fr": ["défense"], "es": ["defensa"], "ar": ["دفاع"], "ru": ["оборона"], "tr": ["savunma"], "zh": ["国防"], "ro": ["apărare"]},
    "defense":      {"pt": ["defesa"], "fr": ["défense"], "es": ["defensa"], "ar": ["دفاع"], "ru": ["оборона"], "tr": ["savunma"], "zh": ["国防"], "ro": ["apărare"]},
    "military":     {"pt": ["militar"], "fr": ["militaire"], "es": ["militar"], "ar": ["عسكري"], "ru": ["военный"], "tr": ["askeri"], "zh": ["军事"], "ro": ["militar"]},
    "army":         {"pt": ["exército"], "fr": ["armée"], "es": ["ejército"], "ar": ["جيش"], "ru": ["армия"], "tr": ["ordu"], "zh": ["陆军"]},
    "navy":         {"pt": ["marinha"], "fr": ["marine"], "es": ["marina"], "ar": ["بحرية"], "ru": ["военно-морской флот"], "tr": ["donanma"], "zh": ["海军"]},
    "air force":    {"pt": ["força aérea"], "fr": ["armée de l'air"], "es": ["fuerza aérea"], "ar": ["القوات الجوية"], "ru": ["военно-воздушные силы"], "tr": ["hava kuvvetleri"], "zh": ["空军"]},
    "armed forces": {"pt": ["forças armadas"], "fr": ["forces armées"], "es": ["fuerzas armadas"], "ar": ["القوات المسلحة"], "ru": ["вооружённые силы"], "tr": ["silahlı kuvvetler"], "zh": ["武装部队"], "ro": ["forțele armate"]},
    "weapon":       {"pt": ["arma"], "fr": ["arme"], "es": ["arma"], "ar": ["سلاح"], "ru": ["оружие"], "tr": ["silah"], "zh": ["武器"]},
    "weapons":      {"pt": ["armas", "armamento"], "fr": ["armes", "armement"], "es": ["armas", "armamento"], "ar": ["أسلحة"], "ru": ["оружие", "вооружение"], "tr": ["silahlar"], "zh": ["武器"]},
    "ammunition":   {"pt": ["munição"], "fr": ["munitions"], "es": ["munición"], "ar": ["ذخيرة"], "ru": ["боеприпасы"], "tr": ["mühimmat"], "zh": ["弹药"], "ro": ["muniție"]},
    "missile":      {"pt": ["míssil"], "fr": ["missile"], "es": ["misil"], "ar": ["صاروخ"], "ru": ["ракета"], "tr": ["füze"], "zh": ["导弹"]},
    "drone":        {"pt": ["drone"], "fr": ["drone"], "es": ["dron"], "ar": ["طائرة بدون طيار"], "ru": ["беспилотник"], "tr": ["insansız hava aracı"], "zh": ["无人机"]},
    "uav":          {"pt": ["VANT"], "fr": ["drone"], "es": ["VANT"], "ar": ["طائرة بدون طيار"], "ru": ["БПЛА"], "tr": ["İHA"], "zh": ["无人机"]},
    "tank":         {"pt": ["carro de combate"], "fr": ["char"], "es": ["tanque"], "ar": ["دبابة"], "ru": ["танк"], "tr": ["tank"], "zh": ["坦克"]},
    "artillery":    {"pt": ["artilharia"], "fr": ["artillerie"], "es": ["artillería"], "ar": ["مدفعية"], "ru": ["артиллерия"], "tr": ["topçu"], "zh": ["炮兵"]},

    # Compliance/regulatory
    "sanction":     {"pt": ["sanção"], "fr": ["sanction"], "es": ["sanción"], "ar": ["عقوبة"], "ru": ["санкция"], "tr": ["yaptırım"], "zh": ["制裁"]},
    "sanctions":    {"pt": ["sanções"], "fr": ["sanctions"], "es": ["sanciones"], "ar": ["عقوبات"], "ru": ["санкции"], "tr": ["yaptırımlar"], "zh": ["制裁"], "ro": ["sancțiuni"]},
    "embargo":      {"pt": ["embargo"], "fr": ["embargo"], "es": ["embargo"], "ar": ["حظر"], "ru": ["эмбарго"], "tr": ["ambargo"], "zh": ["禁运"]},
    "export":       {"pt": ["exportação"], "fr": ["exportation"], "es": ["exportación"], "ar": ["تصدير"], "ru": ["экспорт"], "tr": ["ihracat"], "zh": ["出口"]},
    "import":       {"pt": ["importação"], "fr": ["importation"], "es": ["importación"], "ar": ["استيراد"], "ru": ["импорт"], "tr": ["ithalat"], "zh": ["进口"]},
    "licence":      {"pt": ["licença"], "fr": ["licence"], "es": ["licencia"], "ar": ["ترخيص"], "ru": ["лицензия"], "tr": ["lisans"], "zh": ["许可证"]},
    "license":      {"pt": ["licença"], "fr": ["licence"], "es": ["licencia"], "ar": ["ترخيص"], "ru": ["лицензия"], "tr": ["lisans"], "zh": ["许可证"]},
    "broker":       {"pt": ["corretor", "intermediário"], "fr": ["courtier"], "es": ["corredor", "intermediario"], "ar": ["وسيط"], "ru": ["брокер", "посредник"], "tr": ["aracı"], "zh": ["经纪人"]},
    "brokering":    {"pt": ["corretagem", "intermediação"], "fr": ["courtage"], "es": ["correduría", "intermediación"], "ar": ["الوساطة"], "ru": ["брокерство"], "tr": ["aracılık"], "zh": ["经纪"]},
    "compliance":   {"pt": ["conformidade"], "fr": ["conformité"], "es": ["cumplimiento"], "ar": ["امتثال"], "ru": ["соблюдение"], "tr": ["uyum"], "zh": ["合规"]},

    # Government entities
    "ministry of defence":{"pt": ["ministério da defesa"], "fr": ["ministère de la défense"], "es": ["ministerio de defensa"], "ar": ["وزارة الدفاع"], "ru": ["министерство обороны"], "tr": ["savunma bakanlığı"], "zh": ["国防部"], "ro": ["ministerul apărării naționale"]},
    "government":   {"pt": ["governo"], "fr": ["gouvernement"], "es": ["gobierno"], "ar": ["حكومة"], "ru": ["правительство"], "tr": ["hükümet"], "zh": ["政府"]},

    # Country names (only where they differ materially)
    "turkey":       {"pt": ["turquia"], "fr": ["turquie"], "es": ["turquía"], "ar": ["تركيا"], "ru": ["турция"], "tr": ["türkiye"], "zh": ["土耳其"]},
    "russia":       {"pt": ["rússia"], "fr": ["russie"], "es": ["rusia"], "ar": ["روسيا"], "ru": ["россия"], "tr": ["rusya"], "zh": ["俄罗斯"]},
    "china":        {"pt": ["china"], "fr": ["chine"], "es": ["china"], "ar": ["الصين"], "ru": ["китай"], "tr": ["çin"], "zh": ["中国"]},
    "france":       {"pt": ["frança"], "fr": ["france"], "es": ["francia"], "ar": ["فرنسا"], "ru": ["франция"], "tr": ["fransa"], "zh": ["法国"]},

    # LatAm-specific terms
    "armoured vehicle": {"pt": ["viatura blindada"], "fr": ["véhicule blindé"], "es": ["vehículo blindado"], "tr": ["zırhlı araç"], "zh": ["装甲车"]},
    "infantry fighting vehicle": {"pt": ["viatura de combate de infantaria"], "fr": ["véhicule de combat d'infanterie"], "es": ["vehículo de combate de infantería"], "tr": ["piyade savaş aracı"]},
    "modernisation": {"pt": ["modernização"], "fr": ["modernisation"], "es": ["modernización"], "tr": ["modernizasyon"]},
    "budget":       {"pt": ["orçamento"], "fr": ["budget"], "es": ["presupuesto"], "ar": ["ميزانية"], "ru": ["бюджет"], "tr": ["bütçe"], "zh": ["预算"]},
    "detonator":    {"pt": ["detonador"], "fr": ["détonateur"], "es": ["detonador"], "ar": ["صاعق"], "tr": ["fünye"]},
    "explosive":    {"pt": ["explosivo"], "fr": ["explosif"], "es": ["explosivo"], "ar": ["متفجر"], "tr": ["patlayıcı"]},
    "border":       {"pt": ["fronteira"], "fr": ["frontière"], "es": ["frontera"], "ar": ["حدود"], "tr": ["sınır"]},
    "patrol":       {"pt": ["patrulha"], "fr": ["patrouille"], "es": ["patrulla"], "ar": ["دورية"], "tr": ["devriye"]},
    "security":     {"pt": ["segurança"], "fr": ["sécurité"], "es": ["seguridad"], "ar": ["أمن"], "ru": ["безопасность"], "tr": ["güvenlik"], "zh": ["安全"]},
}


def _translate_query(query: str, lang: str) -> str:
    """Substitute known defence-BD terms in `query` with their `lang` equivalents.

    Longest-phrase-first matching to prevent "air force" being broken into
    "air" + "force". Returns the English query unchanged if no substitutions
    apply — we still issue the original query alongside to catch
    English-language sources in the target locale.
    """
    if not query or lang == "en":
        return query
    q = query
    # Sort by descending phrase length so multi-word keys match before
    # their single-word components.
    for term in sorted(_TERM_TRANSLATIONS.keys(), key=lambda k: -len(k)):
        choices = _TERM_TRANSLATIONS[term].get(lang)
        if not choices:
            continue
        replacement = choices[0]
        # Case-insensitive replacement with word boundaries on
        # alphabetic terms; Arabic / Chinese scripts don't use \b so
        # we fall back to plain replacement for non-latin.
        pat = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
        q = pat.sub(replacement, q)
    return q


@fail_wire(module="web_search", gap_type="source_failure")
async def search_multilingual(
    query: str,
    languages: list[str] | None = None,
    *,
    max_results: int = 15,
    translate_query: bool = True,
    use_brave: bool | None = None,   # R-F2318 — forwarded to each per-language search()
    screening: bool = False,   # R-F2846 — forwarded to each per-language search()
) -> list[SearchResult]:
    """Search in multiple languages simultaneously.

    When translate_query is True (default), defence-BD terms in the
    query are substituted into each target language so ARIA actually
    finds native-language procurement sources — not just English
    sources that happen to be in a different locale. Critical for
    discovering Angolan, Turkish, Russian, Chinese procurement
    portals that publish in their native language.

    Auto-detects regional languages from query content:
      - Lusophone countries + CPLP → add pt
      - Turkey + Turkish OEMs       → add tr
      - Russia / former USSR         → add ru
      - LatAm Spanish markets        → add es
      - MENA Arabic markets          → add ar
      - Francophone Africa            → add fr
      - China / Chinese OEMs         → add zh
    """
    if languages is None:
        languages = ["en"]
        q_lower = query.lower()
        # Portuguese (Lusophone Africa + Brazil + Portugal)
        if any(kw in q_lower for kw in ["angola", "mozambique", "guinea-bissau",
                                         "cape verde", "cabo verde", "portugal",
                                         "brazil", "brasil", "cplp", "lusophone",
                                         "são tomé", "sao tome"]):
            languages.append("pt")
        # French (Francophone Africa + France)
        if any(kw in q_lower for kw in ["senegal", "mali", "niger", "chad",
                                         "cote d'ivoire", "ivory coast",
                                         "burkina", "cameroon", "cameroun",
                                         "drc", "congo", "gabon", "benin",
                                         "togo", "france", "french"]):
            languages.append("fr")
        # R-F395 (2026-05-13): split MENA-proper from GCC. ARIA self-
        # reported that auto-switching to Arabic for "saudi imported last
        # year" returned 0 relevant results because GCC defence-procurement
        # docs are bilingual (Arabic AND English) with the substantive
        # data published in English. Searching Arabic-only there is a
        # waste of fan-out budget. Keep auto for MENA-proper (Morocco,
        # Algeria, Tunisia, Libya, Egypt, Iraq, Jordan, Lebanon, Yemen)
        # where Arabic-first IS correct; require explicit opt-in for GCC
        # (saudi/uae/qatar/kuwait/bahrain/oman) by passing
        # `languages=["en","ar"]` at call time.
        AR_AUTO = ("morocco", "algeria", "tunisia", "libya",
                   "egypt", "iraq", "jordan", "lebanon", "yemen",
                   "syria", "sudan", "mauritania")
        if any(kw in q_lower for kw in AR_AUTO):
            languages.append("ar")
        # Turkish
        if any(kw in q_lower for kw in ["turkey", "turkiye", "türkiye", "baykar",
                                         "aselsan", "roketsan", "ssb", "otokar"]):
            languages.append("tr")
        # Russian (CIS + former Soviet)
        if any(kw in q_lower for kw in ["russia", "belarus", "kazakhstan",
                                         "uzbekistan", "armenia", "azerbaijan",
                                         "rosoboronexport", "kalashnikov",
                                         "almaz-antey", "s-400", "s-300"]):
            languages.append("ru")
        # Spanish (LatAm)
        if any(kw in q_lower for kw in ["argentina", "chile", "peru", "colombia",
                                         "mexico", "ecuador", "venezuela", "spain",
                                         "bolivia", "uruguay", "paraguay",
                                         "latin america", "latam", "spanish",
                                         "famae", "indumil", "fadea", "fabricaciones militares",
                                         "fuerzas armadas", "ministerio de defensa",
                                         "licitación", "contrato militar",
                                         "guatemala", "honduras", "el salvador",
                                         "costa rica", "panama", "dominican",
                                         "cuba", "nicaragua"]):
            languages.append("es")
        # Romanian
        if any(kw in q_lower for kw in ["romania", "romanian", "bucharest", "bucuresti",
                                         "onrc", "ancex", "romarm", "romaero",
                                         "aerostar", "pro optica", "srl",
                                         "ministerul apărării", "licitatie"]):
            languages.append("ro")
        # Chinese
        if any(kw in q_lower for kw in ["china", "chinese", "norinco", "chengdu",
                                         "poly technologies", "sastind", "catic"]):
            languages.append("zh")

    # Build translated-query variants per language
    queries_by_lang: list[tuple[str, str]] = []
    for lang in languages:
        if translate_query and lang != "en":
            translated = _translate_query(query, lang)
            queries_by_lang.append((lang, translated))
            # Also run the untranslated query with target locale — catches
            # English-language sources indexed against the locale.
            if translated != query:
                queries_by_lang.append((lang, query))
        else:
            queries_by_lang.append((lang, query))

    per_query_cap = max(3, max_results // max(1, len(queries_by_lang)) + 2)
    tasks = [search(q, max_results=per_query_cap, language=lang, use_brave=use_brave,
                    screening=screening)
             for lang, q in queries_by_lang]
    raw = await asyncio.gather(*tasks, return_exceptions=True)

    seen = {}
    for batch in raw:
        if isinstance(batch, Exception):
            continue
        for r in batch:
            key = _get_domain(r.url) + urllib.parse.urlparse(r.url).path.rstrip("/")
            if key not in seen:
                seen[key] = r

    results = sorted(seen.values(), key=lambda r: r.relevance_score, reverse=True)
    final = results[:max_results]

    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="web_search",
            summary=f"search_multilingual '{query[:60]}' [{','.join(languages)}]: {len(final)} results from {len(queries_by_lang)} variants",
            detail="; ".join(f"[{r.language}] {r.title[:70]}" for r in final[:5]),
            entity_name=query[:80],
            success=bool(final),
            extra_topics=["osint"] + [f"lang:{l}" for l in languages if l != "en"],
            confidence="ASSESSED",
        )
    except Exception:
        pass

    return final


# ── Stats and health ────────────────────────────────────────────────────────

# R-F2109: alert the brain when SearXNG is unhealthy so the operator knows
# the primary search backend is down (was dark — only surfaced in health endpoint).
def _wire_searxng_unhealthy(detail: str) -> None:
    try:
        from .engine_wiring import wire_failure
        wire_failure(module="web_search", detail=str(detail)[:200],
                     gap_type="source_failure", source="web_search:searxng_health")
    except Exception:
        pass


@fail_wire(module="web_search", gap_type="source_failure")
async def get_search_health() -> dict:
    """Check which search backends are available.

    R-F2377 (2026-07-06): Brave Answers remains removed, but Brave Search
    was restored as a scoped, user-facing DD/research backend in R-F2318.
    Surface its runtime gate separately as ``brave_search`` so operators can
    distinguish "poor DD data because Brave is unavailable/disabled" from
    "Brave is available but the query/evidence path is weak".

    R-F1629 (2026-06-17): probe the self-hosted SearXNG (search_searxng
    adapter) in addition to the public SEARXNG_INSTANCES list. The public
    list has been empty since 2026-04-20; the self-hosted instance is the
    only active SearXNG backend.
    """
    health = {
        "searxng": False,
        "google_news": False,
        "bing_news": False,
        "gnews_api": {
            "configured": bool(GNEWS_API_KEY),
            "available": bool(GNEWS_API_KEY),
            "mode": "gnews.io_api_v4_search",
        },
        "duckduckgo": True,  # always tried, may be rate-limited
        "brave_search": {
            "configured": bool(BRAVE_API_KEY),
            "globally_disabled": bool(_BRAVE_GLOBALLY_OFF),
            "scope_enabled": brave_is_enabled(),
            "available_for_scoped_user_search": bool(BRAVE_API_KEY) and not bool(_BRAVE_GLOBALLY_OFF),
            "mode": "scoped_primary_user_dd_research_search",
        },
    }
    # R-F1629: probe self-hosted SearXNG first (primary backend)
    try:
        from . import search_searxng as _sx
        if _sx.is_configured():
            res = await _sx.search("test", count=1)
            if res.get("ok") and res.get("results"):
                health["searxng"] = True
            else:
                # R-F2109: SearXNG is configured but returned no results — alert
                _wire_searxng_unhealthy("SearXNG configured but returned 0 results (upstream engines blocked)")
    except Exception as _sx_health_err:
        # R-F2109: SearXNG is configured but unreachable — alert the brain
        if _sx.is_configured():
            _wire_searxng_unhealthy(f"SearXNG unreachable: {_sx_health_err}")
    # Fall back to public instances if self-host is unavailable
    if not health["searxng"] and SEARXNG_INSTANCES:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                for inst in SEARXNG_INSTANCES[:2]:
                    try:
                        r = await client.get(f"{inst}/search", params={"q": "test", "format": "json"})
                        if r.status_code == 200:
                            health["searxng"] = True
                            break
                    except Exception:
                        continue
        except Exception:
            from .engine_wiring import wire_failure as _wf
            _wf(
                module="web_search",
                detail="get_search_health searxng probe failed",
                gap_type="engine_failure",
                source="web_search:get_search_health",
            )
            pass
    # Google News RSS is almost always available
    health["google_news"] = True
    health["bing_news"] = True

    # R-F996 — wire to brain
    wire_success(
        module="web_search",
        summary="Web search",
        source_id="web_search:R-F996",
    )
    return health

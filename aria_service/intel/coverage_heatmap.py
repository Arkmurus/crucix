"""coverage_heatmap — domain × jurisdiction knowledge coverage view
(R-F89, 2026-05-09).

Why this module exists
──────────────────────
Phase 2 of the independence roadmap. Total fact count is a vanity
metric — the honest measure is coverage across the matrix that
Arkmurus's customers actually care about: defence-DD domains × the
20 critical defence markets.

This module builds that matrix from existing knowledge + intel_ledger
data and surfaces it as a heatmap. Cells with low fact density / stale
data become the autonomous engine's targeting priority.

The matrix shape
────────────────
Rows = domains:
  sanctions_screening, eccn_classification, euc_jurisdictions,
  fatf_ml_typologies, fcpa_enforcement, defence_market_briefing,
  procurement_pipeline, weapon_systems, virtual_assets,
  sanctions_divergence, rca_screening, economic_substance, ...

Columns = jurisdictions / markets:
  Lusophone moat: Angola, Mozambique, Cape Verde, Guinea-Bissau, Brazil
  Wider Africa: Nigeria, Ghana, Kenya, Ethiopia, Tanzania, Senegal,
    Côte d'Ivoire, Cameroon, Rwanda, South Africa
  Gulf + MENA: Saudi Arabia, UAE, Qatar, Bahrain, Kuwait, Oman, Jordan,
    Iraq, Lebanon, Israel, Turkey, Egypt
  Asia-Pacific: Indonesia, Vietnam, Philippines, Bangladesh, India,
    Pakistan, South Korea, Japan
  LatAm: Mexico, Colombia, Peru, Venezuela, Argentina
  Europe (emerging): Romania, Poland, Ukraine
  Anchors: US, UK, EU, NATO

Each cell: { fact_count, signal_count, is_stale, last_refreshed_at,
              confidence_grade } — derived from learning_progress (R-F88)
              + a query against the knowledge base for the (domain,
              jurisdiction) pair.

Public API
──────────
    build_heatmap() -> dict
    coverage_score(matrix) -> float
    gap_targets(matrix, max_targets=20) -> list[dict]
    summary() -> dict
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger("aria.coverage_heatmap")

# R-F781 (2026-05-21) — build_heatmap cache + single-flight.
# 28 domains × 31 jurisdictions × ~96k (facts + signals) = ~83M
# iterations per matrix compute, run via asyncio.to_thread. Pre-R-F781
# two concurrent /api/aria/learning/coverage requests started two
# simultaneous to_thread computes — each pinning one executor slot —
# and the default ThreadPoolExecutor (typically 32 workers but shared
# with every other to_thread caller in the process) ran out. Live
# evidence 2026-05-21 10:50 UTC: /api/aria/learning/coverage still
# hung after R-F728's per-cell hoist + R-F775's absorb-off-loop fix.
# Now: 120s TTL cache + per-key inflight future, so two concurrent
# requests share one compute and steady-state requests hit memory.
_HEATMAP_TTL_S = float(os.getenv("ARIA_HEATMAP_CACHE_TTL_S", "120"))
_HEATMAP_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}
_HEATMAP_INFLIGHT: dict[tuple, asyncio.Future] = {}


def _heatmap_cache_key(
    domains: list[str] | None,
    jurisdictions: list[str] | None,
) -> tuple:
    return (
        tuple(domains) if domains is not None else None,
        tuple(jurisdictions) if jurisdictions is not None else None,
    )


def invalidate_heatmap_cache() -> None:
    """Clear the build_heatmap cache. Call from autonomous tasks that
    expect the next /learning/coverage read to reflect fresh knowledge
    (continuous_update, /admin/heatmap-refresh). TTL alone is fine for
    dashboards; explicit invalidation is for write-then-read flows.

    Clears both the result cache AND the inflight-future map: a follower
    mid-`await` on an inflight future would otherwise still receive the
    pre-invalidation result. Inflight futures dropped here are completed
    by their leader's `set_result` regardless — awaiters still resolve,
    they just don't block a fresh recompute on the next call.

    R-F2996 — DO NOT clear `_HEATMAP_DISK_SEEDED` here. That guard is the
    ONE-SHOT cold-start disk seed (R-F931): serve the last-persisted matrix
    on the FIRST post-boot read so a synchronous heavy recompute can't wedge
    the boot loop. Clearing it on every invalidation re-armed that seed, so
    each invalidation (continuous_update fires periodically) RE-SERVED the
    stale on-disk matrix instead of recomputing — a live bug that pinned the
    coverage dashboard on a pre-deploy snapshot across reboots (observed
    2026-07-24: R-F2987's honest freshness fields never surfaced because the
    disk seed kept re-serving the old-format matrix). Invalidation means
    "recompute fresh", which is exactly what LEAVING the seed guard set
    achieves: memory miss → seed already consumed → skip disk → recompute →
    persist fresh. The seed stays a genuine per-process one-shot."""
    _HEATMAP_CACHE.clear()
    _HEATMAP_INFLIGHT.clear()


# R-F931 (2026-05-27) — disk-persist the matrix to the aria_rag volume so a
# COLD boot/cache (post-deploy first /learning/coverage poll) serves the last
# computed matrix instead of recomputing from scratch — which is what produced
# the post-deploy event-loop stall storm (wedge_675: 5.2s + 35.3s at cold boot
# 2026-05-27 08:29-08:30). The disk cache is a one-time cold-start SEED per
# process: after it seeds memory once, the normal 120s-TTL compute+persist path
# takes over (the inverted-index compute below is now cheap + GIL-yielding, so a
# live recompute no longer wedges). Fail-safe: any disk error → compute normally.
# Opt-in (default OFF) so dev/CI without the volume — and the test suite —
# never touch disk. Enabled in production via the fly secret
# ARIA_HEATMAP_DISK_CACHE=/data/coverage_heatmap_cache.json (aria_rag volume).
# The inverted-index compute below already keeps a live recompute non-wedging;
# this disk seed additionally skips the cold-boot recompute entirely.
_HEATMAP_DISK_PATH = os.getenv("ARIA_HEATMAP_DISK_CACHE", "")
_HEATMAP_DISK_TTL_S = float(os.getenv("ARIA_HEATMAP_DISK_TTL_S", "3600"))
_HEATMAP_DISK_SEEDED: set = set()


def _disk_cache_path(cache_key: tuple) -> str | None:
    """Only the default (unfiltered) matrix is disk-persisted — that's the one
    the dashboard polls on cold boot. Filtered queries (custom domains/juris)
    are rare and skip disk."""
    if not _HEATMAP_DISK_PATH:
        return None
    if cache_key != (None, None):
        return None
    return _HEATMAP_DISK_PATH


def _load_disk_cache(cache_key: tuple) -> dict[str, Any] | None:
    path = _disk_cache_path(cache_key)
    if not path:
        return None
    try:
        import json as _json
        with open(path, encoding="utf-8") as f:
            blob = _json.load(f)
        if (time.time() - float(blob.get("_persisted_at", 0))) < _HEATMAP_DISK_TTL_S:
            data = blob.get("data")
            if isinstance(data, dict) and data.get("matrix"):
                return data
    except FileNotFoundError:
        return None
    except Exception as e:  # corrupt/partial file — ignore, recompute
        logger.debug("heatmap disk-cache read failed: %s", e)
    return None


def _save_disk_cache(cache_key: tuple, result: dict[str, Any]) -> None:
    path = _disk_cache_path(cache_key)
    if not path:
        return
    try:
        import json as _json
        import tempfile
        d = os.path.dirname(path)
        # Only persist when the target dir already exists (the aria_rag volume
        # at /data on fly). Never create the volume root — keeps this inert on
        # dev/CI machines that have no /data, so tests don't pollute disk.
        if d and not os.path.isdir(d):
            return
        blob = {"_persisted_at": time.time(), "data": result}
        fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _json.dump(blob, f)
        os.replace(tmp, path)  # atomic
    except Exception as e:
        logger.debug("heatmap disk-cache write failed: %s", e)

# ── Domain rows ───────────────────────────────────────────────────

DOMAINS: list[str] = [
    # Sanctions surface
    "sanctions_screening",
    "sanctions_divergence",
    "rca_screening",

    # Export controls
    "eccn_classification",
    "euc_jurisdictions",
    "wassenaar_dual_use",
    "weapon_systems",

    # Anti-financial-crime
    "fatf_ml_typologies",
    "fatf_tbml",
    "fcpa_enforcement",
    "economic_substance",
    "virtual_assets",

    # Counterparty
    "defence_market_briefing",
    "procurement_pipeline",
    "counter_intelligence",

    # NATO + interoperability
    "nato_standards",
    "international_law",
]

# ── Jurisdiction columns ──────────────────────────────────────────

# Grouped for readability; flattened for the matrix
JURISDICTION_GROUPS: dict[str, list[str]] = {
    "anchors": ["US", "UK", "EU", "UN", "NATO"],
    "lusophone_moat": ["Angola", "Mozambique", "Cape Verde", "Guinea-Bissau",
                       "Brazil", "São Tomé"],
    "wider_africa":   ["Nigeria", "Ghana", "Kenya", "Ethiopia", "Tanzania",
                       "Senegal", "Côte d'Ivoire", "Cameroon", "Rwanda",
                       "South Africa", "Algeria", "Morocco"],
    "gulf_mena":      ["Saudi Arabia", "UAE", "Qatar", "Bahrain", "Kuwait",
                       "Oman", "Jordan", "Iraq", "Lebanon", "Israel",
                       "Turkey", "Egypt"],
    "asia_pacific":   ["Indonesia", "Vietnam", "Philippines", "Bangladesh",
                       "India", "Pakistan", "South Korea", "Japan"],
    "latam":          ["Mexico", "Colombia", "Peru", "Venezuela", "Argentina"],
    "europe_emerging": ["Romania", "Poland", "Ukraine"],
}

JURISDICTIONS: list[str] = [
    j for group in JURISDICTION_GROUPS.values() for j in group
]


# ── Cell density tiers ────────────────────────────────────────────

# Coverage grades for a single cell:
DENSITY_TIERS = [
    ("absent",   0,    0),     # 0 facts (gap)
    ("thin",     1,    9),     # 1-9 facts
    ("moderate", 10,   49),    # 10-49 facts
    ("strong",   50,   199),   # 50-199 facts
    ("deep",     200,  10**9), # 200+ facts
]


def density_tier(fact_count: int) -> str:
    for label, lo, hi in DENSITY_TIERS:
        if lo <= fact_count <= hi:
            return label
    return "absent"


# R-F128 (2026-05-10): jurisdiction synonyms — the dashboard heatmap
# was 100% absent because facts say "Saudi" not "Saudi Arabia",
# "UAE" or "Emirates" not "United Arab Emirates", "USA" not "US",
# etc. The previous matcher required EXACT substring of the canonical
# name and the result was 867 cells absent on a corpus of 20k facts.
JURISDICTION_SYNONYMS: dict[str, list[str]] = {
    "US":            ["us", "usa", "united states", "u.s.", "u.s.a"],
    "UK":            ["uk", "united kingdom", "britain", "british",
                      "great britain"],
    "EU":            ["eu", "european union", "europe"],
    "UN":            ["un", "united nations"],
    "NATO":          ["nato", "atlantic alliance"],
    "Angola":        ["angola", "angolan"],
    "Mozambique":    ["mozambique", "mozambican", "moçamb"],
    "Cape Verde":    ["cape verde", "cabo verde"],
    "Guinea-Bissau": ["guinea-bissau", "guinea bissau", "bissau"],
    "Brazil":        ["brazil", "brazilian", "brasil"],
    "São Tomé":      ["são tomé", "sao tome", "stp"],
    "Saudi Arabia":  ["saudi arabia", "saudi", "ksa", "riyadh"],
    "UAE":           ["uae", "u.a.e", "emirates", "united arab emirates",
                      "abu dhabi", "dubai"],
    "Côte d'Ivoire": ["côte d'ivoire", "cote d'ivoire", "ivory coast",
                      "ivorian"],
    "South Africa":  ["south africa", "south african"],
    "South Korea":   ["south korea", "republic of korea", "rok"],
    "North Korea":   ["north korea", "dprk"],
}


def _juris_synonyms(jurisdiction: str) -> list[str]:
    """Return lowercase synonym list for a jurisdiction (always includes
    the bare name)."""
    syn = JURISDICTION_SYNONYMS.get(jurisdiction)
    if syn:
        return [s.lower() for s in syn]
    return [jurisdiction.lower()]


# R-F2439 — domain-token overrides for domains whose snake_case split produces
# tokens that can never match their real corpus vocabulary. _matches_cell needs
# ALL tokens present, so "fatf_ml_typologies" → ["fatf","typologies"] demands a
# fact literally contain the PLURAL "typologies" — even a fact saying "FATF
# typology" (singular) fails. Override to the substring stem "typolog" (matches
# typology/typologies) so genuine FATF-typology facts are counted. This is
# FATF-specific (still requires "fatf" AND a typology stem) — it does NOT loosen
# the match to count generic money-laundering facts, so it corrects an
# under-report without inflating the metric.
_DOMAIN_TOKEN_OVERRIDES: dict[str, list[str]] = {
    "fatf_ml_typologies": ["fatf", "typolog"],
}


def _domain_tokens(domain: str) -> list[str]:
    """Significant lowercase tokens for a domain — an explicit override when the
    snake_case split (R-F2439) would never match the real vocabulary, else the
    tokens >= 3 chars from the snake_case name."""
    override = _DOMAIN_TOKEN_OVERRIDES.get(domain)
    if override is not None:
        return override
    return [t for t in domain.lower().split("_") if len(t) >= 3]


def _matches_cell(text: str, dom_tokens: list[str],
                  jur_synonyms: list[str]) -> bool:
    """A fact matches a (domain, jurisdiction) cell when ANY jurisdiction
    synonym appears AND ALL substantive domain tokens appear in the text."""
    if not text:
        return False
    if not any(s in text for s in jur_synonyms):
        return False
    return all(tok in text for tok in dom_tokens)


def _fact_text(fact: dict) -> str:
    """Lowercase concatenation of the fact-text fields used for matching."""
    return " ".join(
        str(fact.get(k) or "") for k in
        ("entity", "topic", "content", "summary", "detail", "source")
    ).lower()


def _signal_text(s: dict) -> str:
    return " ".join(
        str(s.get(k) or "") for k in
        ("source", "summary", "detail", "entity", "topic")
    ).lower()


def _fact_ts(fact: dict) -> float | None:
    """R-F2340: epoch seconds of a fact's most recent write (updatedAt, else
    createdAt), or None if absent/unparseable. Facts store these as ISO strings
    (knowledge.py: datetime.now(timezone.utc).isoformat()). Used to derive
    MEASURED per-cell freshness from the real corpus instead of leaving every
    canonical-domain cell's staleness unknown."""
    raw = fact.get("updatedAt") or fact.get("createdAt")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _count_facts_for_cell_sync(
    domain: str,
    jurisdiction: str,
    facts: list[dict],
    signals: list[dict],
) -> tuple[int, int]:
    """R-F728 (2026-05-20): pure-sync per-cell scorer for `build_heatmap`'s
    worker-thread loop. Pre-R-F728 `_count_facts_for_cell` was an `async
    def` invoked once per cell (28 × 31 = 868 cells), and each call
    re-fetched the entire fact set + awaited a fresh `intel_ledger.
    get_recent()` — 868× duplicate work on top of the iteration cost.
    With ~55k facts in production, the per-cell iteration ran 48M+
    times entirely on the event loop; wedge_674 captured a 186.89s
    main-thread stall here (route /api/aria/learning/coverage).

    Now the caller hoists facts + signals fetching above the loop and
    runs the entire matrix in a single `asyncio.to_thread` so the
    event loop stays responsive while one worker thread iterates."""
    fact_count = 0
    signal_count = 0
    dom_tokens = _domain_tokens(domain)
    jur_synonyms = _juris_synonyms(jurisdiction)

    for fact in facts:
        if not isinstance(fact, dict):
            continue
        if _matches_cell(_fact_text(fact), dom_tokens, jur_synonyms):
            fact_count += 1

    for s in signals:
        if not isinstance(s, dict):
            continue
        if _matches_cell(_signal_text(s), dom_tokens, jur_synonyms):
            signal_count += 1

    return fact_count, signal_count


def _count_from_texts(
    fact_texts: list[str],
    signal_texts: list[str],
    dom_tokens: list[str],
    jur_synonyms: list[str],
) -> tuple[int, int]:
    """R-F928 — count cell matches against PRE-LOWERCASED text lists.

    `_compute_matrix_sync` precomputes `_fact_text`/`_signal_text` ONCE per
    fact/signal, then calls this for each cell. Pre-R-F928 the matrix path
    rebuilt `_fact_text(fact)` for every (fact × cell) pair = domains ×
    jurisdictions × facts ≈ 57M string-joins on a 67k-fact corpus, holding
    the GIL ~18s and stalling the event loop (wedge_673). Building the text
    once per fact cuts that string work ~850×; here we only run the cheap
    substring matcher."""
    fact_count = sum(
        1 for t in fact_texts if _matches_cell(t, dom_tokens, jur_synonyms)
    )
    signal_count = sum(
        1 for t in signal_texts if _matches_cell(t, dom_tokens, jur_synonyms)
    )
    return fact_count, signal_count


async def _count_facts_for_cell(domain: str, jurisdiction: str) -> tuple[int, int]:
    """Backwards-compatible single-cell scorer. Fetches facts + signals
    fresh on every call — kept for any caller that needs a one-off
    cell. `build_heatmap` hoists the fetch and uses the sync scorer."""
    facts: list[dict] = []
    try:
        from . import knowledge as _k
        facts = _k.all_facts() if hasattr(_k, "all_facts") else []
    except Exception as e:
        logger.debug("coverage knowledge query failed for %s/%s: %s",
                     domain, jurisdiction, e)

    signals: list[dict] = []
    try:
        from . import intel_ledger as _il
        for method_name in ("get_recent", "all_signals"):
            fn = getattr(_il, method_name, None)
            if not callable(fn):
                continue
            try:
                got = fn()
                if hasattr(got, "__await__"):
                    got = await got
            except Exception:
                continue
            if isinstance(got, list):
                signals = got
                break
    except Exception as e:
        logger.debug("coverage ledger query failed: %s", e)

    return _count_facts_for_cell_sync(domain, jurisdiction, facts, signals)



# ── R-F4129 (C-164) — a cold build must never be cached ─────────────────────
# aria-intel boots for ~10 minutes (§11c) with `knowledge._cache["facts"]` empty.
# A `/learning/coverage` request in that window builds 867 cells / 867 gaps, and
# `_write_heatmap_redis_cache` persisted it with **ex=3600** — so one boot-window
# request made an empty matrix the answer for the next hour, long after the facts
# had loaded. Eight deploys in a day means eight such windows.
#
# The defect is not the emptiness; it is PERSISTING it. A build that saw no facts
# is a measurement taken before the instrument was ready — the same shape as
# C-152 (a store that could not be read was allowed to overwrite the durable
# copy) and R-F2664 before it.
#
# A cold build is still SERVED — the caller asked, and returning nothing is worse
# — but it is not written, and it is labelled so the reader can tell.
def is_cacheable(payload: dict) -> bool:
    """A build that saw ZERO facts is never persisted. Everything else is.

    R-F4132 (C-167) — this docstring used to claim "False only on a POSITIVE
    reading of zero facts", and that `knowledge_cache_facts: None` would not
    block. **Neither was true of the code.** Both branches below `facts_seen == 0`
    returned False, so the `cache_facts` conditional was DEAD and an unmeasurable
    probe blocked exactly like a measured one. Prose and behaviour disagreed, and
    the prose is what the next reader would have trusted.

    The BEHAVIOUR was right and is kept. `facts_seen == 0` has three causes and
    two of them are indistinguishable at the point of decision:

      * cache > 0  — facts exist and the build saw none. Definitely cold.
      * cache == 0 — boot (the cache has not loaded) OR a genuinely empty corpus.
                     **Identical readings.** Blocking costs a transient
                     uncached build on a brand-new deployment, and that resolves
                     the moment the first fact lands. Caching the wrong one costs
                     an hour of empty matrix, which is the defect C-164 exists to
                     stop.
      * cache is None — could not measure. Never treat that as proof of anything.

    So the rule is simply: saw nothing, persist nothing. A payload with no
    diagnostics at all predates R-F4128 and stays cacheable, because treating
    absence as "cold" would refuse every legacy write.
    """
    try:
        d = payload.get("matcher_diagnostics")
    except Exception:
        return True
    if not isinstance(d, dict):
        return True                      # pre-R-F4128 payload
    seen = d.get("facts_seen")
    if not isinstance(seen, int) or seen > 0:
        return True
    return False                         # saw zero facts -> never persist


def mark_cacheability(payload: dict) -> dict:
    """Stamp `built_cold` so a served-but-unpersisted reading says so."""
    try:
        payload["built_cold"] = not is_cacheable(payload)
    except Exception:
        pass
    return payload


async def build_heatmap(
    *,
    domains: list[str] | None = None,
    jurisdictions: list[str] | None = None,
) -> dict[str, Any]:
    """Build the full coverage matrix.

    R-F781 (2026-05-21) wraps the compute with a 120s TTL cache +
    single-flight future so concurrent /learning/coverage requests
    share one matrix compute instead of pinning the executor pool.
    Set ARIA_HEATMAP_CACHE_TTL_S=0 to disable (tests use this).

    Returns:
        {
          "domains":       [...],
          "jurisdictions": [...],
          "matrix":        {domain: {jurisdiction: cell_dict, ...}, ...}
          "summary":       { coverage_score, gap_count, deep_cells, ... }
        }
    """
    if _HEATMAP_TTL_S > 0:
        cache_key = _heatmap_cache_key(domains, jurisdictions)
        cached = _HEATMAP_CACHE.get(cache_key)
        if cached is not None and (time.time() - cached[0]) < _HEATMAP_TTL_S:
            # Shallow copy so the route's `out["from_cache"] = False`
            # mutation at routes/aria.py:2428 (and any future top-level
            # field set by a caller) can't poison the cached reference.
            # Nested dicts (matrix, summary) are still shared; that's
            # acceptable since no caller mutates them and copying a 868-
            # cell matrix per request would defeat the cache speedup.
            return dict(cached[1])

        # R-F931 — COLD-START disk seed: on the first miss this process,
        # serve the last persisted matrix (if fresh within the disk TTL)
        # instead of forcing a synchronous recompute on the boot-time
        # request — that cold recompute is what wedged the loop post-deploy.
        # One-shot per key: after seeding memory, the normal TTL-driven
        # compute+persist path below takes over.
        if cache_key not in _HEATMAP_DISK_SEEDED:
            _HEATMAP_DISK_SEEDED.add(cache_key)
            # R-F1117: offload sync json.load + file I/O to thread executor
            # to avoid blocking the event loop (was causing heartbeat stalls).
            disk = await asyncio.to_thread(_load_disk_cache, cache_key)
            if disk is not None:
                _HEATMAP_CACHE[cache_key] = (time.time(), disk)
                return dict(disk)

        existing = _HEATMAP_INFLIGHT.get(cache_key)
        if existing is not None and not existing.done():
            try:
                shared = await existing
                return dict(shared)
            except Exception:
                # Leader's compute failed; fall through to our own attempt.
                pass

        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        _HEATMAP_INFLIGHT[cache_key] = fut
        try:
            result = await _build_heatmap_uncached(
                domains=domains, jurisdictions=jurisdictions,
            )
            _HEATMAP_CACHE[cache_key] = (time.time(), result)
            # R-F1117: offload sync json.dump + file I/O to thread executor
            # to avoid blocking the event loop (was causing heartbeat stalls).
            await asyncio.to_thread(_save_disk_cache, cache_key, result)
            if not fut.done():
                fut.set_result(result)
            return dict(result)
        except Exception as e:
            if not fut.done():
                fut.set_exception(e)
            raise
        finally:
            _HEATMAP_INFLIGHT.pop(cache_key, None)

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="coverage_heatmap",
        summary="Build Heatmap",
        source_id="coverage_heatmap:R-F996",
    )

    return await _build_heatmap_uncached(
        domains=domains, jurisdictions=jurisdictions,
    )


async def _build_heatmap_uncached(
    *,
    domains: list[str] | None = None,
    jurisdictions: list[str] | None = None,
) -> dict[str, Any]:
    """Underlying compute — pre-R-F781 behaviour, no cache.

    Kept separate so tests can bypass the cache (and so the cached
    wrapper stays a thin readable shim above the compute path)."""
    domain_list = domains or DOMAINS
    juris_list = jurisdictions or JURISDICTIONS

    from . import learning_progress as _lp
    freshness_records = {}
    try:
        all_freshness = await _lp.get_all_domains()
        # R-F4097 (C-152): None = the tracker could not be read. Iterating it
        # would raise; treating it as empty silently drops freshness from every
        # heatmap cell, so skip and leave the records absent.
        for f in (all_freshness or []):
            freshness_records[f.get("domain", "")] = f
    except Exception:
        pass

    # R-F728 (2026-05-20) — hoist facts + signals OUT of the per-cell
    # loop. Pre-R-F728 each of the 868 cells fetched its own copy of
    # ~55k facts and awaited a fresh `intel_ledger.get_recent()`,
    # then iterated locally on the event loop. wedge_674 captured
    # 186.89s of main-thread time in this nested loop. With facts
    # + signals pre-fetched, the entire matrix compute is sync and
    # runs in a single worker thread; the loop is free.
    # R-F4128 (C-163) — record WHY the list is what it is.
    #
    # This read `_k.all_facts() if hasattr(...) else []`, so a missing attribute
    # produced an empty list with no error and no log — which is the R-F164
    # mechanism verbatim ("the hasattr check silently evaluated False, and every
    # coverage cell returned fact_count=0"). The same 867/867 recurred on
    # 2026-08-17 and the payload still could not say which of three causes it was.
    facts: list[dict] = []
    _facts_source = "ok"
    try:
        from . import knowledge as _k
        if not hasattr(_k, "all_facts"):
            _facts_source = "attribute_missing"
        else:
            facts = _k.all_facts() or []
    except Exception as e:
        _facts_source = "error"
        logger.debug("coverage facts fetch failed: %s", e)

    # The discriminator inference could not settle: is the ACCESSOR returning
    # empty, or is the cache behind it empty? None = could not measure, which is
    # never the same as zero.
    _cache_facts = None
    try:
        from . import knowledge as _k2
        _c = getattr(_k2, "_cache", None)
        if isinstance(_c, dict):
            _cf = _c.get("facts")
            _cache_facts = len(_cf) if isinstance(_cf, list) else 0
    except Exception:
        _cache_facts = None

    signals: list[dict] = []
    try:
        from . import intel_ledger as _il
        for method_name in ("get_recent", "all_signals"):
            fn = getattr(_il, method_name, None)
            if not callable(fn):
                continue
            try:
                got = fn()
                if hasattr(got, "__await__"):
                    got = await got
            except Exception:
                continue
            if isinstance(got, list):
                signals = got
                break
    except Exception as e:
        logger.debug("coverage signals fetch failed: %s", e)

    def _compute_matrix_sync() -> dict[str, dict[str, Any]]:
        # R-F931 — INVERTED-INDEX matcher. The R-F928 path removed the per-cell
        # string REBUILD but still ran the matcher facts×domains×jurisdictions
        # ≈ 58M times; one heavy cell's sweep could still exceed the 5s stall
        # threshold on a cold recompute (wedge_675 hit 35.3s post-deploy
        # 2026-05-27 08:30). Now: for each fact/signal, compute WHICH domains +
        # jurisdictions its text matches ONCE (facts×(D+J) ≈ 4.6M), then tally
        # cells from those matches. Truth value per cell is identical to
        # `_matches_cell` — an item counts for (d,j) iff (any jurisdiction
        # synonym in text) AND (all domain tokens in text) — so matrix values
        # are unchanged; just ~(D·J)/(D+J) ≈ 12× fewer ops and no heavy cell.
        dom_tokens_map = {d: _domain_tokens(d) for d in domain_list}
        jur_syn_map = {j: _juris_synonyms(j) for j in juris_list}

        fact_cells: dict[tuple[str, str], int] = {}
        signal_cells: dict[tuple[str, str], int] = {}
        # R-F2340: newest fact timestamp per cell → measured freshness.
        cell_newest_ts: dict[tuple[str, str], float] = {}

        # R-F4128 — per-stage counters. Three different defects produce an
        # identical 867/867: no source, no text, no match. Counting each stage is
        # what separates them, and costs one increment per item in a loop that
        # already walks every fact.
        stage = {"with_text": 0, "matched_domain": 0, "matched_both": 0}

        def _tally(items: list, text_fn, target: dict[tuple[str, str], int],
                   ts_target: dict[tuple[str, str], float] | None = None,
                   _stage: dict | None = None) -> None:
            for idx, it in enumerate(items):
                # R-F931 — yield the GIL every 1024 items so this worker thread
                # can't starve the event loop, regardless of corpus size
                # (bounds R-F703 heartbeat staleness to a ~1024-item slice).
                if (idx & 0x3FF) == 0:
                    time.sleep(0)
                if not isinstance(it, dict):
                    continue
                text = text_fn(it)
                if not text:
                    continue
                if _stage is not None:
                    _stage["with_text"] += 1
                matched_doms = [
                    d for d in domain_list
                    if all(tok in text for tok in dom_tokens_map[d])
                ]
                if not matched_doms:
                    continue
                if _stage is not None:
                    _stage["matched_domain"] += 1
                matched_jurs = [
                    j for j in juris_list
                    if any(s in text for s in jur_syn_map[j])
                ]
                if not matched_jurs:
                    continue
                if _stage is not None:
                    _stage["matched_both"] += 1
                # R-F2340: parse the item's timestamp ONCE per item (not per cell).
                fts = _fact_ts(it) if ts_target is not None else None
                for d in matched_doms:
                    for j in matched_jurs:
                        key = (d, j)
                        target[key] = target.get(key, 0) + 1
                        if fts is not None:
                            prev = ts_target.get(key)
                            if prev is None or fts > prev:
                                ts_target[key] = fts

        _tally(facts, _fact_text, fact_cells, cell_newest_ts, stage)
        _tally(signals, _signal_text, signal_cells)

        # R-F2340: precompute per-domain staleness windows (hours) once.
        now_ts = time.time()
        dom_window_h = {d: _lp._max_staleness_for(d) for d in domain_list}

        m: dict[str, dict[str, Any]] = {}
        for d in domain_list:
            m[d] = {}
            domain_freshness = freshness_records.get(d, {})
            window_h = dom_window_h[d]
            for j in juris_list:
                fact_count = fact_cells.get((d, j), 0)
                signal_count = signal_cells.get((d, j), 0)
                # R-F164: tier off the combined density. Signals weighted 0.5
                # (noisier than curated facts) so cells with strong ledger
                # coverage but thin curated knowledge still surface.
                combined = fact_count + int(signal_count * 0.5)
                tier = density_tier(combined)
                # R-F2340: MEASURED freshness. A cell's staleness is derived from
                # the recency of its own facts (newest updatedAt/createdAt) vs the
                # domain's max-staleness window — grounded in the real corpus, not
                # the disjoint learning_progress auto-topic namespace (R-F2332).
                # Precedence: (1) corpus fact recency, (2) a matching
                # learning_progress domain record (future-proof; no-op today),
                # (3) None = genuinely unknown (cell has no dated facts).
                newest = cell_newest_ts.get((d, j))
                if newest is not None:
                    hours_since = (now_ts - newest) / 3600.0
                    is_stale = hours_since > window_h
                    freshness_known = True
                    hours_since_refresh = round(hours_since, 1)
                elif "is_stale" in domain_freshness:
                    is_stale = domain_freshness.get("is_stale")
                    freshness_known = True
                    hours_since_refresh = domain_freshness.get("hours_since_refresh")
                else:
                    is_stale = None
                    freshness_known = False
                    hours_since_refresh = None
                m[d][j] = {
                    "fact_count":          fact_count,
                    "signal_count":        signal_count,
                    "tier":                tier,
                    "is_stale":            is_stale,
                    "freshness_known":     freshness_known,
                    "hours_since_refresh": hours_since_refresh,
                }
        return m, stage

    matrix, _stage = await asyncio.to_thread(_compute_matrix_sync)

    score, summary_stats = _compute_score(matrix, domain_list, juris_list)
    return {
        "domains":            domain_list,
        "jurisdictions":      juris_list,
        "jurisdiction_groups": JURISDICTION_GROUPS,
        "matrix":             matrix,
        "summary":            summary_stats,
        "coverage_score":     score,
        # R-F4128 (C-163) — ALWAYS present, not only when the matrix is empty.
        # A diagnostics block that appears only on failure cannot describe the
        # dangerous case: a populated matrix built from far fewer facts than
        # expected. Same reasoning as C-39's coverage provenance.
        "matcher_diagnostics": {
            "facts_seen":            len(facts),
            "signals_seen":          len(signals),
            "facts_source":          _facts_source,
            "knowledge_cache_facts": _cache_facts,
            "facts_with_text":       _stage["with_text"],
            "facts_matched_domain":  _stage["matched_domain"],
            "facts_matched_both":    _stage["matched_both"],
            "domains":               len(domain_list),
            "jurisdictions":         len(juris_list),
        },
    }


def _compute_score(
    matrix: dict[str, dict[str, Any]],
    domains: list[str],
    jurisdictions: list[str],
) -> tuple[float, dict[str, Any]]:
    """Composite coverage score in [0, 1].

    Weighted by tier:
      absent=0, thin=0.25, moderate=0.50, strong=0.80, deep=1.00
    Average across all cells. Stale cells get 0.7× weight (still count
    as some coverage but less).
    """
    tier_weights = {"absent": 0.0, "thin": 0.25, "moderate": 0.50, "strong": 0.80, "deep": 1.00}
    total = 0
    n = 0
    deep_count = 0
    gap_count = 0
    stale_count = 0
    unknown_staleness = 0
    for d in domains:
        for j in jurisdictions:
            cell = matrix.get(d, {}).get(j) or {"tier": "absent"}
            tier = cell.get("tier", "absent")
            w = tier_weights.get(tier, 0.0)
            # R-F2332: ONLY a known-stale cell (is_stale is True) is penalised.
            # is_stale None = unknown (no freshness signal for this domain) → no
            # penalty; counted separately so the summary honestly discloses that
            # the freshness data does not cover these cells, rather than deflating
            # the score with a phantom 0.7× on every cell.
            stale = cell.get("is_stale")
            if stale is True:
                w *= 0.7
                stale_count += 1
            elif stale is None and tier != "absent":
                # R-F2987: "freshness unknown" means a POPULATED cell whose facts
                # carry no parseable timestamp — a genuine freshness-measurement
                # gap. An ABSENT (empty) cell is NOT freshness-unknown: it has no
                # data to be fresh or stale, and it is already disclosed as a GAP
                # (gap_count). Counting the 617 empty cells here conflated "no data"
                # with "freshness unmeasured", understating that freshness IS
                # measured for every populated cell (the live "617 freshness
                # unknown == 617 absent" artifact). Scope it to populated cells.
                unknown_staleness += 1
            total += w
            n += 1
            if tier == "deep":
                deep_count += 1
            if tier == "absent":
                gap_count += 1
    score = round(total / n, 3) if n else 0.0
    populated = n - gap_count
    return score, {
        "cells":       n,
        "gap_count":   gap_count,
        "deep_cells":  deep_count,
        "stale_cells": stale_count,
        "staleness_unknown_cells": unknown_staleness,
        # R-F2987: honest freshness framing — freshness is MEASURED for every
        # populated cell except the (few) whose facts are undated. Lets the UI say
        # "freshness measured for N populated cells" instead of relabelling the
        # empty cells as a freshness deficiency.
        "populated_cells": populated,
        "freshness_measured_cells": populated - unknown_staleness,
        "gap_pct":     round(gap_count / n * 100, 1) if n else 0,
    }


def gap_targets(
    heatmap: dict[str, Any],
    *,
    max_targets: int = 20,
) -> list[dict[str, Any]]:
    """Pick the highest-priority gaps for autonomous targeting.

    Priority = absent > thin > moderate. Within tier, prefer cells
    where the domain + jurisdiction combination is high commercial
    value (Lusophone moat + critical anchors get a multiplier).
    """
    matrix = heatmap.get("matrix") or {}
    candidates: list[tuple[float, dict[str, Any]]] = []
    high_value_jurisdictions = set(JURISDICTION_GROUPS["lusophone_moat"]
                                   + JURISDICTION_GROUPS["anchors"])
    for d, jur_map in matrix.items():
        for j, cell in jur_map.items():
            tier = cell.get("tier", "absent")
            base = {"absent": 1.0, "thin": 0.7, "moderate": 0.3}.get(tier, 0.0)
            if base == 0:
                continue
            multiplier = 1.5 if j in high_value_jurisdictions else 1.0
            score = base * multiplier
            candidates.append((score, {
                "domain":       d,
                "jurisdiction": j,
                "tier":         tier,
                "fact_count":   cell.get("fact_count", 0),
                "is_stale":     cell.get("is_stale", True),
                "priority":     round(score, 3),
                "narrative":    f"{d} × {j}: {tier} ({cell.get('fact_count', 0)} facts).",
            }))
    candidates.sort(key=lambda kv: -kv[0])
    return [c[1] for c in candidates[:max_targets]]


def summary() -> dict[str, Any]:
    return {
        "module":              "coverage_heatmap",
        "domains_count":       len(DOMAINS),
        "jurisdictions_count": len(JURISDICTIONS),
        "matrix_size":         len(DOMAINS) * len(JURISDICTIONS),
        "groups":               list(JURISDICTION_GROUPS.keys()),
        "purpose":              "domain × jurisdiction knowledge-coverage view",
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

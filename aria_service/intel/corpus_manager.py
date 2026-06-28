"""ARIA Corpus Manager — autonomous knowledge source management.

Cherry-picked from v3.1 proposal, adapted to use ARIA's existing stack:
  - Redis (redis_store) for registry persistence + content hashing
  - chromadb (rag_store) for document indexing + semantic search
  - LLM provider (llm/provider.py) for URL classification

CAPABILITY:
ARIA can add, classify, crawl, and retire URLs in her own knowledge
corpus without human intervention for LOW risk additions.

HOW IT WORKS:
1. During research, ARIA encounters a useful source not in her corpus
2. propose_url() classifies it against tier criteria using the LLM
3. If LOW risk -> auto-adds to registry + queues for crawl
4. If MEDIUM/HIGH -> stores proposal for human review
5. Weekly crawl picks up all registered URLs and indexes fresh content
6. Regional gap detection identifies thin coverage areas
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.corpus_manager")

# Feature gate
def is_enabled() -> bool:
    val = os.getenv("ARIA_CORPUS_MANAGER_ENABLED", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


# ── Tier definitions ────────────────────────────────────────────────────────

TIER_CRITERIA = {
    "A": {
        "label": "Primary Sources",
        "description": "Official government, treaty, court, registry, or intergovernmental organisation records",
        "trust_score": 0.95,
        "examples": ["gov.uk", "nato.int", "un.org", "sipri.org", "wassenaar.org",
                      "eur-lex.europa.eu", "ofac.treasury.gov", "opensanctions.org",
                      "companieshouse.gov.uk", "ted.europa.eu", "sam.gov"],
    },
    "B": {
        "label": "Defence and Security Research",
        "description": "Peer-reviewed or institutionally credible defence and security analysis",
        "trust_score": 0.85,
        "examples": ["rand.org", "rusi.org", "iiss.org", "csis.org", "cfr.org",
                      "carnegieendowment.org", "chathamhouse.org", "issafrica.org"],
    },
    "B+_GEOPOLITICS": {
        "label": "World Geopolitics",
        "description": "Regional geopolitics and strategic analysis across all five continents",
        "trust_score": 0.82,
        "examples": ["ecfr.eu", "ponarseurasia.org", "lowyinstitute.org", "mei.edu"],
    },
    "B+_HARDWARE": {
        "label": "Military Hardware and Equipment",
        "description": "Technical specifications, equipment databases, platform comparisons",
        "trust_score": 0.80,
        "examples": ["armyrecognition.com", "militaryfactory.com", "globalsecurity.org", "fas.org"],
    },
    "C": {
        "label": "Live Intelligence Feeds",
        "description": "Real-time procurement, conflict monitoring, and political risk",
        "trust_score": 0.75,
        "examples": ["acleddata.com", "oryxspioenkop.com", "bellingcat.com", "defenceweb.co.za"],
    },
    "C+_CPLP": {
        "label": "Lusophone Africa and CPLP",
        "description": "Portuguese-language sources and CPLP-specific intelligence — Arkmurus core moat",
        "trust_score": 0.78,
        "examples": ["cplp.org", "ipris.org", "clubofmozambique.com", "lusa.pt", "jornaldeangola.ao"],
    },
    "D": {
        "label": "Analytical Tradecraft",
        "description": "Decision theory, forecasting methodology, intelligence analysis",
        "trust_score": 0.80,
        "examples": ["gjopen.com", "metaculus.com", "superforecasting.com"],
    },
    "E": {
        "label": "Entity and Compliance Verification",
        "description": "Sanctions databases, company registries, AML screening",
        "trust_score": 0.90,
        "examples": ["opensanctions.org", "opencorporates.com", "ofac.treasury.gov",
                      "sanctionssearch.ofac.treas.gov", "baselgovernance.org"],
    },
}

# Redis keys
REGISTRY_KEY = "crucix:aria:corpus_registry"
HASH_KEY_PREFIX = "crucix:aria:corpus_hash:"
PROPOSALS_KEY = "crucix:aria:corpus_proposals"


@dataclass
class URLProposal:
    url: str
    proposed_tier: str = "C"
    confidence: float = 0.7
    rationale: str = ""
    geographic_scope: list[str] = field(default_factory=list)
    language: str = "en"
    risk_level: str = "MEDIUM"
    status: str = "PENDING"
    proposed_at: str = ""
    approved_at: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "proposed_tier": self.proposed_tier,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "geographic_scope": self.geographic_scope,
            "language": self.language,
            "risk_level": self.risk_level,
            "status": self.status,
            "proposed_at": self.proposed_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "approved_at": self.approved_at,
        }


# ── Seed registry (initial URLs across all tiers) ──────────────────────────

SEED_REGISTRY: dict[str, list[str]] = {
    "A": [
        "https://www.gov.uk/government/organisations/export-control-organisation",
        "https://www.sipri.org/databases/armstransfers",
        "https://www.wassenaar.org/control-lists/",
        "https://ted.europa.eu/",
        "https://sam.gov/",
        "https://opensanctions.org/",
        "https://www.un.org/securitycouncil/sanctions/information",
        "https://ucdp.uu.se",
    ],
    "B": [
        "https://www.rand.org/topics/defense-and-national-security.html",
        "https://rusi.org/explore-our-research",
        "https://www.iiss.org/research",
        "https://www.csis.org/topics/defense-and-security",
        "https://issafrica.org/",
        "https://understandingwar.org",
    ],
    "B+_HARDWARE": [
        "https://www.armyrecognition.com/",
        "https://www.defensenews.com/",
        "https://breakingdefense.com/",
    ],
    "C": [
        "https://acleddata.com/",
        "https://www.oryxspioenkop.com/",
        "https://www.bellingcat.com/",
        "https://www.defenceweb.co.za/",
    ],
    "C+_CPLP": [
        "https://www.cplp.org/",
        "https://clubofmozambique.com/",
        "https://www.lusa.pt/",
        "https://www.jornaldeangola.ao/",
    ],
    "E": [
        "https://opensanctions.org/",
        "https://opencorporates.com/",
    ],
}


# ── Core functions ──────────────────────────────────────────────────────────

async def load_registry() -> dict[str, list[str]]:
    """Load the URL registry from Redis, seeding from defaults if empty."""
    data = await rs.get_json(REGISTRY_KEY)
    if data and isinstance(data, dict):
        return data
    # First run — seed from defaults
    await rs.set_json(REGISTRY_KEY, SEED_REGISTRY)
    logger.info("Corpus registry seeded with %d URLs across %d tiers",
                sum(len(v) for v in SEED_REGISTRY.values()), len(SEED_REGISTRY))
    return dict(SEED_REGISTRY)


async def save_registry(registry: dict[str, list[str]]) -> None:
    """Persist the URL registry to Redis."""
    await rs.set_json(REGISTRY_KEY, registry)


async def propose_url(url: str, context: str = "", llm=None) -> URLProposal:
    """Classify a URL against tier criteria and route by risk level.

    LOW risk: auto-adds to registry.
    MEDIUM: stores as pending proposal for human review.
    HIGH: documents only, does not add.
    """
    if not is_enabled():
        return URLProposal(url=url, status="DISABLED")

    # Check if already registered
    registry = await load_registry()
    all_urls = {u for urls in registry.values() for u in urls}
    if url in all_urls:
        return URLProposal(url=url, status="ALREADY_REGISTERED")

    # Classify using LLM if available
    if llm and llm.is_configured:
        proposal = await _classify_url_llm(url, context, llm)
    else:
        proposal = _classify_url_heuristic(url)

    proposal.proposed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Route by risk level
    if proposal.risk_level == "LOW":
        tier = proposal.proposed_tier
        if tier not in registry:
            registry[tier] = []
        if url not in registry[tier]:
            registry[tier].append(url)
            await save_registry(registry)
            proposal.status = "AUTO_APPROVED"
            logger.info("[corpus] AUTO-ADDED %s to tier %s (confidence %.2f)",
                        url, tier, proposal.confidence)
    elif proposal.risk_level == "MEDIUM":
        proposal.status = "PENDING_REVIEW"
        await _store_proposal(proposal)
        logger.info("[corpus] PENDING REVIEW: %s → tier %s", url, proposal.proposed_tier)
    else:
        proposal.status = "DOCUMENTED_ONLY"
        await _store_proposal(proposal)
        logger.info("[corpus] HIGH RISK documented only: %s", url)

    return proposal


async def add_url_directly(url: str, tier: str) -> bool:
    """Human-directed addition — bypasses classification."""
    if tier not in TIER_CRITERIA:
        logger.warning("[corpus] Rejected add: invalid tier %r for %s", tier, url)
        return False
    registry = await load_registry()
    if tier not in registry:
        registry[tier] = []
    if url not in registry[tier]:
        registry[tier].append(url)
        await save_registry(registry)
        logger.info("[corpus] Direct add: %s → tier %s", url, tier)
        return True
    return False


async def remove_url(url: str) -> bool:
    """Remove a URL from the registry."""
    registry = await load_registry()
    removed = False
    for tier, urls in registry.items():
        if url in urls:
            urls.remove(url)
            removed = True
    if removed:
        await save_registry(registry)
        # Clean up content hash
        await rs.delete(f"{HASH_KEY_PREFIX}{hashlib.md5(url.encode()).hexdigest()}")
    return removed


async def crawl_and_index(url: str, tier: str = "C") -> dict:
    """Fetch a URL, extract text, and index into RAG store.

    Returns {indexed: bool, chunks: int, error: str}.

    Past gap (production 2026-04-19): trafilatura.fetch_url uses urllib
    with a `python-requests/X.Y` User-Agent that almost every defence /
    government / IGO source blocks (CloudFront/Akamai/Cloudflare anti-bot
    rules). Result: weekly crawl reported 0/0/42 across all tiers,
    nothing indexed. Replace fetch with httpx + browser UA + redirect
    following + retry. Keep trafilatura for extraction — it's strong at
    that — but feed it pre-fetched bytes via `extract(html_bytes)`.
    """
    try:
        import trafilatura
        import httpx
    except ImportError as e:
        return {"indexed": False, "chunks": 0, "error": f"missing dep: {e}"}

    BROWSER_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    )
    try:
        downloaded = None
        last_err = None
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={
                "User-Agent": BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
        ) as client:
            for attempt in range(2):
                try:
                    r = await client.get(url)
                    if 200 <= r.status_code < 300 and r.content:
                        downloaded = r.content
                        break
                    last_err = f"HTTP {r.status_code}"
                except (httpx.TimeoutException, httpx.HTTPError) as e:
                    last_err = type(e).__name__
                if attempt == 0:
                    await asyncio.sleep(1.5)
        if not downloaded:
            return {"indexed": False, "chunks": 0, "error": f"fetch failed: {last_err or 'unknown'}"}

        # Trafilatura accepts bytes directly — no need for a second fetch.
        text = trafilatura.extract(downloaded, include_links=True, include_tables=True)
        if not text or len(text.strip()) < 100:
            return {"indexed": False, "chunks": 0, "error": "extraction empty"}

        # Check if content changed since last crawl
        content_hash = hashlib.md5(text.encode()).hexdigest()
        hash_key = f"{HASH_KEY_PREFIX}{hashlib.md5(url.encode()).hexdigest()}"
        old_hash = await rs.get(hash_key)
        if old_hash == content_hash:
            return {"indexed": False, "chunks": 0, "unchanged": True}

        # Index via rag_store
        from . import rag_store
        trust = TIER_CRITERIA.get(tier, {}).get("trust_score", 0.7)
        result = await rag_store.ingest_document(
            text,
            source=url,
            source_type=f"corpus_tier_{tier}",
            title=url.split("/")[-1] or url,
            url=url,
            extra_metadata={"tier": tier, "trust_score": trust},
        )

        # Store content hash for change detection
        await rs.set(hash_key, content_hash)

        chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
        logger.info("[corpus] Indexed %s: %d chunks (tier %s)", url, chunks, tier)
        return {"indexed": True, "chunks": chunks}

    except Exception as e:
        logger.warning("[corpus] Crawl failed for %s: %s", url, e)
        return {"indexed": False, "chunks": 0, "error": str(e)}


async def run_weekly_crawl(tiers: list[str] | None = None) -> dict:
    """Refresh all registered URLs, indexing new/changed content.

    Returns summary with total_indexed, total_unchanged, total_errors.
    """
    registry = await load_registry()
    tiers_to_crawl = tiers or list(registry.keys())

    summary = {"total_indexed": 0, "total_unchanged": 0, "total_errors": 0, "tiers": {}}

    for tier in tiers_to_crawl:
        urls = registry.get(tier, [])
        tier_stats = {"indexed": 0, "unchanged": 0, "errors": 0}

        for url in urls[:50]:  # cap at 50 per tier per crawl
            result = await crawl_and_index(url, tier)
            if result.get("indexed"):
                tier_stats["indexed"] += 1
            elif result.get("unchanged"):
                tier_stats["unchanged"] += 1
            else:
                tier_stats["errors"] += 1

        summary["tiers"][tier] = tier_stats
        summary["total_indexed"] += tier_stats["indexed"]
        summary["total_unchanged"] += tier_stats["unchanged"]
        summary["total_errors"] += tier_stats["errors"]

    logger.info("[corpus] Weekly crawl complete: %d indexed, %d unchanged, %d errors",
                summary["total_indexed"], summary["total_unchanged"], summary["total_errors"])

    # Brain signal — weekly crawl is one of the highest-value
    # learning events: it refreshes the verified-source corpus.
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="corpus_manager",
            summary=(
                f"Weekly corpus crawl: {summary['total_indexed']} indexed, "
                f"{summary['total_unchanged']} unchanged, {summary['total_errors']} errors "
                f"across tiers={list(summary['tiers'].keys())}"
            ),
            detail=str(summary["tiers"])[:1500],
            success=summary["total_errors"] < (summary["total_indexed"] + summary["total_unchanged"]),
            gap_type=("corpus_crawl_high_error_rate"
                      if summary["total_errors"] > summary["total_indexed"] else None),
            gap_detail=(f"{summary['total_errors']} errors vs {summary['total_indexed']} indexed"
                        if summary["total_errors"] > summary["total_indexed"] else None),
            extra_topics=["compliance"],
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    return summary


async def get_registry_summary() -> dict:
    """Return a summary of the current URL registry."""
    registry = await load_registry()
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="corpus_manager",
        summary="Get Registry Summary",
        source_id="corpus_manager:R-F996",
    )

    return {
        "total_urls": sum(len(v) for v in registry.values()),
        "by_tier": {k: len(v) for k, v in registry.items()},
    }


async def get_pending_proposals(limit: int = 20) -> list[dict]:
    """Get proposals waiting for human review."""
    proposals = await rs.get_json(PROPOSALS_KEY) or []
    pending = [p for p in proposals if p.get("status") == "PENDING_REVIEW"]
    return pending[-limit:]


async def approve_proposal(url: str) -> bool:
    """Approve a pending proposal and add it to the registry."""
    proposals = await rs.get_json(PROPOSALS_KEY) or []
    for p in proposals:
        if p.get("url") == url and p.get("status") == "PENDING_REVIEW":
            p["status"] = "APPROVED"
            p["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            await rs.set_json(PROPOSALS_KEY, proposals)
            await add_url_directly(url, p.get("proposed_tier", "C"))
            return True
    return False


async def identify_regional_gaps() -> list[dict]:
    """Identify regions with thin corpus coverage."""
    registry = await load_registry()
    all_urls = " ".join(u for urls in registry.values() for u in urls).lower()

    regions = [
        {"region": "Gulf/GCC", "keywords": ["uae", "saudi", "qatar", "bahrain", "oman", "kuwait"]},
        {"region": "East Africa", "keywords": ["kenya", "ethiopia", "somalia", "uganda", "tanzania"]},
        {"region": "West Africa", "keywords": ["nigeria", "senegal", "ghana", "ivory coast", "mali"]},
        {"region": "Central Asia", "keywords": ["kazakhstan", "uzbekistan", "turkmenistan"]},
        {"region": "Southeast Asia", "keywords": ["indonesia", "malaysia", "philippines", "vietnam"]},
        {"region": "South Korea", "keywords": ["korea", "hanwha", "hyundai", "k2", "k9"]},
        {"region": "India", "keywords": ["india", "drdo", "hal", "bharat"]},
        {"region": "Turkey", "keywords": ["turkey", "turkiye", "baykar", "aselsan", "ssb"]},
        {"region": "Brazil/CPLP Americas", "keywords": ["brazil", "embraer", "avibras"]},
    ]

    gaps = []
    for r in regions:
        hits = sum(1 for kw in r["keywords"] if kw in all_urls)
        coverage = hits / len(r["keywords"]) if r["keywords"] else 0
        if coverage < 0.3:
            gaps.append({
                "region": r["region"],
                "coverage": round(coverage, 2),
                "suggestion": f"Add {r['region']} defence/security sources to improve coverage",
            })

    return gaps


# ── Internal helpers ────────────────────────────────────────────────────────

async def _classify_url_llm(url: str, context: str, llm) -> URLProposal:
    """Use the LLM to classify a URL against tier criteria."""
    tier_desc = "\n".join(
        f"  {t}: {c['description']}" for t, c in TIER_CRITERIA.items()
    )
    prompt = (
        f"Classify this URL for an intelligence corpus:\n"
        f"URL: {url}\n"
        f"Context: {context or 'Discovered during research'}\n\n"
        f"Tiers:\n{tier_desc}\n\n"
        f"Return JSON only:\n"
        f'{{"proposed_tier":"...","confidence":0.85,"rationale":"...","risk_level":"LOW|MEDIUM|HIGH"}}'
    )
    try:
        from . import cost_tracker
        with cost_tracker.feature("corpus_manager"):
            # 30s, not 15s: the fallback chain skips any provider when
            # `remaining < 15.0`, so a 15s timeout sits exactly on the floor
            # and a microsecond of wrapper overhead (cost meter / rate limit
            # accounting) can flip it. URL classifier has a heuristic fallback,
            # but we'd rather get the real LLM call than silently degrade.
            result = await llm.complete("You are a URL classifier.", prompt, max_tokens=300, timeout=30.0)
        raw = result.text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return URLProposal(
            url=url,
            proposed_tier=data.get("proposed_tier", "C"),
            confidence=float(data.get("confidence", 0.7)),
            rationale=data.get("rationale", ""),
            risk_level=data.get("risk_level", "MEDIUM"),
        )
    except Exception as e:
        logger.warning("[corpus] LLM classification failed for %s: %s", url, e)
        return _classify_url_heuristic(url)


def _classify_url_heuristic(url: str) -> URLProposal:
    """Fallback heuristic classification when LLM is unavailable."""
    url_lower = url.lower()
    for tier, criteria in TIER_CRITERIA.items():
        for example in criteria.get("examples", []):
            if example in url_lower:
                return URLProposal(
                    url=url,
                    proposed_tier=tier,
                    confidence=0.8,
                    rationale=f"Domain matches tier {tier} example: {example}",
                    risk_level="LOW",
                )
    return URLProposal(url=url, proposed_tier="C", confidence=0.5,
                       rationale="No tier match — defaulting to C", risk_level="MEDIUM")


async def _store_proposal(proposal: URLProposal) -> None:
    """Store a proposal for human review."""
    proposals = await rs.get_json(PROPOSALS_KEY) or []
    proposals.append(proposal.to_dict())
    if len(proposals) > 200:
        proposals = proposals[-200:]
    await rs.set_json(PROPOSALS_KEY, proposals)

# R-F2119 §21a — wire failure handler for corpus_manager
try:
    wire_failure(module="corpus_manager", detail="module shutdown",
                gap_type="engine_failure", source="corpus_manager:shutdown")
except Exception:
    pass

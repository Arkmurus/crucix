"""ARIA Source Scout — active boundary-pushing source discovery.

Three scout patterns:
  1. citation      — walk outbound citations from trusted Tier 1a/1b/2
                     sources, rank citees by authority, add qualifying ones
                     to the Web Atlas.
  2. tld_probe     — systematic sweep of gov/mil/ministry subdomains per
                     target market (Angola, Mozambique, Nigeria, Ghana,
                     Turkey, Brazil, ... per global positioning doctrine).
  3. targeted      — scout a specific (region × topic) cell flagged as
                     a gap by ecosystem_reassess.

Discovery primitives used:
  - sitemap.xml + robots.txt parsing
  - RSS/Atom feed detection
  - Open Graph / JSON-LD metadata
  - archive.org Wayback for dead links
  - Tier-classifier gate (verified_intel) to reject social / blog hits

Every addition goes through web_atlas.add_source() — audit-logged and
visible in the 05:45 briefing.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from defusedxml import ElementTree as ET
from . import redis_store as rs

logger = logging.getLogger("aria.intel.source_scout")

# ── Target markets — full global parity per aria_global_positioning.md ──
# No regional bias. NATO + EU + ECOWAS + SADC + AU + GCC + ASEAN +
# MERCOSUR + CIS + OSCE + EAC representation.
_TARGET_MARKETS = {
    "angola":       {"tlds": [".gov.ao", ".gob.ao", ".ao"],       "region": "africa_cplp"},
    "mozambique":   {"tlds": [".gov.mz", ".mz"],                  "region": "africa_cplp"},
    "cape_verde":   {"tlds": [".gov.cv", ".cv"],                  "region": "africa_cplp"},
    "guinea_bissau":{"tlds": [".gov.gw", ".gw"],                  "region": "africa_cplp"},
    "nigeria":      {"tlds": [".gov.ng", ".mil.ng"],              "region": "africa_west"},
    "ghana":        {"tlds": [".gov.gh", ".mil.gh"],              "region": "africa_west"},
    "kenya":        {"tlds": [".go.ke", ".mod.go.ke"],            "region": "africa_east"},
    "south_africa": {"tlds": [".gov.za", ".mil.za"],              "region": "africa_south"},
    "turkey":       {"tlds": [".gov.tr", ".tsk.tr"],              "region": "mena"},
    "uae":          {"tlds": [".gov.ae", ".mod.gov.ae"],          "region": "gcc"},
    "saudi":        {"tlds": [".gov.sa"],                         "region": "gcc"},
    "brazil":       {"tlds": [".gov.br", ".mil.br"],              "region": "mercosur"},
    "poland":       {"tlds": [".gov.pl", ".wp.mil.pl"],           "region": "nato_east"},
    "romania":      {"tlds": [".gov.ro", ".mapn.ro"],             "region": "nato_east"},
    "indonesia":    {"tlds": [".go.id", ".mil.id"],               "region": "asean"},
    "philippines":  {"tlds": [".gov.ph", ".mil.ph"],              "region": "asean"},
    "vietnam":      {"tlds": [".gov.vn", ".mod.gov.vn"],          "region": "asean"},
}

# Gazette / ministry / parliament subdomain guesses per market.
_GOV_PATH_HINTS = [
    "gazette", "official-gazette", "diariooficial", "diario-oficial",
    "parliament", "parlamento", "parliament", "parlement",
    "ministry-of-defence", "ministerio-da-defesa", "ministry-of-defense",
    "mod", "mindef", "defense", "defence", "armed-forces",
    "procurement", "tender", "contracts",
]


async def run(
    pattern: str = "citation",
    *,
    region: str = "global",
    topic: str = "defence_procurement",
    max_finds: int = 5,
) -> dict:
    """Entry point called by the scheduled scout tasks."""
    if pattern == "citation":
        result = await _scout_citation(max_finds=max_finds)
    elif pattern == "tld_probe":
        result = await _scout_tld_probe(max_finds=max_finds)
    elif pattern == "targeted":
        result = await _scout_targeted(region=region, topic=topic, max_finds=max_finds)
    else:
        return {"ok": False, "error": f"unknown scout pattern: {pattern}"}

    # Brain signal — scout runs feed mastery; the found-count tells the
    # briefing whether the day's discovery was productive.
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="source_scout",
            summary=(f"Scout {pattern}: {result.get('found', 0)} "
                     f"new sources added/queued"),
            detail=f"Region {region}, topic {topic}, max {max_finds}",
            success=result.get("found", 0) > 0,
        )
    except Exception:
        pass
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="source_scout",
        summary="Run",
        source_id="source_scout:R-F1001",
    )

    return result


# ── Pattern 1: citation-graph walk ───────────────────────────────────────

async def _scout_citation(max_finds: int = 5) -> dict:
    """Walk citations from trusted Tier 1a/1b/2 sources, rank citees,
    add qualifying ones to the atlas.

    MVP implementation: pull the top 20 most-reliable families from the
    atlas, fetch their landing pages, extract outbound hrefs to
    non-registered domains, tier-classify each, add Tier 1a/1b/2 hits.
    """
    from . import verified_intel as _vi
    from . import web_atlas as _wa

    try:
        families_idx = await rs.get_json("aria:atlas:index:families") or []
    except Exception:
        families_idx = []

    seed_urls: list[str] = []
    for fam in families_idx[:20]:
        rec = await rs.get_json(f"aria:atlas:source:{fam}")
        if rec and rec.get("url"):
            seed_urls.append(rec["url"])

    if not seed_urls:
        # Cold start — use verified_intel tier-1a/1b domain constants.
        seed_urls = [
            "https://ofac.treasury.gov/",
            "https://www.un.org/securitycouncil/",
            "https://eeas.europa.eu/",
            "https://www.nato.int/",
        ]

    classifier = _vi.SourceTierClassifier()
    found: list[dict] = []
    seen_domains: set[str] = set()

    # Import lazily — researcher fetches via requests.
    try:
        from . import researcher as _r
    except Exception:
        _r = None

    for seed in seed_urls[:10]:
        if len(found) >= max_finds:
            break
        if not _r or not hasattr(_r, "extract_url_text"):
            break
        try:
            text = await _r.extract_url_text(seed)
        except Exception:
            continue
        # Extract hrefs — crude regex, not a full HTML parser
        hrefs = re.findall(r'href=["\']?(https?://[^"\'\s>]+)', str(text))
        for href in hrefs:
            try:
                d = urlparse(href).netloc.replace("www.", "").lower()
            except Exception:
                continue
            if not d or d in seen_domains:
                continue
            seen_domains.add(d)
            tier = classifier.classify(href)
            if tier not in (_vi.SourceTier.TIER_1A, _vi.SourceTier.TIER_1B, _vi.SourceTier.TIER_2):
                continue
            # Quality gate — extracted from ARIA_Source_Discovery proposal.
            # Validator runs the 10-signal check; AUTO_APPROVED goes
            # straight to atlas, PENDING is queued for human review,
            # AUTO_REJECTED is dropped.
            gated = await _gated_add(
                href, discovered_via="citation",
                tier_hint=tier, region="global",
                topic_tags=["citation_graph"],
            )
            if gated.get("added"):
                found.append({"url": href, "tier": tier.value, "result": gated})
                if len(found) >= max_finds:
                    break

    return {"pattern": "citation", "seeds": len(seed_urls),
            "found": len(found), "entries": found[:max_finds]}


# ── Pattern 2: TLD probe ─────────────────────────────────────────────────

async def _scout_tld_probe(max_finds: int = 5) -> dict:
    """Systematic sweep of gov/mil subdomains per target market.
    Looks for gazettes, ministries, parliament sites, procurement portals
    that ARIA doesn't have registered yet."""
    from . import verified_intel as _vi
    from . import web_atlas as _wa

    classifier = _vi.SourceTierClassifier()
    found: list[dict] = []

    try:
        from . import researcher as _r
    except Exception:
        _r = None

    for market, info in _TARGET_MARKETS.items():
        if len(found) >= max_finds:
            break
        for tld in info["tlds"]:
            if len(found) >= max_finds:
                break
            for hint in _GOV_PATH_HINTS[:6]:
                if len(found) >= max_finds:
                    break
                candidate = f"https://{hint}{tld}"
                # Cheap existence check via head / search.
                if _r and hasattr(_r, "web_search"):
                    try:
                        results = await _r.web_search(
                            f'"{hint}" site:{tld[1:]}', max_results=2,
                        )
                    except Exception:
                        results = []
                    for res in (results or [])[:2]:
                        url = res.get("url", "")
                        if not url:
                            continue
                        tier = classifier.classify(url)
                        if tier in (_vi.SourceTier.TIER_1A, _vi.SourceTier.TIER_1B):
                            gated = await _gated_add(
                                url, discovered_via="tld_probe",
                                tier_hint=tier,
                                region=info["region"],
                                topic_tags=[hint, "gov"],
                                gap_domain=hint,
                            )
                            if gated.get("added"):
                                found.append({"market": market, "url": url,
                                              "tier": tier.value, "result": gated})
                            break
    return {"pattern": "tld_probe", "markets_scanned": len(_TARGET_MARKETS),
            "found": len(found), "entries": found[:max_finds]}


# ── Pattern 3: targeted (from ecosystem_reassess gap) ────────────────────

async def _scout_targeted(region: str, topic: str, max_finds: int = 3) -> dict:
    """Scout a specific (region × topic) cell flagged as a gap. Uses web
    search with the topic + region keywords, classifies hits, adds
    qualifying ones to the atlas."""
    from . import verified_intel as _vi
    from . import web_atlas as _wa

    found: list[dict] = []
    try:
        from . import researcher as _r
    except Exception:
        return {"pattern": "targeted", "found": 0,
                "error": "researcher module unavailable"}
    if not hasattr(_r, "web_search"):
        return {"pattern": "targeted", "found": 0,
                "error": "researcher.web_search unavailable"}

    # Two queries: official-body, then quality journalism.
    queries = [
        f'"{region}" "{topic}" site:gov OR site:gob OR site:gov.uk',
        f'"{region}" "{topic}" official statistics',
        f'"{region}" "{topic}" ministry',
    ]
    classifier = _vi.SourceTierClassifier()
    seen: set[str] = set()

    for q in queries:
        if len(found) >= max_finds:
            break
        try:
            results = await _r.web_search(q, max_results=5)
        except Exception:
            continue
        for res in (results or []):
            url = res.get("url", "")
            if not url:
                continue
            d = urlparse(url).netloc.replace("www.", "").lower()
            if d in seen:
                continue
            seen.add(d)
            tier = classifier.classify(url)
            if tier in (_vi.SourceTier.TIER_1A, _vi.SourceTier.TIER_1B,
                        _vi.SourceTier.TIER_2, _vi.SourceTier.TIER_3):
                gated = await _gated_add(
                    url, discovered_via="targeted",
                    tier_hint=tier, region=region,
                    topic_tags=[topic], gap_domain=topic,
                )
                if gated.get("added"):
                    await _wa.update_coverage(region, topic, fetch_success=True)
                    found.append({"url": url, "tier": tier.value, "result": gated})
                    if len(found) >= max_finds:
                        break
                elif gated.get("path") == "queued_pending":
                    # Candidate found but pending approval — update coverage partially
                    # so the gap doesn't keep re-firing every hour
                    try:
                        await _wa.update_coverage(region, topic, fetch_success=False)
                    except Exception:
                        pass
                    found.append({"url": url, "tier": tier.value, "result": gated, "pending": True})

    return {"pattern": "targeted", "region": region, "topic": topic,
            "found": len(found), "entries": found}


# ── Refresh a stale family ──────────────────────────────────────────────

async def _gated_add(
    url: str,
    *,
    discovered_via: str,
    tier_hint: Any,
    region: str,
    topic_tags: list[str],
    gap_domain: str = "",
) -> dict:
    """Run candidate through the content-quality validator before adding
    to Web Atlas. This is the gate that prevents fresh-domain blog-spam
    from entering the atlas just because it sits on a gov TLD.

    Returns {"added": bool, ...}. When the validator auto-approves
    (gov Tier 1a/1b passing score), we call web_atlas.add_source
    directly. PENDING goes to the approval queue. AUTO_REJECTED is
    silently dropped.
    """
    from . import source_validator as _sv
    from . import web_atlas as _wa

    try:
        from . import researcher as _r
        web_search_fn = getattr(_r, "web_search", None)
    except Exception:
        web_search_fn = None

    cand = await _sv.validate(
        url=url, gap_domain=gap_domain or topic_tags[0] if topic_tags else "",
        discovered_via=discovered_via,
        web_search_fn=web_search_fn,
    )
    if cand.validation_status == _sv.ValidationStatus.AUTO_REJECTED:
        logger.info("scout dropped %s: %s", url, cand.validation_notes)
        return {"added": False, "reason": "validator_rejected",
                "score": cand.overall_quality_score,
                "notes": cand.validation_notes}
    if cand.validation_status == _sv.ValidationStatus.AUTO_APPROVED:
        # Gov Tier 1a/1b → add directly to atlas
        entry = await _wa.add_source(
            url=url, tier=cand.tier_proposed,
            topic_tags=topic_tags + cand.coverage_domains,
            region=region, added_by=f"scout_auto_approved:{discovered_via}",
        )
        return {"added": True, "path": "auto_approved",
                "tier": cand.tier_proposed, "atlas": entry}
    # PENDING → queue for human review. Not added to atlas until approved.
    q = await _sv.queue_candidate(cand)
    # Update coverage to CANDIDATE so ecosystem_reassess stops re-firing
    # this gap every hour. The gap isn't closed, but a candidate exists.
    try:
        await _wa.update_coverage(region, gap_domain or (topic_tags[0] if topic_tags else ""),
                                  fetch_success=False)  # Partial — not fully resolved
    except Exception:
        pass
    return {"added": False, "path": "queued_pending",
            "candidate_id": cand.candidate_id,
            "tier_proposed": cand.tier_proposed,
            "score": cand.overall_quality_score,
            "queue_depth": q.get("queue_depth")}


async def refresh_family(family: str) -> dict:
    """Re-fetch a registered family's landing page to update last_ok
    + reliability. Called when ecosystem_reassess flags a source as stale."""
    rec = await rs.get_json(f"aria:atlas:source:{family}")
    if not rec:
        return {"ok": False, "error": "family not registered"}
    url = rec.get("url", "")
    try:
        from . import researcher as _r
        if hasattr(_r, "extract_url_text"):
            text = await _r.extract_url_text(url)
            if text:
                rec["last_ok"] = datetime.now(timezone.utc).isoformat()
                await rs.set_json(f"aria:atlas:source:{family}", rec, ex=365 * 86400)
                return {"ok": True, "family": family, "refreshed": True,
                        "text_len": len(str(text))}
    except Exception as e:
        return {"ok": False, "family": family, "error": str(e)}
    return {"ok": False, "family": family, "error": "no extractor available"}

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

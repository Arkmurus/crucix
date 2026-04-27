"""Defence source seed — curated Tier-1/Tier-1b/Tier-2 sources for web_atlas.

Why this exists
───────────────
web_atlas was shipping empty on first boot — it only knew about sources
we had fetched from at least once. That meant ARIA's early research on
any new topic had no source-tier preference and treated e.g. a random
LinkedIn post the same as a SIPRI publication or UK ECJU guidance.

This file ships with a curated seed list covering:
  - Tier 1a (regulator / primary source / official gazette)
  - Tier 1b (defence industry authority — SIPRI, Janes equivalents)
  - Tier 2  (specialist defence press — high credibility, not primary)

On startup, `seed_web_atlas()` adds these to the atlas if they're not
already there. Subsequent fetches update reliability EMAs on top. The
seed is never overwritten — it's additive.

Organised by domain so an operator can see at a glance what ARIA
trusts a priori.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aria.defence_source_seed")


# ── Source catalogue ──────────────────────────────────────────────────────
# Format: (url, family, tier, topic_tags)
# - url must be a real reachable homepage (uptime monitor will ping it)
# - family groups subdomains under a canonical name (web_atlas._source_family)
# - tier is "tier_1a" (regulator/primary), "tier_1b" (industry authority),
#   "tier_2" (specialist press)
# - topic_tags help rank_sources_for_topic produce good defaults

_DEFENCE_SOURCES: list[tuple[str, str, str, list[str]]] = [
    # ══════════════════════════════════════════════════════════════════════
    # TIER 1a — PRIMARY REGULATORS / OFFICIAL GAZETTES
    # These are the canonical sources. If they say it, it's the fact.
    # ══════════════════════════════════════════════════════════════════════

    # Sanctions + export control — global
    ("https://ofac.treasury.gov/", "ofac_treasury",
     "tier_1a", ["compliance", "sanctions", "legal"]),
    ("https://www.gov.uk/government/organisations/office-of-financial-sanctions-implementation",
     "uk_ofsi", "tier_1a", ["compliance", "sanctions", "legal"]),
    ("https://www.un.org/securitycouncil/sanctions/information",
     "un_security_council", "tier_1a", ["compliance", "sanctions", "legal", "geopolitics"]),
    ("https://eur-lex.europa.eu/", "eu_lex",
     "tier_1a", ["compliance", "legal", "eu_regulation"]),
    ("https://www.state.gov/bureaus-offices/under-secretary-for-arms-control-and-international-security-affairs/",
     "us_state_dept", "tier_1a", ["compliance", "export_control"]),

    # UK-specific regulators
    ("https://www.gov.uk/government/organisations/export-control-joint-unit",
     "uk_ecju", "tier_1a", ["compliance", "export_control", "uk"]),
    ("https://find-and-update.company-information.service.gov.uk/",
     "uk_companies_house", "tier_1a", ["registry", "uk"]),
    ("https://www.gov.uk/guidance/uk-strategic-export-control-lists-the-consolidated-list-of-strategic-military-and-dual-use-items-that-require-an-export-licence",
     "uk_strategic_control_lists", "tier_1a", ["compliance", "export_control", "uk"]),

    # MDBs — procurement debarment
    ("https://projects.worldbank.org/en/projects-operations/procurement/debarred-firms",
     "world_bank_debarred", "tier_1a", ["compliance", "procurement"]),
    ("https://www.afdb.org/en/projects-and-operations/procurement/debarment-and-sanctions-procedures",
     "afdb_sanctions", "tier_1a", ["compliance", "procurement", "africa"]),

    # NATO + standards
    ("https://www.nato.int/cps/en/natohq/topics_77646.htm",
     "nato_stanags", "tier_1a", ["technical", "nato_standards"]),
    ("https://nso.nato.int/", "nato_nso",
     "tier_1a", ["technical", "nato_standards"]),

    # Procurement portals — primary
    ("https://ted.europa.eu/", "ted_europa",
     "tier_1a", ["procurement", "eu"]),
    ("https://sam.gov/", "us_sam_gov",
     "tier_1a", ["procurement", "us"]),
    ("https://www.contractsfinder.service.gov.uk/",
     "uk_contracts_finder", "tier_1a", ["procurement", "uk"]),
    ("https://www.ungm.org/", "un_global_marketplace",
     "tier_1a", ["procurement", "un"]),

    # Sanctions aggregator (authoritative aggregation of Tier 1a primary lists)
    ("https://www.opensanctions.org/", "opensanctions",
     "tier_1a", ["compliance", "sanctions", "pep"]),

    # SEC + equivalents
    ("https://www.sec.gov/", "us_sec",
     "tier_1a", ["finance", "registry", "us"]),

    # ══════════════════════════════════════════════════════════════════════
    # TIER 1b — DEFENCE INDUSTRY AUTHORITIES
    # Specialist research houses, think tanks with primary data.
    # ══════════════════════════════════════════════════════════════════════

    # SIPRI — arms transfers, military expenditure
    ("https://www.sipri.org/", "sipri",
     "tier_1b", ["market_intel", "geopolitics", "procurement"]),
    ("https://armstrade.sipri.org/", "sipri_armstrade",
     "tier_1b", ["market_intel", "geopolitics"]),

    # Janes — premium defence intelligence (paid API; seed for when wired)
    ("https://www.janes.com/", "janes",
     "tier_1b", ["market_intel", "technical", "geopolitics"]),

    # IISS
    ("https://www.iiss.org/", "iiss",
     "tier_1b", ["geopolitics", "market_intel"]),

    # RUSI
    ("https://rusi.org/", "rusi",
     "tier_1b", ["geopolitics", "compliance", "market_intel"]),

    # ISW — conflict tracking
    ("https://www.understandingwar.org/", "isw",
     "tier_1b", ["geopolitics", "conflict"]),

    # UCDP — Uppsala Conflict Data Program
    ("https://ucdp.uu.se/", "ucdp",
     "tier_1b", ["geopolitics", "conflict"]),

    # ACLED
    ("https://acleddata.com/", "acled",
     "tier_1b", ["geopolitics", "conflict"]),

    # CSIS
    ("https://www.csis.org/", "csis",
     "tier_1b", ["geopolitics", "market_intel"]),

    # Stimson
    ("https://www.stimson.org/", "stimson",
     "tier_1b", ["geopolitics", "compliance"]),

    # ══════════════════════════════════════════════════════════════════════
    # TIER 2 — SPECIALIST DEFENCE PRESS
    # High credibility but not primary. Good for corroboration.
    # ══════════════════════════════════════════════════════════════════════

    ("https://www.defensenews.com/", "defense_news",
     "tier_2", ["market_intel", "geopolitics"]),
    ("https://breakingdefense.com/", "breaking_defense",
     "tier_2", ["market_intel", "technical"]),
    ("https://www.janes.com/defence-news", "janes_news",
     "tier_2", ["market_intel", "technical"]),
    ("https://www.defenceweb.co.za/", "defenceweb",
     "tier_2", ["market_intel", "africa"]),
    ("https://c4defence.com/", "c4defence",
     "tier_2", ["market_intel", "technical", "turkey"]),
    ("https://www.defenseone.com/", "defense_one",
     "tier_2", ["market_intel", "geopolitics"]),
    ("https://www.reuters.com/business/aerospace-defense/",
     "reuters_defense", "tier_2", ["market_intel", "finance"]),
    ("https://www.ft.com/defence", "ft_defence",
     "tier_2", ["market_intel", "finance"]),
    ("https://thediplomat.com/", "the_diplomat",
     "tier_2", ["geopolitics", "asia"]),

    # Region-specific
    ("https://jamestown.org/", "jamestown",
     "tier_2", ["geopolitics", "russia_china"]),
    ("https://www.meforum.org/", "me_forum",
     "tier_2", ["geopolitics", "middle_east"]),
    ("https://www.dailysabah.com/", "daily_sabah",
     "tier_2", ["geopolitics", "turkey"]),
    ("https://jornaldeangola.ao/", "jornal_angola",
     "tier_2", ["geopolitics", "angola", "lusophone"]),
    ("https://www.dailytrust.com/", "daily_trust_nigeria",
     "tier_2", ["geopolitics", "nigeria"]),

    # Academic / open research
    ("https://arxiv.org/", "arxiv",
     "tier_1b", ["academic", "technical"]),
    ("https://api.openalex.org/", "openalex",
     "tier_1b", ["academic"]),
    ("https://api.crossref.org/", "crossref",
     "tier_1a", ["academic", "registry"]),
]


async def seed_web_atlas(skip_if_populated: bool = True) -> dict:
    """Bootstrap the defence source catalogue into web_atlas.

    Args:
        skip_if_populated: If True (default), don't run if web_atlas
            already has sources — avoids re-adding on every restart.
            Pass False to force a re-seed (useful after curating the
            list above).

    Returns a summary dict: {added, skipped, errors, final_count}.
    """
    try:
        from . import web_atlas
    except Exception as e:
        return {"ok": False, "error": f"web_atlas import failed: {e}"}

    # Check current state — skip if already populated. web_atlas.stats()
    # exposes the family count under `source_families`; the previous
    # `total_sources`/`total` keys never existed, so the guard always
    # returned 0 and re-seeded on every boot. That fired ~30 brain_hook +
    # audit_log writes per startup with no new information (visible in
    # fly logs as a 30-line `source_atlas_update` storm at 09:21:00).
    if skip_if_populated:
        try:
            stats = await web_atlas.stats()
            existing_count = int(
                stats.get("source_families", 0)
                or stats.get("total_sources", 0)
                or stats.get("total", 0)
                or 0
            )
            if existing_count >= len(_DEFENCE_SOURCES) // 2:
                return {
                    "ok": True,
                    "action": "skipped",
                    "reason": f"web_atlas already has {existing_count} sources",
                    "final_count": existing_count,
                }
        except Exception:
            pass  # proceed with seeding anyway

    added = 0
    skipped = 0
    errors = 0
    for url, family, tier, tags in _DEFENCE_SOURCES:
        try:
            # web_atlas.add_source signature varies across versions —
            # try the most common then fall back.
            try:
                await web_atlas.add_source(
                    url=url,
                    family=family,
                    tier=tier,
                    topic_tags=tags,
                )
            except TypeError:
                # Older signature
                await web_atlas.add_source(url, family, tier)
            added += 1
        except Exception as e:
            msg = str(e).lower()
            if "already" in msg or "exists" in msg or "duplicate" in msg:
                skipped += 1
            else:
                errors += 1
                logger.debug("[defence_seed] failed on %s: %s", family, e)

    # Feed brain once on successful seed
    try:
        from . import brain_hook as _bh
        await _bh.absorb(
            module="web_atlas",
            summary=(
                f"Defence source seed: added={added} skipped={skipped} "
                f"errors={errors} of {len(_DEFENCE_SOURCES)} candidates"
            ),
            success=errors == 0,
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    logger.info(
        "[defence_seed] complete: added=%d skipped=%d errors=%d",
        added, skipped, errors,
    )
    return {
        "ok": errors == 0,
        "added": added,
        "skipped": skipped,
        "errors": errors,
        "candidates": len(_DEFENCE_SOURCES),
    }


def catalogue_summary() -> dict:
    """Return a read-only summary of the curated catalogue — used for
    dashboard display + the capability_card."""
    by_tier: dict[str, int] = {}
    by_topic: dict[str, int] = {}
    for _url, _family, tier, tags in _DEFENCE_SOURCES:
        by_tier[tier] = by_tier.get(tier, 0) + 1
        for t in tags:
            by_topic[t] = by_topic.get(t, 0) + 1
    return {
        "total_curated": len(_DEFENCE_SOURCES),
        "by_tier": by_tier,
        "top_topics": dict(
            sorted(by_topic.items(), key=lambda kv: -kv[1])[:10]
        ),
    }

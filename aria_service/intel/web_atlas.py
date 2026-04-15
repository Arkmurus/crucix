"""ARIA Web Atlas — living map of the internet as an intelligence substrate.

Problem this solves
───────────────────
Before this module, ARIA's source catalogue was a static YAML file
(`corpus_registry.yaml`) maintained by humans. Her verification pipeline
(Clause 17) knew how to classify URLs into tiers, but had no memory of
which specific source was reliable for which specific topic. Reuters is
excellent on Nigeria but thin on Lusophone Africa; Jornal de Angola is
authoritative on FAA but weak on multilaterals. Tier was assigned, not
earned.

What this module does
─────────────────────
The Web Atlas is a persistent, topic × source reliability map. Every
fact ARIA ingests updates two numbers per (source, topic) pair:
  - confirmation_count: how many facts from this source held up
  - contradiction_count: how many were later contradicted or corrected
The EMA ratio gives a per-topic reliability score in [0, 1]. Over weeks
and months, ARIA learns which sources to trust on which beats.

Coverage map
────────────
The atlas also tracks COVERAGE: for each (region, topic) cell it records
the number of registered sources + last-successful-fetch timestamp. A
cell with 0 sources or >30-day staleness is a GAP — the ECOSYSTEM-REASSESS
task surfaces gaps for the source-scout pipeline to close.

Storage
───────
Redis keys:
  aria:atlas:reliability:<source_family>:<topic>   → {confirmed, contradicted, score, last_update}
  aria:atlas:coverage:<region>:<topic>             → {source_count, last_fetch, gap_level}
  aria:atlas:source:<family>                       → {tier, added_at, last_ok, total_facts}

Plus a daily snapshot YAML mirror at aria_service/intel/web_atlas.yaml
(self-improve whitelisted) so humans can read/edit if needed.

Public API
──────────
  await record_ingest(source_url, topic, success=True) — update reliability
  await record_correction(source_url, topic) — penalty hit
  await get_reliability(source_url, topic) — lookup
  await coverage_map(regions=None) — per-region/topic gap summary
  await rank_sources_for_topic(topic, limit=10) — best sources first
  await add_source(url, tier, topic_tags, region) — atlas growth
  await snapshot_to_yaml() — write mirror for self-improve path
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from . import redis_store as rs

logger = logging.getLogger("aria.intel.web_atlas")

# ── Redis key templates ──────────────────────────────────────────────────

_K_RELIABILITY = "aria:atlas:reliability:{family}:{topic}"
_K_COVERAGE = "aria:atlas:coverage:{region}:{topic}"
_K_SOURCE = "aria:atlas:source:{family}"
_K_INDEX_FAMILIES = "aria:atlas:index:families"
_K_INDEX_TOPICS = "aria:atlas:index:topics"
_K_GAPS = "aria:atlas:gaps"  # sorted set, score=urgency

_YAML_MIRROR = Path(__file__).parent / "web_atlas.yaml"

# EMA alpha for reliability updates — 0.2 gives the last ~5 observations
# the dominant weight while still smoothing.
_EMA_ALPHA = 0.2


def _source_family(url: str) -> str:
    """Reduce a URL to its source family key — used for reliability dedup."""
    try:
        domain = urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return "unknown"
    # Map known families (wire services) to their canonical label.
    FAMILIES = {
        "reuters": ("reuters.com", "reuters.tv", "reutersconnect.com"),
        "ap":      ("apnews.com", "ap.org"),
        "afp":     ("afp.com",),
        "bbc":     ("bbc.co.uk", "bbc.com", "bbci.co.uk"),
        "bloomberg": ("bloomberg.com", "bloomberglaw.com"),
    }
    for fam, domains in FAMILIES.items():
        if any(domain == d or domain.endswith("." + d) for d in domains):
            return fam
    return domain or "unknown"


def _normalise_topic(topic: str) -> str:
    return (topic or "").strip().lower().replace(" ", "_")[:64] or "unknown"


# ── Reliability updates ──────────────────────────────────────────────────

async def record_ingest(
    source_url: str,
    topic: str,
    *,
    success: bool = True,
) -> dict:
    """Update a source's reliability for a given topic after an ingest.

    `success=True` means the fact held up (no contradiction, no user
    correction). `success=False` is a fact that got contradicted or
    corrected. Reliability moves toward 1.0 on success, toward 0.0 on
    failure, EMA-smoothed.
    """
    family = _source_family(source_url)
    topic_k = _normalise_topic(topic)
    key = _K_RELIABILITY.format(family=family, topic=topic_k)
    now = datetime.now(timezone.utc).isoformat()

    existing = await rs.get_json(key) or {
        "family": family, "topic": topic_k,
        "confirmed": 0, "contradicted": 0,
        "score": 0.5, "last_update": now,
    }

    if success:
        existing["confirmed"] = existing.get("confirmed", 0) + 1
    else:
        existing["contradicted"] = existing.get("contradicted", 0) + 1

    observation = 1.0 if success else 0.0
    prior = float(existing.get("score", 0.5))
    existing["score"] = round(prior + _EMA_ALPHA * (observation - prior), 4)
    existing["last_update"] = now
    await rs.set_json(key, existing, ex=365 * 86400)

    # Family index for fast rollups.
    families = await rs.get_json(_K_INDEX_FAMILIES) or []
    if family not in families:
        families.append(family)
        await rs.set_json(_K_INDEX_FAMILIES, families, ex=365 * 86400)
    topics = await rs.get_json(_K_INDEX_TOPICS) or []
    if topic_k not in topics:
        topics.append(topic_k)
        await rs.set_json(_K_INDEX_TOPICS, topics, ex=365 * 86400)

    return existing


async def record_correction(source_url: str, topic: str) -> dict:
    """Explicit penalty — used when the mistake_ledger records a
    user correction traceable to this source+topic."""
    return await record_ingest(source_url, topic, success=False)


async def get_reliability(source_url: str, topic: str) -> dict:
    family = _source_family(source_url)
    topic_k = _normalise_topic(topic)
    key = _K_RELIABILITY.format(family=family, topic=topic_k)
    data = await rs.get_json(key)
    if data:
        return data
    return {
        "family": family, "topic": topic_k,
        "confirmed": 0, "contradicted": 0,
        "score": 0.5,  # unknown prior
        "last_update": None,
    }


async def rank_sources_for_topic(topic: str, limit: int = 10) -> list[dict]:
    """Return the top N sources for a topic, sorted by reliability."""
    topic_k = _normalise_topic(topic)
    families = await rs.get_json(_K_INDEX_FAMILIES) or []
    results: list[dict] = []
    for fam in families:
        key = _K_RELIABILITY.format(family=fam, topic=topic_k)
        data = await rs.get_json(key)
        if data and data.get("confirmed", 0) + data.get("contradicted", 0) > 0:
            results.append(data)
    results.sort(key=lambda d: (d.get("score", 0), d.get("confirmed", 0)), reverse=True)
    return results[:limit]


# ── Coverage map ─────────────────────────────────────────────────────────

async def update_coverage(
    region: str,
    topic: str,
    *,
    fetch_success: bool = True,
) -> dict:
    """Track per-region × topic coverage. Gap level rises with staleness
    + low source count; ECOSYSTEM-REASSESS reads this to seed the scout
    pipeline."""
    region_k = _normalise_topic(region)
    topic_k = _normalise_topic(topic)
    key = _K_COVERAGE.format(region=region_k, topic=topic_k)
    now = datetime.now(timezone.utc).isoformat()
    existing = await rs.get_json(key) or {
        "region": region_k, "topic": topic_k,
        "source_count": 0, "last_fetch": None,
        "gap_level": "unknown",
    }
    if fetch_success:
        existing["source_count"] = existing.get("source_count", 0) + 1
        existing["last_fetch"] = now

    # Gap levels — crude but actionable:
    #   CRITICAL: 0 sources
    #   HIGH:     ≤2 sources OR last_fetch > 90d
    #   MEDIUM:   ≤5 sources OR last_fetch > 30d
    #   OK:       else
    src_count = existing.get("source_count", 0)
    if src_count == 0:
        existing["gap_level"] = "CRITICAL"
    elif src_count <= 2:
        existing["gap_level"] = "HIGH"
    elif src_count <= 5:
        existing["gap_level"] = "MEDIUM"
    else:
        existing["gap_level"] = "OK"

    await rs.set_json(key, existing, ex=180 * 86400)
    return existing


async def coverage_map(regions: Optional[list[str]] = None) -> list[dict]:
    """Return coverage entries, optionally filtered to specific regions."""
    pattern = _K_COVERAGE.format(region="*", topic="*")
    keys = await rs.scan_keys(pattern, count=1000)
    out: list[dict] = []
    for k in keys:
        data = await rs.get_json(k)
        if not data:
            continue
        if regions and data.get("region") not in regions:
            continue
        out.append(data)
    out.sort(key=lambda d: (d.get("gap_level") == "CRITICAL",
                             d.get("gap_level") == "HIGH",
                             -d.get("source_count", 0)),
              reverse=True)
    return out


async def surface_gaps(min_level: str = "HIGH", limit: int = 20) -> list[dict]:
    """Return (region, topic) cells at the given gap level or worse —
    the feed for ECOSYSTEM-REASSESS and the source-scout pipeline."""
    order = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "OK": 0}
    threshold = order.get(min_level, 2)
    cov = await coverage_map()
    gaps = [c for c in cov if order.get(c.get("gap_level", "OK"), 0) >= threshold]
    return gaps[:limit]


# ── Source lifecycle (atlas growth) ──────────────────────────────────────

async def add_source(
    url: str,
    tier: str,
    topic_tags: list[str],
    region: str = "global",
    added_by: str = "aria_autonomous",
) -> dict:
    """Register a new source in the atlas. Called by the source-scout
    pipeline when a qualifying citee/TLD probe hit / RSS feed is found.
    Audit-logged."""
    family = _source_family(url)
    key = _K_SOURCE.format(family=family)
    now = datetime.now(timezone.utc).isoformat()
    existing = await rs.get_json(key)
    action = "updated" if existing else "added"
    record = existing or {
        "family": family, "url": url, "added_at": now,
        "total_facts": 0, "last_ok": None,
    }
    record["tier"] = tier
    record["topics"] = sorted(set(
        (record.get("topics") or []) +
        [_normalise_topic(t) for t in topic_tags]
    ))
    record["region"] = region
    record["added_by"] = added_by
    record["updated_at"] = now
    await rs.set_json(key, record, ex=365 * 86400)

    # Index
    families = await rs.get_json(_K_INDEX_FAMILIES) or []
    if family not in families:
        families.append(family)
        await rs.set_json(_K_INDEX_FAMILIES, families, ex=365 * 86400)

    # Audit-log every atlas mutation (Clause 14 substrate)
    try:
        from . import audit_log as _al
        kind = "source_atlas_add" if action == "added" else "source_atlas_update"
        if kind in _al.RECORDED_ACTIONS:
            await _al.record(
                kind,
                actor="aria_web_atlas",
                inputs={"url": url, "tier": tier, "region": region,
                        "topics": topic_tags[:10]},
                outputs={"family": family, "action": action},
                decision=action,
                notes=f"added_by={added_by}",
                sources=[url],
            )
    except Exception as e:
        logger.debug("audit emit for atlas %s failed: %s", action, e)
    return {"action": action, "family": family, "record": record}


async def promote_source(family: str, new_tier: str, reason: str = "") -> dict:
    """Bump a source's tier — used when reliability score stays ≥0.85
    over ≥20 observations."""
    key = _K_SOURCE.format(family=family)
    record = await rs.get_json(key)
    if not record:
        return {"ok": False, "error": "family not registered"}
    old_tier = record.get("tier")
    record["tier"] = new_tier
    record["tier_history"] = (record.get("tier_history") or []) + [{
        "from": old_tier, "to": new_tier,
        "at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }]
    await rs.set_json(key, record, ex=365 * 86400)
    try:
        from . import audit_log as _al
        if "source_atlas_promote" in _al.RECORDED_ACTIONS:
            await _al.record(
                "source_atlas_promote",
                actor="aria_web_atlas",
                inputs={"family": family, "from": old_tier, "to": new_tier},
                outputs={"reason": reason},
                decision="promoted",
            )
    except Exception:
        pass
    return {"ok": True, "family": family, "from": old_tier, "to": new_tier}


async def demote_source(family: str, new_tier: str, reason: str = "") -> dict:
    """Lower a source's tier — used when reliability score drops ≤0.4."""
    key = _K_SOURCE.format(family=family)
    record = await rs.get_json(key)
    if not record:
        return {"ok": False, "error": "family not registered"}
    old_tier = record.get("tier")
    record["tier"] = new_tier
    record["tier_history"] = (record.get("tier_history") or []) + [{
        "from": old_tier, "to": new_tier,
        "at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    }]
    await rs.set_json(key, record, ex=365 * 86400)
    try:
        from . import audit_log as _al
        if "source_atlas_demote" in _al.RECORDED_ACTIONS:
            await _al.record(
                "source_atlas_demote",
                actor="aria_web_atlas",
                inputs={"family": family, "from": old_tier, "to": new_tier},
                outputs={"reason": reason},
                decision="demoted",
            )
    except Exception:
        pass
    return {"ok": True, "family": family, "from": old_tier, "to": new_tier}


async def stats() -> dict:
    """Top-line atlas stats for the /atlas/stats endpoint and the daily briefing."""
    families = await rs.get_json(_K_INDEX_FAMILIES) or []
    topics = await rs.get_json(_K_INDEX_TOPICS) or []
    cov = await coverage_map()
    gaps_critical = [c for c in cov if c.get("gap_level") == "CRITICAL"]
    gaps_high = [c for c in cov if c.get("gap_level") == "HIGH"]
    return {
        "source_families": len(families),
        "topics_tracked": len(topics),
        "coverage_cells": len(cov),
        "gaps_critical": len(gaps_critical),
        "gaps_high": len(gaps_high),
    }


async def snapshot_to_yaml() -> dict:
    """Write a human-readable YAML mirror of the atlas so ARIA's self-improve
    pipeline can edit it through the whitelisted path."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return {"ok": False, "error": "PyYAML not installed"}
    families = await rs.get_json(_K_INDEX_FAMILIES) or []
    snapshot = {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
                "sources": []}
    for fam in families:
        rec = await rs.get_json(_K_SOURCE.format(family=fam))
        if rec:
            snapshot["sources"].append(rec)
    try:
        _YAML_MIRROR.write_text(
            yaml.safe_dump(snapshot, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return {"ok": True, "path": str(_YAML_MIRROR), "sources": len(snapshot["sources"])}
    except Exception as e:
        return {"ok": False, "error": str(e)}

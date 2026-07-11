"""learning_progress — per-domain freshness tracker (R-F88, 2026-05-09).

Why this module exists
──────────────────────
Phase 2 of the independence roadmap. ARIA's knowledge base grows at
~+3,962 facts/day, but growth alone doesn't tell us whether her brain
'sees, hears, and knows everything' — a fact ingested 6 months ago
may be stale; a domain that hasn't been crawled in 90 days is a blind
spot regardless of total fact count.

This module records per-domain learning progress and surfaces the
'freshness picture' as an operator-readable view. It complements R-F89
(coverage heatmap, the visual surface) and R-F90 (continuous-update
orchestrator, the autonomous-engine wrapper that targets stale domains).

Domain model
────────────
A 'domain' is a coarse topic the operator cares about. We use the same
list as core_mastery + the heatmap regions. Examples:

  sanctions_screening, eccn_classification, euc_jurisdictions,
  fatf_ml_typologies, fatf_tbml, sanctions_divergence,
  defence_market:angola, defence_market:nigeria, fcpa_enforcement,
  npo_abuse_typology, virtual_assets, etc.

Each domain has:
  - last_refreshed_at:   ISO timestamp of most recent successful ingest
  - facts_count:         current fact count tagged in this domain
  - signals_count:       intel_ledger signals tagged in this domain
  - max_staleness_hours: domain-specific freshness window
  - is_stale:            current state (now - last_refreshed > max_staleness)
  - last_refresh_source: which module / autonomous task last refreshed
                          the domain

Each successful ingest into a domain calls `record_refresh(domain, ...)`.
The tracker's state is small enough to live in Redis (24-hour rolling
window cap; older state aged out).

Public API
──────────
    record_refresh(domain: str, *, source: str, facts_added: int = 1) -> None
    get_freshness(domain: str) -> dict
    get_all_domains() -> dict[str, dict]
    stale_domains(now: datetime | None = None) -> list[dict]
    summary() -> dict
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.learning_progress")

_REDIS_KEY = "crucix:aria:learning_progress:domains"
_TTL_SECONDS = 90 * 24 * 3600  # 90-day rolling window

# Domain-specific max-staleness windows. Tuned to source velocity:
#  - sanctions: every 24h (OFAC publishes ad-hoc; daily check is the floor)
#  - FATF: every 7d (FATF reports are quarterly; weekly check is excess)
#  - EUC: every 30d (regulatory; jurisdiction templates change rarely)
#  - weapon_systems: every 14d (catalogue changes are slow)
#  - defence_market:* (per-country): every 24h (procurement signals daily)
#  - fcpa_enforcement: every 7d (DOJ press release cadence)
#  - virtual_assets: every 24h (FATF VA + crypto sanctions list updates)
#
# An override map. Domains not listed here use DEFAULT_MAX_STALENESS_HOURS.

_MAX_STALENESS_OVERRIDES: dict[str, int] = {
    # Sanctions surface (high frequency)
    "sanctions_screening":      24,
    "sanctions_divergence":     24,
    "ofac_sdn":                 24,
    "ofsi_consolidated":        24,
    "eu_fsf":                   24,
    "un_sc_sanctions":          24,

    # FATF + typology (slow-moving)
    "fatf_ml_typologies":       168,  # 7 days
    "fatf_tbml":                168,
    "fatf_recommendations":     720,  # 30 days

    # Export controls (moderate)
    "eccn_classification":      168,
    "wassenaar":                168,
    "euc_jurisdictions":        720,

    # Defence markets (high frequency, per-country)
    # Use prefix-match in get_max_staleness rather than enumerating

    # Enforcement
    "fcpa_enforcement":         168,
    "sec_enforcement":          168,

    # Crypto
    "virtual_assets":           24,
    "crypto_sanctioned_wallets": 24,

    # NATO standards (very slow)
    "nato_standards":           720,
    "weapon_systems":           336,  # 14 days

    # Adversarial / counter-intelligence
    "adversarial_baseline":     168,
    "counter_intelligence":     168,
}

DEFAULT_MAX_STALENESS_HOURS = 168  # 7 days


def _max_staleness_for(domain: str) -> int:
    """Return max-staleness hours for a domain, with prefix-match for
    market-specific domains (e.g. defence_market:angola → 24h)."""
    if domain in _MAX_STALENESS_OVERRIDES:
        return _MAX_STALENESS_OVERRIDES[domain]
    if domain.startswith("defence_market:"):
        return 24
    if domain.startswith("language:"):
        return 720  # languages fundamentally slow-changing
    return DEFAULT_MAX_STALENESS_HOURS


async def _redis():
    from . import redis_store as rs
    return rs


@fail_wire(module="learning_progress", gap_type="engine_failure")
async def record_refresh(
    domain: str,
    *,
    source: str = "",
    facts_added: int = 1,
    signals_added: int = 0,
) -> None:
    """Record that `domain` was just refreshed by `source`.

    Best-effort; never raises. Called from every successful ingest path:
      - intel_ledger.append → 'sanctions_screening' / 'fcpa_enforcement' / etc
      - knowledge.add_fact  → 'fatf_ml_typologies' / 'eccn_classification'
      - autonomous task     → its own task name as `source`
      - sweep ingest        → per-source domain (e.g. 'cyber_threats')

    The tracker is a thin write-mostly layer; reads are operator-driven
    via stale_domains() and the dashboard.
    """
    if not domain or not domain.strip():
        return
    try:
        rs = await _redis()
        existing = await rs.get_json(_REDIS_KEY)
        if not isinstance(existing, dict):
            existing = {}
        record = existing.get(domain) or {
            "domain":            domain,
            "first_seen_at":     datetime.now(timezone.utc).isoformat(),
            "facts_count":       0,
            "signals_count":     0,
            "refresh_count":     0,
        }
        record["last_refreshed_at"]    = datetime.now(timezone.utc).isoformat()
        record["last_refresh_source"]  = source or "unknown"
        record["facts_count"]          = int(record.get("facts_count", 0)) + max(0, facts_added)
        record["signals_count"]        = int(record.get("signals_count", 0)) + max(0, signals_added)
        record["refresh_count"]        = int(record.get("refresh_count", 0)) + 1
        existing[domain] = record
        # Cap to 1000 domains (way more than we'll ever need; defends
        # against pathological input from a typo'd domain string)
        if len(existing) > 1000:
            # Keep most-recently-touched
            sorted_items = sorted(
                existing.items(),
                key=lambda kv: kv[1].get("last_refreshed_at", ""),
                reverse=True,
            )
            existing = dict(sorted_items[:1000])
        await rs.set_json(_REDIS_KEY, existing, ex=_TTL_SECONDS)
    except Exception as e:
        logger.debug("learning_progress record_refresh failed (non-fatal): %s", e)


@fail_wire(module="learning_progress", gap_type="engine_failure")
async def get_freshness(domain: str) -> dict[str, Any]:
    """Return the freshness record for one domain (with computed
    is_stale / hours_since_refresh fields)."""
    if not domain or not domain.strip():
        return {"ok": False, "error": "domain required"}
    try:
        rs = await _redis()
        existing = await rs.get_json(_REDIS_KEY)
    except Exception as e:
        return {"ok": False, "error": f"redis: {e}"}
    if not isinstance(existing, dict):
        existing = {}
    record = existing.get(domain)
    if not record:
        return {
            "domain":      domain,
            "tracked":     False,
            "narrative":   f"{domain}: never refreshed (or not yet tracked).",
            "max_staleness_hours": _max_staleness_for(domain),
        }
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="learning_progress",
        summary="Get Freshness",
        source_id="learning_progress:R-F996",
    )

    return _compute_staleness(record)


def _compute_staleness(record: dict[str, Any]) -> dict[str, Any]:
    """Add computed is_stale / hours_since_refresh / narrative fields."""
    domain = record["domain"]
    max_hours = _max_staleness_for(domain)
    last = record.get("last_refreshed_at")
    if not last:
        return {**record, "tracked": True, "is_stale": True,
                "hours_since_refresh": None, "max_staleness_hours": max_hours,
                "narrative": f"{domain}: tracked but never refreshed."}
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        return {**record, "tracked": True, "is_stale": True,
                "hours_since_refresh": None, "max_staleness_hours": max_hours,
                "narrative": f"{domain}: bad timestamp format."}
    hours_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    is_stale = hours_since > max_hours
    narrative = (
        f"{domain}: {'STALE' if is_stale else 'FRESH'} — last refreshed "
        f"{hours_since:.1f}h ago (window {max_hours}h). "
        f"{record.get('facts_count', 0)} facts, "
        f"{record.get('signals_count', 0)} signals."
    )
    return {
        **record,
        "tracked":              True,
        "max_staleness_hours":  max_hours,
        "hours_since_refresh":  round(hours_since, 2),
        "is_stale":             is_stale,
        "narrative":            narrative,
    }


@fail_wire(module="learning_progress", gap_type="engine_failure")
async def get_all_domains() -> list[dict[str, Any]]:
    """Return all tracked domains with computed staleness fields."""
    try:
        rs = await _redis()
        existing = await rs.get_json(_REDIS_KEY)
    except Exception:
        return []
    if not isinstance(existing, dict):
        return []
    out = [_compute_staleness(r) for r in existing.values()]
    out.sort(key=lambda r: (not r.get("is_stale"), r.get("domain", "")))
    return out


@fail_wire(module="learning_progress", gap_type="engine_failure")
async def stale_domains() -> list[dict[str, Any]]:
    """Return only the domains currently STALE — drives R-F90's
    continuous-update orchestrator."""
    all_d = await get_all_domains()
    return [d for d in all_d if d.get("is_stale")]


@fail_wire(module="learning_progress", gap_type="engine_failure")
async def stats() -> dict[str, Any]:
    """Aggregate dashboard view: tracked, stale, fresh, top-stale."""
    all_d = await get_all_domains()
    stale = [d for d in all_d if d.get("is_stale")]
    fresh = [d for d in all_d if not d.get("is_stale")]
    return {
        "tracked_total":  len(all_d),
        "fresh_count":    len(fresh),
        "stale_count":    len(stale),
        "stale_pct":      round(len(stale) / len(all_d) * 100, 1) if all_d else 0,
        "top_stale": sorted(
            [
                {
                    "domain":              d["domain"],
                    "hours_since_refresh": d.get("hours_since_refresh"),
                    "max_staleness_hours": d.get("max_staleness_hours"),
                }
                for d in stale
            ],
            key=lambda x: -(x["hours_since_refresh"] or 0),
        )[:20],
    }


@fail_wire(module="learning_progress", gap_type="engine_failure")
def summary() -> dict[str, Any]:
    return {
        "module":              "learning_progress",
        "purpose":             "per-domain freshness tracker for the autonomous brain",
        "default_max_staleness_hours": DEFAULT_MAX_STALENESS_HOURS,
        "overrides_count":     len(_MAX_STALENESS_OVERRIDES),
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

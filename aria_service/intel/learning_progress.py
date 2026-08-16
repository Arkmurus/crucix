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


# R-F4067 (C-110) — ONE prefix table, read by both `_max_staleness_for` and
# `_is_protected`. They were about to become two hand-maintained lists of the
# same curated prefixes, which is how the next one silently rots out of sync.
_PREFIX_STALENESS: tuple[tuple[str, int], ...] = (
    ("defence_market:", 24),
    ("language:", 720),   # languages fundamentally slow-changing
)

_MAX_TRACKED_DOMAINS = 1000
# A domain that comes back is a real ingest surface. Live 2026-08-16, 999 of
# the 1000 tracked entries had refresh_count == 1 — one-off research topics
# minted per extracted fact by knowledge.add_fact (R-F96).
_MIN_REFRESHES_TO_PROTECT = 2


def _max_staleness_for(domain: str) -> int:
    """Return max-staleness hours for a domain, with prefix-match for
    market-specific domains (e.g. defence_market:angola → 24h)."""
    if domain in _MAX_STALENESS_OVERRIDES:
        return _MAX_STALENESS_OVERRIDES[domain]
    for prefix, hours in _PREFIX_STALENESS:
        if domain.startswith(prefix):
            return hours
    return DEFAULT_MAX_STALENESS_HOURS


def _is_protected(domain: str, record: dict | None = None) -> bool:
    """Is this a domain the tracker exists to watch, rather than a one-off?

    R-F4067 (C-110). Protection is deliberately NOT an allowlist. Plain
    recurring topics like `compliance` are genuine ingest surfaces and are not
    in `_MAX_STALENESS_OVERRIDES`, so an allowlist-only rule would have evicted
    them just as the flood did. Recurrence is the honest discriminator and it is
    already in the data.
    """
    if domain in _MAX_STALENESS_OVERRIDES:
        return True
    if any(domain.startswith(p) for p, _h in _PREFIX_STALENESS):
        return True
    if record is None:
        return False
    try:
        return int(record.get("refresh_count", 0)) >= _MIN_REFRESHES_TO_PROTECT
    except (TypeError, ValueError):
        return False


def _is_expired_ambient(domain: str, record: dict) -> bool:
    """An unprotected, seen-once topic that is already past its own window.

    It has no SLA left to miss, so it must not hold a slot against a real
    domain. A PROTECTED domain being stale is the signal this module exists to
    emit — never prune one.
    """
    if _is_protected(domain, record):
        return False
    last = record.get("last_refreshed_at")
    if not last:
        return True  # tracked, never refreshed, and not protected
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except Exception:
        return True
    hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    return hours > _max_staleness_for(domain)


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

        # ── R-F4067 (C-110) — drain the one-off topics, protect the domains ──
        #
        # The cap plus "keep most-recently-touched" was the whole defect. Live
        # 2026-08-16: 999 of 1000 entries were minted inside 24h by
        # knowledge.add_fact registering every fact TOPIC as a domain
        # ('rage_bait_pays'_headline, 13-year-old_shoplifting_suspect …). The
        # table turned over in under 48h against a 168h staleness window, so
        # eviction always beat the clock — `stale_count` was pinned at 0 by
        # construction — and every curated domain (sanctions_screening,
        # fatf_ml_typologies, weapon_systems, eccn_classification,
        # fcpa_enforcement, virtual_assets) had been evicted. That starved
        # `stale_domains()`, which is the R-F90 orchestrator's Layer-1 urgency
        # input, so the surfaces with 24h SLAs were never re-targeted.
        #
        # Raising the cap was NOT the fix: it delays the same failure behind an
        # unbounded blob (and this function already read-modify-writes the whole
        # dict on every ingest). Two ordered steps instead:
        #   1. prune ambient entries that are already past their own window —
        #      a seen-once topic has no SLA left to miss;
        #   2. if still over cap, evict UNPROTECTED first, then oldest.
        # A protected domain is never dropped by either step, so it can always
        # be reported stale.
        # Step 1 runs on every write, not only when over cap: without it the
        # store sticks at exactly the cap forever (the flood stops growing but
        # nothing drains), and the curated domains never get their slots back.
        # It is O(n) over a dict this function already reads and writes whole.
        existing = {
            d: r for d, r in existing.items()
            if d == domain or not _is_expired_ambient(d, r)
        }
        if len(existing) > _MAX_TRACKED_DOMAINS:
            sorted_items = sorted(
                existing.items(),
                # protected first, then most-recently-touched
                key=lambda kv: (_is_protected(kv[0], kv[1]),
                                kv[1].get("last_refreshed_at", "")),
                reverse=True,
            )
            existing = dict(sorted_items[:_MAX_TRACKED_DOMAINS])
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
    # R-F4067 (C-110) — the two populations, reported separately. `0 stale /
    # 1000` read as a green light because 999 of the 1000 were one-off research
    # topics that could not go stale before being evicted. The legacy fields
    # keep their exact meaning (other readers depend on them); these say WHICH
    # traffic they are made of, so a headline can be built on the population
    # that has an SLA rather than on the one that never will.
    protected = [d for d in all_d if _is_protected(d.get("domain", ""), d)]
    ambient = [d for d in all_d if not _is_protected(d.get("domain", ""), d)]
    return {
        "tracked_total":  len(all_d),
        "fresh_count":    len(fresh),
        "stale_count":    len(stale),
        "stale_pct":      round(len(stale) / len(all_d) * 100, 1) if all_d else 0,
        "protected_total": len(protected),
        "protected_stale": sum(1 for d in protected if d.get("is_stale")),
        "ambient_total":   len(ambient),
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

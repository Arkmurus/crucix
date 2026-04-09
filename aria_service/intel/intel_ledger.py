"""
Intelligence Ledger — 30-day rolling signal store.
Ported from lib/aria/intel_ledger.mjs.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.ledger")

KEY = "crucix:intel_ledger"
MAX_SIGNALS = 10000
RETENTION_DAYS = 30

_cache: dict | None = None

# ── Entity extraction lists ──────────────────────────────────────────────────

COUNTRIES = [
    "Angola", "Mozambique", "Guinea-Bissau", "Cape Verde", "São Tomé", "Nigeria",
    "Kenya", "Ghana", "Senegal", "Ivory Coast", "Cameroon", "Ethiopia", "Rwanda",
    "Uganda", "Tanzania", "Morocco", "Algeria", "Egypt", "Tunisia", "Libya",
    "South Africa", "Namibia", "Botswana", "Zimbabwe", "Zambia", "DRC", "Congo",
    "Mali", "Burkina Faso", "Niger", "Chad", "Sudan", "South Sudan", "Somalia",
    "Djibouti", "Eritrea", "Madagascar", "Indonesia", "Philippines", "Vietnam",
    "Thailand", "Myanmar", "Malaysia", "Singapore", "India", "Pakistan",
    "Bangladesh", "Sri Lanka", "UAE", "Saudi Arabia", "Qatar", "Kuwait", "Oman",
    "Bahrain", "Iraq", "Jordan", "Lebanon", "Turkey", "Israel", "Iran",
    "Poland", "Romania", "Ukraine", "Brazil", "Colombia", "Mexico", "Peru", "Chile",
]

PRODUCTS = {
    "ammunition": ["ammunition", "ammo", "round", "mortar", "shell"],
    "vehicles": ["vehicle", "armoured", "apc", "ifv", "mrap", "tank"],
    "aircraft": ["aircraft", "fighter", "helicopter", "drone", "uav"],
    "naval": ["vessel", "frigate", "corvette", "submarine", "destroyer"],
    "missiles": ["missile", "rocket", "sam", "patriot", "javelin"],
    "radar": ["radar", "air defense", "shorad", "ewi"],
    "small_arms": ["rifle", "pistol", "machine gun", "carbine"],
    "surveillance": ["surveillance", "isr", "reconnaissance", "sigint"],
    "training": ["training", "exercise", "drill", "simulation"],
}

OEMS = [
    "Lockheed", "Boeing", "Raytheon", "BAE Systems", "Leonardo", "Rheinmetall",
    "Thales", "Turkish Aerospace", "Baykar", "Elbit", "Rafael", "IAI",
    "Paramount", "Denel", "Norinco", "AVIC", "Poly Technologies", "Embraer",
    "Otokar", "FNSS", "Aselsan", "Hanwha", "Hyundai Rotem", "KAI",
    "Damen", "Navantia", "Fincantieri", "MBDA", "Saab", "Kongsberg",
    "General Dynamics", "Northrop", "L3Harris",
]


def _extract_entities(text: str) -> dict:
    tl = text.lower()
    countries = [c for c in COUNTRIES if c.lower() in tl]
    products = [cat for cat, kws in PRODUCTS.items() if any(k in tl for k in kws)]
    oems = [o for o in OEMS if o.lower() in tl]
    return {"countries": countries, "products": products, "oems": oems}


async def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    data = await rs.get_json(KEY)
    if data and "signals" in data:
        _cache = data
    else:
        _cache = {"signals": [], "version": 1}
    _prune()
    return _cache


def _prune() -> None:
    if not _cache:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    _cache["signals"] = [s for s in _cache["signals"] if s.get("ts", "") >= cutoff]
    if len(_cache["signals"]) > MAX_SIGNALS:
        _cache["signals"] = _cache["signals"][:MAX_SIGNALS]


async def _save() -> None:
    if _cache:
        await rs.set_json(KEY, _cache)


# ── Public API ───────────────────────────────────────────────────────────────

async def init() -> None:
    await _load()
    logger.info(f"Intel ledger loaded: {len((_cache or {}).get('signals', []))} signals")


async def purge_signals_by_keyword(keywords: list[str], dry_run: bool = False) -> dict:
    """Remove signals from the ledger whose text contains ANY of the given
    keywords (case-insensitive). Designed for surgical cleanup of polluted
    signals — e.g. fabricated current-event claims that bled into multiple
    chat replies.

    Returns a summary dict with the number matched, removed, and a sample
    of the matched texts so callers can verify before committing.

    When dry_run=True, returns the same shape but does not actually delete.
    """
    db = await _load()
    if not keywords:
        return {"matched": 0, "removed": 0, "sample": [], "dry_run": dry_run}
    needles = [k.lower() for k in keywords if k]
    matched_signals = []
    surviving = []
    for s in db.get("signals", []):
        text = (s.get("text", "") or "").lower()
        source = (s.get("source", "") or "").lower()
        if any(n in text or n in source for n in needles):
            matched_signals.append(s)
        else:
            surviving.append(s)

    sample = [
        {"text": s.get("text", "")[:200], "source": s.get("source", ""), "ts": s.get("ts", "")}
        for s in matched_signals[:10]
    ]

    if not dry_run and matched_signals:
        db["signals"] = surviving
        await _save()
        logger.warning(
            "Ledger purge: removed %d signal(s) matching keywords=%s",
            len(matched_signals), keywords,
        )

    return {
        "matched": len(matched_signals),
        "removed": 0 if dry_run else len(matched_signals),
        "remaining": len(surviving) if not dry_run else len(db.get("signals", [])),
        "sample": sample,
        "dry_run": dry_run,
        "keywords": keywords,
    }


# Propaganda-tier sources — these channels are monitored for OSINT
# situational awareness via the sweep cycle but their content is NOT
# trustworthy enough to enter the chat-injection layer. Keeping them out
# of the intel ledger entirely is the cleanest defence: every downstream
# code path (query_ledger, _build_intel_context, the LLM prompt) becomes
# safe by construction. The list mirrors apis/sources/telegram.mjs
# DEFAULT_CHANNELS — when new biased channels are added there, this set
# must be updated in lockstep.
#
# Past incident 2026-04-09: a single intelslava "Lebanon airstrikes 112
# killed" post propagated into the Vision International ammunition RFQ
# analysis, the Modirum Gespi investigation, AND the Ghana defence
# minister query, with [CONFIRMED] tags. Even after constitution clause
# 13 + the relevance filter + a manual purge, the sweep cycle kept
# re-ingesting fresh propaganda content every cycle and the bleed
# returned. The only structural fix is to block these sources at the
# ledger boundary.
_PROPAGANDA_SOURCES = {
    # Russian state / Russian-aligned
    "intelslava", "mod_russia", "rvvoenkor", "readovkanews", "readovka",
    # Conflict Intelligence Team is sometimes Russian-aligned content
    "cig_telegram",
    # Ukrainian state / Ukrainian-aligned (also single-perspective)
    "deepstateua", "operativnozsu", "generalstaffzsu", "legitimniy",
    "ukraine frontline",
    # Generic single-channel buckets that proved high-noise in 2026-04-09
    "telegram", "tg",
}


def _is_propaganda_source(source: str) -> bool:
    """Return True if this source identifier matches the propaganda set.
    Case-insensitive substring match — catches both 'intelslava' as a
    full source string and 'telegram:intelslava' as a prefixed one."""
    if not source:
        return False
    s = source.lower().strip()
    if s in _PROPAGANDA_SOURCES:
        return True
    return any(p in s for p in _PROPAGANDA_SOURCES if len(p) > 3)


async def ingest_sweep_signals(current_data: dict) -> int:
    """Parse sweep data, extract entities, dedup, store. Returns count added.

    Propaganda-tier sources (intelslava, mod_russia, CIG_telegram, etc.)
    are SKIPPED at ingest time — they never enter the ledger and therefore
    can never be auto-injected into chat replies. This is the structural
    fix for the 2026-04-09 Lebanon contamination incident; clause 13 and
    the relevance filter handle the same content if it slips through via
    other paths, but the ledger boundary is the cleanest place to block.
    """
    db = await _load()
    existing = {s.get("text", "")[:150].lower() for s in db["signals"]}
    added = 0
    skipped_propaganda = 0
    now = datetime.now(timezone.utc).isoformat()

    def _add(text: str, source: str, sig_type: str, url: str = "", severity: str = "medium"):
        nonlocal added, skipped_propaganda
        if not text:
            return
        if _is_propaganda_source(source):
            skipped_propaganda += 1
            return
        if text[:150].lower() in existing:
            return
        ent = _extract_entities(text)
        db["signals"].insert(0, {
            "text": text[:500], "source": source, "type": sig_type, "url": url,
            "countries": ent["countries"], "products": ent["products"], "oems": ent["oems"],
            "severity": severity, "ts": now,
        })
        existing.add(text[:150].lower())
        added += 1

    # OSINT urgent
    for s in (current_data.get("tg", {}).get("urgent") or []):
        _add(s.get("text", ""), s.get("channel", "OSINT"), "osint")

    # Correlations
    for c in (current_data.get("correlations") or []):
        for sig in (c.get("topSignals") or [])[:2]:
            _add(sig.get("text", ""), c.get("region", ""), "correlation", severity=c.get("severity", "medium"))

    # Defence news (may be list of dicts or list of strings)
    for d in (current_data.get("defenseNews") or []):
        if isinstance(d, str):
            _add(d, "defence_news", "defense_news")
        elif isinstance(d, dict):
            _add(d.get("title", ""), d.get("source", "defence_news"), "defense_news", d.get("link", ""))

    # Tenders
    items = (current_data.get("procurementTenders") or {}).get("items") or []
    for t in items:
        if isinstance(t, str):
            _add(t, "tender", "tender")
        elif isinstance(t, dict):
            _add(t.get("title") or t.get("text", ""), t.get("source", "tender"), "tender", t.get("link", ""))

    # BD brain leads
    brain = (current_data.get("bdIntelligence") or {}).get("brain") or {}
    for l in (brain.get("salesLeads") or []):
        _add(f"{l.get('market','')}: {l.get('lead','')}", "brain", "brain_lead")

    _prune()
    await _save()
    if skipped_propaganda > 0:
        logger.info(
            "Ledger ingested %d new signals (%d propaganda-tier signals skipped at boundary)",
            added, skipped_propaganda,
        )
    else:
        logger.info("Ledger ingested %d new signals", added)
    return added


def _recency_multiplier(ts_iso: str, now: datetime | None = None) -> float:
    """Score multiplier based on signal age — fresh signals dominate stale ones.

    today / <2d  → 2.5x
    2-7 days     → 1.5x
    7-14 days    → 1.0x   (baseline)
    14-21 days   → 0.7x
    >21 days     → 0.4x

    Without this weighting, a 28-day-old correlation outranks today's tender simply
    because it has more keyword matches — disastrous for procurement timing.
    """
    if not ts_iso:
        return 0.4
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.4
    now = now or datetime.now(timezone.utc)
    age_days = (now - dt).total_seconds() / 86400
    if age_days < 2: return 2.5
    if age_days < 7: return 1.5
    if age_days < 14: return 1.0
    if age_days < 21: return 0.7
    return 0.4


def query_ledger(query: str) -> str:
    """Time-weighted, entity-aware search for prompt injection.

    Returns a formatted string for LLM context. Recent signals score 2.5x
    higher than 3-week-old ones — restoring the "what's hot now" intuition
    that a senior analyst would apply.
    """
    if not _cache or not _cache["signals"]:
        return ""
    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return ""

    now = datetime.now(timezone.utc)
    query_lower = query.lower()

    scored: list[tuple[float, dict]] = []
    for s in _cache["signals"]:
        score = 0.0
        for c in s.get("countries", []):
            if c.lower() in query_lower:
                score += 5
        for o in s.get("oems", []):
            if o.lower() in query_lower:
                score += 4
        for p in s.get("products", []):
            if p.lower() in query_lower:
                score += 4
        text = s.get("text", "").lower()
        for w in words:
            if w in text:
                score += 2

        # Severity boost — high-severity signals matter more even if older
        sev = (s.get("severity") or "medium").lower()
        if sev == "high":   score += 2
        elif sev == "low":  score -= 1

        if score > 0:
            # Apply temporal weighting AFTER content scoring
            score *= _recency_multiplier(s.get("ts", ""), now)
            scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:12]
    if not top:
        return ""

    lines = [f"\n[INTELLIGENCE LEDGER — recent signals ({len(_cache['signals'])} total, 30d, recency-weighted)]"]
    for score, s in top:
        age = ""
        if s.get("ts"):
            try:
                dt = datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
                days = (now - dt).days
                hrs = (now - dt).total_seconds() / 3600
                if hrs < 24: age = f" ({int(hrs)}h ago)"
                else: age = f" ({days}d ago)"
            except Exception:
                pass
        lines.append(f"- [{s.get('type','?')}] {s.get('text','')[:180]}{age}")
    return "\n".join(lines)


async def get_country_situation(country: str) -> dict:
    db = await _load()
    signals = [s for s in db["signals"] if country.lower() in [c.lower() for c in s.get("countries", [])]]
    return {
        "country": country,
        "signalCount": len(signals),
        "recentSignals": signals[:20],
    }


async def get_stats() -> dict:
    db = await _load()
    by_type: dict[str, int] = {}
    by_country: dict[str, int] = {}
    for s in db["signals"]:
        t = s.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        for c in s.get("countries", []):
            by_country[c] = by_country.get(c, 0) + 1
    return {
        "totalSignals": len(db["signals"]),
        "byType": by_type,
        "byCountry": dict(sorted(by_country.items(), key=lambda x: x[1], reverse=True)[:15]),
    }

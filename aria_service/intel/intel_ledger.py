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


async def ingest_sweep_signals(current_data: dict) -> int:
    """Parse sweep data, extract entities, dedup, store. Returns count added."""
    db = await _load()
    existing = {s.get("text", "")[:150].lower() for s in db["signals"]}
    added = 0
    now = datetime.now(timezone.utc).isoformat()

    def _add(text: str, source: str, sig_type: str, url: str = "", severity: str = "medium"):
        nonlocal added
        if not text or text[:150].lower() in existing:
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
    logger.info(f"Ledger ingested {added} new signals")
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

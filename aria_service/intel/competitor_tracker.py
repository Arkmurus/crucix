"""ARIA Competitor Tracker — firm-level activity in active markets.

Priority 4 from the 2026-04-17 session notes. Asks: *who else is working
Angola / Mozambique / Guinea-Bissau* — and more broadly across every
active market Arkmurus cares about. Signals: ECJU brokering licences,
tender awards, SIPRI transfers, press announcements, MoU signings.

What this module does
─────────────────────
1. Tracks a curated registry of competitor firms (ARIA's peer OEMs +
   broker-advisors + ghost-suppliers). Extensible via add_competitor().
2. Scans tender_monitor alerts and intel_ledger signals for mentions of
   those firms in specific countries, records them as CompetitorActivity.
3. Exposes `who_else_in(country)` for chat + `get_competitor_activity()`
   for the dashboard + `generate_competitor_briefing()` for the team.
4. Feeds brain_hook so competitor mastery reflects observed activity.

Storage:
  crucix:aria:competitor_tracker:registry     → firm registry (JSON dict)
  crucix:aria:competitor_tracker:activities   → rolling 730-day activity log

Public API
──────────
  add_competitor(firm: dict) -> str
  record_activity(activity: dict) -> str
  scan_sources() -> dict                     # CRON hook for COMPETITOR-SCAN-DAILY
  who_else_in(country: str, since_days=180) -> list[dict]
  get_competitor_activity(country=None, firm=None, since_days=180) -> list[dict]
  generate_competitor_briefing() -> str
  get_competitor_context(query: str) -> str
  stats() -> dict
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import redis_store as rs
from . import country_taxonomy as ct

logger = logging.getLogger("aria.competitor_tracker")

REGISTRY_KEY = "crucix:aria:competitor_tracker:registry"
ACTIVITIES_KEY = "crucix:aria:competitor_tracker:activities"
ACTIVITY_RETENTION_DAYS = 36500  # 100 years — permanent; was 730d before 2026-04-21
MAX_ACTIVITIES = 500_000

ACTIVITY_TYPES = (
    "tender_win",        # awarded a contract
    "tender_bid",        # appeared on a bid
    "ecju_licence",      # UK export licence granted
    "sipri_transfer",    # SIPRI recorded transfer
    "press_announcement",# public PR event
    "mou_signed",        # memorandum / cooperation agreement
    "exhibition",        # attended defence expo / pavilion
)

# ── Seed competitor registry ──────────────────────────────────────────────
# Categorised firms ARIA should watch. `alias` strings are matched
# case-insensitively against signal text for passive detection.
_SEED_COMPETITORS: list[dict] = [
    # ── Turkish OEMs — Arkmurus's "peer incumbent" cluster ────────────────
    {"firm": "Baykar", "aliases": ["baykar", "tb2", "bayraktar"],
     "category": "OEM", "country_hq": "TR", "focus_regions": ["ECOWAS", "SADC", "CIS", "GCC"],
     "notes": "TB2/Akinci export leader; primary competitor for African UAV mandates."},
    {"firm": "ASELSAN", "aliases": ["aselsan"], "category": "OEM", "country_hq": "TR",
     "focus_regions": ["GCC", "ASEAN", "ECOWAS"], "notes": "Radar + EW integration."},
    {"firm": "Roketsan", "aliases": ["roketsan"], "category": "OEM", "country_hq": "TR",
     "focus_regions": ["GCC", "SADC", "ASEAN"], "notes": ""},
    {"firm": "STM", "aliases": ["stm savunma", "stm defence"], "category": "OEM",
     "country_hq": "TR", "focus_regions": ["GCC", "ASEAN"], "notes": "Naval + UAV."},
    # ── European majors ───────────────────────────────────────────────────
    {"firm": "Leonardo", "aliases": ["leonardo", "finmeccanica"], "category": "OEM",
     "country_hq": "IT", "focus_regions": ["EU", "NATO", "GCC", "SADC"], "notes": ""},
    {"firm": "Rheinmetall", "aliases": ["rheinmetall"], "category": "OEM", "country_hq": "DE",
     "focus_regions": ["EU", "NATO", "ASEAN"], "notes": "Major land-platform winner."},
    {"firm": "KNDS", "aliases": ["knds", "nexter", "krauss-maffei wegmann"], "category": "OEM",
     "country_hq": "FR", "focus_regions": ["EU", "NATO", "GCC"], "notes": ""},
    {"firm": "Thales", "aliases": ["thales"], "category": "OEM", "country_hq": "FR",
     "focus_regions": ["EU", "NATO", "GCC", "ASEAN"], "notes": ""},
    {"firm": "Saab", "aliases": ["saab ab", "gripen", "erieye"], "category": "OEM",
     "country_hq": "SE", "focus_regions": ["NATO", "MERCOSUR", "ASEAN"], "notes": ""},
    {"firm": "Airbus Defence & Space", "aliases": ["airbus defence", "airbus ds", "airbus defense"],
     "category": "OEM", "country_hq": "FR",
     "focus_regions": ["EU", "NATO", "GCC", "SADC"], "notes": ""},
    {"firm": "BAE Systems", "aliases": ["bae systems"], "category": "OEM", "country_hq": "GB",
     "focus_regions": ["NATO", "GCC", "ASEAN"], "notes": ""},
    {"firm": "Fincantieri", "aliases": ["fincantieri"], "category": "OEM", "country_hq": "IT",
     "focus_regions": ["EU", "NATO", "GCC"], "notes": "Naval."},
    {"firm": "Navantia", "aliases": ["navantia"], "category": "OEM", "country_hq": "ES",
     "focus_regions": ["EU", "MERCOSUR", "ASEAN"], "notes": "Naval."},
    {"firm": "MBDA", "aliases": ["mbda"], "category": "OEM", "country_hq": "FR",
     "focus_regions": ["EU", "NATO", "GCC"], "notes": "Missiles."},
    # ── US primes ─────────────────────────────────────────────────────────
    {"firm": "Lockheed Martin", "aliases": ["lockheed martin", "lockheed"], "category": "OEM",
     "country_hq": "US", "focus_regions": ["NATO", "GCC", "ASEAN"], "notes": ""},
    {"firm": "Raytheon (RTX)", "aliases": ["raytheon", "rtx"], "category": "OEM",
     "country_hq": "US", "focus_regions": ["NATO", "GCC"], "notes": ""},
    {"firm": "General Dynamics", "aliases": ["general dynamics"], "category": "OEM",
     "country_hq": "US", "focus_regions": ["NATO", "GCC", "ASEAN"], "notes": ""},
    {"firm": "Northrop Grumman", "aliases": ["northrop grumman", "northrop"], "category": "OEM",
     "country_hq": "US", "focus_regions": ["NATO", "GCC"], "notes": ""},
    {"firm": "Boeing Defence", "aliases": ["boeing defense", "boeing defence"], "category": "OEM",
     "country_hq": "US", "focus_regions": ["NATO", "GCC", "ASEAN"], "notes": ""},
    {"firm": "L3Harris", "aliases": ["l3harris"], "category": "OEM", "country_hq": "US",
     "focus_regions": ["NATO", "GCC", "ECOWAS"], "notes": ""},
    # ── Chinese / Russian state exporters ────────────────────────────────
    {"firm": "Norinco", "aliases": ["norinco", "china north industries"], "category": "OEM",
     "country_hq": "CN", "focus_regions": ["SADC", "ECOWAS", "CIS"], "notes": ""},
    {"firm": "CATIC", "aliases": ["catic"], "category": "OEM", "country_hq": "CN",
     "focus_regions": ["SADC", "ECOWAS", "ASEAN"], "notes": ""},
    {"firm": "Rosoboronexport", "aliases": ["rosoboronexport"], "category": "STATE_EXPORTER",
     "country_hq": "RU", "focus_regions": ["SADC", "CIS", "ASEAN"], "notes": "Sanctions-exposed."},
    # ── Regional / niche ─────────────────────────────────────────────────
    {"firm": "Elbit Systems", "aliases": ["elbit"], "category": "OEM", "country_hq": "IL",
     "focus_regions": ["NATO", "GCC", "ASEAN", "MERCOSUR"], "notes": ""},
    {"firm": "IAI", "aliases": ["israel aerospace", "iai"], "category": "OEM", "country_hq": "IL",
     "focus_regions": ["ASEAN", "EU", "GCC"], "notes": ""},
    {"firm": "Denel", "aliases": ["denel"], "category": "OEM", "country_hq": "ZA",
     "focus_regions": ["SADC", "ECOWAS"], "notes": ""},
    {"firm": "Hanwha Aerospace", "aliases": ["hanwha aerospace", "hanwha defense"],
     "category": "OEM", "country_hq": "KR",
     "focus_regions": ["NATO", "ASEAN", "MERCOSUR"], "notes": ""},
    # ── UAE / Saudi localisation champions (Priority region expansion) ───
    {"firm": "EDGE Group", "aliases": ["edge group"], "category": "OEM", "country_hq": "AE",
     "focus_regions": ["GCC", "ECOWAS", "SADC"], "notes": "UAE localisation champion."},
    {"firm": "SAMI", "aliases": ["sami", "saudi arabian military industries"], "category": "OEM",
     "country_hq": "SA", "focus_regions": ["GCC"], "notes": "Vision 2030 anchor."},
]


# ── Helpers ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_id(*parts: str) -> str:
    return hashlib.md5("|".join(p or "" for p in parts).encode()).hexdigest()[:12]


async def _load_registry() -> dict:
    data = await rs.get_json(REGISTRY_KEY)
    if isinstance(data, dict) and "firms" in data:
        return data
    # Seed on first access
    firms = {e["firm"]: _normalise_firm(e) for e in _SEED_COMPETITORS}
    seeded = {"firms": firms, "version": 1}
    await rs.set_json(REGISTRY_KEY, seeded)
    return seeded


def _normalise_firm(raw: dict) -> dict:
    return {
        "firm": raw.get("firm", ""),
        "aliases": sorted({(raw.get("firm") or "").lower(), *[a.lower() for a in raw.get("aliases", []) if a]}),
        "category": (raw.get("category") or "OEM").upper(),
        "country_hq": (raw.get("country_hq") or "").upper(),
        "focus_regions": sorted({r.upper() for r in raw.get("focus_regions", []) if r}),
        "notes": raw.get("notes", ""),
    }


async def _load_activities() -> dict:
    data = await rs.get_json(ACTIVITIES_KEY)
    if isinstance(data, dict) and "items" in data:
        return data
    return {"items": [], "version": 1}


async def _save_activities(items: list[dict]) -> None:
    cutoff = (_now() - timedelta(days=ACTIVITY_RETENTION_DAYS)).isoformat()
    items = [a for a in items if a.get("ts", "") >= cutoff]
    if len(items) > MAX_ACTIVITIES:
        items = items[-MAX_ACTIVITIES:]
    # R-F1520: fire-and-forget to avoid blocking the caller with a large blob write.
    # The activity log is rebuilt on next access if the write fails.
    asyncio.ensure_future(rs.set_json(ACTIVITIES_KEY, {"items": items, "version": 1}))


# ── Public API ────────────────────────────────────────────────────────────

async def add_competitor(firm: dict) -> str:
    reg = await _load_registry()
    norm = _normalise_firm(firm)
    reg["firms"][norm["firm"]] = norm
    await rs.set_json(REGISTRY_KEY, reg)
    return norm["firm"]


async def record_activity(activity: dict) -> str:
    reg = await _load_registry()
    firm = activity.get("firm", "")
    if firm and firm not in reg["firms"]:
        # Auto-register stub so scan output is self-healing
        await add_competitor({"firm": firm, "aliases": [], "category": "UNKNOWN"})
    iso2 = ct.to_iso2(activity.get("country", "")) or activity.get("country_iso2", "")
    if isinstance(iso2, str):
        iso2 = iso2.upper()
    ts = activity.get("ts") or _now().isoformat()
    aid = _hash_id(firm, iso2 or "", ts[:10], (activity.get("title") or activity.get("summary") or "")[:80])
    data = await _load_activities()
    if any(a.get("id") == aid for a in data["items"]):
        return aid
    data["items"].append({
        "id": aid,
        "firm": firm,
        "country_iso2": iso2 or "",
        "region": ct.iso2_to_region(iso2) if iso2 else None,
        "activity_type": (activity.get("activity_type") or "press_announcement").lower(),
        "title": activity.get("title", "")[:300],
        "summary": activity.get("summary", "")[:500],
        "value_usd": activity.get("value_usd"),
        "source": activity.get("source", ""),
        "url": activity.get("url", ""),
        "ts": ts,
    })
    await _save_activities(data["items"])
    return aid


async def _scan_tender_alerts(registry: dict) -> int:
    """Walk recent tender_monitor alerts and record competitor mentions."""
    import json as _json
    try:
        raw = await rs.lrange("crucix:aria:tender_monitor:alerts", 0, 299)
    except Exception as e:
        logger.debug("tender alerts unavailable: %s", e)
        return 0
    new_count = 0
    for item in raw or []:
        try:
            t = _json.loads(item)
        except Exception:
            continue
        text = ((t.get("title") or "") + " " + (t.get("description") or "") + " " + (t.get("buyer") or "")).lower()
        for firm, record in registry["firms"].items():
            if any(alias and alias in text for alias in record.get("aliases", [])):
                activity_type = "tender_win" if "awarded" in text or "winner" in text else "tender_bid"
                _id = await record_activity({
                    "firm": firm,
                    "country_iso2": (t.get("country_iso2") or "").upper(),
                    "activity_type": activity_type,
                    "title": t.get("title", "")[:250],
                    "summary": (t.get("description") or "")[:300],
                    "source": f"tender:{t.get('portal','?')}",
                    "url": t.get("url", ""),
                    "ts": t.get("publication_date") or t.get("detected_at") or _now().isoformat(),
                })
                new_count += 1
    return new_count


async def _scan_intel_ledger(registry: dict) -> int:
    """Walk the intel_ledger for competitor mentions."""
    try:
        from . import intel_ledger
        ledger = await intel_ledger._load()
    except Exception as e:
        logger.debug("ledger unavailable: %s", e)
        return 0
    cutoff = (_now() - timedelta(days=90)).isoformat()
    new_count = 0
    for sig in ledger.get("signals", []):
        if sig.get("ts", "") < cutoff:
            continue
        text = (sig.get("text") or "").lower()
        for firm, record in registry["firms"].items():
            if not any(alias and alias in text for alias in record.get("aliases", [])):
                continue
            # Try to attach a country — prefer ledger-extracted countries
            iso2_hits: list[str] = []
            for country in sig.get("countries", []) or []:
                code = ct.to_iso2(country)
                if code:
                    iso2_hits.append(code)
            iso2 = iso2_hits[0] if iso2_hits else ""
            activity_type = (
                "tender_win" if any(k in text for k in ("won", "awarded", "contract signed"))
                else "mou_signed" if any(k in text for k in ("mou", "memorandum", "agreement signed"))
                else "press_announcement"
            )
            _id = await record_activity({
                "firm": firm,
                "country_iso2": iso2,
                "activity_type": activity_type,
                "title": (sig.get("text") or "")[:250],
                "source": f"ledger:{sig.get('source','')}",
                "url": sig.get("url", ""),
                "ts": sig.get("ts", ""),
            })
            new_count += 1
    return new_count


async def scan_sources() -> dict:
    """CRON-hook for COMPETITOR-SCAN-DAILY. Returns a summary dict."""
    reg = await _load_registry()
    tender_hits = await _scan_tender_alerts(reg)
    ledger_hits = await _scan_intel_ledger(reg)
    # Brain hook — competitor mastery is boosted when we actually observe
    try:
        from . import brain_hook
        await brain_hook.absorb_silent(
            module="competitor_tracker",
            summary=f"Competitor scan: {tender_hits} tender hits, {ledger_hits} ledger hits",
            success=True,
            extra_topics=["competitor_intel"],
        )
    except Exception:
        pass
    return {
        "tender_hits": tender_hits,
        "ledger_hits": ledger_hits,
        "firms_tracked": len(reg.get("firms", {})),
        "generated_at": _now().isoformat(),
    }


async def who_else_in(country: str, since_days: int = 180) -> list[dict]:
    """Who's been active in a country recently — primary chat helper."""
    iso2 = ct.to_iso2(country)
    if not iso2:
        return []
    cutoff = (_now() - timedelta(days=since_days)).isoformat()
    data = await _load_activities()
    hits = [a for a in data["items"] if a.get("country_iso2") == iso2 and a.get("ts", "") >= cutoff]
    # Aggregate by firm
    by_firm: dict[str, dict] = {}
    for a in hits:
        firm = a.get("firm", "")
        slot = by_firm.setdefault(firm, {
            "firm": firm, "country_iso2": iso2,
            "activity_count": 0, "activity_types": set(), "latest_ts": "",
            "sources": set(),
        })
        slot["activity_count"] += 1
        slot["activity_types"].add(a.get("activity_type", ""))
        slot["sources"].add(a.get("source", ""))
        if a.get("ts", "") > slot["latest_ts"]:
            slot["latest_ts"] = a.get("ts", "")
    out = []
    for v in by_firm.values():
        v["activity_types"] = sorted(t for t in v["activity_types"] if t)
        v["sources"] = sorted(s for s in v["sources"] if s)
        out.append(v)
    out.sort(key=lambda v: (-v["activity_count"], v["firm"]))
    return out


async def get_competitor_activity(country: Optional[str] = None, firm: Optional[str] = None,
                                  since_days: int = 180) -> list[dict]:
    cutoff = (_now() - timedelta(days=since_days)).isoformat()
    data = await _load_activities()
    iso2 = ct.to_iso2(country) if country else None
    out = []
    for a in data["items"]:
        if a.get("ts", "") < cutoff:
            continue
        if iso2 and a.get("country_iso2") != iso2:
            continue
        if firm and a.get("firm", "").lower() != firm.lower():
            continue
        out.append(a)
    out.sort(key=lambda a: a.get("ts", ""), reverse=True)
    return out


async def generate_competitor_briefing() -> str:
    """Markdown block summarising the most-active markets + firms in 30 days."""
    activity = await get_competitor_activity(since_days=30)
    if not activity:
        return ""
    by_country: dict[str, dict[str, int]] = {}
    for a in activity:
        iso2 = a.get("country_iso2") or "?"
        firm = a.get("firm") or "?"
        by_country.setdefault(iso2, {})[firm] = by_country.get(iso2, {}).get(firm, 0) + 1
    hottest = sorted(by_country.items(), key=lambda kv: -sum(kv[1].values()))[:5]
    lines = ["", "*🎯 COMPETITOR ACTIVITY (last 30d)*"]
    for iso2, firms in hottest:
        total = sum(firms.values())
        top = ", ".join(f"{f} ({n})" for f, n in sorted(firms.items(), key=lambda kv: -kv[1])[:3])
        lines.append(f"• *{iso2}* — {total} activity signal(s): {top}")
    return "\n".join(lines)


async def get_competitor_context(query: str, max_items: int = 3) -> str:
    """Chat-surface competitor context. Emits [COMPETITORS: ...] marker."""
    q = (query or "").lower()
    if not q:
        return ""
    # Try to extract an iso2/country from the query
    try:
        name_table = ct._name_to_iso2_table()
    except Exception:
        name_table = {}
    iso2 = None
    for name, code in name_table.items():
        if name and name in q:
            iso2 = code
            break
    if not iso2:
        for token in q.split():
            if len(token) == 2 and token.isalpha():
                maybe = token.upper()
                if ct.to_iso2(maybe):
                    iso2 = maybe
                    break
    if not iso2:
        return ""
    hits = await who_else_in(iso2, since_days=180)
    if not hits:
        return ""
    parts = [f"COMPETITOR LANDSCAPE in {iso2} (last 180d):"]
    for h in hits[:max_items]:
        parts.append(
            f"  [COMPETITORS: {h['firm']} — {h['activity_count']} signal(s), "
            f"types {','.join(h['activity_types']) or '?'}]"
        )
    return "\n".join(parts)


async def stats() -> dict:
    reg = await _load_registry()
    data = await _load_activities()
    by_firm: dict[str, int] = {}
    by_country: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for a in data["items"]:
        by_firm[a.get("firm", "?")] = by_firm.get(a.get("firm", "?"), 0) + 1
        by_country[a.get("country_iso2", "?")] = by_country.get(a.get("country_iso2", "?"), 0) + 1
        by_type[a.get("activity_type", "?")] = by_type.get(a.get("activity_type", "?"), 0) + 1
    return {
        "firms_tracked": len(reg.get("firms", {})),
        "activities_total": len(data["items"]),
        "by_firm_top10": dict(sorted(by_firm.items(), key=lambda kv: -kv[1])[:10]),
        "by_country_top10": dict(sorted(by_country.items(), key=lambda kv: -kv[1])[:10]),
        "by_type": by_type,
        "generated_at": _now().isoformat(),
    }

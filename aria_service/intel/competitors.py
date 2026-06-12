"""
Competitor Movement Tracker.
Ported from lib/aria/competitors.mjs.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.competitors")

KEY = "crucix:aria:competitors"
MAX_MOVES = 300
_cache: dict | None = None

# ── Known competitors ────────────────────────────────────────────────────────

COMPETITORS = [
    # Turkish
    {"name": "Baykar", "country": "TR", "products": "UAVs (TB2, Akinci)", "threat": "HIGH", "strategy": "Aggressive pricing + Turkish political backing + offset offers"},
    {"name": "Turkish Aerospace", "country": "TR", "products": "UAVs, helicopters", "threat": "HIGH", "strategy": "G2G deals + offset + technology transfer"},
    {"name": "Otokar", "country": "TR", "products": "Armoured vehicles", "threat": "HIGH", "strategy": "20-30% below Western pricing"},
    {"name": "FNSS", "country": "TR", "products": "AFVs, amphibious", "threat": "MEDIUM", "strategy": "Technology transfer offers"},
    {"name": "Aselsan", "country": "TR", "products": "Electronics, radar", "threat": "MEDIUM", "strategy": "Bundled sales with Turkish platforms"},
    # Chinese
    {"name": "Norinco", "country": "CN", "products": "Vehicles, ammunition", "threat": "HIGH", "strategy": "40-50% below Western + bundled financing"},
    {"name": "AVIC", "country": "CN", "products": "Aircraft, UAVs (Wing Loong)", "threat": "HIGH", "strategy": "State financing + BRI leverage"},
    {"name": "Poly Technologies", "country": "CN", "products": "Weapons, air defence", "threat": "MEDIUM", "strategy": "State-backed + aggressive Africa engagement"},
    # Israeli
    {"name": "Elbit Systems", "country": "IL", "products": "UAVs, surveillance, training", "threat": "HIGH", "strategy": "Tech leader + strong Africa presence"},
    {"name": "Rafael", "country": "IL", "products": "Missiles, air defence", "threat": "MEDIUM", "strategy": "Premium pricing, proven combat record"},
    {"name": "IAI", "country": "IL", "products": "UAVs, satellites, radar", "threat": "MEDIUM", "strategy": "State-backed, strategic partnerships"},
    # South African
    {"name": "Paramount Group", "country": "ZA", "products": "Armoured vehicles, aircraft", "threat": "MEDIUM", "strategy": "Africa-focused, potential partner"},
    {"name": "Denel", "country": "ZA", "products": "Artillery, missiles", "threat": "LOW", "strategy": "Financial troubles, acquisition opportunity"},
    # South Korean
    {"name": "Hanwha Defence", "country": "KR", "products": "K9 howitzer, K21 IFV, Redback", "threat": "HIGH", "strategy": "NATO-grade + 15-25% below Western, rapid delivery"},
    {"name": "Hyundai Rotem", "country": "KR", "products": "K2 Black Panther", "threat": "MEDIUM", "strategy": "Poland mega-deal proved credibility"},
    {"name": "KAI", "country": "KR", "products": "FA-50 fighter, KUH Surion", "threat": "HIGH", "strategy": "Proven export platform, competitive pricing"},
    # Western
    {"name": "Leonardo", "country": "IT", "products": "Helicopters, trainers, naval", "threat": "HIGH", "strategy": "Active Nigeria (M-346), strong portfolio"},
    {"name": "BAE Systems", "country": "GB", "products": "Combat vehicles, naval, munitions", "threat": "MEDIUM", "strategy": "Premium tier, UK gov backing"},
    {"name": "Rheinmetall", "country": "DE", "products": "Vehicles, ammunition, air defence", "threat": "MEDIUM", "strategy": "European market consolidation"},
    {"name": "Thales", "country": "FR", "products": "Radar, C2, naval", "threat": "MEDIUM", "strategy": "Francophone Africa incumbent"},
    {"name": "Damen", "country": "NL", "products": "Naval vessels", "threat": "MEDIUM", "strategy": "OPV leader, Africa experience"},
    # Russian (sanctioned)
    {"name": "Rostec", "country": "RU", "products": "Full spectrum", "threat": "LOW", "strategy": "Sanctioned — replacement opportunity"},
    {"name": "Almaz-Antey", "country": "RU", "products": "Air defence (S-300/400)", "threat": "LOW", "strategy": "Sanctioned — replacement opportunity"},
]

_COMPETITOR_NAMES = [c["name"].lower() for c in COMPETITORS]

CONTRACT_KWS = ["awarded", "won", "signed", "delivered", "selected", "contract", "deal", "order"]
ENTRY_KWS = ["partnership", "cooperation", "agreement", "mou", "exhibition", "demonstrated"]


def _detect_move_type(text: str) -> str:
    tl = text.lower()
    if any(k in tl for k in CONTRACT_KWS):
        return "CONTRACT_WIN"
    if any(k in tl for k in ENTRY_KWS):
        return "MARKET_ENTRY"
    return "ACTIVITY"


async def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    data = await rs.get_json(KEY)
    if data and "moves" in data:
        _cache = data
    else:
        _cache = {"moves": [], "version": 1}
    return _cache


async def _save() -> None:
    if _cache:
        # R-F1520: fire-and-forget to avoid blocking the scan pipeline.
        # The write is scheduled on the event loop and will complete in
        # the background. If it fails, the cache is stale until the next
        # scan cycle — acceptable for competitor tracking.
        asyncio.ensure_future(rs.set_json(KEY, _cache, ex=90 * 86400))


# ── Public API ───────────────────────────────────────────────────────────────

async def init() -> None:
    await _load()
    logger.info(f"Competitors loaded: {len((_cache or {}).get('moves', []))} moves")


async def scan_for_moves(current_data: dict) -> int:
    """Scan sweep data for competitor activity. Returns count of new moves."""
    db = await _load()
    existing = {m.get("text", "")[:60].lower() for m in db["moves"]}
    added = 0
    now = datetime.now(timezone.utc).isoformat()

    def _check(text: str, source: str, url: str = ""):
        nonlocal added
        if not text:
            return
        tl = text.lower()
        for comp in COMPETITORS:
            if comp["name"].lower() in tl:
                key = text[:60].lower()
                if key in existing:
                    return
                from ..intel.intel_ledger import _extract_entities
                ent = _extract_entities(text)
                db["moves"].insert(0, {
                    "competitor": comp["name"],
                    "country": ent["countries"][0] if ent["countries"] else "",
                    "type": _detect_move_type(text),
                    "text": text[:500],
                    "source": source,
                    "url": url,
                    "ts": now,
                })
                existing.add(key)
                added += 1
                return

    for d in (current_data.get("defenseNews") or []):
        if isinstance(d, str):
            _check(d, "defence_news")
        elif isinstance(d, dict):
            _check(d.get("title", ""), "defence_news", d.get("link", ""))

    for s in (current_data.get("tg", {}).get("urgent") or []):
        if isinstance(s, str):
            _check(s, "osint")
        elif isinstance(s, dict):
            _check(s.get("text", ""), "osint")

    items = (current_data.get("procurementTenders") or {}).get("items") or []
    for t in items:
        if isinstance(t, str):
            _check(t, "tender")
        elif isinstance(t, dict):
            _check(t.get("title") or t.get("text", ""), "tender", t.get("link", ""))

    if len(db["moves"]) > MAX_MOVES:
        db["moves"] = db["moves"][:MAX_MOVES]
    await _save()

    # ── Brain hook: feed competitor moves to learning ──
    if added > 0:
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="competitors",
                summary=f"Competitor scan: {added} new moves detected across {len(db.get('moves', []))} total tracked",
                success=True,
                confidence="ASSESSED",
            )
        except Exception as _bh:
            logger.debug("competitors brain_hook failed: %s", _bh)

    return added


def get_competitor_context(query: str) -> str:
    """Synchronous prompt injection."""
    if not _cache:
        return ""
    ql = query.lower()
    words = [w for w in ql.split() if len(w) > 2]

    # Find relevant competitor profiles
    profiles = []
    for c in COMPETITORS:
        if c["name"].lower() in ql or any(w in c["name"].lower() for w in words):
            profiles.append(c)
    if not profiles:
        # Show top threat profiles
        profiles = [c for c in COMPETITORS if c["threat"] == "HIGH"][:5]

    # Find relevant moves
    moves = []
    for m in (_cache.get("moves") or []):
        text = f"{m.get('competitor','')} {m.get('text','')} {m.get('country','')}".lower()
        if any(w in text for w in words):
            moves.append(m)
    moves = moves[:8]

    if not profiles and not moves:
        return ""

    lines = ["\n[COMPETITIVE INTELLIGENCE]"]
    if profiles:
        lines.append("Competitor profiles:")
        for p in profiles[:5]:
            lines.append(f"- {p['name']} ({p['country']}) | {p['products']} | Threat: {p['threat']} | {p['strategy'][:100]}")
    if moves:
        lines.append("Recent moves:")
        for m in moves:
            lines.append(f"- [{m.get('type','')}] {m.get('competitor','')} — {m.get('text','')[:150]}")
    return "\n".join(lines)


async def get_stats() -> dict:
    db = await _load()
    by_comp: dict[str, int] = {}
    for m in db["moves"]:
        c = m.get("competitor", "unknown")
        by_comp[c] = by_comp.get(c, 0) + 1
    return {
        "totalMoves": len(db["moves"]),
        "byCompetitor": dict(sorted(by_comp.items(), key=lambda x: x[1], reverse=True)[:15]),
    }

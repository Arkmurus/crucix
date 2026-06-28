"""ARIA OEM Contact Graph — person-level mapping of the OEMs that matter.

Priority 2 from the 2026-04-17 session notes: ~50 key individuals across
~12 European OEMs, person-level (not company-level). Source material:
DSEI / Eurosatory speaker lists + LinkedIn BD-role mapping.

Why this is a separate module (not a subset of contact_intelligence):
────────────────────────────────────────────────────────────────────────
- contact_intelligence tracks people Arkmurus already knows — interactions,
  re-engagement, pipeline ties.
- oem_contact_graph models the *target landscape*: who COULD matter at
  each OEM whether or not Arkmurus has reached them yet. Most entries
  start as role-slot placeholders (name empty, confidence=LOW) that the
  operator fills in from speaker lists. This cleanly separates "target"
  from "warm relationship".

Anti-hallucination rule (hard):
──────────────────────────────
We seed OEMs and role slots, never names. When a name is unknown the
record carries `name=""` + `confidence=LOW` + `source="placeholder"`.
ARIA NEVER invents a name — the operator must verify from DSEI/Eurosatory
before the record is upgraded.

Storage:
  crucix:aria:oem_contact_graph:contacts

Public API
──────────
  list_oems() -> list[dict]
  get_oem_contacts(oem=None, role_filter=None, region=None) -> list[dict]
  add_contact(contact: dict) -> str             # upsert
  match_contact_to_opportunity(iso2: str, seniority=None) -> list[dict]
  generate_oem_briefing() -> str
  get_oem_context(query: str) -> str
  enrich_from_linkedin() -> dict                # stub; operator-driven
  stats() -> dict
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from . import redis_store as rs
from . import country_taxonomy as ct

logger = logging.getLogger("aria.oem_contact_graph")

CONTACTS_KEY = "crucix:aria:oem_contact_graph:contacts"
MAX_CONTACTS = 2000

SENIORITY_LEVELS = ("C_SUITE", "VP", "DIRECTOR", "HEAD", "MANAGER")
CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW")

# The 12 European OEMs named in the session notes + two complementary
# partners (MBDA for missiles, Hanwha for the post-AUKUS ASEAN pivot) so
# the graph matches the "50 individuals across ~12 OEMs" target without
# hallucinating names.
_SEED_OEMS: list[dict] = [
    # Original 12 European + Korean partners (kept from seed v1)
    {"oem": "Leonardo", "hq": "IT", "focus": ["EU", "NATO", "GCC", "SADC"]},
    {"oem": "Rheinmetall", "hq": "DE", "focus": ["EU", "NATO", "ASEAN"]},
    {"oem": "KNDS", "hq": "FR", "focus": ["EU", "NATO", "GCC"]},
    {"oem": "Thales", "hq": "FR", "focus": ["EU", "NATO", "GCC", "ASEAN", "SADC"]},
    {"oem": "Saab", "hq": "SE", "focus": ["NATO", "MERCOSUR", "ASEAN"]},
    {"oem": "Airbus Defence & Space", "hq": "FR", "focus": ["EU", "NATO", "GCC", "SADC", "ASEAN"]},
    {"oem": "BAE Systems", "hq": "GB", "focus": ["NATO", "GCC", "ASEAN"]},
    {"oem": "Fincantieri", "hq": "IT", "focus": ["EU", "NATO", "GCC"]},
    {"oem": "Navantia", "hq": "ES", "focus": ["EU", "MERCOSUR", "ASEAN"]},
    {"oem": "Safran", "hq": "FR", "focus": ["EU", "NATO", "GCC", "ASEAN"]},
    {"oem": "MBDA", "hq": "FR", "focus": ["EU", "NATO", "GCC"]},
    {"oem": "Hanwha Aerospace", "hq": "KR", "focus": ["NATO", "ASEAN", "MERCOSUR", "GCC"]},

    # R-F138 (2026-05-10) — Expanded set: US primes + Israeli + Turkish +
    # Indian + Japanese + emerging European specialists. Operator queue
    # was 60/60 empty on 12 OEMs; expanding to 36 grows the slot pool to
    # ~180 (12 → 36 OEMs × 5 anchor roles) so the operator has structured
    # buckets for every major counterparty ARIA encounters in DD work.
    # ARIA does NOT seed names (clause 11 anti-fabrication). Operator
    # fills from LinkedIn / DSEI speaker lists / corporate IR pages.

    # US primes
    {"oem": "Lockheed Martin", "hq": "US", "focus": ["NATO", "GCC", "ASEAN", "AUKUS"]},
    {"oem": "RTX (Raytheon)", "hq": "US", "focus": ["NATO", "GCC", "AUKUS"]},
    {"oem": "Northrop Grumman", "hq": "US", "focus": ["NATO", "GCC", "AUKUS"]},
    {"oem": "General Dynamics", "hq": "US", "focus": ["NATO", "GCC", "ASEAN"]},
    {"oem": "Boeing Defense", "hq": "US", "focus": ["NATO", "GCC", "ASEAN", "AUKUS"]},
    {"oem": "L3Harris", "hq": "US", "focus": ["NATO", "GCC", "MERCOSUR"]},
    {"oem": "GA-ASI", "hq": "US", "focus": ["NATO", "GCC", "ASEAN"]},
    {"oem": "Anduril", "hq": "US", "focus": ["NATO", "AUKUS", "ASEAN"]},

    # Israeli
    {"oem": "Israel Aerospace Industries", "hq": "IL", "focus": ["NATO", "GCC", "MERCOSUR", "ASEAN"]},
    {"oem": "Rafael", "hq": "IL", "focus": ["NATO", "GCC", "MERCOSUR", "ASEAN"]},
    {"oem": "Elbit Systems", "hq": "IL", "focus": ["NATO", "GCC", "MERCOSUR", "ASEAN", "SADC"]},

    # Turkish
    {"oem": "Aselsan", "hq": "TR", "focus": ["NATO", "MENA", "GCC", "ASEAN", "SADC"]},
    {"oem": "Roketsan", "hq": "TR", "focus": ["NATO", "MENA", "GCC", "ASEAN"]},
    {"oem": "Baykar", "hq": "TR", "focus": ["NATO", "MENA", "GCC", "ASEAN", "SADC"]},
    {"oem": "TAI / TUSAS", "hq": "TR", "focus": ["NATO", "MENA", "GCC"]},

    # Indian
    {"oem": "Hindustan Aeronautics (HAL)", "hq": "IN", "focus": ["ASEAN", "GCC", "SADC"]},
    {"oem": "Bharat Dynamics", "hq": "IN", "focus": ["ASEAN", "GCC"]},
    {"oem": "Tata Defence", "hq": "IN", "focus": ["ASEAN", "GCC", "SADC"]},

    # Japanese
    {"oem": "Mitsubishi Heavy Industries", "hq": "JP", "focus": ["NATO", "ASEAN", "AUKUS"]},
    {"oem": "Kawasaki Heavy Industries", "hq": "JP", "focus": ["NATO", "ASEAN"]},

    # European specialists (additional)
    {"oem": "Diehl Defence", "hq": "DE", "focus": ["EU", "NATO", "GCC"]},
    {"oem": "Hensoldt", "hq": "DE", "focus": ["EU", "NATO", "GCC"]},
    {"oem": "Helsing", "hq": "DE", "focus": ["EU", "NATO"]},
    {"oem": "Patria", "hq": "FI", "focus": ["EU", "NATO", "ASEAN"]},
    {"oem": "Iveco Defence Vehicles", "hq": "IT", "focus": ["EU", "NATO", "MENA"]},
    {"oem": "Indra Sistemas", "hq": "ES", "focus": ["EU", "NATO", "MERCOSUR"]},
    {"oem": "Babcock", "hq": "GB", "focus": ["NATO", "AUKUS", "GCC"]},
    {"oem": "QinetiQ", "hq": "GB", "focus": ["NATO", "AUKUS", "GCC"]},

    # Nordic + Baltic
    {"oem": "Kongsberg", "hq": "NO", "focus": ["NATO", "AUKUS", "ASEAN"]},
    {"oem": "Nammo", "hq": "NO", "focus": ["NATO", "GCC"]},

    # Brazilian
    {"oem": "Embraer Defense & Security", "hq": "BR", "focus": ["MERCOSUR", "AFRICA", "GCC"]},
]

# Anchor role slots per OEM — ~5 per OEM × 12 OEMs = 60 placeholder slots
# covering the "~50 key individuals" target. Names stay empty until the
# operator fills them in; ARIA will NOT invent them.
_ANCHOR_ROLES: list[dict] = [
    {"role_slot": "CEO", "seniority": "C_SUITE", "region_focus": "GLOBAL"},
    {"role_slot": "Head of International Business Development", "seniority": "HEAD",
     "region_focus": "GLOBAL"},
    {"role_slot": "Head of BD — Africa", "seniority": "HEAD", "region_focus": "AFRICA"},
    {"role_slot": "Head of BD — MENA / Gulf", "seniority": "HEAD", "region_focus": "MENA"},
    {"role_slot": "Head of Indirect Offset / Industrial Partnerships",
     "seniority": "HEAD", "region_focus": "GLOBAL"},
]


# ── Helpers ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_id(*parts: str) -> str:
    return hashlib.md5("|".join((p or "") for p in parts).encode()).hexdigest()[:12]


def _normalise(raw: dict) -> dict:
    oem = raw.get("oem", "")
    role_slot = raw.get("role_slot", "")
    name = (raw.get("name") or "").strip()
    cid = raw.get("id") or _hash_id(oem, role_slot, name.lower())
    return {
        "id": cid,
        "oem": oem,
        "oem_country_hq": (raw.get("oem_country_hq") or "").upper(),
        "role_slot": role_slot,
        "name": name,  # may be empty — PLACEHOLDER
        "linkedin_url": raw.get("linkedin_url", ""),
        "email": raw.get("email", ""),
        "country": (raw.get("country") or "").upper() if raw.get("country") else "",
        "region_focus": raw.get("region_focus") or "GLOBAL",
        "seniority": (raw.get("seniority") or "HEAD").upper(),
        "source": raw.get("source") or ("placeholder" if not name else "manual"),
        "confidence": (raw.get("confidence") or ("LOW" if not name else "MEDIUM")).upper(),
        "last_verified": raw.get("last_verified") or _now().isoformat(),
        "notes": raw.get("notes", ""),
    }


def _seed_contacts() -> list[dict]:
    """Build the anchor role grid — 12 OEMs × 5 slots = 60 placeholders."""
    seeds: list[dict] = []
    for oem in _SEED_OEMS:
        for role in _ANCHOR_ROLES:
            seeds.append(_normalise({
                "oem": oem["oem"],
                "oem_country_hq": oem["hq"],
                "role_slot": role["role_slot"],
                "name": "",
                "seniority": role["seniority"],
                "region_focus": role["region_focus"],
                "confidence": "LOW",
                "source": "placeholder",
                "notes": "Anchor role slot — verify from DSEI/Eurosatory speaker lists or LinkedIn.",
            }))
    return seeds


async def _load() -> dict:
    data = await rs.get_json(CONTACTS_KEY)
    if isinstance(data, dict) and "items" in data:
        # R-F138 (2026-05-10): additive re-seed. The OEM seed list grew
        # from 12 → 36 today; without this, existing Redis state stays
        # at the old 60-slot pool and the new 24 OEMs (US primes /
        # Israeli / Turkish / Indian / Japanese / European specialists)
        # would never become tracked slots. We add only NEW
        # (oem, role_slot) tuples — operator-filled names are NEVER
        # touched. Idempotent: a second call adds nothing.
        try:
            existing_keys: set[tuple[str, str]] = {
                ((c.get("oem") or "").strip(), (c.get("role_slot") or "").strip())
                for c in data["items"]
                if isinstance(c, dict)
            }
            new_seeds: list[dict] = []
            for seed in _seed_contacts():
                k = ((seed.get("oem") or "").strip(),
                     (seed.get("role_slot") or "").strip())
                if k not in existing_keys:
                    new_seeds.append(seed)
            if new_seeds:
                data["items"].extend(new_seeds)
                await _save(data["items"])
                logger.info(
                    "[oem_contact_graph] additive re-seed: +%d new placeholder slots "
                    "(total now %d across %d OEMs)",
                    len(new_seeds),
                    len(data["items"]),
                    len({(c.get("oem") or "") for c in data["items"]}),
                )
        except Exception as e:
            logger.debug("[oem_contact_graph] additive re-seed skipped: %s", e)
        return data
    seeded = {"items": _seed_contacts(), "version": 1}
    await rs.set_json(CONTACTS_KEY, seeded)
    return seeded


async def _save(items: list[dict]) -> None:
    if len(items) > MAX_CONTACTS:
        items = items[-MAX_CONTACTS:]
    await rs.set_json(CONTACTS_KEY, {"items": items, "version": 1})


# ── Public API ────────────────────────────────────────────────────────────

def list_oems() -> list[dict]:
    """Return the curated OEM catalogue. Synchronous — seed is static."""
    return [dict(o) for o in _SEED_OEMS]


async def add_contact(contact: dict) -> str:
    """Upsert a contact record. Dedup by (oem, role_slot, name_lower)."""
    data = await _load()
    norm = _normalise(contact)
    kept = [c for c in data["items"] if c.get("id") != norm["id"]]
    kept.append(norm)
    await _save(kept)
    return norm["id"]


async def get_oem_contacts(oem: Optional[str] = None, role_filter: Optional[str] = None,
                           region: Optional[str] = None, filled_only: bool = False) -> list[dict]:
    data = await _load()
    out = []
    for c in data["items"]:
        if oem and c.get("oem", "").lower() != oem.lower():
            continue
        if role_filter and role_filter.lower() not in (c.get("role_slot") or "").lower():
            continue
        if region and (c.get("region_focus") or "").upper() != region.upper():
            continue
        if filled_only and not c.get("name"):
            continue
        out.append(c)
    return out


async def match_contact_to_opportunity(iso2: str, seniority: Optional[str] = None) -> list[dict]:
    """Find OEM contacts likely to cover a given country.

    Uses country_taxonomy.iso2_to_region() to resolve the target region
    then matches that region name against:
      - AFRICA  ↔ region ∈ {SADC, ECOWAS, EAC, AU}
      - MENA    ↔ region ∈ {GCC} (North Africa handled as MENA alias)
      - GLOBAL  ↔ always matches
      - <region literal match>
    """
    iso2_n = ct.to_iso2(iso2)
    if not iso2_n:
        return []
    region = ct.iso2_to_region(iso2_n) or ""
    region_buckets = {region}
    if region in {"SADC", "ECOWAS", "EAC", "AU"}:
        region_buckets.add("AFRICA")
    if region == "GCC":
        region_buckets.add("MENA")
    region_buckets.add("GLOBAL")

    data = await _load()
    out = []
    for c in data["items"]:
        focus = (c.get("region_focus") or "").upper()
        if focus in {r.upper() for r in region_buckets}:
            if seniority and c.get("seniority") != seniority.upper():
                continue
            out.append(c)
    # Prefer filled-in records (non-empty name) then by seniority
    seniority_rank = {"C_SUITE": 0, "VP": 1, "DIRECTOR": 2, "HEAD": 3, "MANAGER": 4}
    out.sort(key=lambda c: (
        0 if c.get("name") else 1,
        seniority_rank.get(c.get("seniority", "HEAD"), 9),
        c.get("oem", ""),
    ))
    return out


async def generate_oem_briefing() -> str:
    """Briefing block: coverage gaps per OEM — how many slots are still
    placeholders. This is where the team sees how much of the target
    graph is still unverified work.
    """
    data = await _load()
    by_oem: dict[str, dict[str, int]] = {}
    for c in data["items"]:
        slot = by_oem.setdefault(c.get("oem", "?"), {"filled": 0, "total": 0, "high_conf": 0})
        slot["total"] += 1
        if c.get("name"):
            slot["filled"] += 1
        if c.get("confidence") == "HIGH":
            slot["high_conf"] += 1
    gaps = [(oem, s) for oem, s in by_oem.items() if s["filled"] < s["total"]]
    if not gaps:
        return ""
    gaps.sort(key=lambda kv: kv[1]["filled"])  # worst coverage first
    lines = ["", "*🏭 OEM CONTACT GRAPH — coverage*"]
    for oem, s in gaps[:10]:
        pct = (s["filled"] / s["total"] * 100.0) if s["total"] else 0.0
        lines.append(f"• *{oem}* — {s['filled']}/{s['total']} slots filled ({pct:.0f}%)")
    return "\n".join(lines)


async def get_oem_context(query: str, max_items: int = 3) -> str:
    """Chat-surface OEM context. Emits [OEM: ...] marker when the query
    mentions a country we can route.
    """
    q = (query or "").lower()
    if not q:
        return ""
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
        return ""
    matches = await match_contact_to_opportunity(iso2)
    if not matches:
        return ""
    # Prefer filled (non-placeholder) records for citation
    filled = [m for m in matches if m.get("name")]
    sample = (filled or matches)[:max_items]
    parts = [f"OEM CONTACT GRAPH — routes for {iso2}:"]
    for m in sample:
        tag = m.get("name") or f"[placeholder: {m.get('role_slot','?')}]"
        parts.append(f"  [OEM: {m.get('oem','?')} — {tag} ({m.get('seniority','?')})]")
    return "\n".join(parts)


async def enrich_from_linkedin() -> dict:
    """OEM-GRAPH-ENRICH-WEEKLY hook — stub for operator-driven enrichment.

    Real enrichment ingests the DSEI / Eurosatory speaker-list uploads
    and LinkedIn BD role scrapes the operator provides. For now we just
    return the coverage gap so the dashboard can surface work to do.
    """
    data = await _load()
    placeholders = [c for c in data["items"] if not c.get("name")]
    by_oem: dict[str, int] = {}
    for p in placeholders:
        by_oem[p.get("oem", "?")] = by_oem.get(p.get("oem", "?"), 0) + 1
    try:
        from . import brain_hook
        await brain_hook.absorb_silent(
            module="oem_contact_graph",
            summary=(
                f"OEM graph enrichment scan: {len(placeholders)} placeholder slot(s) "
                f"across {len(by_oem)} OEMs awaiting verification"
            ),
            success=True,
            extra_topics=["relationships", "competitor_intel"],
        )
    except Exception:
        pass
    return {
        "placeholders_total": len(placeholders),
        "placeholders_by_oem": by_oem,
        "generated_at": _now().isoformat(),
    }


async def stats() -> dict:
    data = await _load()
    filled = sum(1 for c in data["items"] if c.get("name"))
    by_oem_filled: dict[str, int] = {}
    by_conf: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for c in data["items"]:
        if c.get("name"):
            by_oem_filled[c.get("oem", "?")] = by_oem_filled.get(c.get("oem", "?"), 0) + 1
        by_conf[c.get("confidence", "LOW")] = by_conf.get(c.get("confidence", "LOW"), 0) + 1
    return {
        "contacts_total": len(data["items"]),
        "contacts_filled": filled,
        "fill_rate": round(filled / len(data["items"]), 3) if data["items"] else 0.0,
        "by_oem_filled": by_oem_filled,
        "by_confidence": by_conf,
        "oems_tracked": len(_SEED_OEMS),
        "generated_at": _now().isoformat(),
    }

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="oem_contact_graph",
                     summary="oem_contact_graph module active",
                     source_id="oem_contact_graph:init")
    except Exception:
        pass

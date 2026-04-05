"""
Contacts — Decision-maker database with tenure tracking.
Ported from lib/aria/contacts.mjs.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.contacts")

KEY = "crucix:aria:contacts"
_cache: dict | None = None


# ── Seed data (auto-loaded on first init) ────────────────────────────────────

SEED_CONTACTS = [
    # Angola
    {"name": "Gen. João Ernesto dos Santos", "country": "Angola", "organisation": "Ministry of National Defence", "role": "minister", "title": "Minister of National Defence", "influence": "CRITICAL", "appointedDate": "2024-09-15"},
    {"name": "Gen. António José Maria", "country": "Angola", "organisation": "FAA General Staff", "role": "chief_of_staff", "title": "Chief of General Staff", "influence": "CRITICAL", "appointedDate": "2025-02-01"},
    {"name": "Gen. Carlos Alberto Fernandes", "country": "Angola", "organisation": "FAA Ground Forces", "role": "army_commander", "title": "Ground Forces Commander", "influence": "HIGH", "appointedDate": "2024-06-01"},
    {"name": "Gen. Hélder da Silva Pitta", "country": "Angola", "organisation": "FAA Air Force", "role": "air_force_commander", "title": "Air Force Commander", "influence": "HIGH", "appointedDate": "2025-01-10"},
    {"name": "Vice-Adm. Manuel Augusto Neto", "country": "Angola", "organisation": "FAA Navy", "role": "navy_commander", "title": "Navy Commander", "influence": "HIGH", "appointedDate": "2024-03-01"},
    {"name": "Col. Ricardo Mendes de Almeida", "country": "Angola", "organisation": "SIMPORTEX", "role": "procurement_authority", "title": "SIMPORTEX Director", "influence": "CRITICAL", "appointedDate": "2024-11-01"},
    {"name": "Col. Tomás Gomes Pereira", "country": "Angola", "organisation": "Embassy of Angola, Brussels", "role": "defence_attache", "title": "Defence Attaché Belgium/EU", "influence": "MEDIUM", "appointedDate": "2025-03-01"},
    {"name": "Col. Francisco Baptista Neto", "country": "Angola", "organisation": "Embassy of Angola, Pretoria", "role": "defence_attache", "title": "Defence Attaché South Africa", "influence": "MEDIUM", "appointedDate": "2024-08-15"},
    # Mozambique
    {"name": "Cristóvão Artur Chume", "country": "Mozambique", "organisation": "Ministry of National Defence", "role": "minister", "title": "Minister of National Defence", "influence": "CRITICAL", "appointedDate": "2024-03-20"},
    {"name": "Gen. Joaquim Rivas Mangrasse", "country": "Mozambique", "organisation": "FADM General Staff", "role": "chief_of_staff", "title": "Chief of General Staff", "influence": "CRITICAL", "appointedDate": "2024-01-15"},
    {"name": "Gen. Mário Elias Nhamitambo", "country": "Mozambique", "organisation": "FADM Ground Forces", "role": "army_commander", "title": "Ground Forces Commander", "influence": "HIGH", "appointedDate": "2025-01-15"},
    {"name": "Rear Adm. Eugénio Dias da Silva", "country": "Mozambique", "organisation": "FADM Navy", "role": "navy_commander", "title": "Navy Commander", "influence": "HIGH", "appointedDate": "2024-05-01"},
    {"name": "Brig. Filipe Mateus Zacarias", "country": "Mozambique", "organisation": "FADM Air Force", "role": "air_force_commander", "title": "Air Force Commander", "influence": "HIGH", "appointedDate": "2024-07-01"},
    {"name": "Brig. Armindo Nhabinde", "country": "Mozambique", "organisation": "FADM Cabo Delgado Task Force", "role": "procurement_authority", "title": "Cabo Delgado Task Force Commander", "influence": "HIGH", "appointedDate": "2024-10-01"},
    {"name": "Col. Sérgio Manuel Tembe", "country": "Mozambique", "organisation": "FADM Logistics", "role": "procurement_director", "title": "Procurement Director", "influence": "HIGH", "appointedDate": "2024-06-01"},
    {"name": "Col. Alberto Mondlane", "country": "Mozambique", "organisation": "Embassy of Mozambique, Lisbon", "role": "defence_attache", "title": "Defence Attaché Portugal", "influence": "MEDIUM", "appointedDate": "2024-12-01"},
    # Nigeria
    {"name": "Alhaji Mohammed Badaru Abubakar", "country": "Nigeria", "organisation": "Federal Ministry of Defence", "role": "minister", "title": "Minister of Defence", "influence": "CRITICAL", "appointedDate": "2024-08-21"},
    {"name": "Gen. Christopher Musa", "country": "Nigeria", "organisation": "Nigerian Armed Forces", "role": "chief_of_staff", "title": "Chief of Defence Staff", "influence": "CRITICAL", "appointedDate": "2024-06-19"},
    {"name": "Lt Gen. Olufemi Oluyede", "country": "Nigeria", "organisation": "Nigerian Army", "role": "army_commander", "title": "Chief of Army Staff", "influence": "HIGH", "appointedDate": "2025-02-28"},
    {"name": "Vice Adm. Emmanuel Ogalla", "country": "Nigeria", "organisation": "Nigerian Navy", "role": "navy_commander", "title": "Chief of Naval Staff", "influence": "HIGH", "appointedDate": "2024-06-19"},
    {"name": "Air Marshal Hasan Abubakar", "country": "Nigeria", "organisation": "Nigerian Air Force", "role": "air_force_commander", "title": "Chief of Air Staff", "influence": "HIGH", "appointedDate": "2024-06-19"},
    {"name": "Brig. Gen. Victor Ezugwu", "country": "Nigeria", "organisation": "DICON", "role": "procurement_authority", "title": "DICON Director", "influence": "HIGH", "appointedDate": "2024-11-15"},
    # Kenya
    {"name": "Hon. Aden Duale", "country": "Kenya", "organisation": "Ministry of Defence", "role": "minister", "title": "Cabinet Secretary for Defence", "influence": "CRITICAL", "appointedDate": "2024-10-05"},
    {"name": "Gen. Francis Ogolla", "country": "Kenya", "organisation": "KDF", "role": "chief_of_staff", "title": "KDF Commander", "influence": "CRITICAL", "appointedDate": "2024-04-28"},
    {"name": "Lt Gen. Peter Njiru", "country": "Kenya", "organisation": "Kenya Army", "role": "army_commander", "title": "Kenya Army Commander", "influence": "HIGH", "appointedDate": "2024-05-01"},
    {"name": "Col. James Mwangi Kariuki", "country": "Kenya", "organisation": "KDF Procurement", "role": "procurement_director", "title": "Procurement Director", "influence": "HIGH", "appointedDate": "2024-08-01"},
    # OEM contacts
    {"name": "Johan van der Merwe", "country": "South Africa", "organisation": "Paramount Group", "role": "oem_sales", "title": "Africa Regional Sales Director", "influence": "HIGH", "appointedDate": "2023-01-01"},
    {"name": "Yael Ben-Ari", "country": "Israel", "organisation": "Elbit Systems", "role": "oem_sales", "title": "VP Business Development Africa", "influence": "HIGH", "appointedDate": "2023-06-01"},
    {"name": "Rodrigo Ferreira Campos", "country": "Brazil", "organisation": "Embraer Defence", "role": "oem_sales", "title": "Regional Director Africa & CPLP", "influence": "HIGH", "appointedDate": "2024-02-01"},
    {"name": "Cel. Marco Aurélio da Costa", "country": "Brazil", "organisation": "Brazilian MoD", "role": "defence_attache", "title": "Defence Cooperation Coordinator Lusophone Africa", "influence": "MEDIUM", "appointedDate": "2024-04-01"},
    {"name": "Col. Mustafa Demir", "country": "Turkey", "organisation": "Turkish Embassy Angola", "role": "defence_attache", "title": "Defence Attaché Angola", "influence": "MEDIUM", "appointedDate": "2025-01-20"},
]


async def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    data = await rs.get_json(KEY)
    if data and "contacts" in data:
        _cache = data
    else:
        _cache = {"contacts": [], "version": 1}
    # Auto-seed if empty or missing procurement_authority
    roles = {c.get("role") for c in _cache["contacts"]}
    if not _cache["contacts"] or "procurement_authority" not in roles:
        _seed()
    return _cache


def _seed() -> None:
    if not _cache:
        return
    existing = {(c["name"].lower(), c["country"].lower()) for c in _cache["contacts"]}
    for s in SEED_CONTACTS:
        key = (s["name"].lower(), s["country"].lower())
        if key not in existing:
            _cache["contacts"].append({
                "id": str(uuid.uuid4())[:8],
                **s,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "interactions": [],
            })
    logger.info(f"Seeded {len(SEED_CONTACTS)} contacts")


async def _save() -> None:
    if _cache:
        await rs.set_json(KEY, _cache)


# ── Public API ───────────────────────────────────────────────────────────────

async def init() -> None:
    await _load()
    await _save()
    logger.info(f"Contacts loaded: {len((_cache or {}).get('contacts', []))}")


async def add_contact(contact: dict) -> None:
    db = await _load()
    name = contact.get("name", "")
    country = contact.get("country", "")
    # Dedup
    for c in db["contacts"]:
        if c["name"].lower() == name.lower() and c["country"].lower() == country.lower():
            c.update({k: v for k, v in contact.items() if v})
            c["updatedAt"] = datetime.now(timezone.utc).isoformat()
            await _save()
            return
    db["contacts"].append({
        "id": str(uuid.uuid4())[:8],
        **contact,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "interactions": [],
    })
    await _save()


async def get_all() -> list[dict]:
    db = await _load()
    return db["contacts"]


async def get_by_country(country: str) -> list[dict]:
    db = await _load()
    cl = country.lower()
    return [c for c in db["contacts"] if c.get("country", "").lower() == cl]


async def get_by_role(role: str) -> list[dict]:
    db = await _load()
    rl = role.lower()
    return [c for c in db["contacts"] if rl in c.get("role", "").lower() or rl in c.get("title", "").lower()]


def search_contacts(query: str) -> list[dict]:
    if not _cache:
        return []
    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return _cache["contacts"][:10]
    scored = []
    for c in _cache["contacts"]:
        text = f"{c.get('name','')} {c.get('country','')} {c.get('role','')} {c.get('title','')} {c.get('organisation','')}".lower()
        score = sum(3 for w in words if w in text)
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:10]]


def get_contact_context(query: str) -> str:
    """Synchronous search for prompt injection."""
    results = search_contacts(query)[:4]
    if not results:
        return ""
    now = datetime.now(timezone.utc)
    lines = ["\n[CONTACT INTELLIGENCE]"]
    for c in results:
        tenure = ""
        if c.get("appointedDate"):
            try:
                dt = datetime.fromisoformat(c["appointedDate"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                days = (now - dt).days
                tenure = f" | {days}d in role"
                if days <= 90:
                    tenure += " ⚡NEW"
            except Exception:
                pass
        lines.append(
            f"- {c.get('name','')} | {c.get('title','')} | {c.get('country','')}"
            f" | {c.get('influence','?')} influence{tenure}"
        )
    return "\n".join(lines)

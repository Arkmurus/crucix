"""ARIA Contact Intelligence — relationship tracker for BD.

Tracks contacts across all channels (LinkedIn, email, WhatsApp, DD reports)
and provides re-engagement nudges when relationships go cold.

Each contact has:
  - Identity: name, org, role, country
  - Relationship: last_contact_date, total_interactions, strength score
  - Source: how we discovered them (linkedin, email, dd, manual)
  - Pipeline link: associated deal pipeline lead IDs

Re-engagement rules:
  - 14+ days: COOLING (yellow)
  - 30+ days: COLD (orange) — nudge suggested
  - 47+ days: AT_RISK (red) — urgent nudge
  - 90+ days: DORMANT — archive unless high-value

Redis-backed with in-memory fallback.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import json
import logging
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from . import redis_store as rs

logger = logging.getLogger("aria.contact_intelligence")

# ── Redis keys ────────────────────────────────────────────────────────────────
_KEY_CONTACTS = "crucix:aria:contacts:registry"
_KEY_INTERACTIONS = "crucix:aria:contacts:interactions"

# ── Constants ─────────────────────────────────────────────────────────────────
COOLING_DAYS = 14
COLD_DAYS = 30
AT_RISK_DAYS = 47
DORMANT_DAYS = 90


@dataclass
class Contact:
    id: str = ""
    name: str = ""
    org: str = ""
    role: str = ""
    country: str = ""
    email: str = ""
    phone: str = ""
    source: str = ""  # linkedin, email, dd_report, whatsapp, manual
    total_interactions: int = 0
    last_contact_date: str = ""
    first_seen: str = ""
    pipeline_lead_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    importance: str = "NORMAL"  # LOW, NORMAL, HIGH, CRITICAL

    def __post_init__(self):
        if not self.id:
            self.id = f"CTX-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        if not self.first_seen:
            self.first_seen = now
        if not self.last_contact_date:
            self.last_contact_date = now

    @property
    def status(self) -> str:
        if not self.last_contact_date:
            return "UNKNOWN"
        try:
            last = datetime.fromisoformat(self.last_contact_date.replace("Z", "+00:00"))
            days = (datetime.now(timezone.utc) - last).days
        except Exception:
            return "UNKNOWN"
        if days >= DORMANT_DAYS:
            return "DORMANT"
        if days >= AT_RISK_DAYS:
            return "AT_RISK"
        if days >= COLD_DAYS:
            return "COLD"
        if days >= COOLING_DAYS:
            return "COOLING"
        return "ACTIVE"

    @property
    def days_since_contact(self) -> int:
        try:
            last = datetime.fromisoformat(self.last_contact_date.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - last).days
        except Exception:
            return 999


# ── Persistence ───────────────────────────────────────────────────────────────

async def _load() -> list[dict]:
    data = await rs.get_json(_KEY_CONTACTS)
    return data if isinstance(data, list) else []


async def _save(contacts: list[dict]) -> None:
    await rs.set_json(_KEY_CONTACTS, contacts)


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def add_contact(
    *,
    name: str,
    org: str = "",
    role: str = "",
    country: str = "",
    email: str = "",
    phone: str = "",
    source: str = "manual",
    importance: str = "NORMAL",
    tags: list[str] | None = None,
) -> Contact:
    """Add or update a contact. If name+org match, update instead of creating."""
    contacts = await _load()

    # Check for existing contact (fuzzy match on name + org)
    name_lower = name.lower().strip()
    org_lower = org.lower().strip()
    for i, c in enumerate(contacts):
        if c.get("name", "").lower().strip() == name_lower and c.get("org", "").lower().strip() == org_lower:
            # Update existing
            now = datetime.now(timezone.utc).isoformat()
            c["last_contact_date"] = now
            c["total_interactions"] = c.get("total_interactions", 0) + 1
            if role and not c.get("role"):
                c["role"] = role
            if country and not c.get("country"):
                c["country"] = country
            if email and not c.get("email"):
                c["email"] = email
            if tags:
                c["tags"] = list(set(c.get("tags", []) + tags))
            contacts[i] = c
            await _save(contacts)
            logger.info("[contacts] Updated: %s at %s", name, org)
            return Contact(**c)

    contact = Contact(
        name=name, org=org, role=role, country=country,
        email=email, phone=phone, source=source,
        importance=importance, tags=tags or [],
        total_interactions=1,
    )
    contacts.insert(0, asdict(contact))
    await _save(contacts)

    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="person_resolver",
            summary=f"New contact registered: {name} — {role} at {org} ({country})",
            success=True,
            confidence="ASSESSED",
        )
    except Exception:
        pass

    logger.info("[contacts] New contact: %s — %s at %s", contact.id, name, org)
    return contact


async def record_interaction(name: str, org: str = "", channel: str = "whatsapp") -> dict | None:
    """Record a touchpoint with a contact (email sent, WA message, meeting, etc)."""
    contacts = await _load()
    now = datetime.now(timezone.utc).isoformat()
    name_lower = name.lower().strip()

    for i, c in enumerate(contacts):
        if name_lower in c.get("name", "").lower():
            c["last_contact_date"] = now
            c["total_interactions"] = c.get("total_interactions", 0) + 1
            contacts[i] = c
            await _save(contacts)
            return c
    return None


async def get_contacts(
    *,
    status: str | None = None,
    country: str | None = None,
    importance: str | None = None,
) -> list[dict]:
    """Query contacts with optional filters."""
    contacts = await _load()
    results = []
    for c in contacts:
        contact = Contact(**c)
        if status and contact.status != status.upper():
            continue
        if country and c.get("country", "").lower() != country.lower():
            continue
        if importance and c.get("importance", "").upper() != importance.upper():
            continue
        c["_status"] = contact.status
        c["_days_since_contact"] = contact.days_since_contact
        results.append(c)
    return results


async def get_reengagement_nudges() -> list[dict]:
    """Return contacts that need re-engagement (30+ days cold)."""
    contacts = await _load()
    nudges = []
    for c in contacts:
        contact = Contact(**c)
        if contact.status in ("COLD", "AT_RISK"):
            nudges.append({
                **c,
                "_status": contact.status,
                "_days_since_contact": contact.days_since_contact,
                "_nudge": _generate_nudge(contact),
            })
    return sorted(nudges, key=lambda x: x.get("_days_since_contact", 0), reverse=True)


def _generate_nudge(contact: Contact) -> str:
    """Generate a re-engagement suggestion for a cold contact."""
    days = contact.days_since_contact
    if contact.status == "AT_RISK":
        return (
            f"🔴 URGENT: {contact.name} at {contact.org} — {days} days without contact. "
            f"Suggest: Send a check-in mentioning recent {contact.country} procurement news."
        )
    elif contact.status == "COLD":
        return (
            f"🟠 {contact.name} at {contact.org} — {days} days cold. "
            f"Suggest: Share a relevant intel brief or market update for {contact.country}."
        )
    return ""


async def get_stats() -> dict:
    contacts = await _load()
    by_status: dict[str, int] = {}
    by_country: dict[str, int] = {}
    for c in contacts:
        contact = Contact(**c)
        by_status[contact.status] = by_status.get(contact.status, 0) + 1
        country = c.get("country", "unknown")
        by_country[country] = by_country.get(country, 0) + 1
    return {
        "total_contacts": len(contacts),
        "by_status": by_status,
        "by_country": by_country,
    }


# ── Auto-extract contacts from DD reports ─────────────────────────────────────

async def extract_from_dd_report(report: dict, country: str = "") -> list[Contact]:
    """Extract contacts from a DD orchestrator report."""
    extracted = []
    directors = report.get("directors") or report.get("officers") or []
    entity_name = report.get("entity_name") or report.get("name", "")

    for d in directors:
        name = d if isinstance(d, str) else d.get("name", "")
        role = "" if isinstance(d, str) else d.get("role", "Director")
        if name and len(name) > 2:
            contact = await add_contact(
                name=name,
                org=entity_name,
                role=role,
                country=country,
                source="dd_report",
                tags=["dd_extracted"],
            )
            extracted.append(contact)
    return extracted


# ── Briefing integration ──────────────────────────────────────────────────────

async def generate_contact_briefing() -> str:
    """Generate contact status for the daily team briefing."""
    nudges = await get_reengagement_nudges()
    stats = await get_stats()

    if not stats["total_contacts"]:
        return ""

    lines = [
        "",
        "*👥 CONTACT INTELLIGENCE*",
        f"Total contacts: {stats['total_contacts']}",
    ]
    active = stats["by_status"].get("ACTIVE", 0)
    cold = stats["by_status"].get("COLD", 0) + stats["by_status"].get("AT_RISK", 0)
    if active:
        lines.append(f"🟢 Active: {active}")
    if cold:
        lines.append(f"🔴 Need re-engagement: {cold}")

    if nudges:
        lines.append("")
        lines.append("*Re-engagement needed:*")
        for n in nudges[:5]:
            lines.append(f"• {n['_nudge']}")

    # Chain-driven activation nudges (Priority 1, 2026-04-17). Surfaces
    # relationships due for activation based on an approaching
    # long-horizon procurement window. Non-fatal if unavailable.
    try:
        from . import chain_correlator
        activation_nudges = await chain_correlator.relationship_activation_nudges()
        if activation_nudges:
            lines.append("")
            lines.append("*Activation window (causal chain):*")
            for n in activation_nudges[:5]:
                lines.append(f"• {n['_nudge']}")
    except Exception as _e:
        logger.debug("chain activation nudges failed (non-fatal): %s", _e)

    return "\n".join(lines)

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="contact_intelligence",
                     summary="contact_intelligence module active",
                     source_id="contact_intelligence:init")
    except Exception:
        pass

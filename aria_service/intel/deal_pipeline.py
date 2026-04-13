"""ARIA Deal Pipeline — CRM-lite lead tracker for defence BD.

Tracks leads from detection through to close. Leads are auto-created by the
autonomous engine when procurement/tender/opportunity signals fire, and can
also be created manually via chat ("ARIA, add a lead for Angola naval deal").

Stages:
  DETECTED  → first signal seen (auto or manual)
  QUALIFIED → team has reviewed and confirmed it's real
  PROPOSAL  → we're actively preparing or have submitted a proposal
  NEGOTIATION → in commercial/contract discussions
  WON       → deal closed, won
  LOST      → deal closed, lost
  DORMANT   → went cold, no activity for 21+ days (auto-set)

Redis-backed with in-memory fallback. 90-day TTL on closed deals.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from . import redis_store as rs

logger = logging.getLogger("aria.deal_pipeline")

# ── Redis keys ────────────────────────────────────────────────────────────────
_KEY_PIPELINE = "crucix:aria:deal_pipeline:leads"
_KEY_STATS = "crucix:aria:deal_pipeline:stats"
_KEY_HISTORY = "crucix:aria:deal_pipeline:history"  # stage transition log

# ── Constants ─────────────────────────────────────────────────────────────────
STAGES = ["DETECTED", "QUALIFIED", "PROPOSAL", "NEGOTIATION", "WON", "LOST", "DORMANT"]
OPEN_STAGES = {"DETECTED", "QUALIFIED", "PROPOSAL", "NEGOTIATION"}
CLOSED_STAGES = {"WON", "LOST", "DORMANT"}
DORMANT_DAYS = 21  # auto-mark dormant after this many days without activity
CLOSED_TTL_DAYS = 90  # keep closed deals for 90 days then archive

# ── Target markets for auto-detection ─────────────────────────────────────────
TARGET_MARKETS = {
    "angola", "mozambique", "guinea-bissau", "cape verde", "ghana", "nigeria",
    "kenya", "senegal", "côte d'ivoire", "ivory coast", "morocco", "egypt",
    "saudi arabia", "uae", "qatar", "oman", "bahrain", "kuwait",
    "turkey", "brazil", "indonesia", "india", "pakistan", "vietnam",
    "south korea", "japan", "taiwan", "philippines", "malaysia", "thailand",
    "poland", "romania", "czech republic", "slovakia", "hungary",
    "ukraine", "georgia", "kazakhstan", "uzbekistan",
}


@dataclass
class Lead:
    """A single BD lead in the pipeline."""
    id: str = ""
    country: str = ""
    buyer: str = ""
    requirement: str = ""
    estimated_value_usd: float = 0.0
    stage: str = "DETECTED"
    next_action: str = ""
    owner: str = ""
    deadline: str = ""  # ISO date string for tender/bid deadlines
    source: str = ""  # autonomous task ID, chat, manual
    source_task_id: str = ""
    confidence: str = "ASSESSED"
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    stage_history: list[dict] = field(default_factory=list)
    last_activity_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"LEAD-{uuid.uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if not self.last_activity_at:
            self.last_activity_at = now


# ── Persistence ───────────────────────────────────────────────────────────────

async def _load() -> list[dict]:
    data = await rs.get_json(_KEY_PIPELINE)
    return data if isinstance(data, list) else []


async def _save(leads: list[dict]) -> None:
    await rs.set_json(_KEY_PIPELINE, leads)


async def _record_transition(lead_id: str, old_stage: str, new_stage: str, reason: str = "") -> None:
    entry = {
        "lead_id": lead_id,
        "from": old_stage,
        "to": new_stage,
        "reason": reason,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    await rs.lpush(_KEY_HISTORY, json.dumps(entry))
    await rs.ltrim(_KEY_HISTORY, 0, 499)  # keep last 500 transitions


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def create_lead(
    *,
    country: str,
    buyer: str = "",
    requirement: str = "",
    estimated_value_usd: float = 0.0,
    stage: str = "DETECTED",
    next_action: str = "",
    owner: str = "",
    deadline: str = "",
    source: str = "manual",
    source_task_id: str = "",
    confidence: str = "ASSESSED",
    tags: list[str] | None = None,
    notes: list[str] | None = None,
) -> Lead:
    """Create a new lead and persist it."""
    lead = Lead(
        country=country,
        buyer=buyer,
        requirement=requirement,
        estimated_value_usd=estimated_value_usd,
        stage=stage if stage in STAGES else "DETECTED",
        next_action=next_action,
        owner=owner,
        deadline=deadline,
        source=source,
        source_task_id=source_task_id,
        confidence=confidence,
        tags=tags or [],
        notes=notes or [],
    )
    lead.stage_history = [{"stage": lead.stage, "ts": lead.created_at}]

    leads = await _load()

    # Dedup: skip if same country + buyer + requirement exists and is open
    for existing in leads:
        if (existing.get("country", "").lower() == country.lower()
                and existing.get("buyer", "").lower() == buyer.lower()
                and existing.get("requirement", "").lower()[:80] == requirement.lower()[:80]
                and existing.get("stage", "") in OPEN_STAGES):
            logger.info("[pipeline] Duplicate lead skipped: %s / %s", country, buyer)
            return Lead(**existing)

    leads.insert(0, asdict(lead))
    await _save(leads)

    # Brain hook
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="opportunity_detector",
            summary=f"New lead created: {country} — {buyer or 'unknown buyer'} — {requirement[:100]} (${estimated_value_usd:,.0f})",
            success=True,
            confidence=confidence,
        )
    except Exception:
        pass

    logger.info("[pipeline] Lead created: %s — %s / %s ($%s)",
                lead.id, country, buyer, f"{estimated_value_usd:,.0f}")
    return lead


async def update_lead(lead_id: str, **updates) -> dict | None:
    """Update a lead by ID. Returns the updated lead dict or None if not found."""
    leads = await _load()
    now = datetime.now(timezone.utc).isoformat()

    for i, lead in enumerate(leads):
        if lead.get("id") == lead_id:
            old_stage = lead.get("stage", "")

            for k, v in updates.items():
                if k == "notes" and isinstance(v, str):
                    lead.setdefault("notes", []).append(f"[{now[:10]}] {v}")
                elif k == "tags" and isinstance(v, list):
                    lead["tags"] = list(set(lead.get("tags", []) + v))
                elif k in asdict(Lead()):
                    lead[k] = v

            lead["updated_at"] = now
            lead["last_activity_at"] = now

            # Record stage transition
            new_stage = lead.get("stage", old_stage)
            if new_stage != old_stage:
                lead.setdefault("stage_history", []).append({"stage": new_stage, "ts": now})
                await _record_transition(lead_id, old_stage, new_stage, updates.get("notes", ""))

            leads[i] = lead
            await _save(leads)
            logger.info("[pipeline] Lead updated: %s → stage=%s", lead_id, new_stage)
            return lead
    return None


async def get_lead(lead_id: str) -> dict | None:
    leads = await _load()
    for lead in leads:
        if lead.get("id") == lead_id:
            return lead
    return None


async def delete_lead(lead_id: str) -> bool:
    leads = await _load()
    before = len(leads)
    leads = [l for l in leads if l.get("id") != lead_id]
    if len(leads) < before:
        await _save(leads)
        return True
    return False


async def get_pipeline(
    *,
    stage: str | None = None,
    country: str | None = None,
    owner: str | None = None,
    include_closed: bool = False,
) -> list[dict]:
    """Query the pipeline with optional filters."""
    leads = await _load()
    results = []
    for lead in leads:
        if not include_closed and lead.get("stage", "") in CLOSED_STAGES:
            continue
        if stage and lead.get("stage", "").upper() != stage.upper():
            continue
        if country and lead.get("country", "").lower() != country.lower():
            continue
        if owner and lead.get("owner", "").lower() != owner.lower():
            continue
        results.append(lead)
    return results


async def get_stats() -> dict:
    """Pipeline summary stats."""
    leads = await _load()
    by_stage: dict[str, int] = {}
    by_country: dict[str, int] = {}
    total_value = 0.0
    open_count = 0

    for lead in leads:
        stage = lead.get("stage", "DETECTED")
        country = lead.get("country", "unknown")
        by_stage[stage] = by_stage.get(stage, 0) + 1
        if stage in OPEN_STAGES:
            by_country[country] = by_country.get(country, 0) + 1
            total_value += lead.get("estimated_value_usd", 0)
            open_count += 1

    return {
        "total_leads": len(leads),
        "open_leads": open_count,
        "by_stage": by_stage,
        "by_country": by_country,
        "total_pipeline_value_usd": total_value,
    }


# ── Dormancy detection ────────────────────────────────────────────────────────

async def check_dormant_leads() -> list[dict]:
    """Find leads that have gone cold and mark them DORMANT.
    Returns the list of leads that were marked dormant this run."""
    leads = await _load()
    now = datetime.now(timezone.utc)
    newly_dormant = []

    for i, lead in enumerate(leads):
        if lead.get("stage", "") not in OPEN_STAGES:
            continue
        last = lead.get("last_activity_at") or lead.get("updated_at") or lead.get("created_at", "")
        if not last:
            continue
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            days_idle = (now - last_dt).days
        except Exception:
            continue

        if days_idle >= DORMANT_DAYS:
            lead["stage"] = "DORMANT"
            lead["updated_at"] = now.isoformat()
            lead.setdefault("stage_history", []).append({"stage": "DORMANT", "ts": now.isoformat()})
            lead.setdefault("notes", []).append(
                f"[{now.strftime('%Y-%m-%d')}] Auto-marked DORMANT — {days_idle} days without activity"
            )
            leads[i] = lead
            newly_dormant.append(lead)
            await _record_transition(lead["id"], "OPEN", "DORMANT", f"idle {days_idle}d")

    if newly_dormant:
        await _save(leads)
        logger.info("[pipeline] %d leads marked DORMANT", len(newly_dormant))
    return newly_dormant


# ── Deadline tracking ─────────────────────────────────────────────────────────

async def get_upcoming_deadlines(days_ahead: int = 30) -> list[dict]:
    """Return open leads with deadlines within the next N days."""
    leads = await _load()
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)
    upcoming = []

    for lead in leads:
        if lead.get("stage", "") not in OPEN_STAGES:
            continue
        dl = lead.get("deadline", "")
        if not dl:
            continue
        try:
            dl_dt = datetime.fromisoformat(dl.replace("Z", "+00:00"))
            if dl_dt.tzinfo is None:
                dl_dt = dl_dt.replace(tzinfo=timezone.utc)
            days_until = (dl_dt - now).days
            if 0 <= days_until <= days_ahead:
                upcoming.append({**lead, "_days_until_deadline": days_until})
        except Exception:
            continue

    return sorted(upcoming, key=lambda x: x.get("_days_until_deadline", 999))


# ── Stale lead detection ─────────────────────────────────────────────────────

async def get_stale_leads(days_threshold: int = 14) -> list[dict]:
    """Return open leads with no activity for N+ days (but not yet DORMANT)."""
    leads = await _load()
    now = datetime.now(timezone.utc)
    stale = []

    for lead in leads:
        if lead.get("stage", "") not in OPEN_STAGES:
            continue
        last = lead.get("last_activity_at") or lead.get("updated_at", "")
        if not last:
            continue
        try:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            days_idle = (now - last_dt).days
        except Exception:
            continue
        if days_idle >= days_threshold:
            stale.append({**lead, "_days_idle": days_idle})

    return sorted(stale, key=lambda x: x.get("_days_idle", 0), reverse=True)


# ── Auto-create leads from autonomous signals ────────────────────────────────

# Keywords that indicate a real procurement/tender opportunity
_PROCUREMENT_SIGNALS = [
    "tender", "procurement", "rfp", "rfq", "rfi", "bid", "contract",
    "acquisition", "purchase", "modernisation", "modernization",
    "order", "delivery", "supply", "commissioning",
]

_VALUE_PATTERNS = [
    # $XXM, $XX million, USD XXM, etc
    r'\$\s*([\d,.]+)\s*(billion|bn|million|mn|m)\b',
    r'USD\s*([\d,.]+)\s*(billion|bn|million|mn|m)\b',
    r'([\d,.]+)\s*(billion|bn|million|mn|m)\s*(?:USD|dollars)',
    r'(?:worth|valued?\s+at|estimated)\s+(?:at\s+)?\$?([\d,.]+)\s*(billion|bn|million|mn|m)',
]

import re

def _extract_value(text: str) -> float:
    """Try to extract a USD value from text."""
    text_lower = text.lower()
    for pattern in _VALUE_PATTERNS:
        m = re.search(pattern, text_lower)
        if m:
            try:
                num = float(m.group(1).replace(",", ""))
                unit = m.group(2).lower()
                if unit in ("billion", "bn"):
                    return num * 1_000_000_000
                elif unit in ("million", "mn", "m"):
                    return num * 1_000_000
                return num
            except Exception:
                continue
    return 0.0


def _extract_country(text: str) -> str:
    """Extract the most prominent target market from text."""
    text_lower = text.lower()
    for market in sorted(TARGET_MARKETS, key=len, reverse=True):
        if market in text_lower:
            return market.title()
    return ""


def _extract_buyer(text: str) -> str:
    """Try to extract the buyer entity from text."""
    # Common patterns: "SIMPORTEX", "FADM", "MoD", "Ministry of Defence", etc
    buyer_patterns = [
        r'(?:buyer|client|customer|purchaser|procuring entity)[:\s]+([A-Z][A-Za-z\s&\.]+?)(?:\.|,|\n|—)',
        r'(SIMPORTEX|FADM|FAA|MINFAR|GAF|MOD|MoD|DSTL)',
        r'(?:Ministry of (?:Defence|Defense|National Security)(?:\s+of\s+\w+)?)',
    ]
    for pat in buyer_patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1) if m.lastindex else m.group(0)
    return ""


async def auto_create_from_signal(
    *,
    task_id: str,
    task_name: str,
    response_text: str,
    triggered_flags: list[str],
) -> Lead | None:
    """Parse an autonomous task's output for procurement signals and auto-create a lead.

    Called by the autonomous delivery pipeline after a task completes.
    Returns the created Lead if one was created, None otherwise.
    """
    if not response_text or len(response_text) < 100:
        return None

    text_lower = response_text.lower()

    # Must have at least one procurement keyword
    if not any(kw in text_lower for kw in _PROCUREMENT_SIGNALS):
        return None

    country = _extract_country(response_text)
    if not country:
        return None

    buyer = _extract_buyer(response_text)
    value = _extract_value(response_text)
    requirement = ""

    # Extract the first substantive line as requirement description
    for line in response_text.split("\n"):
        line_stripped = line.strip().strip("*_")
        if len(line_stripped) > 40 and any(kw in line_stripped.lower() for kw in _PROCUREMENT_SIGNALS):
            requirement = line_stripped[:200]
            break

    if not requirement:
        # Fall back to task name + triggered flags
        requirement = f"{task_name}: {', '.join(triggered_flags[:5])}"

    lead = await create_lead(
        country=country,
        buyer=buyer,
        requirement=requirement,
        estimated_value_usd=value,
        stage="DETECTED",
        source=f"autonomous:{task_id}",
        source_task_id=task_id,
        confidence="ASSESSED",
        tags=triggered_flags[:10],
    )
    return lead


# ── Pipeline summary for briefings ────────────────────────────────────────────

async def generate_pipeline_summary() -> str:
    """Generate a formatted pipeline summary for WhatsApp/chat briefings."""
    stats = await get_stats()
    leads = await get_pipeline()
    stale = await get_stale_leads(days_threshold=14)
    deadlines = await get_upcoming_deadlines(days_ahead=14)
    dormant = [l for l in await _load() if l.get("stage") == "DORMANT"]

    lines = [
        "*📊 DEAL PIPELINE STATUS*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Open leads: *{stats['open_leads']}* | Pipeline value: *${stats['total_pipeline_value_usd']:,.0f}*",
        "",
    ]

    # By stage
    for stage in ["DETECTED", "QUALIFIED", "PROPOSAL", "NEGOTIATION"]:
        count = stats["by_stage"].get(stage, 0)
        if count:
            emoji = {"DETECTED": "🔵", "QUALIFIED": "🟡", "PROPOSAL": "🟠", "NEGOTIATION": "🔴"}.get(stage, "⚪")
            lines.append(f"{emoji} {stage}: {count}")

    if stats["by_stage"].get("WON", 0):
        lines.append(f"✅ WON: {stats['by_stage']['WON']}")
    if stats["by_stage"].get("DORMANT", 0):
        lines.append(f"💤 DORMANT: {stats['by_stage']['DORMANT']}")

    # Top leads by value
    top = sorted(leads, key=lambda x: x.get("estimated_value_usd", 0), reverse=True)[:5]
    if top:
        lines.append("")
        lines.append("*Top leads by value:*")
        for l in top:
            val = l.get("estimated_value_usd", 0)
            val_str = f"${val:,.0f}" if val else "TBD"
            lines.append(f"• {l['country']} — {l.get('buyer', '?')}: {l.get('requirement', '?')[:60]} ({val_str})")

    # Upcoming deadlines
    if deadlines:
        lines.append("")
        lines.append("*⏰ Upcoming deadlines:*")
        for d in deadlines[:5]:
            days = d.get("_days_until_deadline", "?")
            lines.append(f"• {d['country']} — {d.get('requirement', '?')[:50]} (*{days}d left*)")

    # Stale leads needing attention
    if stale:
        lines.append("")
        lines.append("*⚠️ Leads going cold:*")
        for s in stale[:5]:
            days = s.get("_days_idle", "?")
            lines.append(f"• {s['country']} — {s.get('buyer', '?')}: no activity for {days}d")

    # Action items
    actions = []
    if deadlines:
        d = deadlines[0]
        actions.append(f"Review {d['country']} deadline ({d.get('_days_until_deadline', '?')}d)")
    if stale:
        actions.append(f"Re-engage {stale[0]['country']} lead ({stale[0].get('_days_idle', '?')}d idle)")
    new_detected = [l for l in leads if l.get("stage") == "DETECTED"]
    if new_detected:
        actions.append(f"Qualify {len(new_detected)} new detected lead(s)")

    if actions:
        lines.append("")
        lines.append("*📋 Action items:*")
        for i, a in enumerate(actions[:5], 1):
            lines.append(f"{i}. {a}")

    return "\n".join(lines)


# ── Chat integration helpers ──────────────────────────────────────────────────

async def handle_pipeline_command(message: str) -> str | None:
    """Handle /pipeline commands from chat/WhatsApp.

    /pipeline               → show pipeline summary
    /pipeline add [details] → add a lead manually
    /pipeline [country]     → filter by country
    /pipeline stale         → show stale leads
    /pipeline deadlines     → show upcoming deadlines
    """
    msg = message.strip().lower()

    if msg in ("/pipeline", "show pipeline", "pipeline status"):
        return await generate_pipeline_summary()

    if msg.startswith("/pipeline stale") or msg.startswith("pipeline stale"):
        stale = await get_stale_leads()
        if not stale:
            return "✅ No stale leads — all deals are active."
        lines = ["*⚠️ Stale leads (14+ days idle):*"]
        for s in stale[:10]:
            lines.append(f"• `{s['id']}` {s['country']} — {s.get('buyer', '?')}: {s.get('_days_idle', '?')}d idle")
        return "\n".join(lines)

    if msg.startswith("/pipeline deadlines") or msg.startswith("pipeline deadlines"):
        dl = await get_upcoming_deadlines()
        if not dl:
            return "✅ No upcoming deadlines in the next 30 days."
        lines = ["*⏰ Upcoming deadlines:*"]
        for d in dl[:10]:
            lines.append(f"• `{d['id']}` {d['country']} — {d.get('requirement', '?')[:50]} (*{d.get('_days_until_deadline', '?')}d*)")
        return "\n".join(lines)

    # Country filter
    for market in TARGET_MARKETS:
        if market in msg:
            leads = await get_pipeline(country=market.title())
            if not leads:
                return f"No open leads for {market.title()}."
            lines = [f"*Pipeline — {market.title()}:*"]
            for l in leads:
                val = l.get("estimated_value_usd", 0)
                lines.append(f"• `{l['id']}` [{l['stage']}] {l.get('requirement', '?')[:60]} (${val:,.0f})")
            return "\n".join(lines)

    return None  # Not a pipeline command

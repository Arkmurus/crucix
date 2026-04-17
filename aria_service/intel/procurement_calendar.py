"""ARIA Procurement Calendar — temporal procurement intelligence.

Priority 3 from the 2026-04-17 session notes: country-level procurement
calendar with political cycle dependencies. Angola budget Q2. NATO common
funding November. EU MFF 7-year cycles. Alert 90 days before windows open.

What this module does
─────────────────────
1. Stores recurring procurement cycle events: fiscal year boundaries,
   multi-year defence programmes, regular ministerial cycles, known
   election dates.
2. Computes the next occurrence of every recurring event and returns
   the upcoming set within a given horizon.
3. Emits 90-day advance alerts so BD + chain_correlator can act BEFORE
   the budget window opens.
4. Exposes a briefing block and a chat context helper so every reply
   about "what's upcoming in X?" has calendar grounding.

Seed data is deliberately narrow — only events I can back with a public
source. The operator / autonomous tasks extend it over time via
`add_event()`. Placeholders are marked confidence=LOW so ARIA can cite
them as pending-verification rather than firm facts.

Storage: Redis key `crucix:aria:procurement_calendar:events`.

Public API
──────────
  list_upcoming(iso2=None, days_ahead=180) -> list[dict]
  upcoming_alerts(lead_days=90) -> list[dict]
  generate_calendar_briefing() -> str
  get_calendar_context(query: str) -> str
  add_event(event: dict) -> str
  stats() -> dict
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import redis_store as rs
from . import country_taxonomy as ct

logger = logging.getLogger("aria.procurement_calendar")

EVENTS_KEY = "crucix:aria:procurement_calendar:events"
DEFAULT_LEAD_DAYS = 90
DEFAULT_HORIZON_DAYS = 180
MAX_EVENTS = 2000

EVENT_TYPES = (
    "fiscal_year_start",
    "fiscal_year_end",
    "budget_cycle",
    "election",
    "nato_ministerial",
    "eu_mff_window",
    "defence_review",
    "programme_milestone",
)

CONFIDENCE = ("HIGH", "MEDIUM", "LOW")


# ── Seed data ─────────────────────────────────────────────────────────────
# Curated from public sources. Each entry is a recurring anchor — the module
# computes the next occurrence given cycle_period_days. Prefer wider coverage
# with LOW confidence over narrow coverage with HIGH — the dashboard can
# warn on LOW-confidence items until the operator upgrades them.
#
# `anchor_date` is any past or future occurrence we trust; the computation
# rolls forward by `cycle_period_days` until the result is ≥ today.

_SEED_EVENTS: list[dict] = [
    # ── Fiscal year boundaries (high confidence, public) ─────────────────
    {"iso2": "US", "event_type": "fiscal_year_start", "title": "US Federal FY begins (Oct 1)",
     "recurrence": "annual", "anchor_date": "2024-10-01", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "US Treasury",
     "notes": "NDAA typically authorised Dec; obligations ramp Jan-Mar."},
    {"iso2": "GB", "event_type": "fiscal_year_start", "title": "UK FY begins (Apr 6)",
     "recurrence": "annual", "anchor_date": "2025-04-06", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "HM Treasury",
     "notes": "MoD budget execution ramps Q1; Spring Statement precedes."},
    {"iso2": "JP", "event_type": "fiscal_year_start", "title": "Japan FY begins (Apr 1)",
     "recurrence": "annual", "anchor_date": "2025-04-01", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "MoF Japan", "notes": ""},
    {"iso2": "IN", "event_type": "fiscal_year_start", "title": "India FY begins (Apr 1)",
     "recurrence": "annual", "anchor_date": "2025-04-01", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "Ministry of Finance India",
     "notes": "Budget speech late Jan/early Feb; defence allocation announced."},
    # Calendar-year fiscal systems — budget passes mid-to-late Q4
    {"iso2": "AO", "event_type": "budget_cycle", "title": "Angola OGE budget finalised",
     "recurrence": "annual", "anchor_date": "2024-12-15", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "MEDIUM", "source": "OGE Angola",
     "notes": "Q2 execution phase is historical procurement peak."},
    {"iso2": "MZ", "event_type": "budget_cycle", "title": "Mozambique state budget approved",
     "recurrence": "annual", "anchor_date": "2024-12-20", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "MEDIUM", "source": "Assembleia da República"},
    {"iso2": "BR", "event_type": "budget_cycle", "title": "Brazil LOA budget law passed",
     "recurrence": "annual", "anchor_date": "2024-12-22", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "Ministério do Planejamento"},
    {"iso2": "DE", "event_type": "budget_cycle", "title": "Germany Bundeshaushalt passed",
     "recurrence": "annual", "anchor_date": "2024-11-29", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "Bundestag",
     "notes": "€100bn Sondervermögen runs 2022-2026 — final year FY26 execution elevated."},
    {"iso2": "PL", "event_type": "budget_cycle", "title": "Poland budget act signed",
     "recurrence": "annual", "anchor_date": "2025-01-20", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "Ministerstwo Finansów"},
    {"iso2": "RO", "event_type": "budget_cycle", "title": "Romania state budget passed",
     "recurrence": "annual", "anchor_date": "2024-12-28", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "Ministerul Finanțelor"},
    {"iso2": "TR", "event_type": "budget_cycle", "title": "Türkiye merkezi bütçe passed",
     "recurrence": "annual", "anchor_date": "2024-12-27", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "Hazine ve Maliye Bakanlığı",
     "notes": "SSB (defence industries) allocations released shortly after."},
    {"iso2": "SA", "event_type": "budget_cycle", "title": "Saudi Arabia budget announcement",
     "recurrence": "annual", "anchor_date": "2024-12-09", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "Ministry of Finance KSA",
     "notes": "Vision 2030 localisation targets drive procurement allocation."},
    {"iso2": "AE", "event_type": "budget_cycle", "title": "UAE federal budget announced",
     "recurrence": "annual", "anchor_date": "2024-10-30", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "MEDIUM", "source": "Ministry of Finance UAE"},
    {"iso2": "KR", "event_type": "budget_cycle", "title": "South Korea National Assembly budget",
     "recurrence": "annual", "anchor_date": "2024-12-10", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "Ministry of Economy and Finance"},
    {"iso2": "NG", "event_type": "budget_cycle", "title": "Nigeria Appropriation Act signed",
     "recurrence": "annual", "anchor_date": "2025-01-01", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "MEDIUM", "source": "National Assembly Nigeria"},
    {"iso2": "GH", "event_type": "budget_cycle", "title": "Ghana budget statement delivered",
     "recurrence": "annual", "anchor_date": "2024-11-13", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "MEDIUM", "source": "Ministry of Finance Ghana"},
    {"iso2": "PE", "event_type": "budget_cycle", "title": "Peru public sector budget approved",
     "recurrence": "annual", "anchor_date": "2024-11-30", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "MEDIUM", "source": "MEF Peru"},

    # ── Elections (HIGH / MEDIUM confidence; schedule is public) ─────────
    {"iso2": "US", "event_type": "election", "title": "US Presidential election",
     "recurrence": "quadrennial", "anchor_date": "2024-11-05", "cycle_period_days": 1461,
     "lead_days": 180, "confidence": "HIGH", "source": "FEC",
     "notes": "DoD priority realignment typically Q1 after inauguration."},
    {"iso2": "PL", "event_type": "election", "title": "Poland parliamentary election",
     "recurrence": "quadrennial", "anchor_date": "2023-10-15", "cycle_period_days": 1461,
     "lead_days": 180, "confidence": "HIGH", "source": "PKW"},
    {"iso2": "BR", "event_type": "election", "title": "Brazil general election",
     "recurrence": "quadrennial", "anchor_date": "2026-10-04", "cycle_period_days": 1461,
     "lead_days": 180, "confidence": "HIGH", "source": "TSE"},
    {"iso2": "TR", "event_type": "election", "title": "Türkiye presidential election",
     "recurrence": "quinquennial", "anchor_date": "2023-05-14", "cycle_period_days": 1826,
     "lead_days": 180, "confidence": "MEDIUM", "source": "YSK"},
    {"iso2": "AO", "event_type": "election", "title": "Angola general election",
     "recurrence": "quinquennial", "anchor_date": "2022-08-24", "cycle_period_days": 1826,
     "lead_days": 180, "confidence": "HIGH", "source": "CNE Angola"},
    {"iso2": "MZ", "event_type": "election", "title": "Mozambique general election",
     "recurrence": "quinquennial", "anchor_date": "2024-10-09", "cycle_period_days": 1826,
     "lead_days": 180, "confidence": "HIGH", "source": "CNE Mozambique"},
    {"iso2": "NG", "event_type": "election", "title": "Nigeria general election",
     "recurrence": "quadrennial", "anchor_date": "2023-02-25", "cycle_period_days": 1461,
     "lead_days": 180, "confidence": "HIGH", "source": "INEC"},

    # ── Multilateral windows ─────────────────────────────────────────────
    {"iso2": "EU", "event_type": "eu_mff_window", "title": "EU MFF 2021-2027 closeout",
     "recurrence": "one_off", "anchor_date": "2027-12-31", "cycle_period_days": 0,
     "lead_days": 365, "confidence": "HIGH", "source": "European Council",
     "notes": "EDF commitments concentrated in final 18mo of MFF."},
    {"iso2": "EU", "event_type": "eu_mff_window", "title": "EU MFF 2028-2034 opens",
     "recurrence": "one_off", "anchor_date": "2028-01-01", "cycle_period_days": 0,
     "lead_days": 365, "confidence": "MEDIUM", "source": "European Council",
     "notes": "Negotiations under way; figures provisional."},
    {"iso2": "EU", "event_type": "defence_review", "title": "EDF annual work programme",
     "recurrence": "annual", "anchor_date": "2025-03-31", "cycle_period_days": 365,
     "lead_days": 90, "confidence": "HIGH", "source": "DG DEFIS"},
    {"iso2": "GB", "event_type": "defence_review", "title": "UK Strategic Defence Review cycle",
     "recurrence": "quinquennial", "anchor_date": "2025-06-30", "cycle_period_days": 1826,
     "lead_days": 180, "confidence": "MEDIUM", "source": "HM Government"},
    {"iso2": "FR", "event_type": "defence_review", "title": "France LPM refresh",
     "recurrence": "septennial", "anchor_date": "2023-07-13", "cycle_period_days": 2556,
     "lead_days": 365, "confidence": "HIGH", "source": "Assemblée nationale",
     "notes": "Current LPM 2024-2030 €413bn."},
    {"iso2": "DE", "event_type": "programme_milestone", "title": "Bundeswehr €100bn Sondervermögen end",
     "recurrence": "one_off", "anchor_date": "2026-12-31", "cycle_period_days": 0,
     "lead_days": 365, "confidence": "HIGH", "source": "BMVg",
     "notes": "Post-2026 procurement returns to baseline budget unless replacement fund passed."},
]


# ── Helpers ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _next_occurrence(anchor: datetime, cycle_days: int, recurrence: str, now: datetime) -> Optional[datetime]:
    """Compute the next occurrence ≥ now. Returns None for one-off events
    whose anchor is already in the past."""
    if recurrence == "one_off" or cycle_days <= 0:
        return anchor if anchor >= now else None
    t = anchor
    # Roll forward in cycle_days steps until we land on or after now.
    # Bounded iteration to avoid pathological input.
    for _ in range(200):
        if t >= now:
            return t
        t = t + timedelta(days=cycle_days)
    return None


def _event_id(iso2: str, event_type: str, title: str, anchor_date: str) -> str:
    h = hashlib.md5(f"{iso2}|{event_type}|{title}|{anchor_date}".encode()).hexdigest()[:12]
    return h


async def _load() -> dict:
    data = await rs.get_json(EVENTS_KEY)
    if isinstance(data, dict) and "items" in data:
        return data
    # First load — persist the seed so subsequent callers (and the
    # dashboard) see the base calendar without needing a manual refresh.
    seeded = {"items": [_normalise(e) for e in _SEED_EVENTS], "version": 1}
    await rs.set_json(EVENTS_KEY, seeded)
    return seeded


def _normalise(raw: dict) -> dict:
    iso2 = raw.get("iso2") or ""
    # Supra-national placeholders ("EU", "NATO") are allowed as-is; otherwise
    # normalise via country_taxonomy.
    normalised_iso2 = iso2 if iso2 in ("EU", "NATO") else (ct.to_iso2(iso2) or iso2)
    region = ct.iso2_to_region(normalised_iso2) if normalised_iso2 not in ("EU", "NATO") else normalised_iso2
    event_type = raw.get("event_type", "")
    title = raw.get("title", "")
    anchor_date = raw.get("anchor_date", "")
    return {
        "id": raw.get("id") or _event_id(normalised_iso2, event_type, title, anchor_date),
        "iso2": normalised_iso2,
        "region": region,
        "event_type": event_type,
        "title": title,
        "recurrence": raw.get("recurrence", "annual"),
        "anchor_date": anchor_date,
        "cycle_period_days": int(raw.get("cycle_period_days", 365) or 0),
        "lead_days": int(raw.get("lead_days", DEFAULT_LEAD_DAYS)),
        "confidence": (raw.get("confidence") or "MEDIUM").upper(),
        "source": raw.get("source", ""),
        "notes": raw.get("notes", ""),
    }


async def _save(items: list[dict]) -> None:
    await rs.set_json(EVENTS_KEY, {"items": items, "version": 1})


# ── Public API ────────────────────────────────────────────────────────────

async def add_event(event: dict) -> str:
    """Insert or update a calendar event. Dedup is by deterministic id."""
    data = await _load()
    ev = _normalise(event)
    kept = [e for e in data["items"] if e.get("id") != ev["id"]]
    kept.append(ev)
    if len(kept) > MAX_EVENTS:
        kept = kept[-MAX_EVENTS:]
    await _save(kept)
    return ev["id"]


async def list_upcoming(iso2: Optional[str] = None, days_ahead: int = DEFAULT_HORIZON_DAYS) -> list[dict]:
    """Return events whose next occurrence falls inside the horizon.

    Each result carries a computed `next_occurrence` ISO date + `days_until`
    integer so callers can sort / filter without re-doing the math.
    """
    data = await _load()
    now = _now()
    filt_iso2 = None
    if iso2:
        filt_iso2 = iso2 if iso2.upper() in ("EU", "NATO") else ct.to_iso2(iso2)
    results: list[dict] = []
    for ev in data["items"]:
        if filt_iso2 and ev.get("iso2") != filt_iso2:
            continue
        anchor = _parse_date(ev.get("anchor_date", ""))
        if not anchor:
            continue
        nxt = _next_occurrence(
            anchor,
            int(ev.get("cycle_period_days", 0) or 0),
            ev.get("recurrence", "one_off"),
            now,
        )
        if not nxt:
            continue
        days_until = (nxt - now).days
        if days_until < 0 or days_until > days_ahead:
            continue
        enriched = dict(ev)
        enriched["next_occurrence"] = nxt.isoformat()
        enriched["days_until"] = days_until
        results.append(enriched)
    results.sort(key=lambda e: e["days_until"])
    return results


async def upcoming_alerts(lead_days: int = DEFAULT_LEAD_DAYS) -> list[dict]:
    """Events landing within `lead_days` days. Respects each event's own
    `lead_days` if it's longer (e.g. elections surface 180 days out).
    """
    upcoming = await list_upcoming(days_ahead=max(lead_days, 365))
    alerts = []
    for ev in upcoming:
        event_lead = max(lead_days, int(ev.get("lead_days", DEFAULT_LEAD_DAYS)))
        if ev["days_until"] <= event_lead:
            alerts.append(ev)
    return alerts


async def generate_calendar_briefing() -> str:
    """Markdown block for the 05:45 UTC team briefing."""
    alerts = await upcoming_alerts()
    if not alerts:
        return ""
    # Group by iso2 for readability
    by_iso2: dict[str, list[dict]] = {}
    for a in alerts[:15]:
        by_iso2.setdefault(a.get("iso2", "?"), []).append(a)
    lines = ["", "*📅 PROCUREMENT CALENDAR*"]
    lines.append(f"_{len(alerts)} cycle event(s) within the alert horizon:_")
    for iso2, events in sorted(by_iso2.items(), key=lambda kv: min(e["days_until"] for e in kv[1])):
        for ev in events:
            confidence = ev.get("confidence", "MEDIUM")
            icon = "🟢" if confidence == "HIGH" else "🟡" if confidence == "MEDIUM" else "⚪"
            lines.append(
                f"{icon} *{iso2}* — {ev.get('title','?')} in {ev['days_until']}d "
                f"({ev.get('next_occurrence','')[:10]})"
            )
    return "\n".join(lines)


async def get_calendar_context(query: str, max_items: int = 3) -> str:
    """Chat-surface calendar context. Adds a [CALENDAR: ...] marker when
    the query mentions a covered country or region.
    """
    q = (query or "").lower()
    if not q:
        return ""
    alerts = await upcoming_alerts(lead_days=365)
    if not alerts:
        return ""
    # Match query → iso2 via name table
    matched: list[dict] = []
    try:
        name_table = ct._name_to_iso2_table()
    except Exception:
        name_table = {}
    for a in alerts:
        iso2 = a.get("iso2", "")
        if iso2 and (iso2.lower() in q):
            matched.append(a); continue
        for name, code in name_table.items():
            if code == iso2 and name in q:
                matched.append(a); break
    if not matched:
        return ""
    matched.sort(key=lambda e: e["days_until"])
    parts = ["PROCUREMENT CALENDAR — upcoming for this query:"]
    for a in matched[:max_items]:
        parts.append(
            f"  [CALENDAR: {a.get('iso2')} — {a.get('title')} in "
            f"{a['days_until']}d ({a.get('next_occurrence','')[:10]}, "
            f"conf {a.get('confidence','?')})]"
        )
    return "\n".join(parts)


async def stats() -> dict:
    data = await _load()
    by_type: dict[str, int] = {}
    by_region: dict[str, int] = {}
    by_conf: dict[str, int] = {}
    for ev in data["items"]:
        by_type[ev.get("event_type", "?")] = by_type.get(ev.get("event_type", "?"), 0) + 1
        by_region[ev.get("region", "?") or "?"] = by_region.get(ev.get("region", "?") or "?", 0) + 1
        by_conf[ev.get("confidence", "?")] = by_conf.get(ev.get("confidence", "?"), 0) + 1
    return {
        "events_total": len(data["items"]),
        "by_type": by_type,
        "by_region": by_region,
        "by_confidence": by_conf,
        "generated_at": _now().isoformat(),
    }


async def refresh() -> dict:
    """CALENDAR-REFRESH-WEEKLY hook. Recomputes upcoming alerts, optionally
    pushes high-confidence political-cycle events into chain_correlator's
    shifts store so they feed the same 12-18mo procurement projection path.
    """
    alerts = await upcoming_alerts()
    # Push eligible events into chain_correlator as structural shifts —
    # fiscal/budget cycles and elections map cleanly onto the chain's
    # `major_election` / `budget_announcement` shift types. Non-fatal if
    # chain_correlator is unavailable.
    pushed = 0
    try:
        from . import chain_correlator as cc
        stored = await cc._load(cc.SHIFTS_KEY)
        existing_ids = {s["id"] for s in stored["items"]}
        now = _now()
        for ev in alerts:
            ev_type = ev.get("event_type", "")
            if ev_type == "election":
                shift_type = "major_election"
            elif ev_type in ("budget_cycle", "fiscal_year_start"):
                shift_type = "budget_announcement"
            else:
                continue
            next_occ = ev.get("next_occurrence") or ""
            if not next_occ:
                continue
            iso2 = ev.get("iso2", "")
            if iso2 in ("EU", "NATO"):
                continue
            sid = cc._hash_id(shift_type, iso2, next_occ[:10], ev.get("title", "")[:80])
            if sid in existing_ids:
                continue
            stored["items"].append({
                "id": sid,
                "iso2": iso2,
                "region": ct.iso2_to_region(iso2),
                "shift_type": shift_type,
                "severity": 0.55 if ev.get("confidence") == "HIGH" else 0.45,
                "summary": f"[calendar] {ev.get('title', '')[:180]}",
                "source": f"procurement_calendar:{ev.get('source', '')}",
                "source_tier_score": 0.7 if ev.get("confidence") == "HIGH" else 0.4,
                "ts": next_occ,
                "detected_at": now.isoformat(),
            })
            existing_ids.add(sid)
            pushed += 1
        if pushed:
            await cc._save(cc.SHIFTS_KEY, stored)
    except Exception as e:
        logger.debug("chain_correlator push skipped: %s", e)

    # Brain hook — let mastery reflect calendar upkeep
    try:
        from . import brain_hook
        await brain_hook.absorb_silent(
            module="procurement_calendar",
            summary=f"Calendar refresh: {len(alerts)} upcoming alert(s), {pushed} pushed to chain",
            success=True,
            extra_topics=["procurement"],
        )
    except Exception:
        pass

    return {
        "upcoming_alerts": len(alerts),
        "pushed_to_chain": pushed,
        "generated_at": _now().isoformat(),
    }

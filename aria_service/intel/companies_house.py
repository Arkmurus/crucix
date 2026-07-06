"""ARIA Companies House Integration — UK company registry lookups.

Free API, no key required for basic endpoints. Provides:
  - Company profile (name, address, SIC codes, incorporation date, status)
  - Officers (directors past and present)
  - PSC (Persons of Significant Control — beneficial ownership)
  - Filing history (accounts, confirmation statements)
  - Registered address verification

This closes ghost detection checklist items that were showing '?' in
investigation outputs:
  - Item 1: Incorporation date → company profile
  - Item 2: Physical premises → registered address
  - Item 3: Directors → officer list
  - Item 6: Procurement history → filing history (proxy)
  - Item 10: Shared address → registered address cross-check

API docs: https://developer.company-information.service.gov.uk/
Rate limit: 600 requests/5 minutes (no key), higher with API key.

Feature-gated: ARIA_COMPANIES_HOUSE_ENABLED env var (default ON).
Optional: COMPANIES_HOUSE_API_KEY for higher rate limits.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.intel.companies_house")

_BASE_URL = "https://api.company-information.service.gov.uk"
_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "").strip()
_TIMEOUT = 15.0


@fail_wire(module="companies_house", gap_type="api_missing")
def is_enabled() -> bool:
    val = os.getenv("ARIA_COMPANIES_HOUSE_ENABLED", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def _headers() -> dict:
    h = {"Accept": "application/json"}
    if _API_KEY:
        import base64
        # CH API uses HTTP Basic auth with key as username, empty password
        encoded = base64.b64encode(f"{_API_KEY}:".encode()).decode()
        h["Authorization"] = f"Basic {encoded}"
    return h


# ── Company number extraction ──────────────────────────────────────────────

_UK_COMPANY_NUMBER_RE = re.compile(r"\b(?:SC|NI|OC|SO|NC|R0|IP|AC|FC|GE|LP|SL|NP|CE|CS|PC|RS)?(\d{6,8})\b")


@fail_wire(module="companies_house", gap_type="api_missing")
def extract_company_number(text: str) -> str | None:
    """Try to extract a UK company number from text."""
    m = _UK_COMPANY_NUMBER_RE.search(text)
    if m:
        num = m.group(0)
        # Pad to 8 digits if numeric only
        if num.isdigit():
            num = num.zfill(8)
        return num
    return None


# ── API calls ──────────────────────────────────────────────────────────────

async def _get(path: str) -> dict | None:
    """GET from Companies House API. Returns parsed JSON or None."""
    if not is_enabled():
        return None
    url = f"{_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:  # no-breaker: Companies House is a free authoritative source; breaker belongs at the caller (DD pipeline)
            resp = await client.get(url, headers=_headers())
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                logger.warning("Companies House rate limited")
                return None
            if resp.status_code != 200:
                logger.debug("CH API %s returned %d", path, resp.status_code)
                return None
            return resp.json()
    except Exception as e:
        logger.debug("CH API request failed: %s", e)
        return None


@fail_wire(module="companies_house", gap_type="api_missing")
async def search_companies(query: str, limit: int = 5) -> list[dict]:
    """Search for companies by name."""
    data = await _get(f"/search/companies?q={query}&items_per_page={limit}")
    if not data:
        return []
    items = data.get("items") or []
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="companies_house",
        summary="Search Companies",
        source_id="companies_house:R-F996",
    )

    return [
        {
            "company_number": item.get("company_number"),
            "title": item.get("title"),
            "company_status": item.get("company_status"),
            "date_of_creation": item.get("date_of_creation"),
            "address_snippet": item.get("address_snippet"),
            "company_type": item.get("company_type"),
        }
        for item in items
    ]


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_company_profile(company_number: str) -> dict | None:
    """Get full company profile."""
    data = await _get(f"/company/{company_number}")
    if not data:
        return None
    addr = data.get("registered_office_address") or {}
    return {
        "company_number": data.get("company_number"),
        "company_name": data.get("company_name"),
        "company_status": data.get("company_status"),
        "company_type": data.get("type"),
        "date_of_creation": data.get("date_of_creation"),
        "date_of_cessation": data.get("date_of_cessation"),
        "sic_codes": data.get("sic_codes") or [],
        "registered_address": {
            "line1": addr.get("address_line_1", ""),
            "line2": addr.get("address_line_2", ""),
            "locality": addr.get("locality", ""),
            "postal_code": addr.get("postal_code", ""),
            "country": addr.get("country", ""),
        },
        "has_been_liquidated": data.get("has_been_liquidated", False),
        "has_charges": data.get("has_charges", False),
        "has_insolvency_history": data.get("has_insolvency_history", False),
        "accounts_next_due": (data.get("accounts") or {}).get("next_due"),
        "confirmation_next_due": (data.get("confirmation_statement") or {}).get("next_due"),
        "last_full_members_list": data.get("last_full_members_list_date"),
    }


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_officers(company_number: str) -> list[dict]:
    """Get current and past officers (directors)."""
    data = await _get(f"/company/{company_number}/officers")
    if not data:
        return []
    items = data.get("items") or []
    return [
        {
            "name": item.get("name"),
            "role": item.get("officer_role"),
            "appointed_on": item.get("appointed_on"),
            "resigned_on": item.get("resigned_on"),
            "nationality": item.get("nationality"),
            "country_of_residence": item.get("country_of_residence"),
            "occupation": item.get("occupation"),
            "is_current": item.get("resigned_on") is None,
        }
        for item in items
    ]


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_psc(company_number: str) -> list[dict]:
    """Get Persons of Significant Control (beneficial ownership)."""
    data = await _get(f"/company/{company_number}/persons-with-significant-control")
    if not data:
        return []
    items = data.get("items") or []
    return [
        {
            "name": item.get("name"),
            "kind": item.get("kind"),
            "natures_of_control": item.get("natures_of_control") or [],
            "nationality": item.get("nationality"),
            "country_of_residence": item.get("country_of_residence"),
            "notified_on": item.get("notified_on"),
            "ceased_on": item.get("ceased_on"),
            "is_current": item.get("ceased_on") is None,
        }
        for item in items
    ]


@fail_wire(module="companies_house", gap_type="api_missing")
async def search_officers(name: str, limit: int = 20) -> list[dict]:
    """Search for officers (directors / PSCs) by name.

    Returns items with `officer_id` and `appointments_url` so the caller
    can drill down into company appointments. Each entry carries a
    short address snippet + date_of_birth (partial — month/year only).

    This is the PSC-reverse entry point: "give me every UK company this
    person has been an officer of."
    """
    import urllib.parse as _urlp
    q = _urlp.quote(name or "")
    if not q:
        return []
    data = await _get(f"/search/officers?q={q}&items_per_page={limit}")
    if not data:
        return []
    out: list[dict] = []
    for item in data.get("items") or []:
        links = item.get("links") or {}
        self_link = links.get("self") or ""  # e.g. "/officers/<id>/appointments"
        officer_id = ""
        if "/officers/" in self_link:
            officer_id = self_link.split("/officers/")[-1].split("/")[0]
        dob = item.get("date_of_birth") or {}
        out.append({
            "title": item.get("title"),
            "officer_id": officer_id,
            "appointments_url": self_link,
            "address_snippet": item.get("address_snippet", ""),
            "description": item.get("description", ""),
            "date_of_birth": {"month": dob.get("month"), "year": dob.get("year")} if dob else None,
        })
    return out


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_officer_appointments(officer_id: str, limit: int = 50) -> list[dict]:
    """List every company appointment for a given officer.

    Foundational for PSC-reverse: answers "which UK companies is this
    person a director / PSC of?" Returns both active and resigned roles
    so the caller can flag recently-resigned directors as a risk signal.
    """
    if not officer_id:
        return []
    data = await _get(f"/officers/{officer_id}/appointments?items_per_page={limit}")
    if not data:
        return []
    out: list[dict] = []
    for item in data.get("items") or []:
        appointed_to = item.get("appointed_to") or {}
        out.append({
            "company_name": item.get("appointed_to", {}).get("company_name")
                or item.get("name_elements", {}).get("forename"),
            "company_number": appointed_to.get("company_number"),
            "company_status": appointed_to.get("company_status"),
            "officer_role": item.get("officer_role"),
            "appointed_on": item.get("appointed_on"),
            "resigned_on": item.get("resigned_on"),
            "is_current": item.get("resigned_on") is None,
            "occupation": item.get("occupation"),
            "nationality": item.get("nationality"),
        })
    return out


@fail_wire(module="companies_house", gap_type="api_missing")
async def psc_reverse_lookup(name: str, max_officers: int = 5, max_apts_per_officer: int = 30) -> dict:
    """One-call answer to "which UK companies is this person tied to?"

    Steps:
      1. search_officers(name) — get up to `max_officers` candidates
      2. for each, get_officer_appointments(officer_id)
      3. merge + dedupe by company_number + flag recent resignations

    Returns `{candidates: [...], appointments: [...], summary: str}`.
    Low-signal if name is too common (e.g. "John Smith") — callers should
    require a dob or locality to disambiguate before acting on it.
    """
    candidates = await search_officers(name, limit=max_officers)
    if not candidates:
        return {"candidates": [], "appointments": [], "summary": "No matching officers."}
    all_apts: list[dict] = []
    seen_companies: set[str] = set()
    for cand in candidates[:max_officers]:
        oid = cand.get("officer_id") or ""
        if not oid:
            continue
        apts = await get_officer_appointments(oid, limit=max_apts_per_officer)
        for a in apts:
            cnum = a.get("company_number") or ""
            if cnum and cnum in seen_companies:
                continue
            if cnum:
                seen_companies.add(cnum)
            a["matched_via_officer_id"] = oid
            a["matched_via_name"] = cand.get("title", "")
            all_apts.append(a)
    current_count = sum(1 for a in all_apts if a.get("is_current"))
    resigned_last_12mo = 0
    try:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        cutoff = (_dt.now(_tz.utc) - _td(days=365)).date()
        for a in all_apts:
            r = a.get("resigned_on")
            if r:
                try:
                    if _dt.fromisoformat(r).date() >= cutoff:
                        resigned_last_12mo += 1
                except Exception:
                    pass
    except Exception:
        pass
    summary = (
        f"{len(candidates)} officer candidate(s) matched '{name}' — "
        f"{len(all_apts)} appointments across {len(seen_companies)} UK companies "
        f"({current_count} current, {resigned_last_12mo} resigned in last 12mo)."
    )
    if len(candidates) > 1:
        summary += " ⚠️ Multiple candidates — dob or locality required to disambiguate."
    return {
        "candidates": candidates,
        "appointments": all_apts,
        "companies_total": len(seen_companies),
        "appointments_current": current_count,
        "appointments_resigned_last_12mo": resigned_last_12mo,
        "summary": summary,
    }


@fail_wire(module="companies_house", gap_type="api_missing")
async def get_filing_history(company_number: str, limit: int = 10) -> list[dict]:
    """Get recent filing history."""
    data = await _get(f"/company/{company_number}/filing-history?items_per_page={limit}")
    if not data:
        return []
    items = data.get("items") or []
    return [
        {
            "category": item.get("category"),
            "type": item.get("type"),
            "description": item.get("description"),
            "date": item.get("date"),
            "action_date": item.get("action_date"),
        }
        for item in items
    ]


# ── High-level investigation helper ────────────────────────────────────────

@fail_wire(module="companies_house", gap_type="api_missing")
async def investigate_uk_entity(
    company_number: str | None = None,
    company_name: str | None = None,
) -> dict:
    """Full UK entity investigation — profile + officers + PSC + filings.

    Pass either company_number (direct lookup) or company_name (search first).
    Returns a structured dict ready for the ghost detection checklist.
    """
    if not is_enabled():
        return {"error": "Companies House integration disabled"}

    # Resolve company number from name if needed
    if not company_number and company_name:
        results = await search_companies(company_name, limit=3)
        if not results:
            return {
                "found": False,
                "query": company_name,
                "error": "No UK company found matching this name",
            }
        # Take the best match (first result)
        company_number = results[0].get("company_number")

    if not company_number:
        return {"error": "No company number or name provided"}

    # Fetch all data in parallel-ish (sequential but fast)
    profile = await get_company_profile(company_number)
    if not profile:
        return {
            "found": False,
            "company_number": company_number,
            "error": "Company not found at Companies House",
        }

    officers = await get_officers(company_number)
    psc = await get_psc(company_number)
    filings = await get_filing_history(company_number, limit=10)

    current_officers = [o for o in officers if o.get("is_current")]
    current_psc = [p for p in psc if p.get("is_current")]

    # Ghost detection signals
    ghost_signals = []
    creation_date = profile.get("date_of_creation", "")
    if creation_date:
        try:
            created = datetime.strptime(creation_date, "%Y-%m-%d")
            age_days = (datetime.now() - created).days
            if age_days < 730:  # less than 2 years
                ghost_signals.append(
                    f"RECENT INCORPORATION: {creation_date} ({age_days} days ago)"
                )
        except ValueError:
            pass

    if not current_officers:
        ghost_signals.append("NO CURRENT DIRECTORS listed at Companies House")

    if not current_psc:
        ghost_signals.append("NO PSC (beneficial ownership) disclosed")

    if len(filings) == 0:
        ghost_signals.append("ZERO FILINGS — no accounts or confirmation statements")

    if profile.get("has_been_liquidated"):
        ghost_signals.append("COMPANY HAS BEEN LIQUIDATED")

    if profile.get("has_insolvency_history"):
        ghost_signals.append("INSOLVENCY HISTORY on record")

    # Check if registered at a known formation agent address
    addr = profile.get("registered_address", {})
    addr_line = f"{addr.get('line1', '')} {addr.get('postal_code', '')}".strip()
    _FORMATION_AGENT_MARKERS = [
        "128 city road", "20-22 wenlock road", "71-75 shelton street",
        "86-90 paul street", "Unit 4E Enterprise Court",
        "suite", "floor", "virtual office",
    ]
    addr_lower = addr_line.lower()
    for marker in _FORMATION_AGENT_MARKERS:
        if marker in addr_lower:
            ghost_signals.append(
                f"REGISTERED AT KNOWN FORMATION AGENT ADDRESS: {addr_line}"
            )
            break

    status = profile.get("company_status", "")
    if status and status != "active":
        ghost_signals.append(f"COMPANY STATUS: {status} (not active)")

    investigation = {
        "found": True,
        "company_number": company_number,
        "profile": profile,
        "officers": {
            "current": current_officers,
            "past": [o for o in officers if not o.get("is_current")],
            "total": len(officers),
        },
        "psc": {
            "current": current_psc,
            "total": len(psc),
        },
        "filings": {
            "recent": filings,
            "total_shown": len(filings),
        },
        "ghost_signals": ghost_signals,
        "ghost_signal_count": len(ghost_signals),
        "risk_note": (
            f"⚠️ {len(ghost_signals)} ghost detection signal(s) found"
            if ghost_signals else "No ghost detection signals from Companies House data"
        ),
    }

    # Brain signal — every UK entity investigation is a primary-source
    # compliance signal. Ghost-signal count feeds capability_gap so the
    # predictor warns on similar formation-agent / shell-company patterns.
    try:
        from . import brain_hook as _bh
        company_name_resolved = profile.get("company_name", "") or company_number
        await _bh.absorb(
            module="companies_house",
            summary=(
                f"Companies House investigation: {company_name_resolved} "
                f"({company_number}) — {len(ghost_signals)} ghost signals, "
                f"{len(current_officers)} directors, {len(current_psc)} PSC"
            ),
            detail="; ".join(ghost_signals[:6])[:1500] if ghost_signals else
                   f"profile={profile.get('company_status','?')}, accounts={profile.get('accounts',{}).get('next_due','')}",
            entity_name=company_name_resolved,
            success=True,
            gap_type=("shell_company_pattern" if len(ghost_signals) >= 3 else None),
            gap_detail=(f"{len(ghost_signals)} ghost signals on {company_name_resolved}: "
                        f"{', '.join(ghost_signals[:3])}"
                        if len(ghost_signals) >= 3 else None),
            confidence="CONFIRMED",
        )
    except Exception:
        pass

    return investigation


@fail_wire(module="companies_house", gap_type="api_missing")
def format_for_prompt(investigation: dict) -> str:
    """Format a CH investigation result as a context block for the LLM prompt."""
    if not investigation.get("found"):
        return ""

    profile = investigation.get("profile", {})
    officers = investigation.get("officers", {})
    psc = investigation.get("psc", {})
    filings = investigation.get("filings", {})
    signals = investigation.get("ghost_signals", [])

    lines = [
        "\n[COMPANIES HOUSE — UK REGISTRY DATA]",
        f"Company: {profile.get('company_name')} ({profile.get('company_number')})",
        f"Status: {profile.get('company_status')} | Type: {profile.get('company_type')}",
        f"Incorporated: {profile.get('date_of_creation')}",
        f"SIC codes: {', '.join(profile.get('sic_codes', []))}",
    ]

    addr = profile.get("registered_address", {})
    addr_str = ", ".join(v for v in [addr.get("line1"), addr.get("locality"), addr.get("postal_code")] if v)
    if addr_str:
        lines.append(f"Registered address: {addr_str}")

    if officers.get("current"):
        lines.append(f"\nDirectors ({len(officers['current'])} current):")
        for o in officers["current"][:5]:
            lines.append(f"  - {o['name']} ({o.get('role', 'director')}, appointed {o.get('appointed_on', '?')})")

    if psc.get("current"):
        lines.append(f"\nPSC / Beneficial Ownership ({len(psc['current'])} current):")
        for p in psc["current"][:5]:
            controls = ", ".join(p.get("natures_of_control", []))[:100]
            lines.append(f"  - {p['name']} — {controls}")
    else:
        lines.append("\nPSC: NONE DISCLOSED")

    if filings.get("recent"):
        lines.append(f"\nRecent filings ({filings['total_shown']}):")
        for f_item in filings["recent"][:5]:
            lines.append(f"  - {f_item.get('date', '?')}: {f_item.get('description', f_item.get('category', '?'))}")

    if signals:
        lines.append(f"\n⚠️ GHOST DETECTION SIGNALS ({len(signals)}):")
        for s in signals:
            lines.append(f"  - {s}")

    return "\n".join(lines)

# R-F2119 §21a — wire failure handler for companies_house
try:
    wire_failure(module="companies_house", detail="module shutdown",
                gap_type="engine_failure", source="companies_house:shutdown")
except Exception:
    pass

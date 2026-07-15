"""
ARIA Conflict Tracker — ACLED-powered kinetic event intelligence.

ACLED (Armed Conflict Location & Event Data) is the gold-standard open dataset
for tracking battles, attacks, riots, protests, fatalities, and political
violence across Africa, MENA, Asia, Latin America, and parts of Europe.

Why ARIA needs this:
  - Defence procurement is REACTIVE: a buyer's appetite spikes after a security
    incident. ARIA needs to correlate "Mozambique cabo delgado attack uptick"
    with "FADM ammunition procurement window."
  - Compliance: a country experiencing rapid escalation may move from MEDIUM
    to HIGH risk overnight (Sudan 2023, Niger 2023). Static lists lag.
  - Counter-IED: tracking IED incidents informs which capabilities are in
    demand and which OEMs should be approached.

ACLED API (requires a free myACLED account — https://acleddata.com/register/):
  Verified against https://acleddata.com/api-documentation/getting-started
  on 2026-07-15 (R-F2627).
  - Token:    POST https://acleddata.com/oauth/token
              username=<ACLED_EMAIL> password=<ACLED_PASSWORD>
              grant_type=password client_id=acled
              -> access_token (valid 24h) + refresh_token (valid 14d)
  - Endpoint: GET https://acleddata.com/api/acled/read
              Authorization: Bearer <access_token>
  - Returns JSON with event_date, event_type, sub_event_type, country, location,
    fatalities, actors, source.

  R-F2627 — this module previously called the LEGACY endpoint
  `https://api.acleddata.com/acled/read?key=&email=`. That host + auth model is
  GONE from ACLED's current documentation, so every call failed and silently
  degraded to GDELT: ARIA served lower-fidelity data while reporting nothing
  wrong. `ACLED_API_KEY` is dead — the API it authenticated no longer exists.
  Credentials are now ACLED_EMAIL + ACLED_PASSWORD (CLAUDE.md §18).

If ACLED credentials are not configured, this module falls back to the GDELT
Project's free DOC 2.0 API which provides similar (lower-fidelity) event data.
Unconfigured is NOT an error (gate #5 is operator-owned) and stays quiet; but a
CONFIGURED provider that fails is reported to the brain (§21a) — silent
degradation is exactly how the dead legacy endpoint hid for months.

Module-level state:
    Recent events cached in Redis (24h TTL) so repeated queries don't hit the API.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import os
import time as _t
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import redis_store as rs
from . import proactive

logger = logging.getLogger("aria.conflict")

# R-F2627 — ACLED's CURRENT API (the legacy api.acleddata.com host is dead).
ACLED_TOKEN_URL = "https://acleddata.com/oauth/token"
ACLED_BASE = "https://acleddata.com/api/acled/read"
GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
CACHE_TTL = 6 * 3600  # 6 hours

# ── ACLED OAuth token cache ────────────────────────────────────────────────
# The access token is valid 24h; re-authenticating on every fetch would hammer
# ACLED and risk a rate-limit ban. Module-level is sufficient: the brain runs
# WEB_CONCURRENCY=1, so one process owns all fetches.
_TOKEN_CACHE: dict[str, Any] = {"access_token": None, "expires_at": 0.0, "refresh_token": None}
_TOKEN_SAFETY_MARGIN_S = 300  # renew 5 min early rather than race the expiry

# ── Auto-investigation dedup for CRITICAL escalations ──────────────────────
# Key: country ISO → timestamp of last auto-investigation trigger.
# Prevents re-triggering the same country within 6 hours.
_ESCALATION_ALERT_COOLDOWN = 6 * 3600  # seconds
_ESCALATION_ALERT_KEY = "crucix:conflict:escalation_alerts"

# Country name → ISO3 (ACLED uses ISO3 codes)
ISO3_MAP = {
    # Lusophone Africa — ARIA's incumbent zone
    "angola": "AGO", "mozambique": "MOZ", "guinea-bissau": "GNB", "guinea bissau": "GNB",
    "cape verde": "CPV", "são tomé": "STP", "sao tome": "STP",
    # West Africa
    "nigeria": "NGA", "mali": "MLI", "burkina faso": "BFA", "niger": "NER",
    "chad": "TCD", "ghana": "GHA", "senegal": "SEN", "ivory coast": "CIV", "cote d'ivoire": "CIV",
    "cameroon": "CMR", "guinea": "GIN", "sierra leone": "SLE", "liberia": "LBR",
    # East Africa
    "kenya": "KEN", "ethiopia": "ETH", "somalia": "SOM", "uganda": "UGA",
    "tanzania": "TZA", "rwanda": "RWA", "south sudan": "SSD", "sudan": "SDN",
    "djibouti": "DJI", "eritrea": "ERI",
    # Southern Africa
    "south africa": "ZAF", "zimbabwe": "ZWE", "botswana": "BWA", "namibia": "NAM",
    "zambia": "ZMB", "malawi": "MWI", "drc": "COD", "congo": "COD",
    # MENA
    "egypt": "EGY", "libya": "LBY", "tunisia": "TUN", "algeria": "DZA", "morocco": "MAR",
    "iraq": "IRQ", "syria": "SYR", "yemen": "YEM", "lebanon": "LBN", "jordan": "JOR",
    "saudi arabia": "SAU", "uae": "ARE", "oman": "OMN", "iran": "IRN",
    # Asia
    "afghanistan": "AFG", "pakistan": "PAK", "india": "IND", "bangladesh": "BGD",
    "myanmar": "MMR", "indonesia": "IDN", "philippines": "PHL", "vietnam": "VNM",
    "thailand": "THA", "cambodia": "KHM",
    # Latin America
    "colombia": "COL", "venezuela": "VEN", "mexico": "MEX", "haiti": "HTI",
    "brazil": "BRA", "peru": "PER", "el salvador": "SLV", "honduras": "HND",
    # Europe
    "ukraine": "UKR", "russia": "RUS", "turkey": "TUR",
}


def _iso3(country: str) -> str:
    return ISO3_MAP.get(country.lower().strip(), country.upper()[:3])


# ── ACLED fetch ─────────────────────────────────────────────────────────────

def _acled_configured() -> bool:
    """True iff the operator has set up a myACLED account (gate #5).

    ACLED_API_KEY is deliberately NOT accepted: the legacy key+email API it
    authenticated no longer exists, so honouring it would imply a working
    provider that cannot work (R-F2627).
    """
    return bool(os.getenv("ACLED_EMAIL", "").strip() and os.getenv("ACLED_PASSWORD", "").strip())


def _invalidate_token_cache() -> None:
    """Drop the cached token — used on 401 (revoked/expired) and by tests."""
    _TOKEN_CACHE.update({"access_token": None, "expires_at": 0.0, "refresh_token": None})


async def _acled_token(force: bool = False) -> str | None:
    """Return a valid ACLED OAuth access token, or None.

    R-F2627 — POST /oauth/token per ACLED's documented password grant. Cached
    until `expires_in` (minus a safety margin) so repeated country lookups reuse
    one token. Returns None when unconfigured (quiet — gate #5 is operator-owned)
    and records a gap when CONFIGURED credentials fail (§21a).
    """
    if not _acled_configured():
        return None

    now = _t.time()
    if not force and _TOKEN_CACHE["access_token"] and now < float(_TOKEN_CACHE["expires_at"] or 0):
        return _TOKEN_CACHE["access_token"]

    email = os.getenv("ACLED_EMAIL", "").strip()
    password = os.getenv("ACLED_PASSWORD", "").strip()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:  # no-breaker: best-effort OSINT auth; a breaker would hide conflict signals
            resp = await client.post(
                ACLED_TOKEN_URL,
                data={
                    "username": email,
                    "password": password,
                    "grant_type": "password",
                    "client_id": "acled",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as e:
        wire_failure(
            module="conflict_tracker",
            detail=f"ACLED OAuth request failed: {e}",
            gap_type="source_failure",
            source="conflict_tracker:_acled_token",
        )
        return None

    if resp.status_code != 200:
        wire_failure(
            module="conflict_tracker",
            detail=(
                f"ACLED OAuth rejected the configured credentials (HTTP {resp.status_code}): "
                f"{str(getattr(resp, 'text', ''))[:200]} — check ACLED_EMAIL/ACLED_PASSWORD "
                f"against the myACLED account"
            ),
            gap_type="source_failure",
            source="conflict_tracker:_acled_token",
        )
        return None

    try:
        payload = resp.json() or {}
    except Exception as e:
        wire_failure(
            module="conflict_tracker",
            detail=f"ACLED OAuth returned unparseable JSON: {e}",
            gap_type="source_failure",
            source="conflict_tracker:_acled_token",
        )
        return None

    token = payload.get("access_token")
    if not token:
        wire_failure(
            module="conflict_tracker",
            detail=f"ACLED OAuth response missing access_token: {str(payload)[:200]}",
            gap_type="source_failure",
            source="conflict_tracker:_acled_token",
        )
        return None

    try:
        expires_in = int(payload.get("expires_in") or 86400)
    except (TypeError, ValueError):
        expires_in = 86400
    _TOKEN_CACHE.update({
        "access_token": token,
        "expires_at": now + max(60, expires_in - _TOKEN_SAFETY_MARGIN_S),
        "refresh_token": payload.get("refresh_token"),
    })
    logger.info("ACLED: OAuth token acquired (expires_in=%ss)", expires_in)
    return token


async def _fetch_acled(country_iso3: str, days: int = 30) -> list[dict]:
    """Fetch recent ACLED events for a country. Falls back to GDELT if unavailable.

    R-F2627 — uses ACLED's CURRENT OAuth API. Unconfigured falls back QUIETLY
    (gate #5 is operator-owned, not a bug); a CONFIGURED provider that fails is
    reported to the brain so the degradation can never be silent again.
    """
    if not _acled_configured():
        # Not set up yet — expected state until the operator registers a myACLED
        # account and sets the secrets. Not a failure; do not spam the brain.
        return await _fetch_gdelt_fallback(country_iso3, days)

    token = await _acled_token()
    if not token:
        # _acled_token already recorded the specific reason via wire_failure.
        return await _fetch_gdelt_fallback(country_iso3, days)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    params: dict[str, str] = {
        "iso3": country_iso3,
        "event_date": f"{cutoff}|{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "event_date_where": "BETWEEN",
        "limit": "200",
        "_format": "json",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:  # no-breaker: conflict tracker is best-effort OSINT; breaker would hide conflict signals
            resp = await client.get(
                ACLED_BASE, params=params, headers={"Authorization": f"Bearer {token}"}
            )
            if resp.status_code == 401:
                # Token revoked or expired early — re-auth ONCE, then give up.
                _invalidate_token_cache()
                token = await _acled_token(force=True)
                if token:
                    resp = await client.get(
                        ACLED_BASE, params=params, headers={"Authorization": f"Bearer {token}"}
                    )

            if resp.status_code != 200:
                wire_failure(
                    module="conflict_tracker",
                    detail=(
                        f"ACLED read failed for {country_iso3} (HTTP {resp.status_code}) — "
                        f"serving GDELT fallback, so conflict data is LOWER fidelity"
                    ),
                    gap_type="source_failure",
                    source="conflict_tracker:_fetch_acled",
                )
                return await _fetch_gdelt_fallback(country_iso3, days)

            data = resp.json() or {}
            events = data.get("data", []) or []
            # Provenance: GDELT rows carry _provider='gdelt'; tag ACLED rows too so
            # a consumer can always tell which source backed the verdict.
            for _e in events:
                if isinstance(_e, dict):
                    _e["_provider"] = "acled"
            logger.info("ACLED: fetched %d events for %s (last %dd)", len(events), country_iso3, days)
            wire_success(
                module="conflict_tracker",
                summary=f"ACLED: {len(events)} event(s) for {country_iso3} (last {days}d)",
                entity_name=country_iso3,
                source_id="conflict_tracker:_fetch_acled",
            )
            return events
    except httpx.HTTPError as e:
        wire_failure(
            module="conflict_tracker",
            detail=f"ACLED request failed for {country_iso3}: {e} — serving GDELT fallback",
            gap_type="source_failure",
            source="conflict_tracker:_fetch_acled",
        )
        return await _fetch_gdelt_fallback(country_iso3, days)


async def _fetch_gdelt_fallback(country_iso3: str, days: int = 30) -> list[dict]:
    """GDELT 2.0 DOC API fallback when ACLED is unavailable.

    Less precise (event records vs raw articles) but free and uncapped.
    """
    timespan = f"{days}d"
    query = f'(attack OR conflict OR clash OR insurgency OR ambush OR airstrike OR coup) sourcecountry:{country_iso3}'
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                GDELT_DOC,
                params={
                    "query": query,
                    "mode": "ArtList",
                    "format": "json",
                    "timespan": timespan,
                    "maxrecords": "100",
                },
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            articles = data.get("articles", []) or []
            # Normalise to ACLED-like shape
            normalised = []
            for a in articles:
                normalised.append({
                    "event_date": (a.get("seendate") or "")[:10],
                    "event_type": "Reported incident",
                    "sub_event_type": "GDELT article",
                    "country": country_iso3,
                    "location": a.get("location", ""),
                    "actor1": a.get("domain", "Unknown"),
                    "fatalities": 0,
                    "notes": a.get("title", "")[:300],
                    "source": a.get("domain", "GDELT"),
                    "source_url": a.get("url", ""),
                    "_provider": "gdelt",
                })
            return normalised
    except httpx.HTTPError as e:
        logger.warning("GDELT fallback failed: %s", e)
        return []


# ── Cache layer ─────────────────────────────────────────────────────────────

async def _cached_events(country: str, days: int = 30) -> list[dict]:
    iso = _iso3(country)
    cache_key = f"crucix:aria:conflict:{iso}:{days}"
    cached = await rs.get_json(cache_key)
    if cached and isinstance(cached, list):
        return cached
    events = await _fetch_acled(iso, days)
    if events:
        await rs.set_json(cache_key, events, ex=CACHE_TTL)
    return events


# ── Public API ──────────────────────────────────────────────────────────────

async def get_recent_events(country: str, days: int = 30, limit: int = 50) -> dict:
    """Return recent kinetic events for a country with summary statistics.

    Output shape matches what ARIA's chat layer can ingest as context:
        {
            country, iso, days, total_events, fatalities,
            by_type: {Battles: 12, Protests: 8, ...},
            top_locations: [...],
            recent_events: [...],
            escalation_score: 0..100,
        }
    """
    events = await _cached_events(country, days)
    if not events:
        return {
            "country": country, "iso": _iso3(country), "days": days,
            "total_events": 0, "events": [], "escalation_score": 0,
            "note": "No events returned by ACLED/GDELT — country may be quiet or out of coverage.",
        }

    by_type: dict[str, int] = {}
    by_location: dict[str, int] = {}
    fatalities = 0
    for e in events:
        et = e.get("event_type", "Unknown") or "Unknown"
        by_type[et] = by_type.get(et, 0) + 1
        loc = e.get("location") or e.get("admin1") or ""
        if loc:
            by_location[loc] = by_location.get(loc, 0) + 1
        try:
            fatalities += int(e.get("fatalities", 0) or 0)
        except (ValueError, TypeError):
            pass

    # Escalation score: weighted blend of recency, fatality count, event volume
    week_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    recent_count = sum(1 for e in events if (e.get("event_date") or "") >= week_cutoff)
    older_count = max(1, len(events) - recent_count)
    momentum = recent_count / older_count  # >1 means escalating
    escalation = min(100, int(
        (recent_count * 2.0)
        + (fatalities * 0.3)
        + (momentum * 15)
        + (5 if by_type.get("Battles", 0) > 0 else 0)
    ))

    top_locations = sorted(by_location.items(), key=lambda x: -x[1])[:5]
    recent_events = sorted(events, key=lambda x: x.get("event_date", ""), reverse=True)[:limit]

    esc_label = (
        "CRITICAL" if escalation >= 75 else
        "HIGH" if escalation >= 50 else
        "MODERATE" if escalation >= 25 else
        "LOW"
    )

    result = {
        "country": country,
        "iso": _iso3(country),
        "days": days,
        "total_events": len(events),
        "fatalities": fatalities,
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "top_locations": [{"location": l, "count": c} for l, c in top_locations],
        "escalation_score": escalation,
        "escalation_label": esc_label,
        "momentum": round(momentum, 2),
        "recent_events": [
            {
                "date": e.get("event_date"),
                "type": e.get("event_type"),
                "sub_type": e.get("sub_event_type"),
                "location": e.get("location"),
                "actor1": e.get("actor1"),
                "actor2": e.get("actor2"),
                "fatalities": e.get("fatalities"),
                "notes": (e.get("notes") or "")[:300],
                "source": e.get("source"),
                "source_url": e.get("source_url") or e.get("source_scale"),
            }
            for e in recent_events
        ],
        "provider": events[0].get("_provider", "acled") if events else "none",
    }

    # ── AUTO-INVESTIGATE: push proactive alert on CRITICAL escalation ──────
    # 2026-04-12: when escalation hits CRITICAL, push an alert with
    # auto_investigate=true so the WhatsApp listener fires a background
    # investigation automatically. Dedup: 1 alert per country per 6h.
    if esc_label == "CRITICAL":
        try:
            iso = _iso3(country)
            dedup = await rs.get_json(_ESCALATION_ALERT_KEY) or {}
            last_alert = dedup.get(iso, 0)
            if _t.time() - last_alert > _ESCALATION_ALERT_COOLDOWN:
                top_locs = ", ".join(l["location"] for l in top_locations[:3]) if top_locations else "N/A"
                top_events_summary = ""
                for e in recent_events[:3]:
                    top_events_summary += f"\n• [{e.get('event_date','?')}] {e.get('event_type','?')} — {(e.get('notes') or '')[:120]}"

                await proactive.push_alert({
                    "type": "escalation_critical",
                    "title": f"CRITICAL ESCALATION — {country}",
                    "severity": "critical",
                    "body": (
                        f"Escalation: {escalation}/100 | Events (30d): {len(events)} | "
                        f"Fatalities: {fatalities} | Momentum: {round(momentum, 2)}x\n"
                        f"Hotspots: {top_locs}\n"
                        f"Recent:{top_events_summary}\n\n"
                        f"_Auto-investigation queued. Stand by for deep-dive._"
                    ),
                    "metadata": {
                        "country": country,
                        "iso": iso,
                        "escalation_score": escalation,
                        "total_events": len(events),
                        "fatalities": fatalities,
                        "momentum": round(momentum, 2),
                        "auto_investigate": True,
                    },
                })
                dedup[iso] = _t.time()
                await rs.set_json(_ESCALATION_ALERT_KEY, dedup, ex=7 * 86400)
                logger.info("CRITICAL escalation alert pushed for %s (score %d) — auto-investigate queued", country, escalation)
            else:
                logger.debug("CRITICAL escalation for %s suppressed (last alert %.0fm ago)", country, (_t.time() - last_alert) / 60)
        except Exception as e:
            logger.warning("Failed to push escalation alert for %s: %s", country, e)

    # ── Brain hook: feed conflict intel to learning ──
    try:
        from . import brain_hook
        await brain_hook.absorb(
            module="conflict_tracker",
            summary=f"Conflict tracker {country}: escalation={escalation}/100 ({esc_label}), {len(events)} events, {fatalities} fatalities, momentum={round(momentum, 2)}x",
            entity_name=country,
            success=True,
            confidence="ASSESSED",
        )
    except Exception as _bh:
        logger.debug("conflict_tracker brain_hook failed: %s", _bh)

    return result


async def correlate_with_procurement(country: str, days: int = 60) -> dict:
    """Cross-reference conflict escalation with recent procurement signals.

    Returns a structured assessment ARIA can include in market briefs:
      - Is the country experiencing escalation?
      - Has procurement activity changed in the same window?
      - What capabilities should rise in demand based on event type mix?
    """
    events_summary = await get_recent_events(country, days=days, limit=20)
    escalation = events_summary.get("escalation_score", 0)
    by_type = events_summary.get("by_type", {})

    # Capability demand projection from event mix
    capability_signals: list[str] = []
    if by_type.get("Battles", 0) >= 3:
        capability_signals.append("Small arms, ammunition, IFV/APC, body armour, ISR")
    if by_type.get("Explosions/Remote violence", 0) >= 3:
        capability_signals.append("Counter-IED equipment, EOD robots, mine-resistant vehicles")
    if by_type.get("Strategic developments", 0) >= 2:
        capability_signals.append("Border security, surveillance towers, UAS, communications")
    if by_type.get("Violence against civilians", 0) >= 5:
        capability_signals.append("Armoured patrol vehicles, body cameras, riot control")

    # Procurement window assessment
    if escalation >= 50:
        window = "OPEN — escalation creates urgent procurement appetite. Approach within 30 days."
    elif escalation >= 25:
        window = "WATCH — moderate activity. Monitor for tipping point or contract releases."
    else:
        window = "STABLE — no immediate trigger. Maintain relationship cadence."

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="conflict_tracker",
                     summary="conflict_tracker module active",
                     source_id="conflict_tracker:init")
    except Exception:
        try:
            wire_failure(module="conflict_tracker", detail="module init failed",
                        gap_type="engine_failure", source="conflict_tracker:init")
        except Exception:
            pass

    return {
        "country": country,
        "escalation_score": escalation,
        "escalation_label": events_summary.get("escalation_label"),
        "events_last_period": events_summary.get("total_events", 0),
        "fatalities_last_period": events_summary.get("fatalities", 0),
        "event_mix": by_type,
        "projected_capability_demand": capability_signals,
        "procurement_window": window,
        "recommended_action": (
            "Escalate to BD for immediate engagement" if escalation >= 50 else
            "Add to weekly watchlist; brief Arkmurus partners" if escalation >= 25 else
            "No action — file under routine monitoring"
        ),
        "disclaimer": "Based on ACLED/GDELT open-source event data. Cross-check with classified or commercial sources before acting.",
    }

"""ACLED — Armed Conflict Location & Event Data — entity-scoped lookup.

What ACLED provides for DD
──────────────────────────
  - Recent armed-conflict / political-violence events by country or actor
  - Named-actor queries: "was <entity/person> involved in any event?"
  - Fatality counts, dates, source reports, event types
  - Coverage: 200+ countries, real-time updates

For DD this is the "operational environment" signal — is the target's
jurisdiction currently stable? Is the target entity itself tied to
armed groups? Is a supply chain crossing active conflict zones?

Access model
────────────
ACLED requires registration (free for non-commercial; commercial tiers
for enterprise use). Two auth strategies both supported here so we don't
get locked out if one changes:

  1. Cookie-based browser-style login — POST /user/login?_format=json
     with email+password, reuse the session cookie on subsequent GETs.
  2. OAuth token — POST /oauth/token, use the Bearer token in the
     Authorization header.

Credentials come from env:
  - ACLED_EMAIL     (required)
  - ACLED_PASSWORD  (required)

If either is missing, lookup() returns a graceful error_result and
is_available() returns False — DD orchestrator will flag the capability
gap but continue.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from . import _common

logger = logging.getLogger("aria.sources.acled")

_SOURCE = "acled"
_AUTH = "oauth"

_LOGIN_URL = "https://acleddata.com/user/login?_format=json"
_TOKEN_URL = "https://acleddata.com/oauth/token"
_API_BASE = "https://acleddata.com/api/acled/read"
_UA = "ARIA-DD-Orchestrator research@arkmurus.com"

_SESSION_TTL_S = 3600  # 1h
_SESSION: dict[str, Any] = {
    "method": None,      # "cookie" | "token"
    "cookies": None,
    "token": None,
    "expires_at": 0.0,
}
_SESSION_LOCK = asyncio.Lock()


def _creds() -> tuple[str, str] | None:
    email = (os.getenv("ACLED_EMAIL") or "").strip()
    password = (os.getenv("ACLED_PASSWORD") or "").strip()
    if not email or not password:
        return None
    return email, password


async def _ensure_session() -> dict | None:
    """Log in if we don't have a fresh session. Returns the session dict
    or None if auth failed (caller should surface a capability gap).
    """
    async with _SESSION_LOCK:
        now = time.time()
        if _SESSION["method"] and now < _SESSION["expires_at"]:
            return _SESSION

        c = _creds()
        if c is None:
            return None
        email, password = c

        # Strategy 1: cookie-based
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                r = await client.post(
                    _LOGIN_URL,
                    headers={"Content-Type": "application/json", "User-Agent": _UA},
                    json={"name": email, "pass": password},
                )
                if r.status_code < 400:
                    cookies = dict(r.cookies)
                    if cookies:
                        _SESSION["method"] = "cookie"
                        _SESSION["cookies"] = cookies
                        _SESSION["token"] = None
                        _SESSION["expires_at"] = now + _SESSION_TTL_S
                        logger.info("[acled] session established via cookie login")
                        return _SESSION
        except Exception as e:
            logger.debug("[acled] cookie login failed: %s", e)

        # Strategy 2: OAuth token
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    _TOKEN_URL,
                    data={
                        "username": email,
                        "password": password,
                        "grant_type": "password",
                        "client_id": "acled",
                    },
                    headers={"User-Agent": _UA},
                )
                if r.status_code < 400:
                    data = r.json()
                    token = data.get("access_token")
                    if token:
                        _SESSION["method"] = "token"
                        _SESSION["cookies"] = None
                        _SESSION["token"] = token
                        _SESSION["expires_at"] = now + min(
                            _SESSION_TTL_S, int(data.get("expires_in", _SESSION_TTL_S)),
                        )
                        logger.info("[acled] session established via OAuth token")
                        return _SESSION
        except Exception as e:
            logger.debug("[acled] OAuth login failed: %s", e)

        logger.warning("[acled] both auth strategies failed — DD will flag capability gap")
        return None


async def _fetch(params: dict, timeout: float = 20.0) -> list[dict] | None:
    """Call /api/acled/read with a params dict. Returns the data list or None."""
    sess = await _ensure_session()
    if sess is None:
        return None

    headers = {"User-Agent": _UA, "Accept": "application/json"}
    cookies = None
    if sess["method"] == "token":
        headers["Authorization"] = f"Bearer {sess['token']}"
    else:
        cookies = sess.get("cookies")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(
                _API_BASE, params=params, headers=headers, cookies=cookies,
            )
            r.raise_for_status()
            data = r.json()
            # ACLED returns {success: bool, data: [...], count: int}
            if isinstance(data, dict):
                return data.get("data") or []
            if isinstance(data, list):
                return data
            return []
    except Exception as e:
        logger.warning("[acled] fetch failed: %s", e)
        return None


async def lookup(
    name: str,
    *,
    country: str = "",
    days: int = 180,
    limit: int = 50,
) -> dict:
    """Entity-scoped ACLED lookup.

    Two query modes (both run in parallel if both hints present):
      - Actor query: search for `name` in actor1 / actor2 / assoc_actor
        fields (captures armed groups, militias, named state actors)
      - Country query: if `country` is supplied, also pull the N-day
        event tempo for the jurisdiction so the DD report can reason
        about operational-environment risk

    Severity for the orchestrator:
      - actor hit = RED (entity named in political-violence event)
      - country-level high event tempo = AMBER (enhanced DD on
        operational continuity)
    """
    started = time.time()
    query = {"name": name, "country": country, "days": days}
    result = _common.empty_result(
        _SOURCE, query, auth=_AUTH,
        citation_url="https://acleddata.com/dashboard/",
    )

    c = _creds()
    if c is None:
        return _common.error_result(
            _SOURCE, query,
            "ACLED_EMAIL / ACLED_PASSWORD not configured — free registration at acleddata.com",
            auth=_AUTH, started_at=started,
        )

    if not name and not country:
        return _common.error_result(
            _SOURCE, query, "must supply name and/or country",
            auth=_AUTH, started_at=started,
        )

    try:
        from datetime import datetime, timedelta
        start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        tasks = []
        # Actor query
        if name:
            params_actor = {
                "event_date": f"{start_date}|NOW",
                "event_date_where": "BETWEEN",
                "actor1": name,
                "actor1_where": "LIKE",
                "limit": limit,
            }
            tasks.append(_fetch(params_actor))
            params_actor2 = {
                "event_date": f"{start_date}|NOW",
                "event_date_where": "BETWEEN",
                "actor2": name,
                "actor2_where": "LIKE",
                "limit": limit,
            }
            tasks.append(_fetch(params_actor2))
        # Country-tempo query
        if country:
            params_country = {
                "event_date": f"{start_date}|NOW",
                "event_date_where": "BETWEEN",
                "country": country,
                "country_where": "LIKE",
                "limit": limit,
            }
            tasks.append(_fetch(params_country))

        results_lists = await asyncio.gather(*tasks, return_exceptions=False)

        # Flatten + dedupe by data_id
        seen_ids: set[str] = set()
        hits: list[dict] = []
        for events in results_lists:
            if not events:
                continue
            for e in events:
                eid = str(e.get("data_id") or e.get("event_id_cnty") or id(e))
                if eid in seen_ids:
                    continue
                seen_ids.add(eid)
                hits.append({
                    "name": (e.get("actor1") or e.get("actor2") or "").strip(),
                    "event_date": e.get("event_date") or "",
                    "event_type": e.get("event_type") or "",
                    "sub_event_type": e.get("sub_event_type") or "",
                    "country": e.get("country") or "",
                    "location": e.get("location") or "",
                    "fatalities": e.get("fatalities") or 0,
                    "actor1": e.get("actor1") or "",
                    "actor2": e.get("actor2") or "",
                    "notes": (e.get("notes") or "")[:500],
                    "source": e.get("source") or "",
                    "citation_url": (
                        f"https://acleddata.com/dashboard/#/dashboard?event_date="
                        f"{e.get('event_date', '')}"
                    ),
                })

        # Sort newest-first
        hits.sort(key=lambda h: h.get("event_date", ""), reverse=True)
        result["hits"] = hits[:limit]

        # Severity hint for downstream: if name matched actor fields, RED
        if name:
            actor_hits = [h for h in result["hits"] if
                          _common.similarity(name, h["actor1"]) > 0.7 or
                          _common.similarity(name, h["actor2"]) > 0.7]
            if actor_hits:
                result["severity_hint"] = "RED — entity named in political-violence events"
            elif country and result["hits"]:
                result["severity_hint"] = (
                    f"INFO — {len(result['hits'])} political-violence events in "
                    f"{country} over last {days}d"
                )

        return _common.finalise(result, started)
    except Exception as e:
        logger.warning("[acled] lookup failed for %r: %s", name, e)
        return _common.error_result(
            _SOURCE, query, f"{type(e).__name__}: {e}",
            auth=_AUTH, started_at=started,
        )


async def is_available() -> bool:
    """True iff credentials are configured AND a session can be established."""
    if _creds() is None:
        return False
    sess = await _ensure_session()
    return sess is not None

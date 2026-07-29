"""R-F3403 — The Gazette: the UK's official public record of insolvency notices.

WHY THIS SOURCE. Fundamental #11 (insolvency history) could be answered two ways and
neither was enough. Companies House `/insolvency` (wired by R-F3404/R-F3422) covers the
COMPANY's own formal proceedings, and only while it remains on the register. The Gazette
is the statutory publication of record: winding-up resolutions, liquidator and
administrator appointments, and — crucially — PERSONAL insolvency (bankruptcy orders,
IVAs), which no company register carries at all. For a DD that must resolve to natural
persons, that second half is the point.

VERIFIED LIVE 2026-07-29, not read off documentation:
    GET https://www.thegazette.co.uk/insolvency/notice/data.json?text=Carillion
    -> "Resolutions for Winding-up", "Appointment of Liquidators",
       "CARILLION (ASPIRE SERVICES) LIMITED"
No API key, no registration, no rate-limit headers advertised. Open Government Licence.

The `insolvency` service constrains results to Corporate Insolvency (category 24) and
Personal Insolvency (25) — so the constraint is applied by THE SOURCE rather than by a
filter of ours, which is the difference between "we searched insolvency notices" and "we
searched everything and kept what looked insolvent".

NAME MATCHING IS THE HAZARD HERE. A free-text search returns notices whose text contains
the query, and company names are not unique. So this adapter reports CANDIDATES with the
matched title, never a determination, and the caller must corroborate against a
registration number before asserting the notice is about the subject. That is the
R-F3089 class, and on insolvency it is severe: wrongly attaching a winding-up notice to
a solvent counterparty is the kind of false positive that ends a commercial
relationship.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ._common import (
    OUTCOME_EMPTY,
    OUTCOME_OK,
    OUTCOME_ERROR,
    OUTCOME_TIMEOUT,
    empty_result,
    error_result,
    finalise,
    normalise_name,
    stamp_outcome,
)

logger = logging.getLogger("aria.sources.gazette")

_BASE = "https://www.thegazette.co.uk"
_SOURCE = "gazette"
#: The Gazette's own taxonomy. 24 = Corporate Insolvency, 25 = Personal Insolvency.
CATEGORY_CORPORATE_INSOLVENCY = "24"
CATEGORY_PERSONAL_INSOLVENCY = "25"

_MAX_RESULTS = 20

#: NO `Accept` header — see the note in search_insolvency. Sending
#: `Accept: application/json` makes The Gazette return HTTP 500.
_HEADERS = {"User-Agent": "ARIA-DD/1.0 (+https://imaria.io)"}


def _entries(payload: Any) -> list[dict]:
    """The JSON feed returns `entry` as a dict when there is exactly one result and a
    list when there are several — a shape that silently drops the single-hit case if you
    assume a list. Normalise before anything reads it."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("entry")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    return []


def _title_of(entry: dict) -> str:
    t = entry.get("title")
    if isinstance(t, dict):                       # atom-style {"$": "..."}
        t = t.get("$") or t.get("#text") or ""
    return str(t or "").strip()


def _link_of(entry: dict) -> str:
    link = entry.get("link")
    if isinstance(link, dict):
        return str(link.get("@href") or link.get("href") or "").strip()
    if isinstance(link, list):
        for l in link:
            if isinstance(l, dict) and (l.get("@href") or l.get("href")):
                return str(l.get("@href") or l.get("href")).strip()
    return str(entry.get("id") or "").strip()


async def search_insolvency(
    name: str,
    *,
    personal: bool = False,
    limit: int = _MAX_RESULTS,
    timeout: float = 20.0,
) -> dict:
    """Search the official insolvency notices for `name`.

    `personal=True` searches PERSONAL insolvency (bankruptcy, IVA) instead of corporate
    — the half no company register can answer, and the one a DD needs when the chain has
    resolved to a natural person.

    Returns the canonical adapter shape. `hits` are CANDIDATES matched on notice text;
    `corroboration_required` states that plainly so no consumer can treat a name match
    as an identification.
    """
    started = time.time()
    q = (name or "").strip()
    query = {"text": q, "category": "personal" if personal else "corporate"}
    citation = f"{_BASE}/all-notices/notice"

    if len(q) < 3:
        # Not attempted on purpose — a 2-character query against a national register
        # would return noise, and every hit would be a coincidence.
        res = empty_result(_SOURCE, query, citation_url=citation)
        return stamp_outcome(res, "skipped",
                             detail="query too short to search a national register",
                             module="sources.gazette")

    params = {
        "text": q,
        "categorycode": (CATEGORY_PERSONAL_INSOLVENCY if personal
                         else CATEGORY_CORPORATE_INSOLVENCY),
        "results-page-size": str(max(1, min(50, limit))),
        "sort-by": "latest-date",
    }
    url = f"{_BASE}/insolvency/notice/data.json"

    # ── WHY THIS DOES NOT USE `http_get_json` ────────────────────────────────
    #
    # `http_get_json` always sends `Accept: application/json`, and The Gazette answers
    # that with HTTP 500. ISOLATED 2026-07-29 by holding the User-Agent constant:
    #     ARIA UA + Accept: application/json   -> 500
    #     ARIA UA, no Accept                   -> 200
    #     browser UA + Accept: application/json-> 500
    #     Accept: */*                          -> 200
    # So it is the Accept header, not the agent. The `.json` suffix in the PATH already
    # selects the representation (their docs describe `/notice/data.json` alongside
    # `data.feed` and `data.htm`), and supplying an explicit Accept on top of it breaks
    # their content negotiation.
    #
    # This is exactly the kind of failure that would otherwise present as "no insolvency
    # notices" — a false clean on the one check where a false clean is most expensive —
    # so the adapter owns its client rather than inheriting a header it must not send.
    payload = None
    fetch_error = ""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, params=params, headers=_HEADERS)
            if resp.status_code != 200:
                fetch_error = f"The Gazette returned HTTP {resp.status_code}"
            else:
                payload = resp.json()
    except Exception as exc:
        fetch_error = f"{type(exc).__name__}: {exc}"

    if payload is None:
        # A fetch failure can NEVER be reported as "no notices" — that would be a false
        # clean on an insolvency check.
        res = error_result(_SOURCE, query, fetch_error or "The Gazette did not answer",
                           citation_url=citation, started_at=started)
        return stamp_outcome(res, OUTCOME_TIMEOUT,
                             detail=fetch_error or "no response from thegazette.co.uk",
                             module="sources.gazette")

    entries = _entries(payload)
    if not isinstance(payload, dict):
        res = error_result(_SOURCE, query, "unparseable feed",
                           citation_url=citation, started_at=started)
        return stamp_outcome(res, OUTCOME_ERROR, module="sources.gazette")

    res = empty_result(_SOURCE, query, citation_url=citation)
    _nq = normalise_name(q)
    for e in entries[:limit]:
        title = _title_of(e)
        res["hits"].append({
            "title": title,
            "url": _link_of(e),
            "published": str(e.get("updated") or e.get("published") or "").strip(),
            "notice_category": "personal_insolvency" if personal else "corporate_insolvency",
            # Did the SUBJECT's name actually appear in the notice title? The Gazette
            # matches on full notice text, so a hit can be a notice that merely mentions
            # the name (an insolvency practitioner's other case, a creditor list). This
            # lets the caller separate "named in the title" from "appears somewhere".
            "subject_in_title": bool(_nq) and _nq in normalise_name(title),
        })

    res["corroboration_required"] = (
        "Matched on notice TEXT, not on a registration number. Confirm the company "
        "number or the individual's address before treating a notice as being about "
        "this subject."
    )
    finalise(res, started)
    return stamp_outcome(res, OUTCOME_OK if res["hit_count"] else OUTCOME_EMPTY,
                         module="sources.gazette")


async def search_all(name: str, *, limit: int = _MAX_RESULTS) -> dict:
    """Corporate AND personal insolvency in one call.

    Both halves are reported separately with their own outcome, because "the corporate
    search answered and found nothing" and "the personal search never ran" are different
    facts and a combined `ok` would hide the second.
    """
    corporate = await search_insolvency(name, personal=False, limit=limit)
    personal = await search_insolvency(name, personal=True, limit=limit)
    both_answered = bool(corporate.get("ok")) and bool(personal.get("ok"))
    return {
        "source": _SOURCE,
        "entity": name,
        "ok": both_answered,
        "outcome": OUTCOME_OK if both_answered else "partial",
        "corporate": corporate,
        "personal": personal,
        "hit_count": int(corporate.get("hit_count") or 0) + int(personal.get("hit_count") or 0),
        "citation_url": f"{_BASE}/all-notices/notice",
        "note": ("Both halves carry their own outcome — a combined ok would hide a "
                 "search that never ran"),
    }

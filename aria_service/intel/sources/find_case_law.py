"""R-F3442 — Find Case Law (The National Archives) judgments, by PARTY name.

WHAT IT ADDS. IS-17a ("reported court judgments naming the subject as a party") is
answered today by CourtListener (US federal) and a BAILII proxy. Find Case Law is the
National Archives' official UK judgment service and supports a genuine `party` search,
which is exactly the query a DD needs and the one a keyword search fakes badly.

THE BLOCKER IS LEGAL, NOT TECHNICAL — and that is why this is gated differently from
Registry Trust. The endpoint is free, needs no key, and allows 1000 requests / 5 minutes
per IP. But the **Open Justice Licence** forbids "computational analysis" of the records
without a separate application to The National Archives. Running a DD engine over it is
plausibly computational analysis, so ARIA does not call it until the operator confirms
the licence position:

    FIND_CASE_LAW_LICENCE_GRANTED=1

That flag is a DECLARATION BY THE OPERATOR, not a credential — there is nothing to
authenticate. It exists so that a legal decision is made deliberately and recorded, and
so that nobody discovers ARIA has been quietly doing something a licence forbids. Costing
nothing is not the same as being permitted, and this is the difference.

Because it is free rather than metered, it is NOT election-gated the way CCJ is: once the
licence is confirmed it simply runs. There is no spend to authorise, only permission to
establish. See `_gated_search_permitted` in dd_orchestrator for the metered case.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

_BASE = "https://caselaw.nationalarchives.gov.uk"
_ATOM = "{http://www.w3.org/2005/Atom}"
_TIMEOUT_S = 20.0
_MAX = 20

_WS = re.compile(r"\s+")


def is_configured() -> bool:
    """True only when the operator has confirmed the Open Justice Licence position."""
    return (os.getenv("FIND_CASE_LAW_LICENCE_GRANTED") or "").strip().lower() in (
        "1", "true", "yes", "on")


def configuration_hint() -> str:
    return (
        "Find Case Law is free and needs no key, but the Open Justice Licence forbids "
        "computational analysis without a separate application to The National Archives "
        "(caselaw.nationalarchives.gov.uk/open-justice-licence). This is a legal "
        "decision, not a credential. Once the position is confirmed, set "
        "FIND_CASE_LAW_LICENCE_GRANTED=1 — no code change is needed."
    )


def _norm(s: str) -> str:
    return _WS.sub(" ", str(s or "")).strip()


def parse_atom(xml_text: str, *, limit: int = _MAX) -> list[dict]:
    """Parse the Atom feed into judgment rows. Pure, so it is testable without network."""
    out: list[dict] = []
    root = ET.fromstring(xml_text)
    for entry in root.iter(f"{_ATOM}entry"):
        title = _norm((entry.findtext(f"{_ATOM}title") or ""))
        link = ""
        for l in entry.iter(f"{_ATOM}link"):
            link = l.get("href") or link
        out.append({
            "title": title,
            "url": link,
            "updated": _norm(entry.findtext(f"{_ATOM}updated") or ""),
            "court": _norm(entry.findtext(f"{_ATOM}author/{_ATOM}name") or ""),
        })
        if len(out) >= limit:
            break
    return out


async def search_by_party(name: str, *, limit: int = _MAX) -> dict:
    """Search judgments where `name` is a PARTY.

    Always states whether the service was consulted. `searched=False` NEVER means "no
    judgments" — the caller must render it as a data gap, not a clean line.
    """
    result: dict[str, Any] = {
        "searched": False, "judgments": [], "count": 0, "reason": "",
        "source": "Find Case Law (The National Archives)",
        "citation_url": f"{_BASE}/judgments/search?query={_norm(name).replace(' ', '+')}",
    }
    if not is_configured():
        result["reason"] = "licence_not_confirmed"
        result["detail"] = configuration_hint()
        return result
    if len(_norm(name)) < 3:
        result["reason"] = "name_too_short"
        return result

    try:
        import httpx
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                f"{_BASE}/atom.xml",
                params={"party": _norm(name), "order": "-date"},
                headers={"User-Agent": "ARIA-DD/1.0 (+https://imaria.io)"},
            )
        if resp.status_code != 200:
            result["reason"] = f"http_{resp.status_code}"
            return result
        result["judgments"] = parse_atom(resp.text, limit=limit)
        result["count"] = len(result["judgments"])
        result["searched"] = True
        return result
    except Exception as e:
        logger.warning("[R-F3442] find_case_law search failed: %s", e)
        result["reason"] = f"error:{type(e).__name__}"
        result["detail"] = str(e)[:200]
        return result

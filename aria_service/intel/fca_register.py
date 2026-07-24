"""FCA Financial Services Register adapter (R-F3002).

For a UK entity that trades as an investment / fund / "Capital Management" firm,
the FIRST due-diligence question is: is it FCA-authorised? SIC 64999 evidences
nothing. This adapter answers "is this firm on the FCA Register, and what is its
authorisation status?" against the official FCA Financial Services Register API.

DORMANT BY DEFAULT — the API needs a (free) key + registered email. Until
FCA_API_EMAIL *and* FCA_API_KEY are set, every call returns
``{"configured": False, ...}`` and NEVER a fabricated authorised/not-authorised
result (never a false clean, never a false accusation). Register for a key at
https://register.fca.org.uk/Developer/s/ (free), then set both as Fly secrets.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("aria.intel.fca_register")

# Official FCA Financial Services Register API (v0.1).
_BASE = "https://register.fca.org.uk/services/V0.1"


def _creds() -> tuple[str, str] | None:
    """Return (email, key) iff BOTH secrets are set; else None (dormant)."""
    email = (os.getenv("FCA_API_EMAIL") or "").strip()
    key = (os.getenv("FCA_API_KEY") or "").strip()
    return (email, key) if (email and key) else None


def is_configured() -> bool:
    """True iff FCA_API_EMAIL and FCA_API_KEY are both set."""
    return _creds() is not None


def _is_authorised_status(status: str) -> bool:
    s = (status or "").strip().lower()
    # "Authorised", "Registered" (e.g. payment/e-money), "EEA Authorised" count as
    # currently permissioned; "No longer authorised" / "Cancelled" do NOT.
    return s.startswith("authorised") or s.startswith("eea authorised") or s == "registered"


async def lookup_firm(name: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    """Look up a firm's FCA authorisation status by name. Honest, never fabricated.

    Returns one of:
      dormant       → {"configured": False, "matched": None, "reason": ...}
      auth-rejected → {"configured": True, "error": "FCA API auth rejected ..."}
      no match      → {"configured": True, "matched": False, "query": ..., "reason": ...}
      match         → {"configured": True, "matched": True, "frn": ..., "firm_name": ...,
                       "status": ..., "is_authorised": bool, "detail_url": ..., "match_count": n}
      other error   → {"configured": True, "error": "..."}
    """
    creds = _creds()
    if creds is None:
        return {
            "configured": False, "matched": None,
            "reason": "FCA_API_EMAIL / FCA_API_KEY not set — FCA authorisation not checked",
        }
    email, key = creds
    if not (name or "").strip():
        return {"configured": True, "matched": False, "reason": "empty query"}
    headers = {"X-Auth-Email": email, "X-Auth-Key": key, "Content-Type": "application/json"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout_s, headers=headers) as client:
            r = await client.get(f"{_BASE}/Search", params={"q": name, "type": "firm"})
            if r.status_code in (401, 403):
                return {"configured": True, "error": f"FCA API auth rejected (HTTP {r.status_code}) — check FCA_API_EMAIL/FCA_API_KEY"}
            r.raise_for_status()
            data = r.json() or {}
            rows = data.get("Data") or []
            firm_rows = [x for x in rows if str(x.get("Type") or "").lower() == "firm"] or rows
            if not firm_rows:
                return {
                    "configured": True, "matched": False, "query": name,
                    "reason": "no firm on the FCA Register matched this name (not a clean bill — verify by name/FRN)",
                }
            top = firm_rows[0]
            frn = str(top.get("Reference Number") or top.get("FRN") or "").strip()
            firm_name = str(top.get("Name") or top.get("Organisation Name") or "").strip()
            status = str(top.get("Status") or "").strip()
            # If the search row didn't carry a status, fetch the firm detail for it.
            if frn and not status:
                fr = await client.get(f"{_BASE}/Firm/{frn}")
                if fr.status_code == 200:
                    fd = (fr.json() or {}).get("Data") or []
                    if fd:
                        status = str(fd[0].get("Status") or "").strip()
            return {
                "configured": True, "matched": True, "frn": frn, "firm_name": firm_name,
                "status": status or "unknown",
                "is_authorised": _is_authorised_status(status),
                "detail_url": (f"https://register.fca.org.uk/s/firm?firmReferenceNumber={frn}" if frn else ""),
                "match_count": len(firm_rows),
            }
    except Exception as e:  # noqa: BLE001 — honest error, never a fabricated status
        logger.debug("FCA lookup failed for %s: %s", name, e)
        return {"configured": True, "error": f"{type(e).__name__}: {str(e)[:150]}"}

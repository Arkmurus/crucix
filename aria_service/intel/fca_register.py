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


def _row_name(row: dict) -> str:
    return str((row or {}).get("Name") or (row or {}).get("Organisation Name") or "").strip()


def _row_frn(row: dict) -> str:
    return str((row or {}).get("Reference Number") or (row or {}).get("FRN") or "").strip()


def _is_clone_or_scam_warning(row: dict) -> bool:
    """R-F3011 — FCA publishes SCAM-WARNING records for CLONE firms (fraudsters
    impersonating an authorised firm). Their Name carries a clone/scam marker and
    they carry no genuine FRN. Reporting one as the SUBJECT's authorisation status
    is a false accusation — the real firm IS authorised (it is the one being
    cloned). Detect and exclude these before choosing a match."""
    name = _row_name(row).lower()
    markers = (
        "clone of fca authorised", "clone of an fca authorised", "clone of authorised",
        "clone firm", "(clone)", "clone of a", "scam", "fraudulent", "not authorised by us",
        "unauthorised firm", "unauthorized firm", "cloned",
    )
    return any(m in name for m in markers)


def _norm_tokens(s: str) -> set[str]:
    import re
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t}


# Generic corporate suffixes must not drive a name match (every firm shares them).
_GENERIC_FIRM_TOKENS = frozenset({
    "limited", "ltd", "plc", "llp", "lp", "uk", "gb", "group", "holdings", "holding",
    "the", "and", "co", "company", "international", "services", "management", "capital",
    "investment", "investments", "partners", "asset", "financial", "finance",
})


def _name_match_score(query: str, name: str) -> float:
    """Distinctive-token overlap of the query firm name against a candidate row.
    Ignores generic corporate suffixes so 'Schroder ...' beats a same-suffix
    stranger. 0.0 == no distinctive overlap (probably a different firm)."""
    q, n = _norm_tokens(query), _norm_tokens(name)
    qd = (q - _GENERIC_FIRM_TOKENS) or q
    nd = (n - _GENERIC_FIRM_TOKENS) or n
    if not qd:
        return 0.0
    return len(qd & nd) / len(qd)


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
            # R-F3011 — do NOT blindly take firm_rows[0]. FCA search ranks CLONE-FIRM
            # scam warnings (fraudsters impersonating an authorised firm) high; taking
            # one as the subject's status reports the REAL, authorised firm as
            # "Unauthorised" — a false accusation (defamation-class). Partition the
            # clone/scam warnings out, then pick the best NAME match among genuine firms.
            clone_rows = [x for x in firm_rows if _is_clone_or_scam_warning(x)]
            genuine = [x for x in firm_rows if not _is_clone_or_scam_warning(x)]
            if genuine:
                # Best distinctive-name match wins; ties keep the search rank. A row
                # with a real FRN is preferred over one without (search rank on ties).
                genuine.sort(
                    key=lambda x: (_name_match_score(name, _row_name(x)), 1 if _row_frn(x) else 0),
                    reverse=True,
                )
                top = genuine[0]
                frn = _row_frn(top)
                firm_name = _row_name(top)
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
                    "name_match": round(_name_match_score(name, firm_name), 3),
                    "clone_warning": bool(clone_rows),
                    "clone_count": len(clone_rows),
                    "detail_url": (f"https://register.fca.org.uk/s/firm?firmReferenceNumber={frn}" if frn else ""),
                    "match_count": len(firm_rows),
                }
            # ONLY clone/scam warnings matched this name — the legitimate firm was
            # NOT resolved by name. NEVER assert the subject is unauthorised off a
            # clone warning: is_authorised is None (unknown), reported honestly.
            if clone_rows:
                return {
                    "configured": True, "matched": True, "clone_warning": True,
                    "is_authorised": None, "frn": "", "firm_name": _row_name(clone_rows[0]),
                    "status": "clone-firm scam warning",
                    "reason": (
                        "FCA lists CLONE-FIRM scam warning(s) for names resembling this firm "
                        "(fraudsters impersonating an authorised firm). The legitimate firm's "
                        "authorisation was NOT resolved by name — verify by FRN. This is NOT a "
                        "finding that the subject itself is unauthorised."
                    ),
                    "clone_count": len(clone_rows),
                    "match_count": len(firm_rows),
                }
            # (defensive — firm_rows was non-empty but neither bucket filled)
            return {
                "configured": True, "matched": False, "query": name,
                "reason": "no genuine firm on the FCA Register resolved for this name — verify by FRN",
            }
    except Exception as e:  # noqa: BLE001 — honest error, never a fabricated status
        logger.debug("FCA lookup failed for %s: %s", name, e)
        return {"configured": True, "error": f"{type(e).__name__}: {str(e)[:150]}"}

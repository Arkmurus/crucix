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
import re   # R-F3025 — postcode normalisation at module scope
from typing import Any
from .engine_wiring import wired  # R-F3557 (§21a)

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


def _distinctive_sequence(text: str) -> list[str]:
    """R-F3574 — the distinctive tokens IN ORDER, which the set score discards."""
    seen: set = set()
    out: list[str] = []
    import re
    for tok in (t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t):
        if tok in _GENERIC_FIRM_TOKENS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


#: R-F3574 — a permutation of the same tokens is capped here: below the 0.75
#: identification threshold, but still above _MIN_NAME_MATCH_CORROBORATED (0.34), so a
#: matching postcode can still identify a firm whose name is merely written differently.
_PERMUTED_NAME_SCORE = 0.5


def _name_match_score(query: str, name: str) -> float:
    """Distinctive-token overlap of the query firm name against a candidate row.
    Ignores generic corporate suffixes so 'Schroder ...' beats a same-suffix
    stranger. 0.0 == no distinctive overlap (probably a different firm).

    R-F3574 — ORDER IS PART OF IDENTITY, and a set intersection throws it away.

    LIVE (dd_acaee511f0f4, Wilson James Limited, reg 02269560, a London security
    contractor): the report carried an AMBER finding reading "FCA Register: James
    Wilson (Postcode: BB3 0DB) — No longer registered as an Appointed Representative
    (FRN 806769). NOT currently authorised — verify before any regulated dealing."
    James Wilson is an individual in Blackburn. The subject is a company in London.

    R-F3025's threshold could not catch it. `{wilson, james} & {james, wilson}` is the
    FULL set, so the score was 1.000 — a perfect identification of a reversed name.
    The gate was working exactly as designed and the measure underneath it was blind.

    Reversal is not a spelling variant: "Wilson James Ltd" and "James Wilson Ltd" are
    routinely different companies, and for person-style names the order IS the name.
    So a permutation scores `_PERMUTED_NAME_SCORE` — below the identification
    threshold, above the corroborated one, which leaves a matching postcode able to
    identify a genuine firm written in a different order while a bare name reversal
    can no longer accuse anyone.
    """
    q, n = _norm_tokens(query), _norm_tokens(name)
    qd = (q - _GENERIC_FIRM_TOKENS) or q
    nd = (n - _GENERIC_FIRM_TOKENS) or n
    if not qd:
        return 0.0
    score = len(qd & nd) / len(qd)
    if score >= 1.0:
        qs, ns = _distinctive_sequence(query), _distinctive_sequence(name)
        # Only a REORDERING is penalised. A candidate carrying extra distinctive
        # tokens ("Wilson James Aviation") is a different question, already handled by
        # the set score and the threshold.
        if qs and ns and qs != ns and sorted(qs) == sorted(ns):
            return _PERMUTED_NAME_SCORE
    return score


# ── R-F3025 — ATTRIBUTION GATE ───────────────────────────────────────────────
#
# THE DEFECT (live report dd_16db41eb5fa8). The subject was EFT CONSULT LTD, Swansea
# SA7 9FG, SIC 74901 environmental consulting. Its FIRST key finding, at AMBER, read:
#   "FCA Register: EFT Consultancy Services Limited (PO5 3DZ) — Appointed
#    representative (FRN 924521). NOT currently authorised."
# That is a different company, ~200 miles away, in a different business. R-F3011 made
# the picker choose the BEST name match among genuine firms — but "best" is a
# relative rank, and nothing checked whether the best was GOOD. `_name_match_score`
# was computed, returned, and never read by anyone. {eft, consult} ∩ {eft,
# consultancy} = {eft} → 0.5, which is a coincidence, not an identification.
#
# The gate is deliberately conservative in one direction only: below the threshold we
# report UNKNOWN, never a status. A missed FCA authorisation is a data gap the report
# states; a mis-attributed one is a false regulatory accusation against a named
# company. Those costs are not symmetric.
_MIN_NAME_MATCH = 0.75          # ARIA_FCA_MIN_NAME_MATCH
_MIN_NAME_MATCH_CORROBORATED = 0.34   # accepted only WITH postcode corroboration


def _min_name_match() -> float:
    try:
        return float(os.getenv("ARIA_FCA_MIN_NAME_MATCH", "") or _MIN_NAME_MATCH)
    except (TypeError, ValueError):
        return _MIN_NAME_MATCH


def _norm_postcode(pc: str) -> str:
    """UK postcode, upper-cased with all whitespace removed ('sa7 9fg' → 'SA79FG')."""
    return re.sub(r"\s+", "", str(pc or "")).upper()


def _row_postcode(row: dict) -> str:
    for k in ("Postcode", "PostCode", "Post Code", "postcode"):
        v = (row or {}).get(k)
        if v:
            return _norm_postcode(v)
    return ""


def _postcode_corroborates(subject_pc: str, row: dict) -> bool | None:
    """R-F3025 — True/False when BOTH sides carry a postcode, None when either is
    absent. None is 'cannot corroborate', which must never read as 'contradicted'."""
    sp, rp = _norm_postcode(subject_pc), _row_postcode(row)
    if not sp or not rp:
        return None
    return sp == rp


@wired(module="fca_register", summary="FCA register firm lookup completed", gap_type="source_failure")
async def lookup_firm(name: str, *, timeout_s: float = 10.0, postcode: str = "") -> dict[str, Any]:
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
                # ── R-F3025 — is the best match GOOD ENOUGH to be the subject? ──
                _score = _name_match_score(name, firm_name)
                _pc_ok = _postcode_corroborates(postcode, top)
                _threshold = _min_name_match()
                _accept = (
                    _score >= _threshold
                    or (_pc_ok is True and _score >= _MIN_NAME_MATCH_CORROBORATED)
                )
                if _pc_ok is False and _score < 1.0:
                    # A stated postcode that DISAGREES is positive evidence of a
                    # different firm — only an exact name match survives it.
                    _accept = False
                if not _accept:
                    return {
                        "configured": True, "matched": False, "is_authorised": None,
                        "query": name,
                        "name_match": round(_score, 3),
                        "name_match_threshold": _threshold,
                        "postcode_corroborated": _pc_ok,
                        "best_candidate": {
                            "firm_name": firm_name, "frn": frn,
                            "postcode": _row_postcode(top),
                            "status": str(top.get("Status") or "").strip() or "unknown",
                        },
                        "reason": (
                            f"The closest firm on the FCA Register is '{firm_name}'"
                            + (f" (postcode {_row_postcode(top)})" if _row_postcode(top) else "")
                            + f", name match {_score:.2f} — below the {_threshold:.2f} "
                            "identification threshold"
                            + (" and its postcode does not match the subject's"
                               if _pc_ok is False else "")
                            + ". Its authorisation status is NOT attributed to this subject. "
                            "FCA authorisation for the subject is UNKNOWN — verify by FRN."
                        ),
                    }
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
                    "name_match": round(_score, 3),
                    "name_match_threshold": _threshold,
                    "postcode_corroborated": _pc_ok,   # R-F3025
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

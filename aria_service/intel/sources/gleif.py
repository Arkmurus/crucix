"""GLEIF — Legal Entity Identifier global corporate-identity API.

R-F2261 — API-first structured registry data that a datacenter IP can query (HTTP 200,
never a CAPTCHA block), covering entities in ANY jurisdiction. Used as a GLOBAL FALLBACK
in registry_adapters.lookup_entity when the jurisdiction-specific adapter returns nothing
(or the jurisdiction isn't covered at all) — directly fills the "foreign entity, registry
returned nothing" gap (e.g. a BR entity). §6-clean: free, no key. GLEIF gives legal name,
jurisdiction, registered address, legal form, status, and the LEI (used as company_number);
it has NO director data, so officers/psc stay blank.
"""
from __future__ import annotations

import logging
import re

import httpx

from ..engine_wiring import wire_success, wire_failure
from ..circuit_breaker import get_breaker

logger = logging.getLogger("aria.sources.gleif")

_BASE = "https://api.gleif.org/api/v1"
_TIMEOUT = 15.0


def is_available() -> bool:
    """Free + open — always available (no key)."""
    return True


# R-F2740 — generic company tokens carry no identifying signal; ignored when
# confirming a GLEIF match against the query name.
_GENERIC_NAME_TOKENS = frozenset({
    "ltd", "limited", "llc", "inc", "incorporated", "plc", "company", "co", "corp",
    "corporation", "sa", "sarl", "gmbh", "ag", "bv", "nv", "spa", "srl", "oy", "ab",
    "group", "holdings", "holding", "the", "and", "trading", "services", "international",
})


def _name_confirms(query: str, candidate: str) -> bool:
    """R-F2740 — does the GLEIF record's legal name actually match the queried name?

    A fulltext search returns the best-SCORING record even when NONE matches (score
    can come from ACTIVE status alone). Attaching such a record's LEI + national
    registry id to the subject fabricates its identity — and worse, drives a
    national-registry (KRS/SIREN) lookup for the WRONG company. Require a meaningful
    shared token (ignoring generic company suffixes) before trusting the match.
    """
    def toks(s: str) -> set[str]:
        return {t for t in re.split(r"[^0-9a-z]+", (s or "").lower())
                if len(t) >= 3 and t not in _GENERIC_NAME_TOKENS}
    qt, ct = toks(query), toks(candidate)
    return bool(qt) and bool(ct) and bool(qt & ct)


def _best_match(recs: list, q: str) -> dict:
    """Pick the best record: exact legal-name match + ACTIVE status preferred."""
    ql = q.lower().strip()

    def score(r: dict) -> int:
        e = (r.get("attributes", {}) or {}).get("entity", {}) or {}
        nm = ((e.get("legalName") or {}).get("name", "") or "").lower()
        s = 0
        if nm == ql:
            s += 10
        elif ql and (ql in nm or nm in ql):
            s += 5
        if str(e.get("status") or "").upper() == "ACTIVE":
            s += 2
        return s

    return max(recs, key=score)



def build_profile(attrs: dict, lei: str) -> dict:
    """Map a GLEIF v1 attributes block to the registry-profile contract.

    R-F2261 — keys MUST match what dd_orchestrator._run_identity reads off a registry
    profile (company_status / date_of_creation / registered_office_address), else the
    GLEIF data silently doesn't populate the DD identity fields.

    R-F2839 — `date_of_creation` is a COMPANIES HOUSE field name, where it genuinely
    means the incorporation date, and dd_orchestrator.py:3575/:3731 correctly assign it
    to identity.incorporation_date. This adapter used to fill it from
    `registration.initialRegistrationDate` — the date the LEI was ISSUED, not the date
    the company was FORMED. Every GLEIF-sourced entity therefore carried an
    incorporation date wrong by the gap between formation and LEI issuance.

    Caught by comparing a live report to a competitor's on the same entity
    (SOCAR Trading SA): we shipped 2013-05-28; the true incorporation is 2007-12-17,
    which GLEIF carries all along under `entity.creationDate`. Six years wrong, stated
    as fact. Both consumers were right; the SOURCE was wrong, so this is fixed here.

    A missing creationDate yields an EMPTY date — never a fallback to the LEI date,
    which would reintroduce the defect for exactly the records that lack the real one.
    A wrong date is worse than no date.
    """
    e = attrs.get("entity", {}) or {}
    reg = attrs.get("registration", {}) or {}
    addr = e.get("legalAddress") or {}
    addr_str = ", ".join(x for x in [
        " ".join(addr.get("addressLines") or []) or None,
        addr.get("city"), addr.get("region"), addr.get("postalCode"), addr.get("country"),
    ] if x)
    return {
        "company_name": (e.get("legalName") or {}).get("name", "") or "",
        "company_number": lei,              # LEI as the registry id
        "lei": lei,
        # R-F2503 — the LOCAL national registry id (KRS for PL, SIREN for FR, HRB for
        # DE, …). Lets a number-only national adapter resolve its rich extract from a
        # name via GLEIF. May be a national number in ANY scheme — callers MUST
        # validate the format + verify the fetched entity.
        "registered_as": (e.get("registeredAs") or "").strip(),
        "company_status": (str(e.get("status") or "").lower() or "unknown"),
        "jurisdiction": e.get("jurisdiction", "") or "",
        "registered_office_address": addr_str,
        "legal_form": (e.get("legalForm") or {}).get("id", "") or "",
        # R-F2839 — the company's own formation date.
        "date_of_creation": (e.get("creationDate") or "")[:10],
        # R-F2839 — kept, but under a name that says what it actually is.
        "lei_registered_date": (reg.get("initialRegistrationDate") or "")[:10],
        "sic_codes": [],
    }


async def lookup(name: str, jurisdiction_iso2: str = "", reg_number: str | None = None) -> dict | None:
    """Return {profile, officers, psc, source_url, adapter} for the best GLEIF match, or None.

    Matches registry_adapters.lookup_entity's contract so it drops in as a fallback.
    """
    cb = get_breaker("registry:gleif", failure_threshold=5, cooldown_seconds=300)
    if cb.is_open():
        return None
    q = (name or "").strip()
    if len(q) < 3:
        return None
    try:
        params: dict = {"filter[fulltext]": q, "page[size]": 5}
        if jurisdiction_iso2:
            params["filter[entity.jurisdiction]"] = jurisdiction_iso2.upper().strip()
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_BASE + "/lei-records", params=params,
                                  headers={"Accept": "application/vnd.api+json"})
        if r.status_code != 200:
            cb.record_failure(reason=f"http_{r.status_code}")
            return None
        recs = ((r.json() or {}).get("data") or [])
        if not recs:
            cb.record_success()
            return None
        best = _best_match(recs, q)
        a = best.get("attributes", {}) or {}
        e = a.get("entity", {}) or {}
        lei = a.get("lei", "") or ""
        nm = (e.get("legalName") or {}).get("name", "") or ""
        # R-F2740 — the fulltext search returned records, but only attach the best one
        # if its legal name actually confirms the query. Otherwise the LEI + national
        # registry id would be a fabricated subject identity (and mis-drive KRS/SIREN).
        if not _name_confirms(q, nm):
            logger.debug("GLEIF: best record %r does not confirm query %r — not attaching", nm, q)
            cb.record_success()  # the search worked; there was just no matching entity
            return None
        # R-F2839 — profile construction lives in build_profile() so the field mapping
        # is directly testable. It was not, and a wrong date shipped for months.
        profile = build_profile(a, lei)
        cb.record_success()
        try:
            wire_success(module="gleif",
                         summary=f"GLEIF match '{nm}' ({profile['jurisdiction']}) LEI={lei}",
                         source_id="gleif:lookup")
        except Exception:
            pass
        return {
            "profile": profile,
            "officers": [],   # GLEIF carries no director/officer data
            "psc": [],
            "source_url": f"https://search.gleif.org/#/record/{lei}" if lei else "https://www.gleif.org/",
            "adapter": "gleif",
        }
    except Exception as ex:  # noqa: BLE001 — best-effort fallback, never raises
        cb.record_failure(reason="timeout")
        logger.warning("[gleif] lookup failed for %s: %s", q, ex)
        try:
            wire_failure(module="gleif", detail=f"gleif lookup failed: {ex}",
                         gap_type="registry_lookup", source="gleif:lookup")
        except Exception:
            pass
        return None

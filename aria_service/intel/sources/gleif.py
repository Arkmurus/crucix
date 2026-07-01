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

import httpx

from ..engine_wiring import wire_success, wire_failure
from ..circuit_breaker import get_breaker

logger = logging.getLogger("aria.sources.gleif")

_BASE = "https://api.gleif.org/api/v1"
_TIMEOUT = 15.0


def is_available() -> bool:
    """Free + open — always available (no key)."""
    return True


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
        addr = e.get("legalAddress") or {}
        addr_str = ", ".join(x for x in [
            " ".join(addr.get("addressLines") or []) or None,
            addr.get("city"), addr.get("region"), addr.get("postalCode"), addr.get("country"),
        ] if x)
        reg = a.get("registration", {}) or {}
        # R-F2261 — keys MUST match what dd_orchestrator._run_identity reads off a registry
        # profile (company_status / date_of_creation / registered_office_address), else the
        # GLEIF data silently doesn't populate the DD identity fields.
        profile = {
            "company_name": nm,
            "company_number": lei,              # LEI as the registry id
            "lei": lei,
            "company_status": (str(e.get("status") or "").lower() or "unknown"),
            "jurisdiction": e.get("jurisdiction", "") or "",
            "registered_office_address": addr_str,
            "legal_form": (e.get("legalForm") or {}).get("id", "") or "",
            "date_of_creation": (reg.get("initialRegistrationDate") or "")[:10],
            "sic_codes": [],
        }
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

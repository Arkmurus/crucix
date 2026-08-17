"""ARIA Multi-Jurisdiction Company Registry Adapters.

Provides unified lookup_entity() entry point that dispatches to
per-jurisdiction adapters.  Each adapter follows the same return
format as the UK Companies House module so the DD orchestrator can
consume them identically.

Supported jurisdictions:
  GI — Gibraltar Companies House (HTML scraping)
  PL — Poland KRS (free REST API)
  RO — Romania ONRC / ANAF (REST API for CUI/VAT lookup)
  TR — Turkey MERSIS (HTML scraping)
  BR — Brazil ReceitaWS CNPJ (free REST API)
  AO — Angola GUE (stub — no public API)
  KE — Kenya BRS / eCitizen (HTML scraping + stub fallback)
  SA — Saudi Arabia MOCI (HTML scraping + stub fallback)
  GH — Ghana RGD (HTML scraping + stub fallback)
  PA — Panama Registro Público (HTML scraping + stub fallback) — R-F598
  BG — Bulgaria Commercial Register at Registry Agency (HTML scraping + stub) — R-F598

Design principles:
  - Every adapter returns None on failure (graceful degradation)
  - httpx with 15s timeout everywhere
  - No external HTML parser deps — regex only (same as link_investigator)
  - Source URLs included for DD report citations
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import enum
import logging
import re
import time
from typing import Any


class RegistryStatus(str, enum.Enum):
    """R-F2693 — what a registry lookup ACTUALLY established (DD Grade-A Phase-0).

    Before this, a lookup's outcome was a free string and the only marker of a
    stub/fallback was a `_stub` suffix buried in the adapter NAME. Nothing read it,
    so a stub that looked up NOTHING silently certified identity authority: it
    returns `company_status="unknown"`, the orchestrator copies that into
    `identity.registration_status`, and dd_schema's `registry_substance` is a plain
    `bool(registration_status or …)` — and `bool("unknown")` is True. The grade then
    skips its 25-point "no identity authority" penalty. An adapter whose own
    data_gaps read "no public registry API, recommend manual verification" was
    lifting the evidence grade.

    A closed vocabulary makes "did an authority confirm this?" answerable instead of
    inferred from string truthiness. Only VERIFIED/PARTIAL are authority — everything
    else means we did NOT establish identity from a registry, and must never certify
    it (never-false-clean).
    """

    VERIFIED = "verified"                    # a real registry answered about this entity
    PARTIAL = "partial"                      # real registry, incomplete record
    MANUAL_REQUIRED = "manual_required"      # no API; a human must verify (the `*_stub`s)
    NOT_AVAILABLE = "not_available"          # no adapter / lookup could not run
    PROVIDER_REQUIRED = "provider_required"  # only a paid provider could answer (§6/§17)

    def is_authority(self) -> bool:
        """True ONLY when a real registry actually confirmed something.

        The single question the evidence grade asks. Deliberately a whitelist: a new
        status added later is NOT authority until someone decides it is.
        """
        return self in (RegistryStatus.VERIFIED, RegistryStatus.PARTIAL)

    @classmethod
    def for_adapter(cls, adapter: str) -> "RegistryStatus":
        """Classify from the adapter name, reusing the `*_stub` convention already in
        use by all 8 stub adapters — so no per-adapter edit is needed and a stub added
        later is classified the moment it is named."""
        a = (adapter or "").strip().lower()
        if not a:
            return cls.NOT_AVAILABLE
        if a.endswith("_stub") or "_stub_" in a:
            return cls.MANUAL_REQUIRED
        return cls.VERIFIED

    @classmethod
    def coerce(cls, value: Any) -> "RegistryStatus | None":
        """Parse a persisted string back to the enum; None when absent/unrecognised.

        None means UNKNOWN, never a default — a caller must not read an unparseable
        status as either authority or its absence.
        """
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except Exception:
            return None

import httpx

logger = logging.getLogger("aria.intel.registry_adapters")

_TIMEOUT = 15.0



# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Unified entry point                                               ║
# ╚══════════════════════════════════════════════════════════════════════╝

async def lookup_entity(
    name: str,
    jurisdiction_iso2: str,
    registration_number: str | None = None,
    address: str | None = None,
) -> dict | None:
    """Look up a company in its national registry.

    Returns a normalised dict with keys:
        profile, officers, psc, source_url, adapter
    or None if the jurisdiction is unsupported / lookup failed.

    `address` is used by the US adapter to route to the correct state
    Secretary of State; other adapters ignore it.
    """
    iso2 = (jurisdiction_iso2 or "").upper().strip()
    if iso2 not in _SUPPORTED_JURISDICTIONS:
        # R-F2261 — no jurisdiction-specific adapter, but GLEIF covers ANY jurisdiction
        # with structured LEI identity (free API, datacenter-tolerant) — global fallback.
        return await _gleif_global_fallback(name, iso2, registration_number)

    adapter_fn = _DISPATCH.get(iso2)
    if not adapter_fn:
        return None
    # R-F2863 — LATE-BIND through the module namespace. `_DISPATCH` is built at
    # import time and captures function OBJECTS, so `monkeypatch.setattr(ra,
    # "_lookup_finland", ...)` would no longer be seen and the table would keep
    # calling the original. Before the hoist the table was rebuilt on every call,
    # which gave late binding for free (test_rf302 depends on it).
    adapter_fn = globals().get(getattr(adapter_fn, "__name__", ""), adapter_fn)

    try:
        logger.info("Registry adapter [%s]: looking up '%s' (reg=%s)", iso2, name, registration_number)
        _t0 = time.monotonic()
        if iso2 == "US":
            result = await adapter_fn(name, registration_number, address)
        else:
            result = await adapter_fn(name, registration_number)
        _elapsed = time.monotonic() - _t0
        if result:
            logger.info("Registry adapter [%s]: found %s", iso2, result.get("profile", {}).get("company_name", "?"))
        else:
            logger.info("Registry adapter [%s]: no result for '%s'", iso2, name)

        # ── Brain hook: feed registry lookup to learning ──
        try:
            from . import brain_hook
            _profile = result.get("profile", {}) if result else {}
            _company = _profile.get("company_name", name)
            _status = _profile.get("status", "unknown")
            await brain_hook.absorb(
                module="registry_adapter",
                summary=f"Registry lookup [{iso2}] '{_company}': status={_status}, officers={len(result.get('officers', []))} " if result else f"Registry lookup [{iso2}] '{name}': no result",
                entity_name=_company,
                success=result is not None,
                confidence="CONFIRMED" if result else "ASSESSED",
                gap_type="registry_lookup" if not result else None,
                gap_detail=f"No registry result for {name} in {iso2}" if not result else None,
            )
        except Exception as _bh:
            logger.debug("registry_adapter brain_hook failed: %s", _bh)

        # ── Self-metrics: coverage (hit/miss) + timeliness (lookup latency) ──
        try:
            from . import self_metrics
            await self_metrics.emit(
                "coverage", iso2, "registry_lookup",
                1.0 if result else 0.0,
                context={"name": name[:80], "had_reg_number": bool(registration_number)},
                source_module="registry_adapters",
            )
            await self_metrics.emit(
                "timeliness", iso2, "registry_lookup_seconds",
                _elapsed,
                source_module="registry_adapters",
            )
        except Exception as _sm:
            logger.debug("registry_adapter self_metrics failed: %s", _sm)

        # ── R-F2863 — record OBSERVED liveness for the coverage vault ──
        # Fire-and-forget: bookkeeping must not put a saturation-sensitive
        # state_store write on the DD hot path. "empty" is neither success nor
        # failure — a registry that correctly answers "no such company" is
        # WORKING, but it did not prove liveness either.
        # R-F2915 — a STUB result must never record registry liveness.
        #
        # The stub adapters (angola_gue_stub, kenya_brs_stub, us_unknown_stub, …) do
        # not read a registry. They echo the QUERY back as company_name and attach
        # data_gaps explaining that no public API exists — honest in their own payload,
        # and exactly right for a DD report, which then shows the gap instead of
        # nothing. But `result` is truthy, so this call recorded "success", and
        # registry_coverage turns a success into `live` with a timestamp as evidence.
        #
        # Caught 2026-07-23 by the R-F2911 sweep: 9 jurisdictions (AO BG GH IL KE PA SA
        # US ZA) reported a "match" in 0.0s with 0 officers whose company_name was the
        # probe string itself. Had those persisted, vault.html would have claimed nine
        # live national registries on the strength of ARIA quoting itself back — the
        # precise false-coverage this inventory exists to prevent, and worse than the
        # honest "unproven" it replaced.
        #
        # A stub therefore records "empty": the adapter RAN, and produced no registry
        # evidence. That is true, and it keeps the jurisdiction unproven rather than
        # marking it live or failing.
        # The test is AUTHORITY, not the adapter's name. R-F2693 already defines the
        # closed vocabulary for "did a registry actually confirm this?"
        # (RegistryStatus.is_authority — a whitelist, so a status added later is not
        # authority until someone decides it is). The `*_stub` suffix is only the
        # DEFAULT input to that classification and is explicitly overridable — a real
        # adapter that degrades to a partial/manual result would keep an authoritative
        # name while carrying a non-authoritative status. Keying on the name would
        # record liveness for it; keying on the status cannot.
        _adapter_name = (result or {}).get("adapter", "") if isinstance(result, dict) else ""
        _status = RegistryStatus.coerce((result or {}).get("registry_status")) if isinstance(result, dict) else None
        if _status is None and _adapter_name:
            _status = RegistryStatus.for_adapter(_adapter_name)   # older results carry no field
        _is_authority = bool(result) and _status is not None and _status.is_authority()
        _record_coverage_outcome(
            iso2,
            _adapter_name,
            "success" if _is_authority else "empty",
        )

        # R-F2261 — GLEIF global fallback when the jurisdiction adapter found NOTHING:
        # GLEIF gives structured LEI identity for entities in ANY jurisdiction (free API,
        # datacenter-tolerant) — fills the "foreign entity, registry returned nothing" gap.
        if not result:
            _gleif = await _gleif_global_fallback(name, iso2, registration_number)
            if _gleif:
                return _gleif
        return result
    except Exception as exc:
        logger.warning("Registry adapter [%s] failed: %s", iso2, exc)
        _record_coverage_outcome(iso2, "", "error")   # R-F2863
        return None


def _record_coverage_outcome(iso2: str, adapter: str, outcome: str) -> None:
    """Schedule a coverage-vault write without blocking the lookup. Never raises.

    No-ops when there is no running loop (a sync caller in a test or a script) —
    losing one bookkeeping row is always preferable to raising into a DD run.
    """
    try:
        import asyncio as _asyncio
        from . import registry_coverage as _rc
        _task = _asyncio.get_running_loop().create_task(
            _rc.record_outcome(iso2, adapter, outcome)
        )
        _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    except Exception as _rc_e:
        logger.debug("registry coverage record failed: %s", _rc_e)


async def _lookup_switzerland(name: str, reg_number: str | None) -> dict | None:
    """CH — Zefix (Federal Office of Justice) via the open LINDAS SPARQL endpoint.

    R-F2861. Before this, a Swiss counterparty produced NO registry evidence:
    dd_orchestrator emitted only a manual-action hint, so "verified legal
    identity" was unachievable from a primary source for CH — a real hole given
    how many commodity traders, holding structures and defence intermediaries
    are Swiss-registered.

    The advertised Zefix REST API needs credentials (verified 401 on every
    endpoint 2026-07-22); the same federal dataset is open on LINDAS, so this
    needs no API key — see aria_service/intel/zefix.py.
    """
    from . import zefix

    rows = await zefix.search_company(name, limit=5)
    if not rows:
        return None

    # Prefer an exact registered-name match; otherwise the top hit. A partial
    # CONTAINS match is NOT presented as an identity confirmation on its own —
    # the caller sees the returned company_name and can compare.
    needle = (name or "").strip().lower()
    record = next((r for r in rows if (r.get("name") or "").strip().lower() == needle), rows[0])

    result = _build_result(
        company_name=record.get("name") or name,
        company_number=record.get("uid") or "",
        # Zefix-on-LINDAS does not expose a status/dissolution flag in this
        # projection, so status stays EMPTY rather than being assumed active.
        # Claiming "active" without evidence is the false-clean this platform exists to avoid.
        company_status="",
        date_of_creation="",          # not exposed by this dataset
        registered_office_address="",  # address is a separate URI; not resolved here
        jurisdiction="CH",
        sic_codes=[],
        officers=[],                   # not published in the open dataset
        psc=[],                        # UBO is NOT on Zefix (Swiss UBO is private)
        source_url=record.get("source_url") or "https://www.zefix.ch",
        adapter="switzerland_zefix_lindas",
    )
    if record.get("purpose"):
        result["profile"]["business_purpose"] = record["purpose"]
    if record.get("legal_form_code"):
        result["profile"]["legal_form_code"] = record["legal_form_code"]
    if record.get("municipality_id"):
        result["profile"]["municipality_id"] = record["municipality_id"]

    # Say plainly what this source CANNOT answer, so a downstream layer cannot
    # read silence as a clean result (the Finland adapter sets the precedent).
    result["data_gaps"] = [
        "Directors / board members are not in the open Zefix dataset — pull the "
        "SHAB/FOSC extract at https://www.zefix.ch for "
        f"{record.get('uid') or name} and attach it to the DD record.",
        "Beneficial ownership is NOT public in Switzerland — UBO must be obtained "
        "from the counterparty or via a Swiss UBO declaration.",
        "Registration status (active/dissolved) is not in this projection — "
        "confirm on the Zefix record before relying on the entity being live.",
    ]
    return result


async def _lookup_norway(name: str, reg_number: str | None) -> dict | None:
    """NO — Brønnøysundregistrene (official, fully open; no API key). R-F2862.

    Richer than most adapters: real registration status from published distress
    booleans, plus board/CEO officers WITH date of birth from the open /roller
    endpoint (a DOB sharply reduces sanctions/PEP screening false positives).
    """
    from . import brreg

    record = None
    digits = "".join(ch for ch in str(reg_number or "") if ch.isdigit())
    if len(digits) == 9:
        record = await brreg.get_company(digits)
    if not record:
        rows = await brreg.search_company(name, limit=5)
        if not rows:
            return None
        needle = (name or "").strip().lower()
        record = next((r for r in rows if (r.get("name") or "").strip().lower() == needle), rows[0])

    org_number = record.get("organisation_number") or ""
    officers = await brreg.get_officers(org_number) if org_number else []

    result = _build_result(
        company_name=record.get("name") or name,
        company_number=org_number,
        # "" when brreg did not publish the flags — see brreg._derive_status.
        company_status=record.get("status") or "",
        date_of_creation=record.get("registration_date") or record.get("founded_date") or "",
        registered_office_address=record.get("address") or "",
        jurisdiction="NO",
        sic_codes=record.get("sic_codes") or [],
        officers=officers,
        psc=[],                       # beneficial ownership is NOT in this register
        source_url=(f"https://virksomhet.brreg.no/nb/oppslag/enheter/{org_number}"
                    if org_number else "https://www.brreg.no"),
        adapter="norway_brreg",
    )
    for key in ("state_owned", "sector_code", "former_names", "employees",
                "legal_form", "website"):
        if record.get(key) not in (None, [], ""):
            result["profile"][key] = record[key]

    gaps = [
        "Beneficial ownership (UBO) is not published in Enhetsregisteret — "
        "obtain the Norwegian UBO register extract or a counterparty declaration.",
    ]
    if not record.get("status"):
        gaps.append(
            "Registration status flags were not present in the brreg response — "
            "status is UNCONFIRMED and must not be read as active."
        )
    if record.get("state_owned"):
        gaps.append(
            f"Institutional sector code {record.get('sector_code')} indicates a "
            "STATE-OWNED entity — apply RCA / state-ownership screening."
        )
    result["data_gaps"] = gaps
    return result


async def _lookup_estonia(name: str, reg_number: str | None) -> dict | None:
    """EE — RIK ariregister (official, open; no API key). R-F2865."""
    from . import ariregister

    rows = await ariregister.search_company(name, limit=5)
    if not rows:
        return None
    needle = (name or "").strip().lower()
    record = next((r for r in rows if (r.get("name") or "").strip().lower() == needle), rows[0])

    result = _build_result(
        company_name=record.get("name") or name,
        company_number=record.get("registration_code") or "",
        # "" for any status code we cannot evidence — see ariregister._STATUS_MAP.
        company_status=record.get("status") or "",
        date_of_creation="",           # not exposed by this endpoint
        registered_office_address=record.get("address") or "",
        jurisdiction="EE",
        sic_codes=[],
        officers=[],                   # not in this endpoint
        psc=[],                        # UBO is a separate Estonian register
        source_url=record.get("source_url") or "https://ariregister.rik.ee",
        adapter="estonia_ariregister",
    )
    for key in ("former_names", "legal_form_code", "postal_code"):
        if record.get(key) not in (None, [], ""):
            result["profile"][key] = record[key]

    gaps = [
        "Directors / board members are not in this endpoint — pull the full "
        "ariregister extract at https://ariregister.rik.ee for "
        f"{record.get('registration_code') or name}.",
        "Beneficial ownership is held in a SEPARATE Estonian UBO register and is "
        "not covered by this lookup.",
    ]
    if not record.get("status"):
        gaps.append(
            "Registration status code "
            f"{record.get('status_code_raw') or 'not supplied'} is not a code we "
            "can evidence — status is UNCONFIRMED and must not be read as active."
        )
    result["data_gaps"] = gaps
    return result


async def _gleif_global_fallback(name: str, iso2: str, reg_number: str | None) -> dict | None:
    """R-F2261 — query GLEIF (global LEI corporate identity) when a national registry
    adapter has no result, or the jurisdiction has no adapter at all. Best-effort; the
    returned shape matches lookup_entity's contract. Never raises."""
    try:
        from .sources import gleif as _gleif_src
        res = await _gleif_src.lookup(name, iso2, reg_number)
        if res:
            logger.info("Registry adapter [gleif-fallback]: LEI identity for '%s' (%s)",
                        name, (res.get("profile") or {}).get("jurisdiction", iso2))
        return res
    except Exception as _e:  # noqa: BLE001
        logger.debug("gleif fallback failed: %s", _e)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Gibraltar Companies House  (GI)                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝

_GI_BASE = "https://www.companieshouse.gi"


async def _lookup_gibraltar(name: str, reg_number: str | None) -> dict | None:
    """Gibraltar Companies House — HTML scraping (no REST API available)."""
    search_url = f"{_GI_BASE}/index.html"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            # Try search by name
            params = {"action": "search", "q": reg_number or name}
            resp = await client.get(search_url, params=params)
            if resp.status_code != 200:
                logger.warning("Gibraltar CH returned %d", resp.status_code)
                return None

            html = resp.text

            # Try to extract company info from search results
            # Pattern: company number + name in result rows
            company_number = reg_number
            company_name = name

            # Look for company number patterns like "12345"
            num_match = re.search(
                r'(?:Company\s*(?:No|Number)[:\s]*|#)\s*(\d{3,8})',
                html, re.IGNORECASE,
            )
            if num_match:
                company_number = num_match.group(1)

            # Look for company name in bold/heading tags near the number
            name_match = re.search(
                r'<(?:b|strong|h[1-4])[^>]*>\s*([^<]{3,120})\s*</(?:b|strong|h[1-4])>',
                html, re.IGNORECASE,
            )
            if name_match:
                company_name = _html_unescape(name_match.group(1).strip())

            # Look for status (Active / Dissolved etc.)
            status_match = re.search(
                r'(?:Status|State)[:\s]*([A-Za-z]+)',
                html, re.IGNORECASE,
            )
            company_status = status_match.group(1).lower() if status_match else "unknown"

            # Look for incorporation date
            date_match = re.search(
                r'(?:Incorporat(?:ed|ion)\s*(?:Date)?)[:\s]*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})',
                html, re.IGNORECASE,
            )
            date_of_creation = date_match.group(1) if date_match else ""

            # Look for registered address
            addr_match = re.search(
                r'(?:Registered\s*(?:Office\s*)?Address)[:\s]*([^<]{5,200})',
                html, re.IGNORECASE,
            )
            address = _html_unescape(addr_match.group(1).strip()) if addr_match else ""

            # Extract officers if listed
            officers = []
            officer_pattern = re.finditer(
                r'(?:Director|Secretary|Officer)[:\s]*([^<]{3,80})',
                html, re.IGNORECASE,
            )
            for m in officer_pattern:
                officer_name = _html_unescape(m.group(1).strip())
                if officer_name and len(officer_name) > 2:
                    officers.append({
                        "name": officer_name,
                        "role": "director",
                        "appointed_on": "",
                    })

            # R-F2737 — only attach a Gibraltar hit if the search result corroborates
            # the query. Without this the adapter returned a "hit" for ANY page (incl. a
            # no-results page), with scraped officers/status attached to the subject.
            _ex_name = _html_unescape(name_match.group(1).strip()) if name_match else ""
            _ex_reg = num_match.group(1) if num_match else ""
            if not _scrape_confirms_query(name, reg_number, _ex_name, _ex_reg):
                logger.debug("Gibraltar CH: search result did not confirm query — not attaching")
                return None

            source_url = f"{_GI_BASE}/?q={reg_number or name}"

            return _build_result(
                company_name=company_name,
                company_number=company_number or "",
                company_status=company_status,
                date_of_creation=date_of_creation,
                registered_office_address=address,
                jurisdiction="GI",
                sic_codes=[],
                officers=officers,
                psc=[],
                source_url=source_url,
                adapter="gibraltar_ch",
            )
    except Exception as exc:
        logger.warning("Gibraltar CH scrape failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Poland KRS  (PL)                                                  ║
# ╚══════════════════════════════════════════════════════════════════════╝

_PL_API_BASE = "https://api-krs.ms.gov.pl/api/krs"


async def _pl_resolve_krs(name: str) -> str:
    """R-F2503 — the KRS OdpisPelny API is NUMBER-ONLY, so resolve a KRS number from a
    NAME via GLEIF's local `registered_as`. Returns a 10-digit KRS or "" (the caller
    still name-verifies the fetched extract, so a 10-digit number in another scheme —
    e.g. a NIP — that fetches nothing or the wrong entity is caught downstream)."""
    try:
        from .sources import gleif as _g
        res = await _g.lookup(name, "PL")
        ra = ((res or {}).get("profile") or {}).get("registered_as") or ""
        digits = re.sub(r"\D", "", ra)
        return digits.zfill(10) if len(digits) == 10 else ""
    except Exception:
        return ""


def _krs_current(x):
    """R-F2503 — KRS versions many fields as a LIST of historical entries; each carries
    `nrWpisuWykr` (the entry that CROSSED IT OUT) when superseded. The CURRENT value is
    the entry NOT crossed out (else the last). Returning entry[0] blindly gave a
    company's FORMER name/address (e.g. KRS 0000006865 = CD Projekt, but [0] =
    'OPTIMUS TECHNOLOGIE', its pre-rename name) — a wrong-entity hazard."""
    if isinstance(x, list):
        dicts = [e for e in x if isinstance(e, dict)]
        if not dicts:
            return {}
        active = [e for e in dicts if not e.get("nrWpisuWykr")]
        return active[-1] if active else dicts[-1]
    return x if isinstance(x, dict) else {}


def _pl_name_matches(fetched: str, query: str) -> bool:
    """R-F2503 — token-overlap guard so a name-resolved KRS extract is trusted ONLY when
    it is plausibly the SAME entity as the query — a wrong GLEIF/registered_as match must
    NEVER surface another company's officers (never-false-clean)."""
    def _toks(s: str) -> set:
        s = (s or "").lower()
        s = re.sub(r"\b(s\.?a\.?|sp\.?\s*z\s*o\.?o\.?|spolka|akcyjna|plc|ltd|limited|gmbh|inc)\b", " ", s)
        return {t for t in re.findall(r"[a-z0-9]{2,}", s)}
    q, f = _toks(query), _toks(fetched)
    if not q or not f:
        return False
    return (len(q & f) / len(q)) >= 0.5


async def _lookup_poland(name: str, reg_number: str | None) -> dict | None:
    """Poland KRS — free REST API from the Ministry of Justice. The OdpisPelny endpoint
    is NUMBER-ONLY: a '?nazwa=' name search 404s (R-F2503, confirmed live), so a name-only
    lookup resolves the KRS number via GLEIF's local registered_as, fetches the rich
    extract, and only trusts it if the fetched name verifies against the query."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            data: dict | None = None

            # Resolve the KRS number: operator-supplied first, else via GLEIF. No usable
            # 10-digit number → None → lookup_entity's GLEIF identity fallback.
            _resolved_from_name = False
            if reg_number:
                krs = re.sub(r"\D", "", reg_number)[-10:].zfill(10)
            else:
                krs = await _pl_resolve_krs(name)
                _resolved_from_name = bool(krs)
            if not krs or len(krs) != 10 or not krs.isdigit():
                return None

            url = f"{_PL_API_BASE}/OdpisPelny/{krs}?rejestr=P&format=json"
            resp = await client.get(url)  # no-ssrf-check: fixed _PL_API_BASE; krs is validated as exactly 10 digits
            if resp.status_code == 200:
                data = resp.json()

            if not data:
                return None

            # Navigate the KRS JSON structure
            odpis = data.get("odpis", data)
            dane = odpis.get("dane", odpis)
            dzial1 = dane.get("dzial1", {})
            dzial2 = dane.get("dzial2", {})

            # Company basics. R-F2503 — KRS returns `nazwa` as a LIST of historical
            # names ([{nazwa, nrWpisu}, …], newest first) OR a bare string. Extract the
            # current name string robustly (the old code passed the whole list on as the
            # name → downstream .lower()/.get crashes). numerKRS lives in `identyfikatory`.
            dane_podmiotu = dzial1.get("danePodmiotu") or {}
            _nazwa_raw = dane_podmiotu.get("nazwa")
            _nazwa = _nazwa_raw if isinstance(_nazwa_raw, str) else _krs_current(_nazwa_raw).get("nazwa", "")
            _dane_nazwa = dane.get("nazwa")
            company_name = _nazwa or (_dane_nazwa if isinstance(_dane_nazwa, str) else "") or name
            _ident = dane_podmiotu.get("identyfikatory") or {}
            company_number = (
                (_ident.get("numerKRS") if isinstance(_ident, dict) else "")
                or dane_podmiotu.get("numerKRS")
                or (dane.get("numerKRS") if isinstance(dane.get("numerKRS"), str) else "")
                or reg_number
                or krs            # R-F2503 — the resolved/supplied KRS is authoritative
                or ""
            )

            # Address  (R-F2503 — siedzibaIAdres/adres may be absent/None OR a historical LIST)
            adres = _krs_current(_krs_current(dzial1.get("siedzibaIAdres")).get("adres"))
            address_parts = [
                adres.get("ulica", ""),
                adres.get("nrDomu", ""),
                adres.get("miejscowosc", ""),
                adres.get("kodPocztowy", ""),
                adres.get("kraj", ""),
            ]
            address = ", ".join(p for p in address_parts if p)

            # Status
            status_info = dane.get("statusPodmiotu") or dane_podmiotu.get("statusPodmiotu", "")
            if isinstance(status_info, dict):
                company_status = status_info.get("status", "active")
            else:
                company_status = str(status_info) if status_info else "active"

            # Date of creation
            date_of_creation = (
                dane_podmiotu.get("dataRejestracjiWKRS")
                or dane.get("dataRejestracjiWKRS")
                or ""
            )

            # PKD codes (Polish equivalent of SIC)  (R-F2503 — przedmiotDzialalnosci may be None)
            pkd_list = (dzial1.get("przedmiotDzialalnosci") or {}).get("przedmiotPrzewazajacejDzialalnosci") or []
            if isinstance(pkd_list, dict):
                pkd_list = [pkd_list]
            sic_codes = []
            for pkd in pkd_list:
                if isinstance(pkd, dict):
                    code = pkd.get("kodDzial", "") or pkd.get("kod", "")
                    desc = pkd.get("opis", "")
                    sic_codes.append(f"{code} {desc}".strip())

            # Officers (Zarzad = management board). R-F2503 — dzial2.reprezentacja is a
            # LIST of representation bodies (KRS live shape), each carrying a 'sklad'
            # (composition); the old code did .get('sklad') on the list → crash. Flatten.
            officers = []
            _repr = dzial2.get("reprezentacja")
            _repr_items = _repr if isinstance(_repr, list) else ([_repr] if isinstance(_repr, dict) else [])
            organ_list = []
            for _ri in _repr_items:
                _sklad = _ri.get("sklad") if isinstance(_ri, dict) else None
                if isinstance(_sklad, list):
                    organ_list.extend(_sklad)
                elif isinstance(_sklad, dict):
                    organ_list.append(_sklad)
            for member in organ_list:
                if isinstance(member, dict):
                    officer_name = member.get("nazwisko", "")
                    first_name = member.get("imiona", {})
                    if isinstance(first_name, dict):
                        first_name = first_name.get("imie", "")
                    elif isinstance(first_name, list):
                        first_name = first_name[0] if first_name else ""
                    full_name = f"{first_name} {officer_name}".strip()
                    officers.append({
                        "name": full_name or officer_name,
                        "role": member.get("funkcja", "member"),
                        "appointed_on": "",
                    })

            # PSC / shareholders (wspolnicy). R-F2503 — key may be present with value None.
            psc = []
            wspolnicy = dzial1.get("wspolnicySpZOO") or []
            if isinstance(wspolnicy, dict):
                wspolnicy = [wspolnicy]
            for w in wspolnicy:
                if isinstance(w, dict):
                    psc.append({
                        "name": w.get("nazwisko", w.get("nazwa", "")),
                        "kind": "shareholder",
                        "natures_of_control": [],
                    })

            # R-F2503 — when the KRS was RESOLVED from a name (not operator-supplied),
            # VERIFY the fetched company name matches the query before trusting it. A
            # wrong GLEIF/registered_as match must NEVER return another company's
            # officers (never-false-clean). On mismatch: discard → GLEIF identity.
            if _resolved_from_name and not _pl_name_matches(company_name, name):
                logger.info("PL KRS %s: fetched '%s' != query '%s' — discarding to avoid wrong-entity data",
                            krs, company_name, name)
                return None

            source_url = f"https://wyszukiwarka-krs.ms.gov.pl/api/krs/OdpisPelny/{company_number}"

            return _build_result(
                company_name=company_name,
                company_number=company_number,
                company_status=company_status.lower() if company_status else "unknown",
                date_of_creation=date_of_creation,
                registered_office_address=address,
                jurisdiction="PL",
                sic_codes=sic_codes,
                officers=officers,
                psc=psc,
                source_url=source_url,
                adapter="poland_krs",
            )
    except Exception as exc:
        logger.warning("Poland KRS lookup failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Romania ONRC / ANAF  (RO)                                        ║
# ╚══════════════════════════════════════════════════════════════════════╝

_RO_ANAF_URL = "https://webservicesp.anaf.ro/PlatitorTvaRest/api/v8/ws/tva"


async def _lookup_romania(name: str, reg_number: str | None) -> dict | None:
    """Romania — ANAF public API for CUI/VAT validation + basic company data."""
    cui = _extract_cui(reg_number or name)
    if not cui:
        logger.info("Romania adapter: no CUI found in '%s' / '%s'", name, reg_number)
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            import json
            from datetime import date

            payload = [{"cui": int(cui), "data": date.today().isoformat()}]
            resp = await client.post(
                _RO_ANAF_URL,
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                logger.warning("ANAF API returned %d", resp.status_code)
                return None

            data = resp.json()
            found = data.get("found", data.get("cod", data))
            if isinstance(found, list) and found:
                # Match response by CUI (int-to-int) to avoid type mismatch
                cui_int = int(cui)
                record = next(
                    (item for item in found
                     if int(item.get("date_generale", item).get("cui", 0)) == cui_int),
                    found[0],
                )
            elif isinstance(data, dict) and "date_generale" in data:
                record = data
            elif isinstance(data, list) and data:
                record = data[0]
            else:
                record = data

            # ANAF response structure
            date_gen = record.get("date_generale", record)
            company_name = date_gen.get("denumire") or date_gen.get("den") or name
            company_number = str(date_gen.get("cui", cui))
            address = date_gen.get("adresa") or date_gen.get("adresa_domiciliu_fiscal") or ""

            # Status from VAT registration
            status_vat = date_gen.get("statusInactivi", date_gen.get("stare", ""))
            if status_vat is True or str(status_vat).lower() in ("true", "inactiv"):
                company_status = "inactive"
            else:
                company_status = "active"

            # CAEN code (Romanian equivalent of SIC/NACE)
            caen = date_gen.get("cod_CAEN") or date_gen.get("aut", "")
            sic_codes = [str(caen)] if caen else []

            date_of_creation = str(date_gen.get("data_inregistrare", ""))

            source_url = f"https://portal.onrc.ro/ONRCPortalWeb/appmanager/myONRC/wicket?cui={cui}"

            return _build_result(
                company_name=company_name,
                company_number=company_number,
                company_status=company_status,
                date_of_creation=date_of_creation,
                registered_office_address=address,
                jurisdiction="RO",
                sic_codes=sic_codes,
                officers=[],  # ANAF doesn't return officers
                psc=[],       # ANAF doesn't return PSC
                source_url=source_url,
                adapter="romania_anaf",
            )
    except Exception as exc:
        logger.warning("Romania ANAF lookup failed: %s", exc)
        return None


def _extract_cui(text: str) -> str | None:
    """Extract a Romanian CUI (Cod Unic de Inregistrare) from text.

    CUI is a numeric code, 2-10 digits, sometimes prefixed with 'RO'
    for VAT purposes.
    """
    if not text:
        return None
    # Strip 'RO' prefix if present
    cleaned = re.sub(r'^RO\s*', '', text.strip(), flags=re.IGNORECASE)
    m = re.search(r'\b(\d{2,10})\b', cleaned)
    return m.group(1) if m else None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Turkey MERSIS  (TR)                                               ║
# ╚══════════════════════════════════════════════════════════════════════╝

_TR_MERSIS_SEARCH = "https://mersis.gtb.gov.tr"


async def _lookup_turkey(name: str, reg_number: str | None) -> dict | None:
    """Turkey MERSIS — public company search (HTML scraping)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            # MERSIS search page
            search_term = reg_number or name
            # Try the public search interface
            resp = await client.get(
                f"{_TR_MERSIS_SEARCH}/",
                params={"q": search_term},
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ARIA-DD/1.0)",
                    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.5",
                },
            )
            if resp.status_code != 200:
                logger.warning("MERSIS returned %d", resp.status_code)
                return None

            html = resp.text

            # Extract company info from HTML
            company_name = name
            company_number = reg_number or ""

            # Look for MERSIS number (16 digits)
            mersis_match = re.search(r'(?:MERSIS\s*(?:No|Numaras\xc4\xb1)?)[:\s]*(\d{16})', html, re.IGNORECASE)
            if mersis_match:
                company_number = mersis_match.group(1)

            # Look for company name in Turkish HTML
            name_match = re.search(
                r'(?:Ticaret\s*Unvan\xc4\xb1|Firma\s*Ad\xc4\xb1|Unvan)[:\s]*([^<]{3,150})',
                html, re.IGNORECASE,
            )
            if name_match:
                company_name = _html_unescape(name_match.group(1).strip())

            # Status
            status_match = re.search(
                r'(?:Durum|Status)[:\s]*(Faal|Aktif|Tasfiye|Kapan\xc4\xb1\xc5\x9f|Active|Dissolved)',
                html, re.IGNORECASE,
            )
            if status_match:
                raw_status = status_match.group(1).lower()
                if raw_status in ("faal", "aktif", "active"):
                    company_status = "active"
                elif raw_status in ("tasfiye",):
                    company_status = "liquidation"
                else:
                    company_status = "dissolved"
            else:
                company_status = "unknown"

            # Address
            addr_match = re.search(
                r'(?:Adres|Address)[:\s]*([^<]{5,250})',
                html, re.IGNORECASE,
            )
            address = _html_unescape(addr_match.group(1).strip()) if addr_match else ""

            # NACE code (Turkey uses NACE)
            nace_match = re.search(r'(?:NACE|Faaliyet)[:\s]*([\d.]{3,10})', html, re.IGNORECASE)
            sic_codes = [nace_match.group(1)] if nace_match else []

            # Date
            date_match = re.search(
                r'(?:Kurulu\xc5\x9f\s*Tarih|Tescil\s*Tarih)[:\s]*(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4})',
                html, re.IGNORECASE,
            )
            date_of_creation = date_match.group(1) if date_match else ""

            # Officers
            officers = []
            officer_pattern = re.finditer(
                r'(?:M\xc3\xbcd\xc3\xbcr|Y\xc3\xb6netici|Ortak|Director)[:\s]*([^<]{3,80})',
                html, re.IGNORECASE,
            )
            for m in officer_pattern:
                officer_name = _html_unescape(m.group(1).strip())
                if officer_name and len(officer_name) > 2:
                    officers.append({
                        "name": officer_name,
                        "role": "director",
                        "appointed_on": "",
                    })

            # R-F2737 — only attach a MERSIS hit if the search result corroborates the
            # query; otherwise any page returned scraped officers/status for the subject.
            _ex_name = _html_unescape(name_match.group(1).strip()) if name_match else ""
            _ex_reg = mersis_match.group(1) if mersis_match else ""
            if not _scrape_confirms_query(name, reg_number, _ex_name, _ex_reg):
                logger.debug("Turkey MERSIS: search result did not confirm query — not attaching")
                return None

            source_url = f"{_TR_MERSIS_SEARCH}/?q={search_term}"

            return _build_result(
                company_name=company_name,
                company_number=company_number,
                company_status=company_status,
                date_of_creation=date_of_creation,
                registered_office_address=address,
                jurisdiction="TR",
                sic_codes=sic_codes,
                officers=officers,
                psc=[],
                source_url=source_url,
                adapter="turkey_mersis",
            )
    except Exception as exc:
        logger.warning("Turkey MERSIS scrape failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Brazil CNPJ — ReceitaWS  (BR)                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝

_BR_RECEITAWS_BASE = "https://receitaws.com.br/v1/cnpj"


async def _lookup_brazil(name: str, reg_number: str | None) -> dict | None:
    """Brazil — ReceitaWS free CNPJ API (no auth, 3 req/min free tier)."""
    cnpj = _extract_cnpj(reg_number or name)
    if not cnpj:
        logger.info("Brazil adapter: no CNPJ found in '%s' / '%s'", name, reg_number)
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            url = f"{_BR_RECEITAWS_BASE}/{cnpj}"
            resp = await client.get(url, headers={"Accept": "application/json"})  # no-ssrf-check: fixed _BR_RECEITAWS_BASE; cnpj is digit-normalized
            if resp.status_code == 429:
                logger.warning("ReceitaWS rate limited (3 req/min free tier)")
                return None
            if resp.status_code != 200:
                logger.warning("ReceitaWS returned %d", resp.status_code)
                return None

            data = resp.json()
            if data.get("status") == "ERROR":
                logger.info("ReceitaWS error: %s", data.get("message", ""))
                return None

            company_name = data.get("nome") or data.get("fantasia") or name
            company_number = data.get("cnpj") or cnpj

            # Status: situacao field
            raw_status = (data.get("situacao") or "").lower()
            if raw_status == "ativa":
                company_status = "active"
            elif raw_status in ("baixada", "extinta"):
                company_status = "dissolved"
            elif raw_status in ("suspensa",):
                company_status = "suspended"
            else:
                company_status = raw_status or "unknown"

            date_of_creation = data.get("abertura") or ""

            # Address
            address_parts = [
                data.get("logradouro", ""),
                data.get("numero", ""),
                data.get("complemento", ""),
                data.get("bairro", ""),
                data.get("municipio", ""),
                data.get("uf", ""),
                data.get("cep", ""),
            ]
            address = ", ".join(p for p in address_parts if p and p.strip())

            # CNAE codes (Brazilian SIC equivalent)
            sic_codes = []
            atividade_principal = data.get("atividade_principal") or []
            for at in atividade_principal:
                if isinstance(at, dict):
                    code = at.get("code", "")
                    desc = at.get("text", "")
                    sic_codes.append(f"{code} {desc}".strip())

            # QSA — officers / shareholders
            officers = []
            psc = []
            qsa = data.get("qsa") or []
            for member in qsa:
                if isinstance(member, dict):
                    member_name = member.get("nome", "")
                    qual = member.get("qual", "")
                    entry = {
                        "name": member_name,
                        "role": qual,
                        "appointed_on": "",
                    }
                    officers.append(entry)
                    # QSA members are effectively persons of significant control
                    psc.append({
                        "name": member_name,
                        "kind": qual,
                        "natures_of_control": [qual],
                    })

            source_url = f"https://receitaws.com.br/v1/cnpj/{cnpj}"

            return _build_result(
                company_name=company_name,
                company_number=company_number,
                company_status=company_status,
                date_of_creation=date_of_creation,
                registered_office_address=address,
                jurisdiction="BR",
                sic_codes=sic_codes,
                officers=officers,
                psc=psc,
                source_url=source_url,
                adapter="brazil_cnpj",
            )
    except Exception as exc:
        logger.warning("Brazil CNPJ lookup failed: %s", exc)
        return None


def _extract_cnpj(text: str) -> str | None:
    """Extract a Brazilian CNPJ (14-digit number) from text.

    CNPJ format: XX.XXX.XXX/XXXX-XX or 14 raw digits.
    """
    if not text:
        return None
    # Try formatted CNPJ first
    m = re.search(r'(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})', text)
    if m:
        # Strip formatting
        return re.sub(r'[./-]', '', m.group(1))
    # Try raw 14 digits
    m = re.search(r'\b(\d{14})\b', text)
    return m.group(1) if m else None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Nigeria — CAC (Corporate Affairs Commission)                      ║
# ╚══════════════════════════════════════════════════════════════════════╝
# 2026-04-12: web scrape of public CAC portal. No official API.

async def _lookup_nigeria(name: str, reg_number: str | None) -> dict | None:
    """Search Nigeria Corporate Affairs Commission for a company."""
    from .ua_rotation import random_ua
    try:
        query = reg_number or name
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            resp = await client.get(
                "https://search.cac.gov.ng/home/company_search",
                params={"search": query},
                headers={"User-Agent": random_ua()},
            )
            if resp.status_code != 200:
                logger.warning("[NG CAC] Status %d for '%s'", resp.status_code, query)
                return None

            html = resp.text
            # Extract company details from HTML
            name_match = re.search(r"Company Name[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
            rc_match = re.search(r"RC\s*(?:Number)?[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
            status_match = re.search(r"Status[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
            date_match = re.search(r"(?:Date of (?:Registration|Incorporation))[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
            address_match = re.search(r"(?:Registered )?Address[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)

            company_name = _html_unescape(name_match.group(1).strip()) if name_match else name
            company_number = _html_unescape(rc_match.group(1).strip()) if rc_match else reg_number or ""
            company_status = _html_unescape(status_match.group(1).strip()) if status_match else "unknown"
            date_of_creation = _html_unescape(date_match.group(1).strip()) if date_match else ""
            address = _html_unescape(address_match.group(1).strip()) if address_match else ""

            if not name_match and not rc_match:
                return None  # No match found
            # R-F2737 — the CAC search result must corroborate the query; a page for a
            # different company must not fabricate a subject identifier.
            if not _scrape_confirms_query(name, reg_number,
                                          company_name if name_match else "",
                                          company_number if rc_match else ""):
                logger.debug("Nigeria CAC: search result did not confirm query %r — not attaching", query)
                return None

            return _build_result(
                company_name=company_name,
                company_number=company_number,
                company_status=company_status,
                date_of_creation=date_of_creation,
                registered_office_address=address,
                jurisdiction="NG",
                sic_codes=[],
                officers=[],
                psc=[],
                source_url=f"https://search.cac.gov.ng/home/company_search?search={query}",
                adapter="nigeria_cac",
            )
    except Exception as exc:
        logger.warning("Nigeria CAC lookup failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ��  UAE — Ministry of Economy / DED (Economic Department)             ║
# ╚══════════════════════════════════════════════════════════════════════╝
# 2026-04-12: uses the public UAE business search portal.

async def _lookup_uae(name: str, reg_number: str | None) -> dict | None:
    """Search UAE company registries.

    Strategy (most defence brokers register in financial free zones):
      1. DIFC public register (Dubai International Financial Centre) — has a
         JSON search endpoint. Most international defence-sector firms with
         a UAE presence sit here.
      2. ADGM public register (Abu Dhabi Global Market) — fallback.
      3. DED Dubai mainland — last-resort HTML scrape.

    Mainland 7-emirate DEDs and MoEAT do not expose clean public APIs and
    are intentionally NOT covered here. Returns None if none of the above
    yield usable data.
    """
    from .ua_rotation import random_ua
    query = (reg_number or name or "").strip()
    if not query:
        return None

    try:
        async with httpx.AsyncClient(  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": random_ua(),
                "Accept": "application/json, text/html;q=0.9",
            },
        ) as client:

            # ── 1. DIFC public register ──
            try:
                difc_resp = await client.get(
                    "https://www.difc.ae/api/public-register/search",
                    params={"q": name or query, "limit": 5},
                )
                if difc_resp.status_code == 200:
                    payload = difc_resp.json()
                    hits = payload if isinstance(payload, list) else payload.get("results") or payload.get("data") or []
                    if hits and isinstance(hits, list):
                        target = (name or query).lower()
                        match = next(
                            (h for h in hits if isinstance(h, dict) and (h.get("name") or h.get("legal_name") or "").lower() == target),
                            hits[0] if isinstance(hits[0], dict) else None,
                        )
                        if match:
                            cname = match.get("name") or match.get("legal_name") or name
                            cnum = match.get("license_number") or match.get("registration_number") or match.get("number") or reg_number or ""
                            cstatus = (match.get("status") or "active").lower()
                            cdate = match.get("incorporation_date") or match.get("license_issue_date") or ""
                            caddr_obj = match.get("registered_address") or match.get("address") or {}
                            if isinstance(caddr_obj, dict):
                                caddr = ", ".join(filter(None, [
                                    caddr_obj.get("line1", ""),
                                    caddr_obj.get("city", "Dubai"),
                                    "DIFC, UAE",
                                ]))
                            else:
                                caddr = str(caddr_obj) if caddr_obj else "DIFC, Dubai, UAE"
                            activity = match.get("activity") or match.get("business_activity") or match.get("category") or ""
                            sic = [activity] if activity else []
                            officers_raw = match.get("officers") or match.get("directors") or []
                            officers: list[dict] = []
                            for o in (officers_raw if isinstance(officers_raw, list) else [])[:25]:
                                if isinstance(o, dict) and (o.get("name") or o.get("full_name")):
                                    officers.append({
                                        "name": o.get("name") or o.get("full_name"),
                                        "role": o.get("position") or o.get("role") or "officer",
                                        "appointed_on": o.get("appointed_on") or "",
                                    })
                            return _build_result(
                                company_name=cname,
                                company_number=cnum,
                                company_status=cstatus,
                                date_of_creation=cdate,
                                registered_office_address=caddr,
                                jurisdiction="AE",
                                sic_codes=sic,
                                officers=officers,
                                psc=[],
                                source_url="https://www.difc.ae/public-register",
                                adapter="uae_difc",
                            )
            except Exception as e:
                logger.debug("DIFC lookup failed (continuing to ADGM): %s", e)

            # ── 2. ADGM public register ──
            try:
                adgm_resp = await client.get(
                    "https://www.adgm.com/api/public-registers/search",
                    params={"query": name or query, "size": 5},
                )
                if adgm_resp.status_code == 200:
                    payload = adgm_resp.json()
                    hits = payload if isinstance(payload, list) else payload.get("results") or payload.get("data") or []
                    if hits and isinstance(hits, list):
                        match = next((h for h in hits if isinstance(h, dict)), None)
                        if match:
                            cname = match.get("name") or match.get("legal_name") or name
                            cnum = match.get("registration_number") or match.get("number") or reg_number or ""
                            cstatus = (match.get("status") or "active").lower()
                            return _build_result(
                                company_name=cname,
                                company_number=cnum,
                                company_status=cstatus,
                                date_of_creation=match.get("incorporation_date") or "",
                                registered_office_address="ADGM, Abu Dhabi, UAE",
                                jurisdiction="AE",
                                sic_codes=[match.get("activity")] if match.get("activity") else [],
                                officers=[],
                                psc=[],
                                source_url="https://www.adgm.com/public-registers",
                                adapter="uae_adgm",
                            )
            except Exception as e:
                logger.debug("ADGM lookup failed (continuing to DED): %s", e)

            # ── 3. DED Dubai mainland HTML fallback ──
            try:
                resp = await client.get(
                    "https://www.dubaided.gov.ae/en/Pages/BusinessSearch.aspx",
                    params={"q": query},
                )
                if resp.status_code == 200:
                    html = resp.text
                    name_match = re.search(r"(?:Trade Name|Company)[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
                    lic_match = re.search(r"(?:License|Licence)\s*(?:Number|No)[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
                    status_match = re.search(r"Status[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
                    activity_match = re.search(r"(?:Activity|Business Type)[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
                    if name_match or lic_match:
                        return _build_result(
                            company_name=_html_unescape(name_match.group(1).strip()) if name_match else name,
                            company_number=_html_unescape(lic_match.group(1).strip()) if lic_match else reg_number or "",
                            company_status=_html_unescape(status_match.group(1).strip()) if status_match else "unknown",
                            date_of_creation="",
                            registered_office_address="Dubai, UAE",
                            jurisdiction="AE",
                            sic_codes=[_html_unescape(activity_match.group(1).strip())] if activity_match else [],
                            officers=[],
                            psc=[],
                            source_url=f"https://www.dubaided.gov.ae/en/Pages/BusinessSearch.aspx?q={query}",
                            adapter="uae_ded",
                        )
            except Exception as e:
                logger.debug("DED Dubai lookup failed: %s", e)

            logger.info("UAE adapter: no usable data from DIFC/ADGM/DED for '%s'", query)
            return None
    except Exception as exc:
        logger.warning("UAE registry lookup failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  India — MCA (Ministry of Corporate Affairs)                       ║
# ╚══════════════════════════════════════════════════════════════════════╝
# 2026-04-12: uses MCA public company search API.

async def _lookup_india(name: str, reg_number: str | None) -> dict | None:
    """Search India MCA company registry."""
    from .ua_rotation import random_ua
    try:
        # MCA V3 API (public, no auth required)
        query = reg_number or name
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            resp = await client.get(
                "https://www.mca.gov.in/mcafoportal/showCompanyMaster.do",
                params={"companyName": name} if not reg_number else {"companyID": reg_number},
                headers={"User-Agent": random_ua()},
            )
            if resp.status_code != 200:
                logger.warning("[IN MCA] Status %d for '%s'", resp.status_code, query)
                return None

            html = resp.text
            name_match = re.search(r"Company Name[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
            cin_match = re.search(r"CIN[:\s]*</?\w+[^>]*>\s*([A-Z0-9]+)", html, re.I)
            status_match = re.search(r"(?:Company Status|Status)[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
            date_match = re.search(r"Date of Incorporation[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
            address_match = re.search(r"Registered (?:Office )?Address[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)
            activity_match = re.search(r"(?:Principal Business|Industry|Class)[:\s]*</?\w+[^>]*>\s*([^<]+)", html, re.I)

            company_name = _html_unescape(name_match.group(1).strip()) if name_match else name
            company_number = _html_unescape(cin_match.group(1).strip()) if cin_match else reg_number or ""

            if not name_match and not cin_match:
                return None
            # R-F2737 — the MCA result must corroborate the query (searched by
            # companyName OR companyID); a different company must not be attached.
            if not _scrape_confirms_query(name, reg_number,
                                          company_name if name_match else "",
                                          company_number if cin_match else ""):
                logger.debug("India MCA: result did not confirm query %r — not attaching", query)
                return None

            return _build_result(
                company_name=company_name,
                company_number=company_number,
                company_status=_html_unescape(status_match.group(1).strip()) if status_match else "unknown",
                date_of_creation=_html_unescape(date_match.group(1).strip()) if date_match else "",
                registered_office_address=_html_unescape(address_match.group(1).strip()) if address_match else "",
                jurisdiction="IN",
                sic_codes=[_html_unescape(activity_match.group(1).strip())] if activity_match else [],
                officers=[],
                psc=[],
                source_url=f"https://www.mca.gov.in/mcafoportal/showCompanyMaster.do",
                adapter="india_mca",
            )
    except Exception as exc:
        logger.warning("India MCA lookup failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Slovakia ORSR (Obchodný register SR)  (SK)                        ║
# ╚══════════════════════════════════════════════════════════════════════╝

_SK_BASE = "https://www.orsr.sk"
_SK_RPO_BASE = "https://api.statistics.sk/rpo/v1"


async def _lookup_slovakia(name: str, reg_number: str | None) -> dict | None:
    """Slovak company registry via the OFFICIAL RPO JSON API (R-F2939).

    Replaces the orsr.sk windows-1250 HTML scrape, which had drifted and fell back to
    `company_name = f"IČO {ico}"` — the label as the name (live 2026-07-23: SK returned
    'IČO 31322832'). RPO (Register právnických osôb, api.statistics.sk) is the state's
    JSON API: search by IČO returns exactly one entity with its real name variants,
    address history and establishment date."""
    ico = (reg_number or "").replace(" ", "").strip()
    if not ico.isdigit():
        ico = ""
    try:
        async with httpx.AsyncClient(  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            timeout=_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ARIA-DD/1.0)", "Accept": "application/json"},
        ) as client:
            if ico:
                r = await client.get(f"{_SK_RPO_BASE}/search", params={"identifier": ico})
            elif name:
                r = await client.get(f"{_SK_RPO_BASE}/search", params={"fullName": name})
            else:
                return None
            if r.status_code != 200:
                logger.info("SK RPO search returned %d", r.status_code)
                return None
            results = (r.json() or {}).get("results") or []
            entity = _sk_best_entity(results, name, ico)
            if not entity:
                logger.info("SK RPO: no match for ico=%s name=%s", ico, name)
                return None
            return _parse_sk_rpo(entity)
    except Exception as exc:
        logger.warning("SK RPO lookup failed: %s", exc)
        return None


def _sk_best_entity(results: list, name: str, ico: str) -> dict | None:
    """Pick the entity: an exact IČO match if we searched by IČO; else an exact
    name match; else the single result; else None. Never returns a wrong entity just
    to have one — for DD a wrong match is worse than no match."""
    if not results:
        return None
    if ico:
        for e in results:
            if ico in [str(i.get("value") or "").strip() for i in (e.get("identifiers") or [])]:
                return e
        return None
    def _norm(x: str) -> str:
        # Compare names ignoring punctuation and the common Slovak legal-form suffixes,
        # so "SLOVNAFT" matches "SLOVNAFT, a.s." — but nothing looser, to avoid a wrong hit.
        x = re.sub(r"[.,]", " ", str(x or "").lower())
        x = re.sub(r"\b(a\s*s|s\s*r\s*o|akciov[aá] spolo[cč]nos[tť]|spol s r o|k s|v o s)\b", " ", x)
        return re.sub(r"\s+", " ", x).strip()

    q = _norm(name)
    if not q:
        return None
    exact = [e for e in results if any(_norm(fn.get("value")) == q for fn in (e.get("fullNames") or []))]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None   # multiple exact-normalised matches -> ambiguous, refuse
    return results[0] if len(results) == 1 else None


def _sk_rpo_current(items: list, value_key: str = "value") -> dict | None:
    """RPO fields are validity-dated lists; return the current one (no validTo)."""
    dated = [x for x in (items or []) if isinstance(x, dict)]
    active = [x for x in dated if not x.get("validTo")]
    pool = active or dated
    if not pool:
        return None
    pool.sort(key=lambda x: str(x.get("validFrom") or ""), reverse=True)
    return pool[0]


def _parse_sk_rpo(e: dict) -> dict | None:
    name_item = _sk_rpo_current(e.get("fullNames"))
    company_name = str((name_item or {}).get("value") or "").strip()
    if not company_name:
        return None

    ico = ""
    for i in (e.get("identifiers") or []):
        v = str(i.get("value") or "").strip()
        if v.isdigit():
            ico = v
            break

    def _sk_val(v) -> str:
        # RPO nests several address fields as {value: ...} objects, not plain strings.
        if isinstance(v, dict):
            return str(v.get("value") or "").strip()
        if isinstance(v, list) and v:
            return _sk_val(v[0])
        return str(v or "").strip()

    addr_item = _sk_rpo_current(e.get("addresses")) or {}
    street = _sk_val(addr_item.get("street"))
    bno = _sk_val(addr_item.get("buildingNumber"))
    psc = _sk_val(addr_item.get("postalCodes"))
    town = _sk_val(addr_item.get("municipality"))
    line1 = " ".join(p for p in (street, bno) if p)
    address = ", ".join(p for p in (line1, psc, town) if p) or _sk_val(addr_item.get("formatedAddress"))

    terminated = bool(e.get("termination"))
    status = "terminated" if terminated else "active"

    return _build_result(
        company_name=company_name,
        company_number=ico,
        company_status=status,
        date_of_creation=str(e.get("establishment") or ""),
        registered_office_address=address,
        jurisdiction="SK",
        sic_codes=[],
        officers=[],   # RPO does not expose statutory bodies on this endpoint
        psc=[],
        source_url=f"https://rpo.statistics.sk/rpo/detail/{e.get('id')}" if e.get("id") else "https://rpo.statistics.sk",
        adapter="slovakia_rpo",
        registry_status=RegistryStatus.VERIFIED,
    )




# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Czech Republic — ARES official JSON API  (CZ)                    ║
# ╚══════════════════════════════════════════════════════════════════════╝
# R-F2939 (fix): this constant was collateral-deleted when the SK block above was
# replaced (the block boundary swallowed the CZ banner + constant). _lookup_czech then
# raised NameError -> caught -> None -> lookup_entity fell to the GLEIF fallback, so a
# CZ lookup returned a RELATED-BUT-WRONG entity ("Nadační fond Škoda Auto") instead of
# "Škoda Auto a.s." The unit tests missed it because they exercised the PARSER
# (_parse_ares_vr) directly, not the _lookup_czech ENTRY POINT that references this
# constant — the §3c "test the actual broken path" rule. A guard test now imports and
# calls the entry points.
_CZ_ARES_BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"


async def _lookup_czech(name: str, reg_number: str | None) -> dict | None:
    """Czech commercial registry via the OFFICIAL ARES JSON API (R-F2939).

    Replaces the or.justice.cz HTML scrape. That scrape's markup had drifted, so the
    company-name regex matched nothing and the code fell back to
    `company_name = f"IČO {ico}"` — fabricating the LABEL as the name. Live 2026-07-23
    a CZ DD showed entity_name "IČO " with an empty registration number. ARES
    (ares.gov.cz) is the government's own JSON API: it returns the real name, address,
    incorporation date and the full statutory-body list, so there is nothing to scrape
    and nothing to fabricate.
    """
    ico = (reg_number or "").replace(" ", "").strip()
    if not ico.isdigit():
        ico = ""
    try:
        async with httpx.AsyncClient(  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            timeout=_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ARIA-DD/1.0)", "Accept": "application/json"},
        ) as client:
            if not ico and name:
                # Resolve an IČO from the name via the ARES search endpoint.
                sr = await client.post(
                    f"{_CZ_ARES_BASE}/ekonomicke-subjekty/vyhledat",
                    json={"obchodniJmeno": name, "pocet": 5},
                )
                if sr.status_code == 200:
                    ico = _cz_best_ico((sr.json() or {}).get("ekonomickeSubjekty") or [], name)
            if not ico:
                logger.info("CZ ARES: no IČO for '%s'", name)
                return None
            vr = await client.get(f"{_CZ_ARES_BASE}/ekonomicke-subjekty-vr/{ico}")
            if vr.status_code != 200:
                logger.info("CZ ARES VR returned %d for IČO %s", vr.status_code, ico)
                return None
            return _parse_ares_vr(vr.json(), ico)
    except Exception as exc:
        logger.warning("CZ ARES lookup failed: %s", exc)
        return None


def _cz_best_ico(subjects: list, query: str) -> str:
    """Pick the IČO of the best name match from an ARES search, else the top hit.
    Never guesses a number — only returns an IČO the registry itself returned."""
    q = (query or "").strip().lower()
    best = ""
    for sub in subjects or []:
        nm = str(sub.get("obchodniJmeno") or "").strip().lower()
        ic = str(sub.get("ico") or "").strip()
        if not ic:
            continue
        if nm == q:
            return ic
        if not best:
            best = ic
    return best


def _ares_current(val) -> str:
    """ARES VR returns many fields as HISTORY arrays — [{hodnota, datumZapisu,
    datumVymazu?}, ...] — not scalars. Return the CURRENT value: the entry with no
    datumVymazu (i.e. not superseded), else the latest by datumZapisu. A scalar or a
    single {hodnota} is passed through. Without this the whole name-history array was
    used as the company name."""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, dict):
        return str(val.get("hodnota") or "").strip()
    if isinstance(val, list):
        dicts = [x for x in val if isinstance(x, dict)]
        active = [x for x in dicts if not x.get("datumVymazu")]
        pool = active or dicts
        if not pool:
            return ""
        pool.sort(key=lambda x: str(x.get("datumZapisu") or ""), reverse=True)
        return str(pool[0].get("hodnota") or "").strip()
    return ""


def _parse_ares_vr(data: dict, ico: str) -> dict | None:
    """Parse the ARES 'veřejný rejstřík' (VR) record into the normalised result."""
    z = (data.get("zaznamy") or [{}])[0]
    company_name = _ares_current(z.get("obchodniJmeno"))
    if not company_name:
        return None   # no authoritative name -> not a usable identity, never fabricate one

    address = ""
    for a in (z.get("adresy") or []):
        if isinstance(a, dict) and a.get("datumVymazu"):   # a superseded address
            continue
        aa = a.get("adresa") if isinstance(a, dict) and isinstance(a.get("adresa"), dict) else a
        if isinstance(aa, dict) and aa.get("textovaAdresa"):
            address = str(aa["textovaAdresa"]).strip()
            break

    raw_status = str(z.get("stavSubjektu") or "").upper()
    status = "active" if raw_status.startswith("AKTIV") else (z.get("stavSubjektu") or "unknown")

    officers = []
    for organ in (z.get("statutarniOrgany") or []):
        for m in (organ.get("clenoveOrganu") or []):
            if m.get("datumVymazu"):        # a removal date => FORMER member, exclude
                continue
            fo = m.get("fyzickaOsoba") or {}
            person = " ".join(x for x in (fo.get("jmeno"), fo.get("prijmeni")) if x).strip()
            if person and person not in [o["name"] for o in officers]:
                officers.append({
                    "name": person,
                    "role": str(m.get("nazevAngazma") or "director"),
                    "appointed_on": str(m.get("datumZapisu") or ""),
                })

    return _build_result(
        company_name=company_name,
        company_number=_ares_current(z.get("ico")) or str(ico),
        company_status=status,
        date_of_creation=str(z.get("datumZapisu") or ""),
        registered_office_address=address,
        jurisdiction="CZ",
        sic_codes=[],
        officers=officers,
        psc=[],
        source_url=f"https://ares.gov.cz/ekonomicke-subjekty/res/{ico}",
        adapter="czech_ares",
        registry_status=RegistryStatus.VERIFIED,
    )




# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Hungary (e-cégjegyzék.hu)  (HU)                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝

async def _lookup_hungary(name: str, reg_number: str | None) -> dict | None:
    """Hungarian company registry (e-cegjegyzek.hu) — HTML scraping.
    Cégjegyzékszám format: NN-NN-NNNNNN (e.g. 01-10-046896)
    """
    _query_reg = reg_number  # R-F2737 — capture BEFORE the page reg overwrites it below
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            if reg_number:
                # Direct lookup by cégjegyzékszám
                clean_reg = reg_number.strip()
                url = f"https://www.e-cegjegyzek.hu/?cegadatfriss662-data=show&cegadatfrissites662-cegjegyzekszam={clean_reg}"
            else:
                # Search by name
                url = f"https://www.e-cegjegyzek.hu/?cegadatfriss662-data=show&cegadatfrissites662-cegnev={name}"

            resp = await client.get(url)  # no-ssrf-check: fixed e-cegjegyzek.hu origin; user values remain query parameters
            if resp.status_code != 200:
                return None

            h = resp.text

            # Company name — "Cégnév" or first prominent name
            company_name = ""
            cn_match = re.search(r'C[ée]gn[ée]v[:\s]*</?\w[^>]*>\s*([^<]{3,120})', h, re.IGNORECASE)
            if cn_match:
                company_name = _html_unescape(cn_match.group(1).strip())
            if not company_name:
                cn_match2 = re.search(r'<b>\s*([^<]{3,120}(?:Kft\.|Zrt\.|Nyrt\.|Bt\.|Kkt\.))', h, re.IGNORECASE)
                if cn_match2:
                    company_name = _html_unescape(cn_match2.group(1).strip())

            # Address — "Székhely"
            address = ""
            addr_match = re.search(r'Sz[ée]khely[:\s]*</?\w[^>]*>\s*([^<]{5,200})', h, re.IGNORECASE)
            if addr_match:
                address = _html_unescape(addr_match.group(1).strip())

            # Registration number from page
            reg_match = re.search(r'C[ée]gjegyz[ée]ksz[aá]m[:\s]*</?\w[^>]*>\s*(\d{2}-\d{2}-\d{6})', h, re.IGNORECASE)
            if reg_match:
                reg_number = reg_match.group(1)

            # Incorporation date — "Bejegyzés dátuma"
            inc_date = ""
            date_match = re.search(r'Bejegyz[ée]s\s+d[aá]tuma[:\s]*</?\w[^>]*>\s*(\d{4}\.\d{2}\.\d{2})', h, re.IGNORECASE)
            if date_match:
                inc_date = date_match.group(1)

            # Legal form — "Cégforma"
            legal_form = ""
            form_match = re.search(r'C[ée]gforma[:\s]*</?\w[^>]*>\s*([^<]{3,80})', h, re.IGNORECASE)
            if form_match:
                legal_form = _html_unescape(form_match.group(1).strip())

            # Status — look for "Működő" (active) or "Megszűnt" (dissolved)
            status = "active"
            if re.search(r'Megsz[űü]nt|V[ée]gelsz[aá]mol[aá]s|Felsz[aá]mol[aá]s', h, re.IGNORECASE):
                status = "dissolved"

            # Directors — "Vezető tisztségviselő" or "ügyvezető"
            officers = []
            dir_section = re.search(r'(?:Vezet[őo]\s+tiszts[ée]gvisel[őo]|[Üü]gyvezet[őo]|Igazgat[oó]s[aá]g)(.*?)(?:Fel[üu]gyel[őo]|C[ée]gjegyz[ée]k|T[öo]rzst[őo]ke|Jegyzett\s+t[őo]ke)', h, re.IGNORECASE | re.DOTALL)
            if dir_section:
                # Hungarian names: Lastname Firstname format
                names = re.findall(r'\b([A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]+\s+[A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]{2,})', dir_section.group(1))
                for pname in names[:8]:
                    if pname not in [o["name"] for o in officers]:
                        officers.append({"name": pname, "role": "director", "appointed_on": ""})

            # Business activities — "Tevékenység" or "Főtevékenység"
            activities = []
            act_match = re.search(r'(?:F[őo])?[Tt]ev[ée]kenys[ée]g(.*?)(?:Vezet[őo]|[Üü]gyvezet|C[ée]gjegyz|Jegyzett)', h, re.IGNORECASE | re.DOTALL)
            if act_match:
                acts = re.findall(r'>([^<]{5,200})<', act_match.group(1))
                activities = [_html_unescape(a.strip()) for a in acts if len(a.strip()) > 5 and not a.strip().startswith('<')][:15]

            logger.info("Hungary e-cégjegyzék parsed: name='%s' addr='%s' directors=%d activities=%d status=%s",
                        company_name, address[:60], len(officers), len(activities), status)

            if not company_name and not address:
                return None  # page didn't contain company data
            # R-F2737 — a NAME search must be corroborated (a keyed cégjegyzékszám URL is
            # an exact lookup — the key itself is the corroboration, so it is trusted).
            if not _query_reg and not _scrape_confirms_query(name, None, company_name, ""):
                logger.debug("Hungary e-cégjegyzék: name search did not confirm query %r — not attaching", name)
                return None

            return _build_result(
                company_name=company_name or f"Reg {reg_number}",
                company_number=reg_number or "",
                company_status=status,
                date_of_creation=inc_date,
                registered_office_address=address,
                jurisdiction="HU",
                sic_codes=activities[:5],
                officers=officers,
                psc=[],
                source_url=url,
                adapter="hungary_e_cegjegyzek",
            )
    except Exception as exc:
        logger.warning("Hungary registry lookup failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Germany Handelsregister  (DE)                                     ║
# ╚══════════════════════════════════════════════════════════════════════╝
#
# Source: OffeneRegister.de — open-data mirror of the German Handelsregister
# (no auth required). Coverage is good for major HR-registered companies but
# patchier for smaller GbRs and recently-formed entities. The official
# Handelsregister.de portal requires login + per-document fees, so for free
# DD it is the practical choice.

_DE_API_BASE = "https://api.offeneregister.de/api/v0"

# Maps OffeneRegister legal-form codes / status strings to a normalised
# tri-state matching what the orchestrator expects from other adapters.
_DE_STATUS_MAP = {
    "currently registered": "active",
    "active": "active",
    "registered": "active",
    "dissolved": "dissolved",
    "deleted": "dissolved",
    "in liquidation": "in_liquidation",
    "liquidation": "in_liquidation",
    "insolvency": "insolvency",
    "insolvent": "insolvency",
}


def _de_parse_hr_number(text: str) -> tuple[str, str] | None:
    """Pull (register_type, number) out of an HR string like 'HRB 12345'
    or 'HRA-99'. Returns None if no recognisable HR pattern."""
    if not text:
        return None
    m = re.search(r"\b(HR[ABG]|GnR|PR|VR)\s*[\-\.]?\s*(\d{1,7})\b", text, re.IGNORECASE)
    if m:
        return m.group(1).upper(), m.group(2)
    return None


async def _lookup_germany(name: str, reg_number: str | None) -> dict | None:
    """Germany Handelsregister via OffeneRegister.de (open-data API).

    Strategy:
      1. If reg_number is an HR number, search by HR number directly.
      2. Otherwise search by name and take the best hit.
      3. Best-effort officer enrichment via /companies/{id}/officers.
    """
    try:
        async with httpx.AsyncClient(  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "ARIA-DD/1.0"},
        ) as client:
            company: dict | None = None

            # ── Try HR-number-first lookup ──
            hr = _de_parse_hr_number(reg_number or "") or _de_parse_hr_number(name or "")
            if hr:
                hr_type, hr_num = hr
                url = f"{_DE_API_BASE}/companies/by_id"
                resp = await client.get(url, params={  # no-ssrf-check: fixed _DE_API_BASE; identifiers remain query parameters
                    "register_type": hr_type,
                    "register_number": hr_num,
                })
                if resp.status_code == 200:
                    payload = resp.json()
                    if isinstance(payload, list) and payload:
                        company = payload[0]
                    elif isinstance(payload, dict) and payload.get("name"):
                        company = payload

            # ── Fallback: name search ──
            if not company and name:
                url = f"{_DE_API_BASE}/companies/by_name"
                resp = await client.get(url, params={"name": name.strip(), "limit": 5})  # no-ssrf-check: fixed _DE_API_BASE; name remains a query parameter
                if resp.status_code == 200:
                    payload = resp.json()
                    hits = payload if isinstance(payload, list) else payload.get("results", [])
                    if hits:
                        # Best hit = exact case-insensitive match if present, else first.
                        target_lower = name.strip().lower()
                        company = next(
                            (h for h in hits if (h.get("name") or "").lower() == target_lower),
                            hits[0],
                        )

            if not company or not isinstance(company, dict):
                return None

            # ── Normalise core fields ──
            company_name = company.get("name") or name or ""
            register_type = (company.get("register_type") or company.get("registerType") or "").upper()
            register_number = str(company.get("register_number") or company.get("registerNumber") or "")
            register_court = company.get("register_court") or company.get("registerCourt") or ""

            if register_type and register_number:
                company_number = f"{register_type} {register_number}".strip()
                if register_court:
                    company_number = f"{company_number} ({register_court})"
            else:
                company_number = company.get("id") or reg_number or ""

            # ── Status ──
            raw_status = (company.get("current_status") or company.get("status") or "").strip().lower()
            company_status = _DE_STATUS_MAP.get(raw_status, raw_status or "unknown")

            # ── Address ──
            addr = company.get("registered_office") or company.get("address") or {}
            if isinstance(addr, dict):
                addr_parts = [
                    addr.get("street", ""),
                    addr.get("house_number", ""),
                    addr.get("postal_code", ""),
                    addr.get("city", ""),
                    addr.get("country", "Germany"),
                ]
                address = ", ".join(p for p in addr_parts if p)
            else:
                address = str(addr) if addr else ""

            # ── Activity codes (German WZ codes — economic activity classification) ──
            wz = company.get("wz_codes") or company.get("activity_codes") or []
            if isinstance(wz, str):
                wz = [wz]
            sic_codes = [str(c) for c in wz if c]

            # ── Date of creation ──
            date_of_creation = (
                company.get("registration_date")
                or company.get("registered_at")
                or company.get("incorporation_date")
                or ""
            )

            # ── Best-effort officer enrichment ──
            officers: list[dict] = []
            company_id = company.get("id") or company.get("company_id")
            if company_id:
                try:
                    of_url = f"{_DE_API_BASE}/companies/{company_id}/officers"
                    of_resp = await client.get(of_url)
                    if of_resp.status_code == 200:
                        of_payload = of_resp.json()
                        of_list = of_payload if isinstance(of_payload, list) else of_payload.get("officers", [])
                        for o in of_list[:25]:  # cap at 25
                            if not isinstance(o, dict):
                                continue
                            full = (
                                o.get("name")
                                or " ".join(filter(None, [o.get("first_name", ""), o.get("last_name", "")])).strip()
                            )
                            if not full:
                                continue
                            officers.append({
                                "name": full,
                                "role": o.get("position") or o.get("role") or "officer",
                                "appointed_on": o.get("appointed_on") or o.get("start_date") or "",
                            })
                except Exception as e:
                    logger.debug("DE officer enrichment failed (non-fatal): %s", e)

            # ── PSC: German Transparency Register data is not on OffeneRegister.
            # Leave empty rather than fabricate. Caller can chain to a separate
            # Transparenzregister lookup if needed (TODO follow-up).
            psc: list[dict] = []

            source_url = f"https://www.offeneregister.de/companies/{company_id}" if company_id else "https://www.offeneregister.de"

            # Defensive: only return a result if we actually got registry data
            # back. If the API shape differs from expectations and we'd ship a
            # result populated only with the user's own input, return None
            # instead — that lets DD report "no registry data" honestly rather
            # than fake-confirming the entity.
            got_real_data = bool(
                (register_type and register_number)
                or address
                or officers
                or company_id
            )
            if not got_real_data:
                logger.info("DE adapter: API responded but no usable fields extracted for '%s'", name)
                return None

            return _build_result(
                company_name=company_name,
                company_number=company_number,
                company_status=company_status,
                date_of_creation=date_of_creation,
                registered_office_address=address,
                jurisdiction="DE",
                sic_codes=sic_codes,
                officers=officers,
                psc=psc,
                source_url=source_url,
                adapter="germany_offeneregister",
            )
    except Exception as exc:
        logger.warning("Germany Handelsregister lookup failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  France — recherche-entreprises.api.gouv.fr (FR)                   ║
# ╚══════════════════════════════════════════════════════════════════════╝
#
# Source: France's official open-data company API, served from the SIRENE
# database. No auth, no key, generous rate limits. Replaces the older direct
# INSEE SIRENE API which required OAuth.
#
# Returns SIRET (14-digit establishment id) + SIREN (9-digit company id),
# legal form, NAF activity code, état administratif (active vs ceased),
# registered address, and dirigeants (directors).

_FR_API_BASE = "https://recherche-entreprises.api.gouv.fr"


def _fr_extract_siren(text: str) -> str | None:
    """Return a 9-digit SIREN if one appears in the input."""
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 9:
        return digits[:9]
    return None


async def _lookup_france(name: str, reg_number: str | None) -> dict | None:
    """France SIRENE via recherche-entreprises.api.gouv.fr (open data, no auth)."""
    try:
        async with httpx.AsyncClient(  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "ARIA-DD/1.0"},
        ) as client:
            company: dict | None = None

            # ── SIREN-direct lookup if reg_number contains 9+ digits ──
            siren = _fr_extract_siren(reg_number or "") or _fr_extract_siren(name or "")
            if siren:
                resp = await client.get(f"{_FR_API_BASE}/search", params={"q": siren, "per_page": 1})
                if resp.status_code == 200:
                    payload = resp.json()
                    results = payload.get("results", [])
                    if results:
                        company = results[0]

            # ── Fallback: name search ──
            if not company and name:
                resp = await client.get(
                    f"{_FR_API_BASE}/search",
                    params={"q": name.strip(), "per_page": 5},
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    results = payload.get("results", [])
                    if results:
                        target = name.strip().lower()
                        company = next(
                            (r for r in results if (r.get("nom_complet") or r.get("nom_raison_sociale") or "").lower() == target),
                            results[0],
                        )

            if not company or not isinstance(company, dict):
                return None

            # ── Normalise core fields ──
            company_name = (
                company.get("nom_complet")
                or company.get("nom_raison_sociale")
                or company.get("denomination")
                or name
                or ""
            )

            siren_value = company.get("siren") or siren or ""
            siege = company.get("siege") or {}
            siret_value = siege.get("siret") or company.get("siret") or ""
            company_number = siret_value or siren_value or reg_number or ""

            # ── État administratif: 'A' = active, 'C' = ceased ──
            etat = (company.get("etat_administratif") or siege.get("etat_administratif") or "").upper()
            if etat == "A":
                company_status = "active"
            elif etat == "C":
                company_status = "ceased"
            else:
                company_status = "unknown"

            date_of_creation = (
                company.get("date_creation")
                or company.get("date_creation_entreprise")
                or siege.get("date_creation")
                or ""
            )

            # ── Address (siège social) ──
            addr_parts = [
                siege.get("numero_voie", ""),
                siege.get("type_voie", ""),
                siege.get("libelle_voie", ""),
                siege.get("code_postal", ""),
                siege.get("libelle_commune", ""),
                "France",
            ]
            address = " ".join(p for p in addr_parts if p).strip() or siege.get("adresse", "") or ""

            # ── NAF activity code ──
            naf = (
                company.get("activite_principale")
                or siege.get("activite_principale")
                or ""
            )
            naf_label = company.get("libelle_activite_principale") or ""
            sic_codes = []
            if naf:
                sic_codes.append(f"{naf} {naf_label}".strip())

            # ── Dirigeants (officers) ──
            officers: list[dict] = []
            for d in (company.get("dirigeants") or [])[:25]:
                if not isinstance(d, dict):
                    continue
                full = (
                    d.get("nom_complet")
                    or " ".join(filter(None, [d.get("prenoms", ""), d.get("nom", "")])).strip()
                    or d.get("denomination", "")
                )
                if not full:
                    continue
                officers.append({
                    "name": full,
                    "role": d.get("qualite") or d.get("role") or "dirigeant",
                    "appointed_on": d.get("date_de_naissance") or "",
                })

            # Defensive: only return a result if we got real registry fields.
            got_real_data = bool(siren_value or siret_value or address or officers)
            if not got_real_data:
                logger.info("FR adapter: API responded but no usable fields for '%s'", name)
                return None

            source_url = (
                f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren_value}"
                if siren_value
                else "https://annuaire-entreprises.data.gouv.fr"
            )

            return _build_result(
                company_name=company_name,
                company_number=company_number,
                company_status=company_status,
                date_of_creation=date_of_creation,
                registered_office_address=address,
                jurisdiction="FR",
                sic_codes=sic_codes,
                officers=officers,
                psc=[],
                source_url=source_url,
                adapter="france_recherche_entreprises",
            )
    except Exception as exc:
        logger.warning("France registry lookup failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Angola — GUE / IGAPE  (AO)                                       ║
# ╚══════════════════════════════════════════════════════════════════════╝
# Angola has no public registry REST API.  The Guichet Único da Empresa
# (GUE) portal at https://gue.gov.ao provides in-person / PDF-based
# verification only.  This adapter is a stub that ensures DD runs for
# Angolan entities still produce a structured result with clear data_gaps
# rather than silently skipping registry checks.


async def _lookup_angola(name: str, reg_number: str | None) -> dict | None:
    """Angola — stub adapter (no public registry API available).

    Returns a minimal result with data_gaps explaining the limitation.
    Recommends manual verification via IGAPE or GUE office.
    """
    # R-F2695 — the best-effort homepage scrape that used to live here is GONE.
    # It GET'd the portal's HOMEPAGE with NO query — the subject's name was never
    # sent — then regexed that HTML for an identifier + a name label and, on any
    # match, returned them AS THE SUBJECT'S. dd_orchestrator assigns those to
    # report.identity.registration_number / entity_name, so boilerplate or a worked
    # example on the portal's front page could be reported as this entity's
    # registration number. A lookup that never searched for the entity cannot
    # confirm it, and no regex on a homepage can fix that — the branch was deleted
    # rather than tightened. If the portal ever exposes a real search-by-name
    # endpoint, add a call that SENDS the name (see _lookup_kenya / _lookup_ghana /
    # _lookup_saudi_arabia, which do). Falls through to the honest stub below.

    # Return a stub result so the DD report has a registry entry with
    # explicit data_gaps rather than nothing at all.
    result = _build_result(
        company_name=name,
        company_number=reg_number or "",
        company_status="unknown",
        date_of_creation="",
        registered_office_address="",
        jurisdiction="AO",
        sic_codes=[],
        officers=[],
        psc=[],
        source_url="https://gue.gov.ao",
        adapter="angola_gue_stub",
    )
    result["data_gaps"] = [
        "Angola has no public company registry API.",
        "The Guichet Único da Empresa (GUE) at https://gue.gov.ao handles registrations but does not expose online search.",
        "Recommend manual verification via IGAPE (Instituto de Gestão de Activos e Participações do Estado) or a local legal representative.",
        "NIF (Número de Identificação Fiscal) can be verified in-person at the AGT (Administração Geral Tributária).",
    ]
    return result


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Kenya — Business Registration Service (BRS)  (KE)                 ║
# ╚══════════════════════════════════════════════════════════════════════╝
# The eCitizen portal (https://www.ecitizen.go.ke) provides company search
# via the BRS at https://brs.go.ke.  The search endpoint is behind a
# session/CSRF wall, so this adapter attempts a direct hit and falls back
# to a stub with guidance.

_KE_BRS_SEARCH = "https://brs.go.ke/public-search"


async def _lookup_kenya(name: str, reg_number: str | None) -> dict | None:
    """Kenya Business Registration Service — attempt BRS search, stub fallback."""
    from .ua_rotation import random_ua
    query = reg_number or name
    _unconfirmed = False  # R-F2736 — a page came back but did NOT match the query
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            # Attempt the BRS public search endpoint
            resp = await client.get(
                _KE_BRS_SEARCH,
                params={"q": query},
                headers={
                    "User-Agent": random_ua(),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-KE,en;q=0.9",
                },
            )
            if resp.status_code == 200:
                html = resp.text

                # Try to extract company data from BRS results
                name_match = re.search(
                    r'(?:Company Name|Entity Name|Business Name)[:\s]*</?\w+[^>]*>\s*([^<]{3,120})',
                    html, re.IGNORECASE,
                )
                reg_match = re.search(
                    r'(?:Registration Number|PVT|CPR)[/\-\s]*</?\w+[^>]*>\s*([^<]{3,30})',
                    html, re.IGNORECASE,
                )
                status_match = re.search(
                    r'(?:Status|State)[:\s]*</?\w+[^>]*>\s*([^<]{3,30})',
                    html, re.IGNORECASE,
                )
                date_match = re.search(
                    r'(?:Date of (?:Registration|Incorporation))[:\s]*</?\w+[^>]*>\s*([^<]{5,30})',
                    html, re.IGNORECASE,
                )
                address_match = re.search(
                    r'(?:Registered Office|Postal Address|Address)[:\s]*</?\w+[^>]*>\s*([^<]{5,200})',
                    html, re.IGNORECASE,
                )

                _ex_name = _html_unescape(name_match.group(1).strip()) if name_match else ""
                _ex_reg = _html_unescape(reg_match.group(1).strip()) if reg_match else ""
                if name_match or reg_match:
                    # R-F2736 — attach ONLY if the page corroborates the query.
                    if _scrape_confirms_query(name, reg_number, _ex_name, _ex_reg):
                        return _build_result(
                            company_name=_ex_name or name,
                            company_number=_ex_reg or reg_number or "",
                            company_status=_html_unescape(status_match.group(1).strip()).lower() if status_match else "unknown",
                            date_of_creation=_html_unescape(date_match.group(1).strip()) if date_match else "",
                            registered_office_address=_html_unescape(address_match.group(1).strip()) if address_match else "",
                            jurisdiction="KE",
                            sic_codes=[],
                            officers=[],
                            psc=[],
                            source_url=f"{_KE_BRS_SEARCH}?q={query}",
                            adapter="kenya_brs",
                        )
                    _unconfirmed = True
                    logger.debug("Kenya BRS: scraped page did not confirm query %r — not attaching id", query)
    except Exception as exc:
        logger.debug("Kenya BRS search failed (falling back to stub): %s", exc)

    # Stub fallback — BRS is typically behind eCitizen login
    result = _build_result(
        company_name=name,
        company_number=reg_number or "",
        company_status="unknown",
        date_of_creation="",
        registered_office_address="",
        jurisdiction="KE",
        sic_codes=[],
        officers=[],
        psc=[],
        source_url="https://brs.go.ke",
        adapter="kenya_brs_stub",
    )
    result["data_gaps"] = ([
        f"Kenya BRS returned a page but its registry data did NOT match '{query}' — "
        f"no confirmed record; identifier NOT attached (R-F2736)."
    ] if _unconfirmed else []) + [
        "Kenya BRS (Business Registration Service) public search requires eCitizen session authentication.",
        "Company search available at https://www.ecitizen.go.ke via the BRS service.",
        "Recommend manual verification via eCitizen portal or direct enquiry to the Registrar of Companies, Nairobi.",
        "PVT/CPR numbers can be verified through the eCitizen business name search.",
    ]
    return result


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Saudi Arabia — Ministry of Commerce (MOCI)  (SA)                  ║
# ╚══════════════════════════════════════════════════════════════════════╝
# The Ministry of Commerce (mc.gov.sa) maintains the Commercial Registry
# (CR).  There is no stable public REST API, but the MOCI portal exposes
# a company-search page that sometimes returns structured data.

_SA_MC_SEARCH = "https://mc.gov.sa/en/eservices/Pages/Commercial-data.aspx"


async def _lookup_saudi_arabia(name: str, reg_number: str | None) -> dict | None:
    """Saudi Arabia Ministry of Commerce — attempt CR lookup, stub fallback."""
    from .ua_rotation import random_ua
    query = reg_number or name
    _unconfirmed = False  # R-F2733 — a page came back but did NOT match the query
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            # Attempt the MOCI commercial data page
            resp = await client.get(
                _SA_MC_SEARCH,
                params={"CRNumber": reg_number} if reg_number else {"entityName": name},
                headers={
                    "User-Agent": random_ua(),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-SA,en;q=0.9,ar;q=0.5",
                },
            )
            if resp.status_code == 200:
                html = resp.text

                # Try to extract CR data from the MOCI page
                name_match = re.search(
                    r'(?:Entity Name|Company Name|Trade Name|اسم المنشأة)[:\s]*</?\w+[^>]*>\s*([^<]{3,120})',
                    html, re.IGNORECASE,
                )
                cr_match = re.search(
                    r'(?:CR Number|Commercial Registration|رقم السجل التجاري)[:\s]*</?\w+[^>]*>\s*(\d{7,15})',
                    html, re.IGNORECASE,
                )
                status_match = re.search(
                    r'(?:Status|Entity Status|حالة المنشأة)[:\s]*</?\w+[^>]*>\s*([^<]{3,30})',
                    html, re.IGNORECASE,
                )
                date_match = re.search(
                    r'(?:Issue Date|Incorporation|تاريخ)[:\s]*</?\w+[^>]*>\s*([^<]{5,30})',
                    html, re.IGNORECASE,
                )
                address_match = re.search(
                    r'(?:Address|City|المدينة)[:\s]*</?\w+[^>]*>\s*([^<]{3,200})',
                    html, re.IGNORECASE,
                )
                activity_match = re.search(
                    r'(?:Activity|Business Activity|النشاط)[:\s]*</?\w+[^>]*>\s*([^<]{3,200})',
                    html, re.IGNORECASE,
                )

                _ex_name = _html_unescape(name_match.group(1).strip()) if name_match else ""
                _ex_reg = cr_match.group(1) if cr_match else ""
                if name_match or cr_match:
                    # R-F2733 — only attach the scraped CR/name to the subject if it
                    # CORROBORATES the query; a non-matching page must not fabricate an id.
                    if _scrape_confirms_query(name, reg_number, _ex_name, _ex_reg):
                        return _build_result(
                            company_name=_ex_name or name,
                            company_number=_ex_reg or reg_number or "",
                            company_status=_html_unescape(status_match.group(1).strip()).lower() if status_match else "unknown",
                            date_of_creation=_html_unescape(date_match.group(1).strip()) if date_match else "",
                            registered_office_address=_html_unescape(address_match.group(1).strip()) if address_match else "",
                            jurisdiction="SA",
                            sic_codes=[_html_unescape(activity_match.group(1).strip())] if activity_match else [],
                            officers=[],
                            psc=[],
                            source_url=_SA_MC_SEARCH,
                            adapter="saudi_moci",
                        )
                    _unconfirmed = True
                    logger.debug("Saudi MOCI: scraped page did not confirm query %r — not attaching id", query)
    except Exception as exc:
        logger.debug("Saudi MOCI search failed (falling back to stub): %s", exc)

    # Stub fallback — MOCI portal may require Absher/NAFATH auth
    result = _build_result(
        company_name=name,
        company_number=reg_number or "",
        company_status="unknown",
        date_of_creation="",
        registered_office_address="",
        jurisdiction="SA",
        sic_codes=[],
        officers=[],
        psc=[],
        source_url="https://mc.gov.sa",
        adapter="saudi_moci_stub",
    )
    result["data_gaps"] = [
        "Saudi Arabia Ministry of Commerce (MOCI) commercial registry search may require Absher/NAFATH authentication.",
        "CR (Commercial Registration) numbers can be verified at https://mc.gov.sa/en/eservices/Pages/Commercial-data.aspx.",
        "Recommend verification via the MOCI portal or a local legal representative with NAFATH access.",
        "700-number (unified licence) or CR number is required for official verification.",
    ]
    if _unconfirmed:
        result["data_gaps"].insert(
            0, f"MOCI portal returned a page but its registry data did NOT match '{query}' — "
               f"no confirmed record; identifier NOT attached (R-F2733).")
    return result


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Ghana — Registrar General's Department (RGD)  (GH)               ║
# ╚══════════════════════════════════════════════════════════════════════╝
# The RGD Online Registry (https://rgd.gov.gh) provides company search.
# The portal is frequently gated behind login/CAPTCHA, so this adapter
# attempts a hit and falls back to a stub.

_GH_RGD_SEARCH = "https://rgd.gov.gh/online-search.php"


async def _lookup_ghana(name: str, reg_number: str | None) -> dict | None:
    """Ghana Registrar General's Department — attempt RGD search, stub fallback."""
    from .ua_rotation import random_ua
    query = reg_number or name
    _unconfirmed = False  # R-F2733 — a page came back but did NOT match the query
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            # Attempt the RGD online search
            resp = await client.get(
                _GH_RGD_SEARCH,
                params={"search": query},
                headers={
                    "User-Agent": random_ua(),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-GH,en;q=0.9",
                },
            )
            if resp.status_code == 200:
                html = resp.text

                # Try to extract company data from RGD results
                name_match = re.search(
                    r'(?:Company Name|Entity Name|Business Name)[:\s]*</?\w+[^>]*>\s*([^<]{3,120})',
                    html, re.IGNORECASE,
                )
                reg_match = re.search(
                    r'(?:Registration Number|Company No|CS\d+)',
                    html, re.IGNORECASE,
                )
                reg_num_match = re.search(
                    r'(?:Registration Number|Company No)[:\s]*</?\w+[^>]*>\s*([^<]{3,30})',
                    html, re.IGNORECASE,
                )
                status_match = re.search(
                    r'(?:Status|State)[:\s]*</?\w+[^>]*>\s*([^<]{3,30})',
                    html, re.IGNORECASE,
                )
                date_match = re.search(
                    r'(?:Date of (?:Registration|Incorporation))[:\s]*</?\w+[^>]*>\s*([^<]{5,30})',
                    html, re.IGNORECASE,
                )
                address_match = re.search(
                    r'(?:Registered Office|Address)[:\s]*</?\w+[^>]*>\s*([^<]{5,200})',
                    html, re.IGNORECASE,
                )

                _ex_name = _html_unescape(name_match.group(1).strip()) if name_match else ""
                _ex_reg = _html_unescape(reg_num_match.group(1).strip()) if reg_num_match else ""
                if name_match or reg_num_match:
                    # R-F2733 — only attach the scraped id/name if it CORROBORATES the query.
                    if _scrape_confirms_query(name, reg_number, _ex_name, _ex_reg):
                        return _build_result(
                            company_name=_ex_name or name,
                            company_number=_ex_reg or reg_number or "",
                            company_status=_html_unescape(status_match.group(1).strip()).lower() if status_match else "unknown",
                            date_of_creation=_html_unescape(date_match.group(1).strip()) if date_match else "",
                            registered_office_address=_html_unescape(address_match.group(1).strip()) if address_match else "",
                            jurisdiction="GH",
                            sic_codes=[],
                            officers=[],
                            psc=[],
                            source_url=f"{_GH_RGD_SEARCH}?search={query}",
                            adapter="ghana_rgd",
                        )
                    _unconfirmed = True
                    logger.debug("Ghana RGD: scraped page did not confirm query %r — not attaching id", query)
    except Exception as exc:
        logger.debug("Ghana RGD search failed (falling back to stub): %s", exc)

    # Stub fallback — RGD portal frequently requires login/CAPTCHA
    result = _build_result(
        company_name=name,
        company_number=reg_number or "",
        company_status="unknown",
        date_of_creation="",
        registered_office_address="",
        jurisdiction="GH",
        sic_codes=[],
        officers=[],
        psc=[],
        source_url="https://rgd.gov.gh",
        adapter="ghana_rgd_stub",
    )
    result["data_gaps"] = [
        "Ghana Registrar General's Department (RGD) online search may require login or CAPTCHA verification.",
        "Company search available at https://rgd.gov.gh/online-search.php.",
        "Recommend manual verification via the RGD office in Accra or through a local legal representative.",
        "Ghana company registration numbers typically follow the format CS123456789.",
    ]
    if _unconfirmed:
        result["data_gaps"].insert(
            0, f"RGD portal returned a page but its registry data did NOT match '{query}' — "
               f"no confirmed record; identifier NOT attached (R-F2733).")
    return result


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  South Africa — CIPC (Companies & Intellectual Property Commission) ║
# ╚══════════════════════════════════════════════════════════════════════╝

_ZA_CIPC_BASE = "https://www.cipc.co.za"
_ZA_BIZPORTAL = "https://www.bizportal.gov.za"


async def _lookup_south_africa(name: str, reg_number: str | None) -> dict | None:
    """South Africa CIPC / BizPortal — best-effort lookup, stub fallback.

    Full CIPC data requires paid credentials + manual disclosure requests.
    Public disclosure of directors / shareholders is limited; the detailed
    Form CoR 39 enumeration is behind login. This adapter returns a stub
    result with data_gaps so the DD orchestrator can cite the limitation
    honestly rather than invent officer records.
    """
    from .ua_rotation import random_ua
    query = reg_number or name
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            # BizPortal search page — exploratory; often returns a login wall.
            resp = await client.get(
                f"{_ZA_BIZPORTAL}/Account/Login",
                headers={
                    "User-Agent": random_ua(),
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-ZA,en;q=0.9",
                },
            )
            # If we land on anything other than 200, we don't try deeper parsing
            if resp.status_code != 200:
                logger.debug("CIPC BizPortal reachability check: %d", resp.status_code)
    except Exception as exc:
        logger.debug("South Africa CIPC adapter probe failed: %s", exc)

    # Normalise a stub result. SA registration numbers typically follow
    # the YYYY/NNNNNN/NN format (e.g. 2021/123456/07) — expose that pattern
    # in data_gaps so operators can validate a supplied number.
    result = _build_result(
        company_name=name,
        company_number=reg_number or "",
        company_status="unknown",
        date_of_creation="",
        registered_office_address="",
        jurisdiction="ZA",
        sic_codes=[],
        officers=[],
        psc=[],
        source_url=f"{_ZA_CIPC_BASE}",
        adapter="south_africa_cipc_stub",
    )
    result["data_gaps"] = [
        "CIPC does not expose a free programmatic company lookup API.",
        "BizPortal (https://www.bizportal.gov.za) requires login; "
        "disclosure searches are fee-based.",
        "SA company registration numbers follow YYYY/NNNNNN/NN — "
        "e.g. 2021/123456/07.",
        "Recommend: (a) request a CIPC Disclosure Certificate via a "
        "local attorney, or (b) query OpenSanctions + OpenCorporates for "
        "cross-reference.",
    ]
    return result


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Israel — Companies Registrar (Reshamh Hahavarot)                    ║
# ╚══════════════════════════════════════════════════════════════════════╝

_IL_REGISTRAR_BASE = "https://www.gov.il/en/departments/corporations_authority"
_IL_REGISTRAR_DATA = "https://data.gov.il"
_IL_COMPANIES_DATASET = "https://data.gov.il/dataset/ica_companies"
_IL_COMPANIES_RESOURCE = "f004176c-b85f-4542-8901-7b3176f9a054"


async def _lookup_israel(name: str, reg_number: str | None) -> dict | None:
    """Israel Companies Registrar — official daily open-data lookup.

    The Ministry of Justice publishes the Corporations Authority company list
    through data.gov.il's CKAN DataStore. Full extracts remain a separate paid
    service; this adapter returns only fields present in the open dataset.
    """
    from .ua_rotation import random_ua
    query = reg_number or name
    _unconfirmed = False  # R-F2736 — a record came back but did NOT match the query
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            # data.gov.il CKAN API — shape: /api/3/action/datastore_search
            resp = await client.get(
                f"{_IL_REGISTRAR_DATA}/api/3/action/datastore_search",
                params={
                    "resource_id": _IL_COMPANIES_RESOURCE,
                    "q": query,
                    "limit": 5,
                },
                headers={
                    "User-Agent": random_ua(),
                    "Accept": "application/json",
                    "Accept-Language": "en-IL,en;q=0.9,he;q=0.7",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                records = ((data.get("result") or {}).get("records") or [])
                for rec in records:
                    _hebrew_name = str(rec.get("שם חברה") or "").strip()
                    _english_name = str(
                        rec.get("שם באנגלית") or rec.get("company_name") or ""
                    ).strip()
                    _ex_reg = str(rec.get("מספר חברה") or rec.get("company_id") or "").strip()
                    # R-F3088 — the official dataset has separate Hebrew and English
                    # name columns. English queries cannot corroborate the Hebrew column,
                    # so accept whichever source-supplied name actually matches. Check all
                    # returned records because CKAN full-text ranking is not an identity
                    # guarantee.
                    _matched_name = next((
                        candidate for candidate in (_english_name, _hebrew_name)
                        if candidate and _scrape_confirms_query(
                            name, reg_number, candidate, _ex_reg
                        )
                    ), "")
                    if not _matched_name:
                        _unconfirmed = True
                        continue
                    _address = ", ".join(part for part in (
                        " ".join(part for part in (
                            str(rec.get("שם רחוב") or "").strip(),
                            str(rec.get("מספר בית") or "").strip(),
                        ) if part),
                        str(rec.get("שם עיר") or "").strip(),
                        str(rec.get("מיקוד") or "").strip(),
                        str(rec.get("מדינה") or "").strip(),
                    ) if part)
                    return _build_result(
                        company_name=_matched_name,
                        company_number=_ex_reg or reg_number or "",
                        company_status=(rec.get("סטטוס חברה") or rec.get("status") or "unknown"),
                        date_of_creation=(rec.get("תאריך התאגדות") or rec.get("date_of_registration") or ""),
                        registered_office_address=_address,
                        jurisdiction="IL",
                        sic_codes=[],
                        officers=[],
                        psc=[],
                        source_url=_IL_COMPANIES_DATASET,
                        adapter="israel_registrar_datagovil",
                    )
    except Exception as exc:
        logger.debug("Israel data.gov.il probe failed: %s", exc)

    result = _build_result(
        company_name=name,
        company_number=reg_number or "",
        company_status="unknown",
        date_of_creation="",
        registered_office_address="",
        jurisdiction="IL",
        sic_codes=[],
        officers=[],
        psc=[],
        source_url=_IL_REGISTRAR_BASE,
        adapter="israel_registrar_stub",
    )
    result["data_gaps"] = ([
        f"Israel data.gov.il returned a record but it did NOT match '{query}' — "
        f"no confirmed record; identifier NOT attached (R-F2736)."
    ] if _unconfirmed else []) + [
        "The official open dataset did not return a confirmed matching company.",
        "Full Companies Registrar extracts are a separate paid service.",
        "IL company numbers are 9 digits. Non-profits use a separate "
        "Amutot registrar; charities use a third registry.",
        "Recommend: engage a local due-diligence firm for full registry "
        "search + beneficial-ownership disclosure (not public).",
    ]
    return result


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Shared helpers                                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝

# R-F2733 — generic company-name tokens ignored when matching a scraped result to
# the query (they carry no identifying signal).
_REG_GENERIC_TOKENS = frozenset({
    "ltd", "limited", "llc", "inc", "incorporated", "plc", "company", "co", "corp",
    "corporation", "sa", "sarl", "gmbh", "group", "holdings", "holding", "the", "and",
    "trading", "services", "international", "enterprises", "est", "establishment",
})


def _scrape_confirms_query(
    query_name: str | None, query_reg: str | None,
    extracted_name: str | None, extracted_reg: str | None,
) -> bool:
    """R-F2733 — may a scraped registry result be attached to the SUBJECT?

    A best-effort portal scrape must CORROBORATE the query before its registry
    number / name is lent to the subject. A portal that returns a different company,
    a generic landing page, or a form placeholder must NOT fabricate a subject
    identifier (the R-F2695 / R-F2703 honesty class). The rule:
      * If a registration number was queried, the page's registration number is the
        strong anchor — it must be PRESENT and MATCH (absence is inconclusive → no).
      * Otherwise (name search), require a meaningful shared token between the queried
        and extracted names (ignoring generic company suffixes).
    """
    def _norm_reg(s: str | None) -> str:
        return re.sub(r"[^0-9a-z]", "", str(s or "").lower())

    def _sig_tokens(s: str | None) -> set[str]:
        return {t for t in re.split(r"[^0-9a-z]+", str(s or "").lower())
                if len(t) >= 3 and t not in _REG_GENERIC_TOKENS}

    if query_reg and _norm_reg(query_reg):
        er = _norm_reg(extracted_reg)
        return bool(er) and er == _norm_reg(query_reg)
    qn = _sig_tokens(query_name)
    en = _sig_tokens(extracted_name)
    return bool(qn) and bool(en) and bool(qn & en)


def _build_result(
    *,
    company_name: str,
    company_number: str,
    company_status: str,
    date_of_creation: str,
    registered_office_address: str,
    jurisdiction: str,
    sic_codes: list[str],
    officers: list[dict],
    psc: list[dict],
    source_url: str,
    adapter: str,
    registry_status: "RegistryStatus | str | None" = None,
) -> dict:
    """Build the normalised result dict consumed by the DD orchestrator.

    R-F2693 — `registry_status` states whether this result is REGISTRY AUTHORITY or a
    stub/fallback. Defaults to deriving from the adapter name (the `*_stub` convention
    the 8 stub adapters already follow), so no adapter needs editing and a NEW stub is
    classified correctly the moment it is named. Pass it explicitly to override (e.g.
    a real adapter that degraded to a partial hit).
    """
    if registry_status is None:
        registry_status = RegistryStatus.for_adapter(adapter)
    return {
        "profile": {
            "company_name": company_name,
            "company_number": company_number,
            "company_status": company_status,
            "date_of_creation": date_of_creation,
            "registered_office_address": registered_office_address,
            "jurisdiction": jurisdiction,
            "sic_codes": sic_codes,
        },
        "officers": officers,
        "psc": psc,
        "source_url": source_url,
        "adapter": adapter,
        "registry_status": getattr(registry_status, "value", registry_status),
    }


def _html_unescape(text: str) -> str:
    """Minimal HTML entity unescaping without external deps."""
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&nbsp;", " ")
    return text.strip()


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  United States — per-state Secretary of State dispatch               ║
# ╚══════════════════════════════════════════════════════════════════════╝
#
# The US has no federal company registry. Each state maintains its own
# Secretary of State business database. This adapter dispatches on state
# inferred from the address, then falls back to a stub with manual-
# verification guidance if no state can be determined or no adapter
# exists for that state.

_US_STATE_KEYWORDS = {
    "FL": ["florida", "miami", "orlando", "tampa", "jacksonville", "tallahassee",
           "fort lauderdale", "ft lauderdale", "st petersburg", "boca raton", "sunny isles"],
    "DE": ["delaware", "wilmington", "dover"],
    "NY": ["new york", "manhattan", "brooklyn", "queens", "bronx", "ny "],
    "CA": ["california", "los angeles", "san francisco", "san diego", "sacramento",
           "san jose", "oakland", "beverly hills"],
    "TX": ["texas", "houston", "dallas", "austin", "san antonio", "fort worth"],
    "NV": ["nevada", "las vegas", "reno", "carson city"],
    "WY": ["wyoming", "cheyenne", "sheridan"],
}

_US_STATE_ABBR_RE = re.compile(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b")  # "FL 33160" or "FL 33160-1234"

_US_STATE_NAMES = {
    "FL": "Florida", "DE": "Delaware", "NY": "New York", "CA": "California",
    "TX": "Texas", "NV": "Nevada", "WY": "Wyoming",
}

_FL_SUNBIZ_SEARCH = "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults"
_DE_SOS_SEARCH = "https://icis.corp.delaware.gov/Ecorp/EntitySearch/NameSearch.aspx"


def _detect_us_state(address: str | None, name: str = "", reg_number: str | None = None) -> str | None:
    """Best-effort US state detection from address, name, or reg-number prefix."""
    haystack = " ".join(filter(None, [address or "", name or ""])).lower()
    # 1. Zip-code-adjacent abbreviation (strongest signal)
    if address:
        m = _US_STATE_ABBR_RE.search(address)
        if m and m.group(1) in _US_STATE_KEYWORDS:
            return m.group(1)
    # 2. City / state-name keyword match
    for state, keywords in _US_STATE_KEYWORDS.items():
        for kw in keywords:
            if kw in haystack:
                return state
    # 3. Registration-number prefix (e.g. some FL docs start with L/P)
    # Not reliable enough to rely on — skip.
    return None


async def _lookup_united_states(
    name: str,
    reg_number: str | None,
    address: str | None = None,
) -> dict | None:
    """US dispatch — routes to state-specific Secretary of State lookup.

    Florida (Sunbiz) and Delaware (ICIS) have public search pages. Other
    states return a stub with manual-verification guidance so the DD
    orchestrator always gets structured output instead of None.
    """
    state = _detect_us_state(address, name, reg_number)

    # ── Florida Sunbiz ──
    if state == "FL":
        return await _lookup_us_florida(name, reg_number, address)
    # ── Delaware ICIS ──
    if state == "DE":
        return await _lookup_us_delaware(name, reg_number, address)

    # ── Stub states (NY/CA/TX/NV/WY + unknown) ──
    state_hint = _US_STATE_NAMES.get(state, "the relevant US state")
    return _build_us_stub(name, reg_number, address, state, state_hint)


async def _lookup_us_florida(
    name: str,
    reg_number: str | None,
    address: str | None,
) -> dict:
    """Florida Division of Corporations (Sunbiz) — public HTML search.

    Sunbiz has no public JSON API; the Inquiry page returns an HTML
    results table. We perform a best-effort parse of the first row.
    """
    from .ua_rotation import random_ua
    search_term = (name or "").strip()
    if not search_term:
        return _build_us_stub(name, reg_number, address, "FL", "Florida")

    params = {
        "inquiryType": "EntityName",
        "searchNameOrder": search_term.upper(),
        "aggregateId": "",
        "searchTerm": search_term,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            resp = await client.get(
                _FL_SUNBIZ_SEARCH,
                params=params,
                headers={
                    "User-Agent": random_ua(),
                    "Accept": "text/html",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if resp.status_code == 200 and resp.text:
                html = resp.text
                # Sunbiz results table: each row has a link like
                # <a href="/Inquiry/CorporationSearch/SearchResultDetail?inquirytype=EntityName&directionType=Initial&searchNameOrder=...&aggregateId=...">ENTITY NAME</a>
                detail_re = re.compile(
                    r'href="(/Inquiry/CorporationSearch/SearchResultDetail\?[^"]+)"[^>]*>([^<]+)</a>',
                    re.IGNORECASE,
                )
                matches = detail_re.findall(html)
                for detail_path, entity_name in matches[:3]:
                    clean = _html_unescape(entity_name).strip()
                    if not clean:
                        continue
                    # Pull the aggregateId (= Document Number) from the URL
                    doc_m = re.search(r"aggregateId=([^&]+)", detail_path)
                    document_number = doc_m.group(1) if doc_m else ""
                    # Status is sometimes in the adjacent cell — not reliable
                    # across Sunbiz layout changes, so leave unknown and note.
                    result = _build_result(
                        company_name=clean,
                        company_number=document_number or (reg_number or ""),
                        company_status="unknown",
                        date_of_creation="",
                        registered_office_address=address or "",
                        jurisdiction="US-FL",
                        sic_codes=[],
                        officers=[],
                        psc=[],
                        source_url=f"https://search.sunbiz.org{detail_path}",
                        adapter="us_florida_sunbiz",
                    )
                    result["data_gaps"] = [
                        "Sunbiz exposes registration + address but NOT ultimate "
                        "beneficial ownership. UBO is filed with FinCEN BOI under "
                        "the Corporate Transparency Act — not public.",
                        "Officers / registered-agent require following the "
                        "aggregateId detail link and re-parsing the HTML.",
                        f"Manual: open {result['source_url']} to confirm status "
                        f"(active / dissolved / inactive) + registered agent.",
                    ]
                    return result
    except Exception as exc:
        logger.debug("Florida Sunbiz lookup failed: %s", exc)

    # Search ran but returned no hits — treat as "not found in FL registry"
    # which is itself a strong signal on a DD.
    result = _build_us_stub(name, reg_number, address, "FL", "Florida")
    result["data_gaps"].insert(
        0,
        f"Sunbiz search for '{name}' returned no HTML match rows. "
        f"Entity may be filed under a variant name or not registered in FL. "
        f"Treat as NOT-VERIFIED until manual Sunbiz search confirms or rules out.",
    )
    return result


async def _lookup_us_delaware(
    name: str,
    reg_number: str | None,
    address: str | None,
) -> dict:
    """Delaware Secretary of State — ICIS entity name search.

    Delaware's ICIS portal performs a JavaScript POST-back on a form;
    scraping is fragile. We probe it for reachability only and return a
    stub with manual-verification guidance. Full ICIS data is fee-based
    (per-document), so the manual step is the operator-correct path.
    """
    from .ua_rotation import random_ua
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            resp = await client.get(
                _DE_SOS_SEARCH,
                headers={
                    "User-Agent": random_ua(),
                    "Accept": "text/html",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            logger.debug("Delaware ICIS reachability: %d", resp.status_code)
    except Exception as exc:
        logger.debug("Delaware ICIS probe failed: %s", exc)

    result = _build_us_stub(name, reg_number, address, "DE", "Delaware")
    result["data_gaps"].insert(
        0,
        "Delaware ICIS performs a JS-driven form POST; no stable HTML "
        "parse. Full entity detail is fee-based per document.",
    )
    return result


def _build_us_stub(
    name: str,
    reg_number: str | None,
    address: str | None,
    state: str | None,
    state_hint: str,
) -> dict:
    """Stub result for US states without an automated adapter."""
    jurisdiction_code = f"US-{state}" if state else "US"
    manual_links = {
        "FL": "https://search.sunbiz.org/Inquiry/CorporationSearch/ByName",
        "DE": "https://icis.corp.delaware.gov/Ecorp/EntitySearch/NameSearch.aspx",
        "NY": "https://apps.dos.ny.gov/publicInquiry/",
        "CA": "https://bizfileonline.sos.ca.gov/search/business",
        "TX": "https://mycpa.cpa.state.tx.us/coa/",
        "NV": "https://esos.nv.gov/EntitySearch/OnlineEntitySearch",
        "WY": "https://wyobiz.wyo.gov/Business/FilingSearch.aspx",
    }
    manual_url = manual_links.get(state or "", "")

    result = _build_result(
        company_name=name,
        company_number=reg_number or "",
        company_status="unknown",
        date_of_creation="",
        registered_office_address=address or "",
        jurisdiction=jurisdiction_code,
        sic_codes=[],
        officers=[],
        psc=[],
        source_url=manual_url or "https://www.naass.org/state-business-links/",
        adapter=f"us_{(state or 'unknown').lower()}_stub",
    )
    gaps = [
        f"US has no federal company registry — each state maintains its own "
        f"Secretary of State database.",
        f"Ultimate beneficial ownership is NOT public at any US state level. "
        f"UBO lives in FinCEN BOI filings (Corporate Transparency Act, 2024) — "
        f"request the BOI report directly from the counterparty during DD.",
    ]
    if state and manual_url:
        gaps.append(
            f"Manual verification ({state_hint}): open {manual_url} and search "
            f"for '{name}'. Confirm: (a) registration exists, (b) status is "
            f"ACTIVE, (c) registered agent, (d) principal address matches disclosure."
        )
    else:
        gaps.append(
            "State could not be inferred from address. Ask counterparty which "
            "US state the entity is registered in before proceeding."
        )
    gaps.append(
        "If the entity is a US LLC acting as an international defence financier, "
        "apply enhanced scrutiny: ghost-entity check, virtual-office detector, "
        "and OFAC SDN / BIS Entity List screen are mandatory."
    )
    result["data_gaps"] = gaps
    return result


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Finland PRH OpenData YTJ  (FI)                                     ║
# ╚══════════════════════════════════════════════════════════════════════╝
#
# R-F302: Finland is a real defence-OEM jurisdiction (Patria, Sako, Insta,
# Modirum/GESPI, Aselsan Nordic). PRH (Patentti- ja rekisterihallitus)
# operates the Trade Register at avoindata.prh.fi/opendata-ytj-api/v3 —
# JSON, no auth, free, public. The endpoint supports search by business
# name (`?name=…`) AND by Business ID / Y-tunnus (`?businessId=…`).
#
# Surface that matters for DD:
#   - businessId / Y-tunnus (8-digit + check digit)
#   - registration status (active / dissolved / liquidation)
#   - registered office address
#   - principal industry classification (TOL2008 == NACE)
#   - registered names / aliases
#   - registration date
#
# Officers are NOT exposed on the free OpenData endpoint — they sit
# behind PRH Virre paid API. Adapter returns officers=[] and a data_gap
# noting how to fetch them manually.

_FI_PRH_BASE = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"


def _extract_finnish_y_tunnus(text: str) -> str | None:
    """Y-tunnus pattern: 7 digits + hyphen + check digit. Examples:
    `1234567-8`, `0987654-3`."""
    if not text:
        return None
    m = re.search(r"\b(\d{7}-\d)\b", text)
    return m.group(1) if m else None


async def _lookup_finland(name: str, reg_number: str | None) -> dict | None:
    """Finland PRH — free JSON API. Search by Y-tunnus first if available,
    else by name. Returns the best match (status=active preferred)."""
    y_tunnus = _extract_finnish_y_tunnus(reg_number or "") or _extract_finnish_y_tunnus(name or "")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level
            if y_tunnus:
                url = f"{_FI_PRH_BASE}?businessId={y_tunnus}"
            else:
                if not name or len(name.strip()) < 3:
                    return None
                url = f"{_FI_PRH_BASE}?name={httpx.QueryParams({'name': name})['name']}"
            resp = await client.get(url)  # no-ssrf-check: fixed _FI_PRH_BASE; user values remain query parameters
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                logger.warning("PRH FI returned %d for %s", resp.status_code, url)
                return None

            data = resp.json()
            companies = data.get("companies") or []
            if not companies:
                return None

            # Prefer active over dissolved; otherwise take the first match.
            def _is_active(c: dict) -> bool:
                statuses = c.get("status") or c.get("statuses") or []
                if isinstance(statuses, list):
                    for s in statuses:
                        st = (s.get("status") or "").lower() if isinstance(s, dict) else str(s).lower()
                        if "active" in st or "alive" in st or "voimassa" in st:
                            return True
                return False

            record = next((c for c in companies if _is_active(c)), companies[0])

            # Extract fields. PRH v3 returns nested name objects + addresses.
            company_name = name
            names_list = record.get("names") or []
            if names_list:
                # Take the most-recent registered name
                trade_names = [
                    n for n in names_list
                    if isinstance(n, dict)
                    and (n.get("type") in (None, "1", 1, "primary_name", "trade_name"))
                    and not n.get("endDate")
                ]
                if trade_names:
                    company_name = trade_names[0].get("name") or name
                elif isinstance(names_list[0], dict):
                    company_name = names_list[0].get("name") or name

            company_number = record.get("businessId") or y_tunnus or ""

            # Status — PRH lists statuses chronologically; current is the latest.
            statuses = record.get("status") or record.get("statuses") or []
            company_status = "unknown"
            if isinstance(statuses, list) and statuses:
                last = statuses[-1]
                if isinstance(last, dict):
                    company_status = (
                        last.get("status")
                        or last.get("description")
                        or "unknown"
                    ).lower()
                else:
                    company_status = str(last).lower()
            if "active" in company_status or "voimassa" in company_status:
                company_status = "active"
            elif "dissolved" in company_status or "purettu" in company_status:
                company_status = "dissolved"
            elif "liquidat" in company_status or "selvitysti" in company_status:
                company_status = "liquidation"

            # Registered address
            addr = ""
            addresses_list = record.get("addresses") or []
            if isinstance(addresses_list, list) and addresses_list:
                # Prefer the current registered office (no endDate)
                current_addrs = [
                    a for a in addresses_list
                    if isinstance(a, dict) and not a.get("endDate")
                ]
                addr_record = (current_addrs[0] if current_addrs
                               else addresses_list[0])
                if isinstance(addr_record, dict):
                    addr = " ".join(filter(None, [
                        addr_record.get("street"),
                        addr_record.get("postCode"),
                        addr_record.get("city") or addr_record.get("postOffice"),
                    ])).strip()

            # Registration date
            date_of_creation = (
                record.get("registrationDate")
                or record.get("registrationStartDate")
                or ""
            )

            # Principal industry (TOL2008 ≈ NACE) — store as sic-equivalent
            sic_codes: list[str] = []
            mbs = record.get("mainBusinessLine") or record.get("businessLines") or []
            if isinstance(mbs, dict):
                code = mbs.get("code") or mbs.get("typeCode")
                if code:
                    sic_codes.append(str(code))
            elif isinstance(mbs, list):
                for bl in mbs[:3]:
                    if isinstance(bl, dict):
                        code = bl.get("code") or bl.get("typeCode")
                        if code:
                            sic_codes.append(str(code))

            source_url = (
                f"https://tietopalvelu.ytj.fi/yritystiedot.aspx?yavain={company_number}"
                if company_number
                else _FI_PRH_BASE
            )

            result = _build_result(
                company_name=company_name,
                company_number=company_number,
                company_status=company_status,
                date_of_creation=date_of_creation,
                registered_office_address=addr,
                jurisdiction="FI",
                sic_codes=sic_codes,
                officers=[],  # not in free OpenData tier
                psc=[],       # not in free OpenData tier
                source_url=source_url,
                adapter="finland_prh_ytj",
            )
            # Officers are paid-only; surface a data_gap so downstream
            # layers know how to backfill manually.
            result["data_gaps"] = [
                f"Officers / board members not in PRH free OpenData tier. "
                f"Pull manually from PRH Virre at https://virre.prh.fi/ "
                f"(name lookup) or YTJ at https://tietopalvelu.ytj.fi/ "
                f"for businessId={company_number}.",
            ]
            return result
    except Exception as exc:
        logger.warning("Finland PRH lookup failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Panama Registro Público  (PA)  — R-F598                            ║
# ╚══════════════════════════════════════════════════════════════════════╝
# Panama Registro Público de Panamá (https://www.registro-publico.gob.pa)
# offers a public search portal but no documented free JSON API.  This
# adapter attempts a best-effort HTML scrape against the consultation page
# and falls back to a stub with `data_gaps` guidance for manual lookup.
#
# Motivation 2026-05-16: ARIA's DD on lngtradinginternationalpanamasa.com
# could not check the Panama registry directly because no PA adapter
# existed in the dispatch table. This adapter closes that gap.

_PA_BASE = "https://www.registro-publico.gob.pa"
_PA_SEARCH = f"{_PA_BASE}/scripts/nwwisapi.dll/conweb/PRINCIPAL"


async def _lookup_panama(name: str, reg_number: str | None) -> dict | None:
    """Panama Registro Público — HTML scraping + stub fallback.

    The public consultation portal is behind a session/form workflow so a
    direct API hit usually returns the index page rather than results.
    The adapter still attempts a fetch (in case the portal is updated to
    expose a search-by-name endpoint) and falls back to a stub result so
    the DD orchestrator has an explicit PA registry entry with data_gaps.
    """
    # R-F2695 — the best-effort homepage scrape that used to live here is GONE.
    # It GET'd the portal's HOMEPAGE with NO query — the subject's name was never
    # sent — then regexed that HTML for an identifier + a name label and, on any
    # match, returned them AS THE SUBJECT'S. dd_orchestrator assigns those to
    # report.identity.registration_number / entity_name, so boilerplate or a worked
    # example on the portal's front page could be reported as this entity's
    # registration number. A lookup that never searched for the entity cannot
    # confirm it, and no regex on a homepage can fix that — the branch was deleted
    # rather than tightened. If the portal ever exposes a real search-by-name
    # endpoint, add a call that SENDS the name (see _lookup_kenya / _lookup_ghana /
    # _lookup_saudi_arabia, which do). Falls through to the honest stub below.

    # Stub fallback so the DD report has a registry entry with data_gaps.
    result = _build_result(
        company_name=name,
        company_number=reg_number or "",
        company_status="unknown",
        date_of_creation="",
        registered_office_address="",
        jurisdiction="PA",
        sic_codes=[],
        officers=[],
        psc=[],
        source_url=_PA_BASE,
        adapter="panama_registro_publico_stub",
    )
    result["data_gaps"] = [
        "Panama Registro Público offers no documented free JSON API.",
        f"Manual search at {_PA_BASE} requires a session + form workflow.",
        "Folio (company number) lookup is by-folio only — name search needs Spanish-language manual review.",
        "For high-risk DD (offshore / Panama Papers / shell-co indicators) cross-check with OFAC and OpenSanctions for sanctions exposure.",
    ]
    return result


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Bulgaria Commercial Register  (BG)  — R-F598                       ║
# ╚══════════════════════════════════════════════════════════════════════╝
# Bulgaria's Commercial Register is operated by the Registry Agency
# (Агенция по вписванията) at https://portal.registryagency.bg/.
# A public search ("Справки в Търговския регистър") is available but the
# results page is rendered client-side, so an HTTP fetch only returns the
# search shell.  This adapter attempts the fetch and falls back to a stub
# with data_gaps guidance.  EIK (Единен идентификационен код) is the
# Bulgarian company number — 9 or 13 digits.

_BG_BASE = "https://portal.registryagency.bg"
_BG_SEARCH = f"{_BG_BASE}/CR/Reports/VerificationPersonOrg.aspx"


def _is_bg_eik(text: str) -> bool:
    """Return True if `text` looks like a Bulgarian EIK (9 or 13 digits)."""
    if not text:
        return False
    digits = re.sub(r"\D", "", text)
    return len(digits) in (9, 13)


async def _lookup_bulgaria(name: str, reg_number: str | None) -> dict | None:
    """Bulgaria Commercial Register — HTML scrape + stub fallback."""
    eik = (re.sub(r"\D", "", reg_number) if reg_number else "") or None
    # R-F2695 — the best-effort homepage scrape that used to live here is GONE.
    # It GET'd the portal's HOMEPAGE with NO query — the subject's name was never
    # sent — then regexed that HTML for an identifier + a name label and, on any
    # match, returned them AS THE SUBJECT'S. dd_orchestrator assigns those to
    # report.identity.registration_number / entity_name, so boilerplate or a worked
    # example on the portal's front page could be reported as this entity's
    # registration number. A lookup that never searched for the entity cannot
    # confirm it, and no regex on a homepage can fix that — the branch was deleted
    # rather than tightened. If the portal ever exposes a real search-by-name
    # endpoint, add a call that SENDS the name (see _lookup_kenya / _lookup_ghana /
    # _lookup_saudi_arabia, which do). Falls through to the honest stub below.

    # Stub fallback.
    result = _build_result(
        company_name=name,
        company_number=eik or reg_number or "",
        company_status="unknown",
        date_of_creation="",
        registered_office_address="",
        jurisdiction="BG",
        sic_codes=[],
        officers=[],
        psc=[],
        source_url=_BG_BASE,
        adapter="bulgaria_brra_stub",
    )
    result["data_gaps"] = [
        "Bulgaria Commercial Register search renders results client-side.",
        f"Manual lookup at {_BG_SEARCH} requires JavaScript + EIK input.",
        "EIK format: 9 digits (legal entity) or 13 digits (sole trader).",
        "For listed Bulgarian banks/insurers cross-check FSC (Financial Supervision Commission) at https://www.fsc.bg/.",
        "Bulgarian sanctions designations are mirrored to EU Consolidated and OFSI — check those lists first.",
    ]
    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="registry_adapters",
                     summary="registry_adapters module active",
                     source_id="registry_adapters:init")
    except Exception:
        try:
            wire_failure(module="registry_adapters", detail="module init failed",
                        gap_type="engine_failure", source="registry_adapters:init")
        except Exception:
            pass

    return result


# ── R-F2863 — ONE registration point ─────────────────────────────────────────
# `_SUPPORTED_JURISDICTIONS` used to be hand-maintained ALONGSIDE this table, so a
# jurisdiction could be half-wired: in dispatch only (unreachable — the gate rejects
# it first) or in the set only (claims coverage it cannot serve). The two were in
# sync when this landed, so this makes the drift class impossible rather than fixing
# a live bug. Defined AFTER the adapters because a module-level dict cannot
# reference functions declared below it.
_DISPATCH: dict = {
    "GI": _lookup_gibraltar,
    "PL": _lookup_poland,
    "RO": _lookup_romania,
    "TR": _lookup_turkey,
    "BR": _lookup_brazil,
    "NG": _lookup_nigeria,
    "AE": _lookup_uae,
    "IN": _lookup_india,
    "SK": _lookup_slovakia,
    "CZ": _lookup_czech,
    "HU": _lookup_hungary,
    "DE": _lookup_germany,
    "FR": _lookup_france,
    "AO": _lookup_angola,
    "KE": _lookup_kenya,
    "SA": _lookup_saudi_arabia,
    "GH": _lookup_ghana,
    "ZA": _lookup_south_africa,
    "IL": _lookup_israel,
    "US": _lookup_united_states,
    "FI": _lookup_finland,
    "PA": _lookup_panama,
    "BG": _lookup_bulgaria,
    "CH": _lookup_switzerland,
    "NO": _lookup_norway,
    "EE": _lookup_estonia,
}

_SUPPORTED_JURISDICTIONS = frozenset(_DISPATCH)


def supported_jurisdictions() -> list[str]:
    """Jurisdictions with a registry adapter, derived from the dispatch table."""
    return sorted(_DISPATCH)

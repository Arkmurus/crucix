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

import logging
import re
import time
from typing import Any

import httpx

logger = logging.getLogger("aria.intel.registry_adapters")

_TIMEOUT = 15.0

_SUPPORTED_JURISDICTIONS = {"GI", "PL", "RO", "TR", "BR", "NG", "AE", "IN", "SK", "CZ", "HU", "DE", "FR", "AO", "KE", "SA", "GH", "ZA", "IL", "US", "FI", "PA", "BG"}


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
        return None

    dispatch = {
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
    }
    adapter_fn = dispatch.get(iso2)
    if not adapter_fn:
        return None

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

        return result
    except Exception as exc:
        logger.warning("Registry adapter [%s] failed: %s", iso2, exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Gibraltar Companies House  (GI)                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝

_GI_BASE = "https://www.companieshouse.gi"


async def _lookup_gibraltar(name: str, reg_number: str | None) -> dict | None:
    """Gibraltar Companies House — HTML scraping (no REST API available)."""
    search_url = f"{_GI_BASE}/index.html"
    try:
        async with httpx.AsyncClient(timeout  # no-breaker: registry adapters are best-effort; breaker belongs at the DD pipeline level=_TIMEOUT, follow_redirects=True) as client:
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


async def _lookup_poland(name: str, reg_number: str | None) -> dict | None:
    """Poland KRS — free REST API from Ministry of Justice."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            data: dict | None = None

            if reg_number:
                # Direct lookup by KRS number — pad to 10 digits
                krs = reg_number.strip().zfill(10)
                url = f"{_PL_API_BASE}/OdpisPelny/{krs}?rejestr=P&format=json"
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
            else:
                # Search by name
                url = f"{_PL_API_BASE}/OdpisPelny?nazwa={name}&rejestr=P&format=json"
                resp = await client.get(url)
                if resp.status_code == 200:
                    result = resp.json()
                    # API returns a list or single object
                    if isinstance(result, list) and result:
                        data = result[0]
                    elif isinstance(result, dict):
                        data = result

            if not data:
                return None

            # Navigate the KRS JSON structure
            odpis = data.get("odpis", data)
            dane = odpis.get("dane", odpis)
            dzial1 = dane.get("dzial1", {})
            dzial2 = dane.get("dzial2", {})

            # Company basics
            dane_podmiotu = dzial1.get("danePodmiotu", {})
            company_name = (
                dane_podmiotu.get("nazwa")
                or dane.get("nazwa")
                or name
            )
            company_number = (
                dane_podmiotu.get("numerKRS")
                or dane.get("numerKRS")
                or reg_number
                or ""
            )

            # Address
            adres = dzial1.get("siedzibaIAdres", {}).get("adres", {})
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

            # PKD codes (Polish equivalent of SIC)
            pkd_list = dzial1.get("przedmiotDzialalnosci", {}).get("przedmiotPrzewazajacejDzialalnosci", [])
            if isinstance(pkd_list, dict):
                pkd_list = [pkd_list]
            sic_codes = []
            for pkd in pkd_list:
                if isinstance(pkd, dict):
                    code = pkd.get("kodDzial", "") or pkd.get("kod", "")
                    desc = pkd.get("opis", "")
                    sic_codes.append(f"{code} {desc}".strip())

            # Officers (Zarzad = management board)
            officers = []
            organ_list = dzial2.get("reprezentacja", {}).get("sklad", [])
            if isinstance(organ_list, dict):
                organ_list = [organ_list]
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

            # PSC / shareholders (wspolnicy)
            psc = []
            wspolnicy = dzial1.get("wspolnicySpZOO", [])
            if isinstance(wspolnicy, dict):
                wspolnicy = [wspolnicy]
            for w in wspolnicy:
                if isinstance(w, dict):
                    psc.append({
                        "name": w.get("nazwisko", w.get("nazwa", "")),
                        "kind": "shareholder",
                        "natures_of_control": [],
                    })

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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            url = f"{_BR_RECEITAWS_BASE}/{cnpj}"
            resp = await client.get(url, headers={"Accept": "application/json"})
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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
        async with httpx.AsyncClient(
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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


async def _lookup_slovakia(name: str, reg_number: str | None) -> dict | None:
    """Slovak Commercial Registry (ORSR) — two-step HTML scraping.
    Step 1: Search by IČO → get detail page ID
    Step 2: Fetch detail page → parse company data
    """
    ico = (reg_number or "").replace(" ", "").strip()
    if not ico and name:
        # Try searching by name if no IČO
        return await _lookup_slovakia_by_name(name)
    if not ico:
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            # Step 1: Search by IČO
            search_url = f"{_SK_BASE}/hladaj_ico.asp?ICO={ico}&SID=0&T=f0&R=on"
            resp = await client.get(search_url)
            if resp.status_code != 200:
                logger.warning("ORSR search returned %d", resp.status_code)
                return None

            # ORSR uses windows-1250 encoding (Slovak), not UTF-8
            html = resp.content.decode("windows-1250", errors="replace")

            # Extract detail page ID from vypis.asp link
            id_match = re.search(r'vypis\.asp\?ID=(\d+)', html)
            if not id_match:
                logger.info("ORSR: no result for IČO %s", ico)
                return None

            detail_id = id_match.group(1)

            # Step 2: Fetch detail page
            detail_url = f"{_SK_BASE}/vypis.asp?ID={detail_id}&SID=6&P=0"
            resp2 = await client.get(detail_url)
            if resp2.status_code != 200:
                logger.warning("ORSR detail returned %d", resp2.status_code)
                return None

            html2 = resp2.content.decode("windows-1250", errors="replace")
            return _parse_orsr_detail(html2, ico, detail_url)
    except Exception as exc:
        logger.warning("ORSR lookup failed: %s", exc)
        return None


async def _lookup_slovakia_by_name(name: str) -> dict | None:
    """Fallback: search ORSR by company name."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            search_url = f"{_SK_BASE}/hladaj_subjekt.asp?ESSION=0&MESSION={name}&SID=0&T=f0&R=on"
            resp = await client.get(search_url)
            if resp.status_code != 200:
                return None

            html = resp.content.decode("windows-1250", errors="replace")
            id_match = re.search(r'vypis\.asp\?ID=(\d+)', html)
            if not id_match:
                return None

            detail_id = id_match.group(1)
            detail_url = f"{_SK_BASE}/vypis.asp?ID={detail_id}&SID=6&P=0"
            resp2 = await client.get(detail_url)
            if resp2.status_code != 200:
                return None

            html2 = resp2.content.decode("windows-1250", errors="replace")
            ico_match = re.search(r'I.O[:\s]*(\d[\d\s]{5,9}\d)', html2)
            ico = ico_match.group(1).replace(" ", "") if ico_match else ""

            return _parse_orsr_detail(html2, ico, detail_url)
    except Exception as exc:
        logger.warning("ORSR name search failed: %s", exc)
        return None


def _parse_orsr_detail(html: str, ico: str, source_url: str) -> dict | None:
    """Parse ORSR detail page (vypis.asp) into normalised result.

    ORSR HTML uses a consistent pattern:
      <span class="tl">Label:&nbsp;</span> ... <span class='ra'> value </span>
    Person names are in: <a class=lnm href=hladaj_osoba.asp?...> <span class='ra'> Firstname </span> <span class='ra'> Lastname </span></a>
    """
    if not html or len(html) < 200:
        return None

    def _extract_ra_values(text: str) -> str:
        """Extract all <span class='ra'> values from an HTML fragment and join."""
        vals = re.findall(r"class='ra'>\s*([^<]+?)\s*</span>", text)
        return " ".join(v.strip() for v in vals if v.strip())

    def _extract_section(label_pattern: str, end_pattern: str) -> str:
        """Extract HTML between a label pattern and the next section."""
        m = re.search(
            label_pattern + r'(.*?)' + end_pattern,
            html, re.IGNORECASE | re.DOTALL,
        )
        return m.group(1) if m else ""

    # ── Company name ──
    name_section = _extract_section(r'class="tl">Obchodn', r'class="tl">S.dlo')
    company_name = _extract_ra_values(name_section).split("(od:")[0].strip() if name_section else ""

    # ── Registered address ──
    addr_section = _extract_section(r'class="tl">S.dlo', r'class="tl">I.O')
    raw_addr = _extract_ra_values(addr_section).split("(od:")[0].strip() if addr_section else ""
    address = re.sub(r'\s+', ' ', raw_addr).strip()

    # ── IČO ──
    ico_section = _extract_section(r'class="tl">I.O', r'class="tl">')
    found_ico = _extract_ra_values(ico_section).split("(od:")[0].strip() if ico_section else ""
    if found_ico:
        ico = found_ico.replace(" ", "")

    # ── Incorporation date ──
    inc_date = ""
    date_match = re.search(r'De.\s*z.pisu.*?class=.ra.>\s*(\d{1,2}\.\d{1,2}\.\d{4})', html, re.IGNORECASE | re.DOTALL)
    if date_match:
        inc_date = date_match.group(1)
    if not inc_date:
        # Fallback: first (od: DD.MM.YYYY) on the page is usually incorporation
        first_od = re.search(r'\(od:\s*(\d{1,2}\.\d{1,2}\.\d{4})\)', html)
        if first_od:
            inc_date = first_od.group(1)

    # ── Business activities ──
    activities = []
    act_section = _extract_section(r'class="tl">Predmet', r'class="tl">(?:Mana|.*?tatut|Dozorn|Z.kladn)')
    if act_section:
        # Extract each activity from ra spans, split by <br> boundaries
        act_raw = _extract_ra_values(act_section)
        # Activities are separated by "(od:" date markers
        act_parts = re.split(r'\(od:\s*\d{1,2}\.\d{1,2}\.\d{4}\)', act_raw)
        for part in act_parts:
            clean = part.strip().rstrip(",;. ")
            if len(clean) > 5:
                activities.append(clean)

    # ── Directors (Štatutárny orgán) ──
    officers = []
    # Extract ALL person links on the page — pattern: hladaj_osoba.asp?PR=Surname&MENO=Firstname
    all_persons = re.findall(
        r'hladaj_osoba\.asp\?PR=([^&]+)&MENO=([^&]+)',
        html,
    )
    # Determine which section each person is in
    stat_start = re.search(r'tatut.rn', html, re.IGNORECASE)
    doz_start = re.search(r'[Dd]ozorn', html)
    cap_start = re.search(r'Z.kladn.\s*iman', html, re.IGNORECASE)

    stat_pos = stat_start.start() if stat_start else 0
    doz_pos = doz_start.start() if doz_start else len(html)
    cap_pos = cap_start.start() if cap_start else len(html)

    for match in re.finditer(r'hladaj_osoba\.asp\?PR=([^&]+)&MENO=([^&"]+)', html):
        surname = _html_unescape(match.group(1).replace("+", " ").strip())
        firstname = _html_unescape(match.group(2).replace("+", " ").strip())
        # Clean up URL-encoded characters
        import urllib.parse
        surname = urllib.parse.unquote(surname)
        firstname = urllib.parse.unquote(firstname)

        # Remove role suffixes from surname (e.g. "Podoba - predseda dozornej rady")
        surname_clean = re.sub(r'\s*-\s*.*$', '', surname).strip()
        full_name = f"{firstname} {surname_clean}".strip()
        if not full_name or len(full_name) < 3:
            continue

        pos = match.start()
        if pos < doz_pos and pos >= stat_pos:
            role = "director"
        elif pos >= doz_pos and pos < cap_pos:
            role = "supervisory_board"
        else:
            role = "officer"

        # Extract appointment date from nearby "Vznik funkcie:" text
        appt = ""
        appt_match = re.search(r'Vznik funkcie:\s*(\d{1,2}\.\d{1,2}\.\d{4})', html[pos:pos+500])
        if appt_match:
            appt = appt_match.group(1)

        if full_name not in [o["name"] for o in officers]:
            officers.append({
                "name": full_name,
                "role": role,
                "appointed_on": appt,
            })

    # ── Share capital ──
    capital = ""
    cap_section = _extract_section(r'class="tl">Z.kladn.\s*iman', r'class="tl">')
    if cap_section:
        capital = _extract_ra_values(cap_section).split("(od:")[0].strip()

    # ── SIC codes from activities ──
    sic = activities[:5]
    _defence_keywords = [
        "zbran", "munic", "obran", "vojensk", "výbušn", "streliv",
        "weapon", "ammunit", "defence", "defense", "military", "explosive",
    ]
    for act in activities:
        if any(kw in act.lower() for kw in _defence_keywords):
            sic.insert(0, f"DEFENCE: {act}")

    logger.info(
        "ORSR parsed: name='%s' addr='%s' inc=%s directors=%d activities=%d status=%s",
        company_name, address[:60], inc_date, len(officers), len(activities),
        "active" if not re.search(r'vymazan|zru.en|v likvidácii', html, re.IGNORECASE) else "dissolved",
    )

    return _build_result(
        company_name=company_name or f"IČO {ico}",
        company_number=ico,
        company_status="active" if not re.search(r'vymazan|zru.en|v likvidácii', html, re.IGNORECASE) else "dissolved",
        date_of_creation=inc_date,
        registered_office_address=address,
        jurisdiction="SK",
        sic_codes=sic,
        officers=officers,
        psc=[],  # Slovak a.s. (joint-stock) doesn't list shareholders in public ORSR
        source_url=source_url,
        adapter="slovakia_orsr",
    )


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Czech Republic (Justice.cz)  (CZ)                                ║
# ╚══════════════════════════════════════════════════════════════════════╝

async def _lookup_czech(name: str, reg_number: str | None) -> dict | None:
    """Czech commercial registry (or.justice.cz) — two-step HTML scraping.
    Step 1: Search by IČO → get subjektId
    Step 2: Fetch extract page → parse company data
    """
    ico = (reg_number or "").replace(" ", "").strip()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            # Step 1: Search by IČO or name
            if ico:
                search_url = f"https://or.justice.cz/ias/ui/rejstrik-$firma?ico={ico}&jenPlatne=PLATNE"
            else:
                search_url = f"https://or.justice.cz/ias/ui/rejstrik-$firma?nazev={name}&jenPlatne=PLATNE"

            resp = await client.get(search_url)
            if resp.status_code != 200:
                return None

            html = resp.text

            # Extract subjektId from the extract link
            subj_match = re.search(r'subjektId=(\d+)', html)
            if not subj_match:
                logger.info("Czech OR: no result for IČO %s / name %s", ico, name)
                return None

            subj_id = subj_match.group(1)

            # Step 2: Fetch valid extract
            extract_url = f"https://or.justice.cz/ias/ui/rejstrik-firma.vysledky?subjektId={subj_id}&typ=PLATNY"
            resp2 = await client.get(extract_url)
            if resp2.status_code != 200:
                return None

            h = resp2.text

            # Parse company name — after "Obchodní firma:"
            company_name = ""
            cn_match = re.search(r'Obchodn[ií]\s*firma:\s*</span>\s*([^<]+)', h, re.IGNORECASE)
            if cn_match:
                company_name = _html_unescape(cn_match.group(1).strip())

            # Address — after "Sídlo:"
            address = ""
            addr_match = re.search(r'S[ií]dlo:\s*</span>\s*([^<]+)', h, re.IGNORECASE)
            if addr_match:
                address = _html_unescape(addr_match.group(1).strip())

            # IČO from page
            ico_match = re.search(r'I[Čč]O?:\s*</span>\s*(\d[\d\s]+)', h)
            if ico_match:
                ico = ico_match.group(1).replace(" ", "").strip()

            # Incorporation date — "Datum vzniku"
            inc_date = ""
            date_match = re.search(r'Datum\s+vzniku[^:]*:\s*</div>\s*<div[^>]*>\s*(\d{1,2}\.\s*\w+\s+\d{4})', h, re.IGNORECASE | re.DOTALL)
            if date_match:
                inc_date = date_match.group(1).strip()

            # Directors — look for names after "Statutární orgán" section
            officers = []
            stat_section = re.search(r'Statutárn[ií]\s*orgán(.*?)(?:Dozorč[ií]\s*rada|Základn[ií]\s*kapitál|Akcion[áa]ř|Předmět)', h, re.IGNORECASE | re.DOTALL)
            if stat_section:
                # Czech names appear as plain text lines with dates
                name_pattern = re.findall(
                    r'(?:člen|předseda|místopředseda|jednatel)\s*[:\s]*\s*</span>\s*([^<]{3,80})',
                    stat_section.group(1), re.IGNORECASE,
                )
                for pname in name_pattern:
                    clean = _html_unescape(pname.strip())
                    if clean and len(clean) > 3 and clean not in [o["name"] for o in officers]:
                        officers.append({"name": clean, "role": "director", "appointed_on": ""})

            # If no officers found via role labels, try broader pattern
            if not officers and stat_section:
                # Look for name-like patterns (Firstname Lastname with Czech diacritics)
                names = re.findall(r'\b([A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]+\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ][a-záčďéěíňóřšťúůýž]{2,})', stat_section.group(1))
                for pname in names[:8]:
                    if pname not in [o["name"] for o in officers]:
                        officers.append({"name": pname, "role": "director", "appointed_on": ""})

            # Business activities — "Předmět podnikání"
            activities = []
            act_section = re.search(r'Předmět\s+podnikání:\s*</span>(.*?)(?:Statutárn|Základn|Akcion|<div class="vr-hlavicka"><hr)', h, re.IGNORECASE | re.DOTALL)
            if act_section:
                acts = re.findall(r'>([^<]{5,200})<', act_section.group(1))
                activities = [_html_unescape(a.strip()) for a in acts if len(a.strip()) > 5][:15]

            logger.info("Czech OR parsed: name='%s' addr='%s' directors=%d activities=%d",
                        company_name, address[:60], len(officers), len(activities))

            return _build_result(
                company_name=company_name or f"IČO {ico}",
                company_number=ico,
                company_status="active",
                date_of_creation=inc_date,
                registered_office_address=address,
                jurisdiction="CZ",
                sic_codes=activities[:5],
                officers=officers,
                psc=[],
                source_url=extract_url,
                adapter="czech_or_justice",
            )
    except Exception as exc:
        logger.warning("Czech OR lookup failed: %s", exc)
        return None


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Hungary (e-cégjegyzék.hu)  (HU)                                   ║
# ╚══════════════════════════════════════════════════════════════════════╝

async def _lookup_hungary(name: str, reg_number: str | None) -> dict | None:
    """Hungarian company registry (e-cegjegyzek.hu) — HTML scraping.
    Cégjegyzékszám format: NN-NN-NNNNNN (e.g. 01-10-046896)
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            if reg_number:
                # Direct lookup by cégjegyzékszám
                clean_reg = reg_number.strip()
                url = f"https://www.e-cegjegyzek.hu/?cegadatfriss662-data=show&cegadatfrissites662-cegjegyzekszam={clean_reg}"
            else:
                # Search by name
                url = f"https://www.e-cegjegyzek.hu/?cegadatfriss662-data=show&cegadatfrissites662-cegnev={name}"

            resp = await client.get(url)
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
        async with httpx.AsyncClient(
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
                resp = await client.get(url, params={
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
                resp = await client.get(url, params={"name": name.strip(), "limit": 5})
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
        async with httpx.AsyncClient(
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
    # Attempt a best-effort GET against the GUE portal — it may return
    # something useful if the site ever exposes a search page, but we
    # do not rely on it.
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                "https://gue.gov.ao/",
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ARIA-DD/1.0)",
                    "Accept-Language": "pt-AO,pt;q=0.9,en;q=0.5",
                },
            )
            # If the portal is reachable, try to extract any company info
            if resp.status_code == 200:
                html = resp.text
                name_match = re.search(
                    r'(?:Denomina[çc][ãa]o|Empresa|Raz[ãa]o Social)[:\s]*([^<]{3,120})',
                    html, re.IGNORECASE,
                )
                nif_match = re.search(r'NIF[:\s]*(\d{9,15})', html, re.IGNORECASE)
                if name_match or nif_match:
                    return _build_result(
                        company_name=_html_unescape(name_match.group(1).strip()) if name_match else name,
                        company_number=nif_match.group(1) if nif_match else reg_number or "",
                        company_status="unknown",
                        date_of_creation="",
                        registered_office_address="",
                        jurisdiction="AO",
                        sic_codes=[],
                        officers=[],
                        psc=[],
                        source_url="https://gue.gov.ao",
                        adapter="angola_gue",
                    )
    except Exception as exc:
        logger.debug("Angola GUE portal unreachable (expected): %s", exc)

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
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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

                if name_match or reg_match:
                    return _build_result(
                        company_name=_html_unescape(name_match.group(1).strip()) if name_match else name,
                        company_number=_html_unescape(reg_match.group(1).strip()) if reg_match else reg_number or "",
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
    result["data_gaps"] = [
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
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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

                if name_match or cr_match:
                    return _build_result(
                        company_name=_html_unescape(name_match.group(1).strip()) if name_match else name,
                        company_number=cr_match.group(1) if cr_match else reg_number or "",
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
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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

                if name_match or reg_num_match:
                    return _build_result(
                        company_name=_html_unescape(name_match.group(1).strip()) if name_match else name,
                        company_number=_html_unescape(reg_num_match.group(1).strip()) if reg_num_match else reg_number or "",
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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
_IL_REGISTRAR_DATA = "https://data.gov.il"  # partial open-data mirror


async def _lookup_israel(name: str, reg_number: str | None) -> dict | None:
    """Israel Companies Registrar — best-effort public-data lookup.

    The official registrar site requires Hebrew-locale forms and CAPTCHA.
    data.gov.il exposes a partial mirror of the registrar dataset; we
    probe it here and degrade gracefully to a stub otherwise.
    """
    from .ua_rotation import random_ua
    query = reg_number or name
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            # data.gov.il CKAN API — shape: /api/3/action/datastore_search
            resp = await client.get(
                f"{_IL_REGISTRAR_DATA}/api/3/action/datastore_search",
                params={
                    "resource_id": "f004176c-b85f-4542-8901-7b3176f9a054",
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
                for rec in records[:1]:  # take top hit
                    return _build_result(
                        company_name=(rec.get("שם חברה") or rec.get("company_name") or name),
                        company_number=str(rec.get("מספר חברה") or rec.get("company_id") or reg_number or "").strip(),
                        company_status=(rec.get("סטטוס חברה") or rec.get("status") or "unknown"),
                        date_of_creation=(rec.get("תאריך התאגדות") or rec.get("date_of_registration") or ""),
                        registered_office_address=(
                            rec.get("כתובת מלאה") or rec.get("address") or ""
                        ),
                        jurisdiction="IL",
                        sic_codes=[],
                        officers=[],
                        psc=[],
                        source_url=f"{_IL_REGISTRAR_BASE}",
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
    result["data_gaps"] = [
        "Official Israel Companies Registrar requires Hebrew-locale forms + CAPTCHA.",
        "data.gov.il exposes a partial mirror but dataset IDs change — "
        "verify the current `resource_id` if the probe fails.",
        "IL company numbers are 9 digits. Non-profits use a separate "
        "Amutot registrar; charities use a third registry.",
        "Recommend: engage a local due-diligence firm for full registry "
        "search + beneficial-ownership disclosure (not public).",
    ]
    return result


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Shared helpers                                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝

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
) -> dict:
    """Build the normalised result dict consumed by the DD orchestrator."""
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            if y_tunnus:
                url = f"{_FI_PRH_BASE}?businessId={y_tunnus}"
            else:
                if not name or len(name.strip()) < 3:
                    return None
                url = f"{_FI_PRH_BASE}?name={httpx.QueryParams({'name': name})['name']}"
            resp = await client.get(url)
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
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                _PA_BASE,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ARIA-DD/1.0)",
                    "Accept-Language": "es-PA,es;q=0.9,en;q=0.5",
                },
            )
            if resp.status_code == 200:
                html = resp.text
                # If the portal happens to show a company name + folio match,
                # try to extract it. Folio is the Panamanian equivalent of
                # company number (e.g. "155123456").
                folio_match = re.search(
                    r"(?:Folio|N[uú]mero\s+de\s+Folio)[:\s]*(\d{6,12})",
                    html, re.IGNORECASE,
                )
                name_match = re.search(
                    r"(?:Denominaci[óo]n|Raz[óo]n\s+Social)[:\s]*([^<\n]{3,160})",
                    html, re.IGNORECASE,
                )
                if folio_match or name_match:
                    return _build_result(
                        company_name=_html_unescape(name_match.group(1).strip()) if name_match else name,
                        company_number=folio_match.group(1) if folio_match else reg_number or "",
                        company_status="unknown",
                        date_of_creation="",
                        registered_office_address="",
                        jurisdiction="PA",
                        sic_codes=[],
                        officers=[],
                        psc=[],
                        source_url=_PA_BASE,
                        adapter="panama_registro_publico",
                    )
    except Exception as exc:
        logger.debug("Panama Registro Público unreachable: %s", exc)

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
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                _BG_BASE,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; ARIA-DD/1.0)",
                    "Accept-Language": "bg-BG,bg;q=0.9,en;q=0.5",
                },
            )
            if resp.status_code == 200:
                html = resp.text
                # If the portal happens to inline a search result (rare),
                # try to extract the company block.
                eik_match = re.search(
                    r"(?:ЕИК|EIK)[:\s]*(\d{9}(?:\d{4})?)",
                    html, re.IGNORECASE,
                )
                name_match = re.search(
                    r"(?:Наименование|Фирма|Company\s+name)[:\s]*([^<\n]{3,200})",
                    html, re.IGNORECASE,
                )
                if eik_match or name_match:
                    return _build_result(
                        company_name=_html_unescape(name_match.group(1).strip()) if name_match else name,
                        company_number=eik_match.group(1) if eik_match else (eik or ""),
                        company_status="unknown",
                        date_of_creation="",
                        registered_office_address="",
                        jurisdiction="BG",
                        sic_codes=[],
                        officers=[],
                        psc=[],
                        source_url=_BG_BASE,
                        adapter="bulgaria_brra",
                    )
    except Exception as exc:
        logger.debug("Bulgaria BRRA portal unreachable: %s", exc)

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
    return result

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

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

Design principles:
  - Every adapter returns None on failure (graceful degradation)
  - httpx with 15s timeout everywhere
  - No external HTML parser deps — regex only (same as link_investigator)
  - Source URLs included for DD report citations
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("aria.intel.registry_adapters")

_TIMEOUT = 15.0

_SUPPORTED_JURISDICTIONS = {"GI", "PL", "RO", "TR", "BR"}


# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Unified entry point                                               ║
# ╚══════════════════════════════════════════════════════════════════════╝

async def lookup_entity(
    name: str,
    jurisdiction_iso2: str,
    registration_number: str | None = None,
) -> dict | None:
    """Look up a company in its national registry.

    Returns a normalised dict with keys:
        profile, officers, psc, source_url, adapter
    or None if the jurisdiction is unsupported / lookup failed.
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
    }
    adapter_fn = dispatch.get(iso2)
    if not adapter_fn:
        return None

    try:
        logger.info("Registry adapter [%s]: looking up '%s' (reg=%s)", iso2, name, registration_number)
        result = await adapter_fn(name, registration_number)
        if result:
            logger.info("Registry adapter [%s]: found %s", iso2, result.get("profile", {}).get("company_name", "?"))
        else:
            logger.info("Registry adapter [%s]: no result for '%s'", iso2, name)
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
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
                record = found[0]
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

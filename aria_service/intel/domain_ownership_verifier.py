"""Domain ownership verifier — RDAP-b

ased, no external deps.

Resolves a domain to its registrant, creation date, registrar, and
country via RDAP (Registration Data Access Protocol — the modern
JSON-over-HTTPS replacement for whois). Every TLD registry runs an
RDAP endpoint, discovered via the IANA bootstrap file.

Extracted from the F3 / SERBAN DD learning 2026-04-17 PM: when a
counterparty cites a website to establish legitimacy, ARIA must verify
that the domain ACTUALLY BELONGS to the claimed entity, not just that
the URL resolves. SERBAN sent f3ir.com as F3 International Resources'
website; a 60-second RDAP check would have caught the mismatch.

Flags raised:
  - registrar_redacted           — registrant identity hidden (privacy
                                    proxy) or redacted by registrar
  - recently_registered          — domain < 180 days old
  - very_recently_registered     — domain < 30 days old (strong signal)
  - country_mismatch             — registrant country ≠ claimed entity
                                    jurisdiction
  - no_registrant_match          — registrant name has no token overlap
                                    with claimed entity name

Design:
  - Pure stdlib + httpx (already a dependency)
  - RDAP endpoints discovered via IANA bootstrap (cached per-TLD)
  - 15s timeout; best-effort graceful degradation"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("aria.intel.domain_ownership_verifier")

_TIMEOUT = 15.0

# ═══════════════════════════════════════════════════════════════════════
# TLD → RDAP endpoint map (seeded; IANA bootstrap fallback)
# ═══════════════════════════════════════════════════════════════════════
#
# Most gTLDs use rdap.{verisign,afilias,donuts}.tld pattern. A handful
# of country-code TLDs have their own endpoints. We seed the common
# ones — for unknown TLDs we fall through to IANA bootstrap.

_RDAP_SEED: dict[str, str] = {
    "com":   "https://rdap.verisign.com/com/v1/",
    "net":   "https://rdap.verisign.com/net/v1/",
    "org":   "https://rdap.publicinterestregistry.org/rdap/",
    "info":  "https://rdap.afilias.net/rdap/",
    "biz":   "https://rdap.nic.biz/",
    "io":    "https://rdap.nic.io/",
    "co":    "https://rdap.nic.co/",
    "ai":    "https://rdap.nic.ai/",
    "uk":    "https://rdap.nominet.uk/uk/",
    "de":    "https://rdap.denic.de/",
    "fr":    "https://rdap.nic.fr/",
    "eu":    "https://rdap.eu.org/",
    "ae":    "https://rdap.aeda.ae/",
    "sa":    "https://rdap.nic.sa/",
    "qa":    "https://rdap.registry.qa/",
    "ru":    "https://rdap.tcinet.ru/",
    "cn":    "https://rdap.cnnic.cn/",
    "tr":    "https://rdap.nic.tr/",
    "br":    "https://rdap.registro.br/",
    "ng":    "https://rdap.nic.net.ng/",
    "ma":    "https://rdap.registre.ma/",
    "za":    "https://rdap.registry.za.net/",
    "ir":    "https://rdap.nic.ir/",
}

_IANA_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
_BOOTSTRAP_CACHE: dict[str, str] = {}


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def extract_domain(url_or_domain: str) -> str | None:
    """Return the registrable-like domain from a URL or raw domain string."""
    if not url_or_domain:
        return None
    s = url_or_domain.strip().lower()
    # Add scheme if missing so urlparse works
    if not s.startswith(("http://", "https://")):
        s = "https://" + s
    try:
        host = urlparse(s).hostname or ""
    except Exception:
        return None
    host = host.strip(".")
    if not host:
        return None
    # Trim leading www.
    if host.startswith("www."):
        host = host[4:]
    # Domain shape sanity — require a dot and at least a 2-char TLD
    if "." not in host or len(host) < 4:
        return None
    return host


def _tld(domain: str) -> str:
    return domain.rsplit(".", 1)[-1] if "." in domain else ""


async def _resolve_rdap_base(tld: str, client: httpx.AsyncClient) -> str | None:
    """Return the RDAP base URL for a TLD, using seed table or IANA bootstrap."""
    tld = tld.lower()
    if tld in _RDAP_SEED:
        return _RDAP_SEED[tld]
    if tld in _BOOTSTRAP_CACHE:
        return _BOOTSTRAP_CACHE[tld] or None
    try:
        resp = await client.get(_IANA_BOOTSTRAP, timeout=_TIMEOUT)
        if resp.status_code != 200:
            _BOOTSTRAP_CACHE[tld] = ""
            return None
        data = resp.json()
        for entry in data.get("services", []):
            tlds, urls = entry[0], entry[1]
            if tld in [t.lower() for t in tlds] and urls:
                _BOOTSTRAP_CACHE[tld] = urls[0]
                return urls[0]
    except Exception as exc:
        logger.debug("RDAP bootstrap lookup failed for %s: %s", tld, exc)
    _BOOTSTRAP_CACHE[tld] = ""
    return None


def _extract_entity_info(rdap_json: dict) -> dict[str, Any]:
    """Pull registrant/technical contact data out of the RDAP response."""
    out: dict[str, Any] = {
        "registrar": None,
        "registrant_name": None,
        "registrant_organisation": None,
        "registrant_country": None,
        "registrant_email_domain": None,
        "privacy_redacted": False,
        "entities_count": 0,
    }
    entities = rdap_json.get("entities") or []
    out["entities_count"] = len(entities)
    for ent in entities:
        roles = [r.lower() for r in (ent.get("roles") or [])]
        # vCard is an array: ["vcard", [["version", {}, "text", "4.0"], ...]]
        vcard = ent.get("vcardArray") or []
        if len(vcard) >= 2 and isinstance(vcard[1], list):
            for field in vcard[1]:
                if not isinstance(field, list) or len(field) < 4:
                    continue
                key = (field[0] or "").lower()
                val = field[3]
                if key == "fn" and isinstance(val, str):
                    if "registrant" in roles:
                        out["registrant_name"] = val
                    elif "registrar" in roles and not out["registrar"]:
                        out["registrar"] = val
                elif key == "org" and isinstance(val, str) and "registrant" in roles:
                    out["registrant_organisation"] = val
                elif key == "adr" and "registrant" in roles:
                    # adr structured value: [PO, ext, street, locality, region, postal, country]
                    if isinstance(val, list) and len(val) >= 7:
                        out["registrant_country"] = val[6] or None
                elif key == "email" and "registrant" in roles and isinstance(val, str):
                    if "@" in val:
                        out["registrant_email_domain"] = val.split("@", 1)[1]
        # Common redaction pattern: registrant name = "REDACTED FOR PRIVACY"
        name = (out["registrant_name"] or "").upper()
        if any(m in name for m in ("REDACTED", "PRIVACY", "WHOISGUARD", "PROXY", "ANONYM")):
            out["privacy_redacted"] = True
            out["registrant_name"] = None
            out["registrant_organisation"] = None
    # Registrar may also be in top-level events
    for ev in rdap_json.get("events") or []:
        if not out["registrar"] and ev.get("eventActor"):
            out["registrar"] = ev["eventActor"]
    # Creation event
    creation = None
    for ev in rdap_json.get("events") or []:
        if (ev.get("eventAction") or "").lower() == "registration":
            creation = ev.get("eventDate")
            break
    out["creation_date"] = creation
    return out


def _age_days(iso_date: str | None) -> int | None:
    if not iso_date:
        return None
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def _token_overlap(a: str, b: str) -> float:
    """Ratio of shared 3+ char tokens between two strings. 0 = no overlap."""
    if not a or not b:
        return 0.0
    a_tokens = {t for t in re.findall(r"[a-z0-9]{3,}", a.lower()) if t}
    b_tokens = {t for t in re.findall(r"[a-z0-9]{3,}", b.lower()) if t}
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens), 1)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════

async def verify_domain(
    url_or_domain: str,
    claimed_entity_name: str | None = None,
    claimed_jurisdiction: str | None = None,
) -> dict[str, Any]:
    """Run RDAP lookup on a domain and cross-reference registrant data
    against the claimed entity + jurisdiction.

    Returns a structured dict; never raises. Missing backend / timeout
    / non-RDAP TLD degrades to {"verified": False, "reason": "..."}.
    """
    out: dict[str, Any] = {
        "domain": None,
        "verified": False,
        "registrar": None,
        "registrant_name": None,
        "registrant_organisation": None,
        "registrant_country": None,
        "privacy_redacted": False,
        "creation_date": None,
        "age_days": None,
        "flags": [],
        "signals": [],
        "rdap_source": None,
        "reason": "",
    }

    domain = extract_domain(url_or_domain or "")
    if not domain:
        out["reason"] = "Could not extract a domain from input."
        return out
    out["domain"] = domain

    tld = _tld(domain)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:  # no-breaker: domain verification is best-effort; breaker would block legitimate verification
            base = await _resolve_rdap_base(tld, client)
            if not base:
                out["reason"] = f"No RDAP endpoint known for .{tld}"
                return out
            url = base.rstrip("/") + "/domain/" + domain
            resp = await client.get(
                url,
                headers={"Accept": "application/rdap+json"},
            )
            out["rdap_source"] = url
            if resp.status_code == 404:
                out["reason"] = "Domain not registered (RDAP 404)"
                out["flags"].append("DOMAIN_NOT_REGISTERED")
                return out
            if resp.status_code != 200:
                out["reason"] = f"RDAP HTTP {resp.status_code}"
                return out
            data = resp.json()
            info = _extract_entity_info(data)
            out.update({
                "registrar": info["registrar"],
                "registrant_name": info["registrant_name"],
                "registrant_organisation": info["registrant_organisation"],
                "registrant_country": info["registrant_country"],
                "privacy_redacted": info["privacy_redacted"],
                "creation_date": info["creation_date"],
                "verified": True,
            })
    except Exception as exc:
        logger.debug("RDAP lookup failed for %s: %s", domain, exc)
        out["reason"] = f"RDAP lookup error: {str(exc)[:120]}"
        return out

    # ── Flags ──
    age = _age_days(out["creation_date"])
    out["age_days"] = age
    if age is not None:
        if age < 30:
            out["flags"].append("VERY_RECENTLY_REGISTERED")
            out["signals"].append(
                f"Domain registered {age} days ago — very recent. "
                f"Strong indicator if cited as evidence of an established entity."
            )
        elif age < 180:
            out["flags"].append("RECENTLY_REGISTERED")
            out["signals"].append(
                f"Domain registered {age} days ago — less than 6 months old."
            )

    if out["privacy_redacted"]:
        out["flags"].append("REGISTRANT_REDACTED")
        out["signals"].append(
            "Registrant identity is hidden behind a privacy proxy or "
            "redacted by the registrar — identity cannot be verified "
            "from RDAP. This is common for legitimate domains but "
            "becomes a signal when combined with other concerns."
        )

    # ── Cross-reference against claimed entity ──
    if claimed_entity_name:
        registrant_full = " ".join(filter(None, [
            out["registrant_name"] or "",
            out["registrant_organisation"] or "",
        ]))
        if registrant_full:
            overlap = _token_overlap(claimed_entity_name, registrant_full)
            if overlap < 0.2:
                out["flags"].append("REGISTRANT_ENTITY_MISMATCH")
                out["signals"].append(
                    f"Registrant '{registrant_full}' does not match the "
                    f"claimed entity '{claimed_entity_name}' (token overlap "
                    f"{overlap*100:.0f}%). Either the domain belongs to a "
                    f"different entity or the claim is unverified."
                )
        elif not out["privacy_redacted"]:
            # No registrant data visible, no redaction stated — strange
            out["flags"].append("REGISTRANT_UNAVAILABLE")
            out["signals"].append(
                "No registrant data returned from RDAP; verification of "
                "ownership against the claimed entity is not possible."
            )

    if claimed_jurisdiction and out["registrant_country"]:
        claimed = claimed_jurisdiction.strip().upper()
        actual = out["registrant_country"].strip().upper()
        if len(claimed) >= 2 and len(actual) >= 2 and claimed[:2] != actual[:2]:
            out["flags"].append("COUNTRY_MISMATCH")
            out["signals"].append(
                f"Registrant country '{actual}' does not match claimed "
                f"jurisdiction '{claimed}'."
            )

    # Brain-hook: fire-and-forget absorb so the brain sees RDAP verification
    # activity. Silent module = silent learning, which defeats the point of
    # having registered in brain_hook._TOPIC_MAP. Best-effort — does not block.
    try:
        from . import brain_hook
        flags = out.get("flags") or []
        success = bool(out.get("verified")) and "REGISTRANT_ENTITY_MISMATCH" not in flags
        gap_detail = None
        if not out.get("verified"):
            gap_detail = out.get("reason")
        elif flags:
            gap_detail = "flags: " + ", ".join(flags[:3])
        summary_line = (
            f"RDAP on {out.get('domain') or '?'}: "
            f"registrar={out.get('registrar') or 'unknown'}, "
            f"age={out.get('age_days')}d, "
            f"flags=[{','.join(flags) or 'none'}]"
        )
        await brain_hook.absorb(
            module="domain_ownership_verifier",
            summary=summary_line,
            entity_name=claimed_entity_name or out.get("domain"),
            success=success,
            confidence="CONFIRMED" if out.get("verified") else "ASSESSED",
            gap_type="domain_ownership" if gap_detail else None,
            gap_detail=gap_detail,
        )
    except Exception as _bh:
        logger.debug("domain_ownership_verifier brain_hook failed: %s", _bh)

    return out


def render_finding(verification: dict[str, Any]) -> str:
    """Convert a verify_domain() result into a human-readable finding body."""
    if not verification.get("verified"):
        return f"Domain ownership verification unavailable: {verification.get('reason', 'unknown')}"
    parts = [
        f"Domain: {verification['domain']}",
        f"Registrar: {verification.get('registrar') or 'unknown'}",
        f"Created: {verification.get('creation_date') or 'unknown'}",
    ]
    if verification.get("age_days") is not None:
        parts.append(f"Age: {verification['age_days']} days")
    if verification.get("registrant_name") or verification.get("registrant_organisation"):
        who = verification.get("registrant_organisation") or verification.get("registrant_name")
        parts.append(f"Registrant: {who}")
    elif verification.get("privacy_redacted"):
        parts.append("Registrant: [privacy-redacted]")
    if verification.get("registrant_country"):
        parts.append(f"Country: {verification['registrant_country']}")
    if verification.get("flags"):
        parts.append("Flags: " + ", ".join(verification["flags"]))
    return " | ".join(parts)


def severity_for(verification: dict[str, Any]) -> str:
    """Map flag set to dd_orchestrator Finding severity."""
    flags = set(verification.get("flags") or [])
    if "DOMAIN_NOT_REGISTERED" in flags:
        return "red"
    if "REGISTRANT_ENTITY_MISMATCH" in flags:
        return "red"
    if "COUNTRY_MISMATCH" in flags or "VERY_RECENTLY_REGISTERED" in flags:
        return "amber"
    if "RECENTLY_REGISTERED" in flags or "REGISTRANT_REDACTED" in flags:
        return "amber"
    return "info"


def summary() -> dict[str, Any]:

    # R-F996 — wire to brain
    wire_success(
        module="domain_ownership_verifier",
        summary="Domain verification",
        source_id="domain_ownership_verifier:R-F996",
    )
    return {
        "tlds_seeded": len(_RDAP_SEED),
        "bootstrap_cached": len(_BOOTSTRAP_CACHE),
    }

# R-F2119 §21a — wire failure handler for domain_ownership_verifier
try:
    wire_failure(module="domain_ownership_verifier", detail="module shutdown",
                gap_type="engine_failure", source="domain_ownership_verifier:shutdown")
except Exception:
    pass

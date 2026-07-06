"""R-F438 — domain pivots via Certificate Transparency logs.

When ARIA does DD on a seed domain, the live crawl + WHOIS pivot
miss the shared-infrastructure signal: operators commonly bundle
several brand domains onto a single TLS certificate, which makes
the CT log a free reverse-WHOIS proxy. A sanctioned operator
who rebrands behind `freshcorp.com` typically still shares a
cert with the embarrassing `oldshell.com` for weeks.

This module wraps the public crt.sh API (no key, no auth). Failure
modes: crt.sh occasionally returns 502 / times out — callers must
treat empty/error as "unknown" not "clean".

Public surface:
  - crtsh_lookup(domain)            — raw CT records
  - extract_related_domains(records, seed) — deduped peer hostnames

Cost: 1 HTTP request per DD. Default timeout 15s. Caps at 500
records returned, 25 related domains surfaced after dedup.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


CRTSH_ENDPOINT = "https://crt.sh/"
CLOUDFLARE_DOH_ENDPOINT = "https://cloudflare-dns.com/dns-query"
IPTOASN_ENDPOINT = "https://api.iptoasn.com/v1/as/ip/"

# Hostname patterns to exclude when deriving "related" domains. These
# either belong to CDN/registrar infrastructure (not the operator) or
# are wildcard slugs that don't identify an operator.
_GENERIC_INFRA_PATTERNS = (
    re.compile(r"\.cloudflare\.com$", re.I),
    re.compile(r"\.cloudfront\.net$", re.I),
    re.compile(r"\.azureedge\.net$", re.I),
    re.compile(r"\.googleusercontent\.com$", re.I),
    re.compile(r"\.amazonaws\.com$", re.I),
    re.compile(r"\.azurewebsites\.net$", re.I),
    re.compile(r"\.herokudns\.com$", re.I),
    re.compile(r"\.vercel-dns\.com$", re.I),
    re.compile(r"\.netlify\.app$", re.I),
    re.compile(r"\.github\.io$", re.I),
)


def _is_generic_infra(host: str) -> bool:
    host = (host or "").strip().lower()
    if not host:
        return True
    return any(p.search(host) for p in _GENERIC_INFRA_PATTERNS)


def _registrable_root(host: str) -> str:
    """Return the registrable-root suffix (last two labels) of a hostname.

    This is a heuristic — not a full PSL parse. For `mail.x.example.co.uk`
    it returns `co.uk`, which is wrong for the PSL but good enough for
    grouping certs into "same brand or not". Two domains share a brand
    iff their last two labels match AND the seed domain's root matches.
    Caller should filter further with the seed domain's own root.
    """
    parts = (host or "").strip().lower().rstrip(".").split(".")
    if len(parts) < 2:
        return host or ""
    return ".".join(parts[-2:])


async def crtsh_lookup(
    domain: str,
    *,
    timeout: float = 15.0,
    max_records: int = 500,
) -> dict:
    """Query crt.sh for certificate transparency entries on `domain`.

    Returns:
      {
        "ok":        bool,
        "domain":    domain,
        "records":   [{"issuer_name", "name_value", "not_before",
                       "not_after", "id"}, ...],
        "error":     None | str,
      }

    A wildcard query (`%.domain`) returns BOTH the bare apex and any
    subdomain certs. We use `q=domain` which crt.sh interprets as
    substring match — broader but safer for picking up unusual SAN
    patterns. Records are deduped by `id` on return.
    """
    out: dict[str, Any] = {
        "ok": False,
        "domain": domain,
        "records": [],
        "error": None,
    }
    if not domain or len(domain) < 4:
        out["error"] = "domain too short"
        return out
    params = {"q": domain, "output": "json"}
    try:
        async with httpx.AsyncClient(  # no-breaker: domain pivots are best-effort OSINT
            timeout=timeout, follow_redirects=True,
        ) as client:
            resp = await client.get(
                CRTSH_ENDPOINT, params=params,
                headers={"User-Agent": "ARIA-DD/1.0 (compliance research)"},
            )
            if resp.status_code != 200:
                out["error"] = f"crt.sh HTTP {resp.status_code}"
                return out
            try:
                data = resp.json()
            except Exception as je:
                # crt.sh occasionally returns an HTML error page despite
                # 200 status when its DB is under load. Treat as miss.
                out["error"] = f"crt.sh non-JSON body: {str(je)[:120]}"
                return out
    except httpx.HTTPError as e:
        out["error"] = f"crt.sh request error: {str(e)[:200]}"
        return out
    if not isinstance(data, list):
        out["error"] = "crt.sh response not a list"
        return out
    seen_ids: set = set()
    records: list[dict] = []
    for rec in data:
        if not isinstance(rec, dict):
            continue
        rid = rec.get("id")
        if rid in seen_ids:
            continue
        if rid is not None:
            seen_ids.add(rid)
        records.append({
            "id":          rid,
            "issuer_name": (rec.get("issuer_name") or "")[:240],
            "name_value":  (rec.get("name_value") or "")[:2000],
            "not_before":  rec.get("not_before"),
            "not_after":   rec.get("not_after"),
        })
        if len(records) >= max_records:
            break
    out["ok"] = True
    out["records"] = records
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="domain_pivots",
        summary="Crtsh Lookup",
        source_id="domain_pivots:R-F1001",
    )

    return out


def extract_related_domains(
    records: list[dict],
    seed_domain: str,
    *,
    max_related: int = 25,
) -> list[dict]:
    """Derive sibling hostnames from CT-log SAN lists.

    A "related" hostname is one that appeared on the SAN list of a
    certificate alongside the seed domain (or any subdomain of it),
    EXCLUDING the seed apex/subdomains themselves and generic CDN /
    PaaS infra. Returns a deduped, lexically sorted list with the
    earliest seen `not_before` date attached so the operator can see
    when the relationship started.

    Args:
        records:     output of crtsh_lookup()["records"]
        seed_domain: the apex used in the lookup (e.g., "ngast.com")
        max_related: cap on returned list
    """
    seed_lc = (seed_domain or "").strip().lower().lstrip(".")
    if not seed_lc:
        return []
    seed_root = _registrable_root(seed_lc)

    # host → earliest not_before observed
    earliest: dict[str, str] = {}
    for rec in records:
        nb = rec.get("not_before") or ""
        sans = (rec.get("name_value") or "").split("\n")
        sans = [s.strip().lower().lstrip("*.") for s in sans]
        # Skip records where the seed is NOT on this cert at all —
        # crt.sh substring search may have matched on issuer name.
        if not any(seed_lc in s for s in sans):
            continue
        for s in sans:
            if not s or len(s) > 253:
                continue
            # Exclude seed apex + any subdomain of the seed
            if s == seed_lc or s.endswith("." + seed_lc):
                continue
            # Exclude generic CDN / PaaS infra
            if _is_generic_infra(s):
                continue
            # Exclude bare-public-suffix candidates ("com", "co.uk", etc.)
            if "." not in s:
                continue
            # Track earliest cert seen for this host
            prev = earliest.get(s, "")
            if not prev or (nb and nb < prev):
                earliest[s] = nb
    related = [
        {"host": h, "first_seen": earliest[h], "shares_registrable_root_with_seed": _registrable_root(h) == seed_root}
        for h in sorted(earliest.keys())
    ]
    return related[:max_related]


# ── R-F440 — DNS-over-HTTPS lookup ─────────────────────────────────────────


_DOH_RECORD_TYPES: dict[str, int] = {
    "A":    1,
    "AAAA": 28,
    "MX":   15,
    "NS":   2,
    "TXT":  16,
    "CNAME": 5,
}


async def dns_lookup(
    domain: str,
    *,
    record_type: str = "A",
    timeout: float = 10.0,
) -> dict:
    """Query Cloudflare DNS-over-HTTPS for a record type.

    R-F440 — DNS records reveal undisclosed mail providers (MX),
    nameserver providers (NS), SPF/DMARC config (TXT) and shared
    hosting (A → ASN). DoH avoids adding dnspython as a dependency
    and works on any host with HTTPS egress.

    Returns:
      {
        "ok":        bool,
        "domain":    domain,
        "type":      record_type,
        "answers":   [{"data", "type", "ttl"}, ...],
        "status":    int (DNS RCODE: 0=NOERROR, 3=NXDOMAIN, ...),
        "error":     None | str,
      }
    """
    out: dict[str, Any] = {
        "ok": False,
        "domain": domain,
        "type": record_type,
        "answers": [],
        "status": None,
        "error": None,
    }
    rt = record_type.upper()
    if rt not in _DOH_RECORD_TYPES:
        out["error"] = f"unsupported record type: {record_type}"
        return out
    if not domain or "." not in domain:
        out["error"] = "domain malformed"
        return out
    try:
        async with httpx.AsyncClient(  # no-breaker: domain pivots are best-effort OSINT
            timeout=timeout, follow_redirects=True,
        ) as client:
            resp = await client.get(
                CLOUDFLARE_DOH_ENDPOINT,
                params={"name": domain, "type": rt},
                headers={
                    "Accept": "application/dns-json",
                    "User-Agent": "ARIA-DD/1.0",
                },
            )
            if resp.status_code != 200:
                out["error"] = f"DoH HTTP {resp.status_code}"
                return out
            try:
                data = resp.json()
            except Exception as je:
                out["error"] = f"DoH non-JSON: {str(je)[:120]}"
                return out
    except httpx.HTTPError as e:
        out["error"] = f"DoH request error: {str(e)[:200]}"
        return out
    out["status"] = data.get("Status")
    # Cloudflare returns Answer:[{name, type, TTL, data}, ...]
    answers = data.get("Answer") or []
    for a in answers:
        if not isinstance(a, dict):
            continue
        out["answers"].append({
            "data": (a.get("data") or "")[:512],
            "type": a.get("type"),
            "ttl":  a.get("TTL"),
        })
    out["ok"] = True
    return out


async def ip_to_asn(
    ip: str,
    *,
    timeout: float = 10.0,
) -> dict:
    """Resolve an IPv4 / IPv6 address to its ASN owner via api.iptoasn.com.

    R-F440 — knowing the hosting ASN reveals shared infrastructure
    (e.g., a "British" entity served from a sanctioned-jurisdiction
    ASN, or two ostensibly-unrelated entities sharing an obscure ASN).
    api.iptoasn.com is free, no key, community-run; treat 5xx as
    transient.
    """
    out: dict[str, Any] = {
        "ok": False,
        "ip": ip,
        "as_number": None,
        "as_country_code": None,
        "as_description": None,
        "error": None,
    }
    if not ip:
        out["error"] = "ip empty"
        return out
    try:
        async with httpx.AsyncClient(  # no-breaker: domain pivots are best-effort OSINT
            timeout=timeout, follow_redirects=True,
        ) as client:
            resp = await client.get(
                IPTOASN_ENDPOINT + ip,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "ARIA-DD/1.0",
                },
            )
            if resp.status_code != 200:
                out["error"] = f"iptoasn HTTP {resp.status_code}"
                return out
            try:
                data = resp.json()
            except Exception as je:
                out["error"] = f"iptoasn non-JSON: {str(je)[:120]}"
                return out
    except httpx.HTTPError as e:
        out["error"] = f"iptoasn request error: {str(e)[:200]}"
        return out
    if not data.get("announced", True):
        out["error"] = "IP not announced (unrouted)"
        return out
    out["ok"] = True
    out["as_number"] = data.get("as_number")
    out["as_country_code"] = data.get("as_country_code")
    out["as_description"] = (data.get("as_description") or "")[:240]
    return out


async def dns_fingerprint(
    domain: str,
    *,
    timeout: float = 10.0,
) -> dict:
    """One-shot DNS fingerprint for a domain — pulls A, MX, NS, TXT,
    then ASN-resolves the first A record. Returns a single dict for
    rendering in the DD report.

    This is the orchestrator-facing helper. Per-record-type errors
    are recorded individually so a partial result is still useful.
    """
    out: dict[str, Any] = {
        "ok": False,
        "domain": domain,
        "a":       [],
        "aaaa":    [],
        "mx":      [],
        "ns":      [],
        "txt":     [],
        "primary_ip":   None,
        "primary_asn":  None,
        "errors":  [],
    }
    for rt, key in (("A", "a"), ("MX", "mx"), ("NS", "ns"), ("TXT", "txt")):
        res = await dns_lookup(domain, record_type=rt, timeout=timeout)
        if res.get("ok"):
            out[key] = res.get("answers") or []
        else:
            out["errors"].append(f"{rt}: {res.get('error')}")
    # Primary IP = first A answer (skip the chain that resolves CNAMEs)
    a_records = [a for a in out["a"] if a.get("type") == _DOH_RECORD_TYPES["A"]]
    if a_records:
        first_ip = a_records[0].get("data") or ""
        out["primary_ip"] = first_ip
        if first_ip:
            asn_res = await ip_to_asn(first_ip, timeout=timeout)
            if asn_res.get("ok"):
                out["primary_asn"] = {
                    "as_number":       asn_res.get("as_number"),
                    "country":         asn_res.get("as_country_code"),
                    "description":     asn_res.get("as_description"),
                }
            else:
                out["errors"].append(f"ASN: {asn_res.get('error')}")
    out["ok"] = bool(out["a"] or out["mx"] or out["ns"] or out["txt"])
    return out

# R-F2119 §21a — wire failure handler for domain_pivots
try:
    wire_failure(module="domain_pivots", detail="module shutdown",
                gap_type="engine_failure", source="domain_pivots:shutdown")
except Exception:
    pass

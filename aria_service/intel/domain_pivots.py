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

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


CRTSH_ENDPOINT = "https://crt.sh/"

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
        async with httpx.AsyncClient(
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

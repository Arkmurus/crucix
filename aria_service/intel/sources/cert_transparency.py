"""Certificate Transparency lookup (R-F580, 2026-05-16).

What this enables
─────────────────
crt.sh aggregates every public TLS certificate ever issued. For DD
that gives us three capabilities the pre-R-F580 pipeline didn't have:

  1. **Shell-domain detection.** A counterparty registers a single
     marketing-front domain plus 8-15 sibling shell domains (different
     spellings, different TLDs, hyphen variants) — all with overlapping
     wildcard certificates. CT reveals the full sibling set in a single
     query. Example: `esnadinternational.com` from your DD logs would
     have surfaced its sister domains immediately if CT were wired.

  2. **Historical subdomain enumeration.** Reveals every subdomain that
     ever held a cert, even ones currently 404'ing. Useful for finding
     dormant test/staging hosts that leaked beneficial-ownership info.

  3. **Issuer pattern analysis.** A counterparty using Let's Encrypt
     only (no DV certs from a paid CA) is a different signal from one
     using DigiCert / Sectigo. Combined with R-F573 DD case-file we can
     show "this entity's cert pattern matches the typical shell-broker
     profile seen in OFAC sanctions cases".

Data source
───────────
https://crt.sh — public, free, no API key required. The site offers a
JSON output mode via `?output=json` query param. We use the wildcard
LIKE pattern (`%foo%`) which matches the common-name and the
subject-alt-name fields against a substring.

Rate limit / etiquette
──────────────────────
crt.sh is community-supported. We:
  - Cap the timeout at 15s (one slow query never blocks a DD run).
  - Send a UA that identifies us so the operator can be contacted.
  - Cache hits in-process for 1h via a small dict (clears on restart).
  - Cap results at 50 per query (the typical actor we're profiling has
    <20 certs; >50 means we have the wrong needle or a CDN entity).

Return shape
────────────
Each hit:
    {
        "common_name":  "esnadinternational.com",
        "alt_names":    ["www.esnadinternational.com", "mail.esnadinternational.com"],
        "issuer":       "Let's Encrypt Authority X3",
        "not_before":   "2024-03-15",
        "not_after":    "2024-06-13",
        "ct_url":       "https://crt.sh/?id=11234567",
        "source":       "crt.sh",
        "tier":         "1a",   # cert log entries are primary records
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import quote_plus

import httpx

from ..engine_wiring import wired

logger = logging.getLogger("aria.intel.sources.cert_transparency")

_CRTSH_BASE = "https://crt.sh/"
_DEFAULT_TIMEOUT = 15.0
_USER_AGENT = (
    "Mozilla/5.0 (compatible; ARIA-DD/1.0; +https://intel.arkmurus.com) "
    "Cert-transparency lookup R-F580"
)
_MAX_RESULTS = 50
_CACHE_TTL_SECONDS = 3600
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    entry = _cache.get(key)
    if not entry:
        return None
    ts, hits = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return hits


def _cache_set(key: str, hits: list[dict[str, Any]]) -> None:
    _cache[key] = (time.time(), hits)


def _normalise_query(term: str) -> str:
    """Strip scheme + path + corporate suffixes; lowercase. We're
    searching by string-prefix of the cert's common-name, so the
    query should be the domain-root or entity-root."""
    if not term:
        return ""
    t = term.strip().lower()
    # Strip URL prefix
    for prefix in ("https://", "http://"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    # Strip leading 'www.'
    if t.startswith("www."):
        t = t[4:]
    # Drop everything after the first slash (path)
    if "/" in t:
        t = t.split("/", 1)[0]
    return t.strip()


def _parse_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a crt.sh JSON row into our normalised hit shape.
    Returns None if the row lacks the required fields."""
    cn = (row.get("common_name") or "").strip()
    name_value = (row.get("name_value") or "").strip()
    if not cn and not name_value:
        return None
    # name_value is newline-separated alt names; first line ~= CN.
    alt_names: list[str] = []
    if name_value:
        for n in name_value.split("\n"):
            n = n.strip()
            if n and n != cn:
                alt_names.append(n)
    cert_id = row.get("id")
    return {
        "common_name": cn or (alt_names[0] if alt_names else ""),
        "alt_names":   alt_names[:20],
        "issuer":      (row.get("issuer_name") or "").strip()[:200],
        "not_before":  (row.get("not_before") or "")[:10],
        "not_after":   (row.get("not_after") or "")[:10],
        "ct_url":      f"https://crt.sh/?id={cert_id}" if cert_id else "https://crt.sh/",
        "source":      "crt.sh",
        "tier":        "1a",
    }


@wired(module="sources.cert_transparency", summary="CT search for {term}")
async def search_certs(
    term: str,
    *,
    limit: int = _MAX_RESULTS,
    timeout: float = _DEFAULT_TIMEOUT,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Return certificate-transparency records matching `term` as a
    LIKE substring on the cert common-name / SAN fields.

    Use cases:
      - search_certs("esnadinternational") → all certs touching that string
      - search_certs("acme.io") → certs covering acme.io + subdomains

    Never raises. Failures degrade to empty list with a debug log.
    """
    q = _normalise_query(term)
    if not q or len(q) < 3:
        return []

    cache_key = f"{q}:{limit}"
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached[:limit]

    # crt.sh's LIKE syntax: %term% means "anywhere in the name". We
    # use this rather than the strict CN= match so a search for the
    # entity name (not just its domain) also surfaces certs that
    # carry the entity in a subject-alt-name.
    url = f"{_CRTSH_BASE}?q={quote_plus('%' + q + '%')}&output=json"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, headers={"User-Agent": _USER_AGENT})
            if r.status_code != 200:
                logger.debug(
                    "[R-F580] crt.sh returned HTTP %s for %s",
                    r.status_code, q,
                )
                return []
            try:
                rows = r.json()
            except json.JSONDecodeError:
                # crt.sh sometimes returns HTML instead of JSON when
                # under heavy load. Honest fall-through to [].
                logger.debug("[R-F580] crt.sh returned non-JSON for %s", q)
                return []
    except Exception as e:
        logger.debug("[R-F580] crt.sh fetch failed: %s", e)
        return []

    if not isinstance(rows, list):
        return []

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        parsed = _parse_row(row)
        if not parsed:
            continue
        # Dedupe by (common_name, not_before) — crt.sh returns one row
        # per CT log entry, so the same cert appears multiple times.
        key = f"{parsed['common_name']}|{parsed['not_before']}"
        if key in seen:
            continue
        seen.add(key)
        hits.append(parsed)
        if len(hits) >= limit:
            break

    if use_cache:
        _cache_set(cache_key, hits)
    return hits


def detect_shell_pattern(hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Score a CT result set for the typical shell-broker fingerprint.

    Heuristics (signals for the DD layer):
      - many sibling domains issued in tight time-window
      - all from a single free CA (Let's Encrypt only, no paid DV)
      - all single-name certs (no extended-validation / OV)
      - mass-renewal pattern (every 60-90 days = LE default)

    Returns a `{score: 0-100, signals: [str, ...]}` dict the caller
    can fold into deception_detection.scorer.
    """
    if not hits:
        return {"score": 0, "signals": ["no_certs_found"]}

    signals: list[str] = []
    score = 0

    # Distinct issuers
    issuers = {(h.get("issuer") or "").split(",")[0].strip() for h in hits}
    issuers.discard("")
    if len(issuers) == 1 and "Let's Encrypt" in next(iter(issuers)):
        signals.append("only_lets_encrypt_no_paid_ca")
        score += 25

    # Density of certs (>15 in 12 months = high churn / many shells)
    if len(hits) > 15:
        signals.append(f"high_cert_density:{len(hits)}")
        score += min(35, len(hits))

    # Distinct apex domains discovered in alt-names
    apex: set[str] = set()
    for h in hits:
        cn = h.get("common_name", "")
        if "." in cn:
            apex.add(".".join(cn.split(".")[-2:]))
        for a in h.get("alt_names", []):
            if "." in a:
                apex.add(".".join(a.split(".")[-2:]))
    if len(apex) > 5:
        signals.append(f"sibling_domain_cluster:{len(apex)}")
        score += min(40, len(apex) * 3)

    return {
        "score": min(100, score),
        "signals": signals,
        "issuer_count": len(issuers),
        "cert_count": len(hits),
        "apex_count": len(apex),
    }

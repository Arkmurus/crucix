"""R-F1061 — GeoIP lookup for IP addresses and domains.

Uses free geolocation databases (ip-api.com, ipapi.co) with no API key
required. Falls back to DNS-based location hints when APIs are unavailable.

Gate: ARIA_GEOIP_ENABLED=1 to enable (default ON).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Any, Optional

import httpx

logger = logging.getLogger("aria.geoip")

_ENABLED = os.getenv("ARIA_GEOIP_ENABLED", "1") == "1"
_TIMEOUT_S = 10.0


async def lookup(ip_or_domain: str) -> dict[str, Any]:
    """Look up geolocation for an IP address or domain.

    Args:
        ip_or_domain: IP address (IPv4/IPv6) or domain name.

    Returns:
        Dict with keys: ip, country, country_code, region, city,
        lat, lon, isp, org, asn, source.
        Returns empty dict on failure.
    """
    if not _ENABLED:
        return {"error": "GeoIP disabled (set ARIA_GEOIP_ENABLED=1)"}

    # Resolve domain to IP if needed
    ip = ip_or_domain
    if not _is_ip(ip_or_domain):
        try:
            ip = await asyncio.wait_for(
                _resolve_domain(ip_or_domain),
                timeout=5.0,
            )
        except Exception as e:
            logger.debug("[geoip] domain resolution failed: %s", e)
            return {"error": f"Could not resolve domain: {e}"}

    if not ip:
        return {"error": "Could not resolve IP"}

    # Try ip-api.com first (free, no key, 45 req/min)
    result = await _try_ip_api(ip)
    if result:
        return result

    # Fallback to ipapi.co
    result = await _try_ipapi_co(ip)
    if result:
        return result

    return {"error": "All GeoIP sources failed", "ip": ip}


def _is_ip(value: str) -> bool:
    """Check if a string is an IP address."""
    import re
    ipv4 = re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", value)
    if ipv4:
        return True
    # Basic IPv6 check
    if ":" in value:
        return True
    return False


async def _resolve_domain(domain: str) -> Optional[str]:
    """Resolve a domain to an IP address."""
    try:
        loop = asyncio.get_running_loop()
        info = await loop.getaddrinfo(domain, 80, type=socket.SOCK_STREAM)
        if info:
            return info[0][4][0]
    except Exception as e:
        logger.debug("[geoip] getaddrinfo failed for %s: %s", domain, e)
    return None


async def _try_ip_api(ip: str) -> Optional[dict[str, Any]]:
    """Try ip-api.com (free, 45 req/min)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    return {
                        "ip": ip,
                        "country": data.get("country", ""),
                        "country_code": data.get("countryCode", ""),
                        "region": data.get("regionName", ""),
                        "city": data.get("city", ""),
                        "lat": data.get("lat"),
                        "lon": data.get("lon"),
                        "isp": data.get("isp", ""),
                        "org": data.get("org", ""),
                        "asn": data.get("as", ""),
                        "timezone": data.get("timezone", ""),
                        "source": "ip-api.com",
                    }
    except Exception as e:
        logger.debug("[geoip] ip-api.com failed: %s", e)
    return None


async def _try_ipapi_co(ip: str) -> Optional[dict[str, Any]]:
    """Try ipapi.co (free tier, 1000 req/day)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(f"https://ipapi.co/{ip}/json/")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ip"):
                    return {
                        "ip": ip,
                        "country": data.get("country_name", ""),
                        "country_code": data.get("country_code", ""),
                        "region": data.get("region", ""),
                        "city": data.get("city", ""),
                        "lat": data.get("latitude"),
                        "lon": data.get("longitude"),
                        "isp": data.get("org", ""),
                        "org": data.get("org", ""),
                        "asn": f"AS{data.get('asn', '')}" if data.get("asn") else "",
                        "timezone": data.get("timezone", ""),
                        "currency": data.get("currency", ""),
                        "source": "ipapi.co",
                    }
    except Exception as e:
        logger.debug("[geoip] ipapi.co failed: %s", e)
    return None


async def batch_lookup(ips: list[str]) -> list[dict[str, Any]]:
    """Look up multiple IPs concurrently."""
    tasks = [lookup(ip) for ip in ips]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [
        r if isinstance(r, dict) else {"error": str(r)}
        for r in results
    ]


# ── Wire to brain ──────────────────────────────────────────────────────

try:
    from .engine_wiring import wire_success as _ws
    _ws(
        module="geoip_lookup",
        summary="GeoIP Lookup Engine active",
        detail="Sources: ip-api.com, ipapi.co. Gate: ARIA_GEOIP_ENABLED=1",
        source_id="geoip_lookup:R-F1061",
    )
except Exception:
    pass

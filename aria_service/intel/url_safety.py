"""URL-safety guard for outbound HTTP fetches.

Every module that follows a user-influenced URL (researcher,
knowledge_spider, scraper, source_discovery, crawl_enhancements,
extract_url_*) MUST pass candidates through `is_safe_url(url)` before
issuing the fetch. Returns (True, "") if the URL is safe to fetch,
or (False, reason) otherwise.

What we block
─────────────
1. Non-http(s) schemes            file:, ftp:, gopher:, dict:, ldap:, etc.
2. Loopback / link-local          127.0.0.0/8, ::1, 169.254.0.0/16, fe80::/10
3. RFC1918 private                10/8, 172.16/12, 192.168/16
4. Fly.io private network         fdaa::/16 (every fly machine shares this)
5. Carrier-grade NAT              100.64.0.0/10
6. Cloud metadata services        169.254.169.254 (AWS/Azure/GCP — fly doesn't
                                  have this, but defence in depth)
7. Reserved / broadcast           0.0.0.0/8, 224.0.0.0/4, 240.0.0.0/4
8. Internal TLDs                  *.internal, *.local, *.lan, *.localdomain
9. URL credentials                http://user:pass@host — refuses
                                  (attacker could smuggle creds in logs /
                                  ship them to attacker-controlled hosts)
10. Excessive length              > 2048 chars is almost certainly junk

Why at fetch time, not parse time
──────────────────────────────────
DNS resolution matters. `http://evil.example.com` might resolve to
10.0.0.1 (attacker-controlled DNS pointing at internal infra). We
resolve + validate the actual IP, not just the hostname. This is the
only reliable SSRF defence.

Performance
───────────
socket.getaddrinfo is cached in-process via a tiny LRU so repeated
calls to the same host don't pay DNS cost every fetch. The cache has
a 5-minute TTL so stale entries don't pin a bad resolution forever.

Usage
─────
    from aria_service.intel.url_safety import is_safe_url
    ok, reason = is_safe_url(user_url)
    if not ok:
        logger.warning("Blocked URL fetch: %s (%s)", user_url, reason)
        return None
    # ... proceed with fetch
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import asyncio
import ipaddress
import logging
import socket
import time
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("aria.intel.url_safety")

_MAX_URL_LEN = 2048
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Internal TLD suffixes — hostnames ending in these never route to public
# internet and are usually internal service names. RFC 6762 reserves .local
# for mDNS; ops teams commonly use .internal / .lan / .localdomain too.
_INTERNAL_SUFFIXES = (
    ".internal",
    ".local",
    ".lan",
    ".localdomain",
    ".fly.dev.internal",  # if anyone adds a fly-internal alias
)

# Hostnames that should never be reached from a tool fetch even if they
# resolve to public IPs. Tiny allowlist — extend only when justified.
_BLOCKED_HOSTS = frozenset({
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",   # GCP metadata
    "metadata.aws.internal",
})

# In-memory DNS cache — {hostname: (resolved_ips, expires_at_epoch)}
_DNS_CACHE: dict[str, tuple[list[str], float]] = {}
_DNS_CACHE_TTL_S = 300.0
_DNS_CACHE_MAX = 1024


def _ips_for_host(host: str) -> list[str]:
    """Resolve `host` to a list of IP addresses (v4 + v6). LRU cached.
    Returns empty list on resolution failure."""
    now = time.time()
    cached = _DNS_CACHE.get(host)
    if cached and cached[1] > now:
        return cached[0]
    try:
        # getaddrinfo returns tuples; we want the sockaddr IPs only
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return []
    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and sockaddr[0]:
            ip = sockaddr[0]
            # Strip zone-id suffix on IPv6 link-local (fe80::...%eth0)
            if "%" in ip:
                ip = ip.split("%", 1)[0]
            ips.append(ip)
    # Trim cache if it's too big
    if len(_DNS_CACHE) >= _DNS_CACHE_MAX:
        _DNS_CACHE.clear()
    _DNS_CACHE[host] = (ips, now + _DNS_CACHE_TTL_S)
    return ips


def _is_private_ip(ip: str) -> tuple[bool, str]:
    """Return (is_private, category) for an IP. Categories are human-
    readable reason strings used in the block-log."""
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        return (True, "unparseable_ip")

    # Loopback — 127/8 + ::1
    if ip_obj.is_loopback:
        return (True, "loopback")
    # Link-local — 169.254/16 + fe80::/10 (also blocks AWS/GCP metadata IP)
    if ip_obj.is_link_local:
        return (True, "link_local")
    # Multicast — 224/4 + ff00::/8
    if ip_obj.is_multicast:
        return (True, "multicast")
    # Reserved / unspecified
    if ip_obj.is_reserved or ip_obj.is_unspecified:
        return (True, "reserved")
    # RFC1918 + IPv6 ULA — `is_private` covers both
    if ip_obj.is_private:
        return (True, "private_rfc1918_or_ula")
    # Carrier-grade NAT (100.64/10) — not flagged by ipaddress lib as private
    if isinstance(ip_obj, ipaddress.IPv4Address):
        if int(ip_obj) >> 22 == int(ipaddress.IPv4Address("100.64.0.0")) >> 22:
            return (True, "carrier_grade_nat")
    # Fly.io private network — fdaa::/16. ipaddress marks this as
    # private (is_private=True for ULA fc00::/7), so already blocked
    # above. Explicit check makes the log category clearer.
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if str(ip_obj).startswith("fdaa:"):
            return (True, "fly_private_network")

    return (False, "")


def is_safe_url(url: str) -> tuple[bool, str]:
    """Validate a URL before fetching. Returns (True, "") if safe, or
    (False, reason) if blocked. Resolves DNS for the hostname and checks
    every resolved IP — a name that resolves to ANY blocked IP is rejected
    entirely (defence against DNS rebinding).

    Never raises. Unknown resolution failures return (False, "dns_fail")
    rather than defaulting to allow."""
    if not url or not isinstance(url, str):
        return (False, "empty_url")
    if len(url) > _MAX_URL_LEN:
        return (False, "url_too_long")

    try:
        parsed = urlparse(url)
    except Exception as e:
        return (False, f"parse_error:{type(e).__name__}")

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return (False, f"disallowed_scheme:{scheme or 'none'}")

    # Credential URLs — reject entirely. Legitimate use cases are
    # vanishingly rare and the exfiltration risk is real.
    if parsed.username or parsed.password:
        return (False, "credentials_in_url")

    host = (parsed.hostname or "").lower().strip().rstrip(".")
    if not host:
        return (False, "no_hostname")

    if host in _BLOCKED_HOSTS:
        return (False, f"blocked_hostname:{host}")

    for suf in _INTERNAL_SUFFIXES:
        if host.endswith(suf):
            return (False, f"internal_tld:{suf}")

    # If the host is already a literal IP, skip DNS and validate directly.
    literal = False
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False

    ips_to_check: list[str]
    if literal:
        ips_to_check = [host]
    else:
        ips_to_check = _ips_for_host(host)
        if not ips_to_check:
            return (False, "dns_fail")

    for ip in ips_to_check:
        blocked, reason = _is_private_ip(ip)
        if blocked:
            return (False, f"{reason}:{ip}")

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="url_safety",
        summary="Is Safe Url",
        source_id="url_safety:R-F1001",
    )

    return (True, "")


async def is_safe_url_async(url: str) -> tuple[bool, str]:
    """Validate a fetch target without resolving DNS on the serving loop."""
    return await asyncio.to_thread(is_safe_url, url)


def assert_safe_url(url: str) -> None:
    """Raises ValueError if the URL is unsafe. Use in contexts where
    the caller would prefer to crash-early rather than log-and-skip."""
    ok, reason = is_safe_url(url)
    if not ok:
        raise ValueError(f"URL failed SSRF safety check: {reason}")


async def safe_get(client, url, *, max_redirects: int = 3, **kwargs):
    """R-F1814 — SSRF-safe HTTP GET. Validates ``url`` AND every redirect hop with
    is_safe_url, with ``follow_redirects`` forced OFF so an open redirect to an
    internal host cannot bypass the guard. Raises ``ValueError('ssrf_blocked:<reason>')``
    if any hop is unsafe. ``client`` is an httpx.AsyncClient (caller owns  # no-breaker: URL safety is the SSRF guard itself; breaker at caller
    timeout/headers). This is the single SSRF-checked fetch boundary every
    user-URL fetcher should use instead of a raw ``client.get``.
    """
    import httpx as _httpx
    cur = url
    for _ in range(max_redirects + 1):
        ok, reason = await is_safe_url_async(cur)
        if not ok:
            raise ValueError(f"ssrf_blocked:{reason}")
        kwargs["follow_redirects"] = False
        resp = await client.get(cur, **kwargs)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if loc:
                cur = str(_httpx.URL(resp.url).join(loc))
                continue
        return resp
    raise ValueError("ssrf_blocked:too_many_redirects")

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

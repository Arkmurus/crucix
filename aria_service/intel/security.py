"""
ARIA Security Layer — Protects against malicious content, SSRF, and injection.

ARIA actively crawls the web, reads emails, processes attachments, and fetches URLs.
Each of these is an attack vector. This module provides:

1. URL SANITISATION — blocks internal/private IPs (SSRF), dangerous protocols
2. CONTENT SCANNING — detects malicious patterns in fetched content
3. CRAWL SAFETY — depth limits, domain allowlists, rate limiting
4. ATTACHMENT SAFETY — size limits, file type validation, content inspection
5. PROMPT INJECTION DEFENCE — detects attempts to manipulate ARIA via injected text
6. INPUT VALIDATION — sanitises all user inputs before processing

The server (Seenode/Fly.io) handles network-level security (TLS, rate limiting,
authentication). This module handles application-level threats specific to ARIA's
intelligence gathering activities.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import asyncio
import ipaddress
import logging
import os
import re
import socket
import time
from urllib.parse import urlparse

logger = logging.getLogger("aria.security")

# ── URL Sanitisation (SSRF Protection) ───────────────────────────────────────

# Private/reserved IP ranges that should never be fetched
_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 private
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

# Blocked hostnames
_BLOCKED_HOSTS = {
    "localhost", "0.0.0.0", "127.0.0.1", "::1",
    "metadata.google.internal",        # GCP metadata
    "169.254.169.254",                 # AWS/Azure/GCP metadata
    "metadata.internal",
}

# Allowed protocols
_ALLOWED_SCHEMES = {"http", "https"}

# Dangerous file extensions that should never be downloaded
_DANGEROUS_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".scr", ".pif",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".ps1",
    ".dll", ".sys", ".drv", ".cpl", ".inf", ".reg",
    ".sh", ".bash", ".csh", ".ksh",
    ".app", ".dmg", ".pkg",
    ".deb", ".rpm",
    ".jar", ".class",
    ".py", ".rb", ".pl",  # script files from untrusted sources
}

# Max sizes
MAX_URL_LENGTH = 2048
MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024   # 50MB
MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024  # 25MB
MAX_CRAWL_DEPTH = 3
MAX_CRAWL_PAGES = 30
MAX_CONTENT_LENGTH = 500_000  # 500KB of text


# R-F1102 — operator-gated relax flags for DD investigations.
# These are OFF by default (safe mode). Set to "1" to relax for
# legitimate due-diligence reading. NEVER bypass SSRF or CAPTCHA guards.
_ALLOW_AUTH_REQUIRED_URLS = os.getenv("ARIA_ALLOW_AUTH_REQUIRED_URLS", "0") == "1"
_ALLOW_SCRIPT_EXTENSIONS = os.getenv("ARIA_ALLOW_SCRIPT_EXTENSIONS", "0") == "1"


# ── R-F3473: DNS resolution cache for the SSRF rebinding check ──────────────
#
# LIVE 2026-07-30, third stall cause named by R-F3464's attribution (after
# R-F3467 and R-F3468 each removed the previous one):
#   loop stack: socket.py:getaddrinfo:981 <- security.py:validate_url:133
#                                         <- security.py:sanitise_url:263
#
# The rebinding check below is correct and stays. The problem is that
# socket.getaddrinfo BLOCKS, and it is slowest exactly when it FAILS: an
# unresolvable host burns the resolver timeout before raising, and a crawler
# retrying dead domains pays that again for every URL — on the event loop.
#
# The sync API remains for non-async callers. Production coroutines use the async
# wrappers below so a cache miss cannot block the serving loop; the cache still
# avoids repeated positive and negative lookups.
#
# What is cached is the VERDICT, never a bare "this host is fine" — a cache that
# forgot why it said yes would be an SSRF bypass.
_DNS_CACHE: dict[str, tuple[float, tuple[bool, str]]] = {}
_DNS_CACHE_MAX = int(os.getenv("ARIA_DNS_CACHE_MAX", "512"))
_DNS_CACHE_TTL_S = float(os.getenv("ARIA_DNS_CACHE_TTL_S", "300"))


def _dns_cache_clear() -> None:
    """Drop every cached resolution verdict (tests, and operator recovery)."""
    _DNS_CACHE.clear()


def _resolve_verdict(hostname: str) -> tuple[bool, str]:
    """(is_safe, reason) for the DNS-rebinding half of the SSRF guard, cached.

    A host whose A/AAAA record points into a private range is refused. An
    unresolvable host is ALLOWED (unchanged behaviour): the fetch will simply
    fail, so a transient DNS blip must not permanently reject a good source.
    """
    now = time.time()
    hit = _DNS_CACHE.get(hostname)
    if hit is not None and (now - hit[0]) < _DNS_CACHE_TTL_S:
        return hit[1]

    verdict: tuple[bool, str] = (True, "")
    try:
        for _info in socket.getaddrinfo(hostname, None):
            _addr = str(_info[4][0]).split("%")[0]
            try:
                _rip = ipaddress.ip_address(_addr)
            except ValueError:
                continue
            for _net in _PRIVATE_RANGES:
                if _rip in _net:
                    verdict = (False, f"Host resolves to private IP: {hostname} -> {_addr}")
                    break
            if not verdict[0]:
                break
    except Exception:
        # DNS failure/unresolvable — allow; the fetch attempt will fail naturally.
        # Cached anyway: this is the SLOW path (resolver timeout) and the one a
        # crawler hits repeatedly on dead domains.
        verdict = (True, "")

    if len(_DNS_CACHE) >= _DNS_CACHE_MAX:
        # Bounded: hostnames can be attacker-supplied, so this must not grow
        # without limit. Evict the oldest entry.
        try:
            oldest = min(_DNS_CACHE.items(), key=lambda kv: kv[1][0])[0]
            _DNS_CACHE.pop(oldest, None)
        except ValueError:
            pass
    _DNS_CACHE[hostname] = (now, verdict)
    return verdict


def validate_url(url: str) -> tuple[bool, str]:
    """Validate a URL is safe to fetch. Returns (is_safe, reason)."""
    if not url or not isinstance(url, str):
        return False, "Empty or invalid URL"

    if len(url) > MAX_URL_LENGTH:
        return False, f"URL too long ({len(url)} > {MAX_URL_LENGTH})"

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "URL parse failed"

    # Check scheme
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False, f"Blocked protocol: {parsed.scheme}"

    # Check hostname
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "No hostname"

    if hostname in _BLOCKED_HOSTS:
        return False, f"Blocked host: {hostname}"

    # Check for IP address (could be SSRF attempt)
    try:
        ip = ipaddress.ip_address(hostname)
        for net in _PRIVATE_RANGES:
            if ip in net:
                return False, f"Private/reserved IP: {hostname}"
    except ValueError:
        # Not an IP literal — it's a hostname. R-F2212 (2026-07-01): the pre-fix
        # code passed EVERY non-IP hostname unconditionally, so a user/vault
        # source pointing at a fly 6PN host (aria-intel.internal, aria-web.internal,
        # aria-wa.internal, aria-searxng.internal) or a bare single-label service
        # name sailed through — confirmed live: http://aria-intel.internal:8000
        # was accepted and would be fetched by news_monitor (SSRF into the private
        # network, internal responses exfiltrated into the corpus). Close it two ways:
        _h = hostname.rstrip(".")
        # (1) block fly-internal suffixes + bare single-label names (never public).
        if _h.endswith(".internal") or "." not in _h:
            return False, f"Blocked internal host: {hostname}"
        # (2) resolve DNS and re-check EVERY resolved address against the private
        #     ranges — defeats a public hostname whose A/AAAA record points at a
        #     private/link-local/loopback IP (DNS-rebinding SSRF). Best-effort: an
        #     unresolvable host is left to the fetcher (which will simply fail),
        #     so a transient DNS blip can't permanently reject a good public source.
        # R-F3473 — cached: same check, but a repeat lookup (and especially a
        # repeat FAILING lookup) no longer blocks the event loop again.
        _ok, _why = _resolve_verdict(_h)
        if not _ok:
            return False, _why

    # Check for dangerous file extensions
    # R-F1102: ARIA_ALLOW_SCRIPT_EXTENSIONS=1 allows GET on .js/.py/.jar etc.
    # (GET doesn't execute them — the block was overcautious for reads).
    path_lower = (parsed.path or "").lower()
    if not _ALLOW_SCRIPT_EXTENSIONS:
        for ext in _DANGEROUS_EXTENSIONS:
            if path_lower.endswith(ext):
                return False, f"Dangerous file type: {ext}"

    # Reject URLs that require auth — they 302 to a login page and we end up
    # ingesting login-page HTML as "article content". Live incident
    # 2026-04-27: LinkedIn admin URLs got into the research queue, redirected
    # to login, and 6 chunks of LinkedIn login HTML were written to RAG.
    # R-F1102: ARIA_ALLOW_AUTH_REQUIRED_URLS=1 bypasses this for DD investigations.
    if not _ALLOW_AUTH_REQUIRED_URLS and _is_auth_required_url(hostname, path_lower):
        return False, f"Auth-required URL (would redirect to login): {hostname}{path_lower[:50]}"

    # R-F66b (2026-05-09): block known low-value navigational pages.
    # Distinct from auth-required (which 302's to login) — these pages
    # return 200 with real content, but the content is product-help
    # boilerplate, not defence-DD intelligence. Live evidence 2026-05-09
    # research cycle: linkedin.com/help/linkedin/answer/4788 and /answer/67
    # were processed as 9-fact "articles" each email cycle, polluting the
    # corpus with LinkedIn FAQ content. Blocking saves ~30s of Anthropic
    # extraction per email cycle and keeps the knowledge base clean.
    if _is_low_value_url(hostname, path_lower):
        return False, f"Low-value navigational URL (product help / footer): {hostname}{path_lower[:50]}"

    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="security",
        summary="Validate Url",
        source_id="security:R-F996",
    )

    return True, "OK"


async def validate_url_async(url: str) -> tuple[bool, str]:
    """Validate a URL without running DNS resolution on the serving loop."""
    return await asyncio.to_thread(validate_url, url)


# Hosts + path prefixes that always require authentication. Fetching these
# returns the login page (200 OK after redirect), which then gets ingested.
_AUTH_REQUIRED_URL_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("linkedin.com", ("/comm/", "/admin", "/sales/", "/messaging/", "/feed/", "/in/me", "/notifications/")),
    ("www.linkedin.com", ("/comm/", "/admin", "/sales/", "/messaging/", "/feed/", "/in/me", "/notifications/")),
    ("facebook.com", ("/login", "/checkpoint")),
    ("www.facebook.com", ("/login", "/checkpoint")),
    ("twitter.com", ("/i/", "/messages")),
    ("x.com", ("/i/", "/messages")),
]


# R-F66b: hosts + path prefixes that return 200 with real but low-value
# product-help content. These slip past the auth-required filter (no login
# redirect) but produce only navigational/FAQ noise when ingested.
_LOW_VALUE_URL_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("linkedin.com", ("/help/", "/legal/", "/psettings/", "/learning/")),
    ("www.linkedin.com", ("/help/", "/legal/", "/psettings/", "/learning/")),
    ("help.linkedin.com", ("/",)),
    ("support.linkedin.com", ("/",)),
    ("twitter.com", ("/help/", "/about/")),
    ("x.com", ("/help/", "/about/")),
    ("help.twitter.com", ("/",)),
    ("help.x.com", ("/",)),
    ("facebook.com", ("/help/", "/policies/")),
    ("www.facebook.com", ("/help/", "/policies/")),
]


def _is_auth_required_url(hostname: str, path_lower: str) -> bool:
    """True when the URL is known to require auth and would redirect to a login page."""
    for host_pat, path_prefixes in _AUTH_REQUIRED_URL_PATTERNS:
        if hostname == host_pat or hostname.endswith("." + host_pat):
            for prefix in path_prefixes:
                if prefix in path_lower:
                    return True
    return False


def _is_low_value_url(hostname: str, path_lower: str) -> bool:
    """True when the URL returns real content but it's product-help noise.

    R-F66b: distinct from auth-required because these pages 200 with a
    real-looking page that the article reader will happily extract 9 'facts'
    from — none of them defence-DD intelligence. Block at the security layer
    so the LLM never sees them.
    """
    for host_pat, path_prefixes in _LOW_VALUE_URL_PATTERNS:
        if hostname == host_pat or hostname.endswith("." + host_pat):
            for prefix in path_prefixes:
                if prefix in path_lower:
                    return True
    return False


# R-F3355 — schemes ARIA mints for its OWN records. These are IDENTIFIERS, not
# locators: `web_search.py` synthesises `memory://<sha1>` for a RAG hit that has
# no URL purely so the dedupe key stays stable across retrievals. Nothing can
# fetch one, and nothing should try. Kept here beside `validate_url` because
# this module already owns URL classification and every fetch path imports it —
# before R-F3355 the same knowledge was re-implemented ad hoc in at least three
# places (financial_health.py, dd_orchestrator's _ADVERSE_SELF_SOURCE_MARKERS)
# and simply MISSING on the researcher fetch path, which is what caused the
# ledger flood.
INTERNAL_REF_SCHEMES: tuple[str, ...] = ("memory://", "rag://", "aria://", "brain_hook:")


def is_internal_ref(url: object) -> bool:
    """True when `url` is an ARIA-internal record pointer rather than a locator.

    Total on junk input (None / non-str) so it is safe to call at any boundary.
    """
    if not isinstance(url, str):
        return False
    return url.strip().lower().startswith(INTERNAL_REF_SCHEMES)


def sanitise_url(url: str) -> str | None:
    """Sanitise and validate a URL. Returns clean URL or None if dangerous."""
    safe, reason = validate_url(url)
    if not safe:
        # R-F3355 — an internal record pointer is REFUSED exactly as before
        # (returns None); it is just not reported as an anomaly, because it
        # isn't one. Live 2026-07-28 this single expected rejection held 86-88
        # of the 200 shared ledger slots (43-44%) and evicted real errors — see
        # error_streak.py's own note that a warning burst >200 destroys
        # evidence. The WASTED FETCH is fixed at the boundary
        # (researcher._fetch_article_text); this only stops the accounting of
        # an expected outcome as a defect. Genuinely dangerous schemes
        # (javascript:/file:/data:) still log at WARNING and still reach the
        # ledger — the guard must not go blind.
        if is_internal_ref(url):
            logger.debug(
                "sanitise_url: internal record ref is not fetchable (expected): %s",
                url[:100],
            )
        else:
            logger.warning("Blocked URL: %s — %s", url[:100], reason)
        return None
    return url.strip()


async def sanitise_url_async(url: str) -> str | None:
    """Sanitise a URL without running DNS resolution on the serving loop."""
    return await asyncio.to_thread(sanitise_url, url)


# ── Content Security ─────────────────────────────────────────────────────────

# Patterns that indicate truly malicious content. Tightened 2026-04-27 after
# zona-militar.com (WordPress + Google Tag Manager) tripped the broad
# `<script>.*?eval(` / `document.write` / `onload=` patterns on every fetch
# and produced log noise that masked real signals. The new patterns require
# actual obfuscation/injection indicators (atob/Function/data: scheme).
_MALWARE_PATTERNS = [
    # eval() with obfuscation indicators (atob/Function/escape/unescape) is
    # a classic XSS payload signature. Plain eval() in tag-manager scripts
    # is too common to flag.
    re.compile(r"\beval\s*\(\s*(?:atob|unescape|decodeURIComponent|Function)\s*\(", re.I),
    # document.write injecting another <script> is the script-injection
    # pattern. Naked document.write (AdSense/Twitter widgets) is benign,
    # and so is document.write of <script src="https://googleads..."> —
    # F62 fix 2026-04-28: defensa.com event listings tripped the prior
    # pattern on every fetch because their AdSense / Google Tag Manager
    # uses synchronous document.write('<script src="..."'>) injection.
    # Now require either a dangerous src scheme (javascript:/data:) or
    # an inline payload with eval/atob/Function/unescape inside the
    # injected tag. Plain ad-network injection passes through.
    re.compile(
        r"document\.write\s*\([^)]{0,500}<script[^>]*"
        r"(?:src\s*=\s*['\"]?(?:javascript|data):"
        r"|>[^<]{0,500}(?:eval|atob|Function|unescape)\s*\()",
        re.I,
    ),
    # iframe / embed loading from javascript: or data: schemes is a real XSS
    # vector. Plain iframe/embed src= is fine (YouTube, Vimeo, etc.).
    re.compile(r"<iframe[^>]*src\s*=\s*['\"](?:javascript|data):", re.I),
    re.compile(r"<embed[^>]*src\s*=\s*['\"](?:javascript|data):", re.I),
    re.compile(r"<object[^>]*data\s*=\s*['\"](?:javascript|data):", re.I),
    # Inline event handlers calling eval/Function/atob (XSS payload).
    # Plain `onclick="doStuff()"` on UI buttons is benign.
    re.compile(r"on\w+\s*=\s*['\"][^'\"]*(?:eval|Function|atob|unescape)\s*\(", re.I),
    # window.location redirects to javascript: or data: schemes.
    re.compile(r"window\.location(?:\.href)?\s*=\s*['\"](?:javascript|data):", re.I),
]

# Prompt injection patterns — someone trying to manipulate ARIA through content
_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.I),
    re.compile(r"you\s+are\s+now\s+(?:a|an)\s+(?:different|new)", re.I),
    re.compile(r"system\s*:\s*you\s+are", re.I),
    re.compile(r"forget\s+(?:all\s+)?(?:your|previous)\s+(?:instructions|rules)", re.I),
    re.compile(r"override\s+(?:your|system)\s+(?:prompt|instructions)", re.I),
    re.compile(r"\[SYSTEM\]|\[ADMIN\]|\[OVERRIDE\]", re.I),
    re.compile(r"<\|(?:im_start|system|endoftext)\|>", re.I),
]


def scan_content(text: str, source: str = "unknown") -> dict:
    """Scan text content for malicious patterns. Returns safety assessment."""
    if not text:
        return {"safe": True, "threats": []}

    threats = []
    # Check for max size
    if len(text) > MAX_CONTENT_LENGTH:
        text = text[:MAX_CONTENT_LENGTH]

    # Check for malware patterns
    for pattern in _MALWARE_PATTERNS:
        if pattern.search(text[:50000]):
            threats.append({"type": "malware_pattern", "pattern": pattern.pattern[:50]})

    # Check for prompt injection
    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(text[:10000]):
            threats.append({"type": "prompt_injection", "pattern": pattern.pattern[:50]})

    if threats:
        logger.warning(
            "Content threats detected from %s: %d threats (%s)",
            source[:50], len(threats),
            ", ".join(t["type"] for t in threats),
        )

    return {
        "safe": len(threats) == 0,
        "threats": threats,
        "scanned_length": len(text),
        "source": source,
    }


def strip_dangerous_content(html: str) -> str:
    """Strip potentially dangerous HTML elements while preserving text content."""
    if not html:
        return ""

    # Remove dangerous tags entirely
    cleaned = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", html, flags=re.I)
    cleaned = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", cleaned, flags=re.I)
    cleaned = re.sub(r"<iframe[^>]*>[\s\S]*?</iframe>", "", cleaned, flags=re.I)
    cleaned = re.sub(r"<object[^>]*>[\s\S]*?</object>", "", cleaned, flags=re.I)
    cleaned = re.sub(r"<embed[^>]*>[\s\S]*?</embed>", "", cleaned, flags=re.I)
    cleaned = re.sub(r"<form[^>]*>[\s\S]*?</form>", "", cleaned, flags=re.I)

    # Remove event handlers
    cleaned = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', "", cleaned, flags=re.I)

    # Remove javascript: URLs
    cleaned = re.sub(r'href\s*=\s*["\']javascript:[^"\']*["\']', "", cleaned, flags=re.I)

    return cleaned


# ── Attachment Safety ────────────────────────────────────────────────────────

# Safe MIME types for processing
_SAFE_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",        # xlsx
    "application/msword",         # doc
    "application/vnd.ms-excel",   # xls
    "text/plain",
    "text/csv",
    "text/html",
    "text/xml",
    "application/json",
    "application/xml",
    "image/jpeg",
    "image/png",
    "image/gif",
}

# Safe file extensions
_SAFE_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    ".txt", ".csv", ".md", ".json", ".xml",
    ".jpg", ".jpeg", ".png", ".gif",
    ".log", ".html", ".htm",
}


def validate_attachment(filename: str, mime: str, size: int) -> tuple[bool, str]:
    """Validate an attachment is safe to process."""
    # Check size
    if size > MAX_ATTACHMENT_SIZE:
        return False, f"Too large: {size / 1024 / 1024:.1f}MB > {MAX_ATTACHMENT_SIZE / 1024 / 1024}MB"

    # Check extension
    ext = ""
    if filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext and ext not in _SAFE_EXTENSIONS:
            return False, f"Unsafe file type: {ext}"

        # Double extension check (e.g., "report.pdf.exe")
        parts = filename.lower().split(".")
        if len(parts) >= 3:
            for part in parts[1:]:
                if f".{part}" in _DANGEROUS_EXTENSIONS:
                    return False, f"Suspicious double extension: {filename}"

    # Check MIME
    mime_lower = (mime or "").lower()
    if mime_lower and mime_lower not in _SAFE_MIME_TYPES:
        # Allow if extension is safe (MIME can be wrong)
        if ext not in _SAFE_EXTENSIONS:
            return False, f"Unsafe MIME type: {mime_lower}"

    return True, "OK"


# ── Input Sanitisation ───────────────────────────────────────────────────────

def sanitise_text_input(text: str, max_length: int = 10000) -> str:
    """Sanitise user text input."""
    if not text or not isinstance(text, str):
        return ""

    # Truncate
    text = text[:max_length]

    # Remove null bytes
    text = text.replace("\x00", "")

    # Remove control characters (except newlines and tabs)
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    return text.strip()


def sanitise_filename(filename: str) -> str:
    """Sanitise a filename — remove path traversal and dangerous chars."""
    if not filename:
        return "unknown"

    # Remove path components
    filename = filename.replace("\\", "/").split("/")[-1]

    # Remove dangerous characters
    filename = re.sub(r'[<>:"|?*\x00-\x1f]', "_", filename)

    # Prevent path traversal
    filename = filename.lstrip(".")

    return filename[:255] or "unknown"


# ── Internal Entity Whitelist (False Alert Suppression) ────────────────────
# 2026-04-12: internal entities, module names, and infrastructure references
# that appear in logs/prompts/code but should NEVER trigger sanctions alerts
# or security warnings. Case-insensitive matching.

_INTERNAL_WHITELIST: set[str] = {
    # ARIA system
    "aria", "arkmurus", "arkmurus ltd", "arkmurus research intelligence agent",
    "crucix", "crucix intelligence", "seenode", "fly.io", "fly",
    # Infrastructure
    "chromadb", "redis", "ollama", "deepseek", "anthropic", "openai",
    "telegram", "whatsapp", "baileys", "fastapi",
    # Known-good test entities (from past false positives)
    "aria-intel", "aria_service", "aria_zoom_service",
    # Module references that look like entity names
    "tender_monitor", "conflict_tracker", "regional_navigation",
    "sanctions_classify", "dd_orchestrator",
}

# Country names that are frequently looked up but are NOT sanctions targets.
# These suppress the "why is ARIA looking up China?" type false alerts.
_LEGITIMATE_LOOKUP_COUNTRIES: set[str] = {
    "china", "russia", "nigeria", "iran", "north korea", "syria",
    "iraq", "yemen", "libya", "venezuela", "cuba", "belarus", "myanmar",
}


def is_internal_entity(name: str) -> bool:
    """Return True if the entity name is a known internal/system reference.

    Used to suppress false alerts from sanctions screens, security audits,
    and compliance monitoring when system names appear in the data flow.
    """
    if not name:
        return False
    return name.lower().strip() in _INTERNAL_WHITELIST


def is_legitimate_lookup(country: str) -> bool:
    """Return True if the country lookup is expected and legitimate.

    Sanctions lookups for Russia, China, etc. are normal DD activity —
    they should not trigger meta-alerts about suspicious research patterns.
    """
    if not country:
        return False
    return country.lower().strip() in _LEGITIMATE_LOOKUP_COUNTRIES

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

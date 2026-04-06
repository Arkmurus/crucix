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

import ipaddress
import logging
import re
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
        pass  # Not an IP — hostname is fine

    # Check for dangerous file extensions
    path_lower = (parsed.path or "").lower()
    for ext in _DANGEROUS_EXTENSIONS:
        if path_lower.endswith(ext):
            return False, f"Dangerous file type: {ext}"

    return True, "OK"


def sanitise_url(url: str) -> str | None:
    """Sanitise and validate a URL. Returns clean URL or None if dangerous."""
    safe, reason = validate_url(url)
    if not safe:
        logger.warning("Blocked URL: %s — %s", url[:100], reason)
        return None
    return url.strip()


# ── Content Security ─────────────────────────────────────────────────────────

# Patterns that indicate malicious content
_MALWARE_PATTERNS = [
    re.compile(r"<script[^>]*>.*?eval\s*\(", re.I | re.S),
    re.compile(r"<script[^>]*>.*?document\.write", re.I | re.S),
    re.compile(r"<iframe[^>]*src\s*=\s*['\"](?:javascript|data):", re.I),
    re.compile(r"on(?:load|error|click|mouseover)\s*=\s*['\"]", re.I),
    re.compile(r"window\.location\s*=\s*['\"](?:javascript|data):", re.I),
    re.compile(r"<object[^>]*data\s*=", re.I),
    re.compile(r"<embed[^>]*src\s*=", re.I),
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

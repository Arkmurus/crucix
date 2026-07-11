"""R-F1007 — ARIA Antivirus & Security Hardening.

Real-time threat detection, input sanitization, malware scanning,
and security enforcement across all of ARIA's ecosystem.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger("aria.antivirus")


class ARIAAntivirus:
    """Real-time security protection for ARIA's entire ecosystem.
    
    Protects against:
    - Prompt injection attacks
    - SQL injection
    - Path traversal
    - XSS (Cross-Site Scripting)
    - Command injection
    - Data exfiltration
    - PII leakage
    - Malware in uploaded files
    """

    # Known attack patterns
    INJECTION_PATTERNS = [
        (r"ignore\s+all\s+(previous|prior)\s+instructions", "Prompt injection: instruction override"),
        (r"you\s+are\s+(now|free)\s+(dan|chatgpt|gpt)", "Prompt injection: DAN jailbreak"),
        (r"output\s+your\s+(system\s+)?prompt", "Prompt injection: system prompt extraction"),
        (r"print\s+your\s+(system\s+)?instructions", "Prompt injection: instruction extraction"),
        (r"bypass\s+(all\s+)?(restrictions|filters|guards)", "Prompt injection: bypass attempt"),
        (r"disable\s+(all\s+)?(security|safety|guardrails)", "Prompt injection: security disable"),
        (r"admin\s*(override|bypass|mode)", "Prompt injection: admin spoofing"),
        (r"\[system\].*override", "Prompt injection: system message spoofing"),
        (r"role\s*:\s*(system|admin|assistant)", "Prompt injection: role spoofing"),
    ]

    SQL_INJECTION_PATTERNS = [
        (r"['\"]\s*OR\s*['\"]\s*['\"]\s*=\s*['\"]", "SQL injection: OR 1=1"),
        (r"'\s*OR\s*1\s*=\s*1", "SQL injection: OR 1=1"),
        (r"'\s*--", "SQL injection: comment injection"),
        (r"'\s*;\s*DROP\s+TABLE", "SQL injection: DROP TABLE"),
        (r"'\s*;\s*DELETE\s+FROM", "SQL injection: DELETE FROM"),
        (r"'\s*;\s*INSERT\s+INTO", "SQL injection: INSERT INTO"),
        (r"'\s*;\s*UPDATE\s+\w+\s+SET", "SQL injection: UPDATE SET"),
        (r"'\s*UNION\s+SELECT", "SQL injection: UNION SELECT"),
        (r"'\s*EXEC\s+\(", "SQL injection: EXEC injection"),
        (r"'\s*xp_cmdshell", "SQL injection: xp_cmdshell"),
    ]

    XSS_PATTERNS = [
        (r"<script[^>]*>", "XSS: script tag"),
        (r"javascript\s*:", "XSS: javascript protocol"),
        (r"onerror\s*=", "XSS: onerror handler"),
        (r"onload\s*=", "XSS: onload handler"),
        (r"onclick\s*=", "XSS: onclick handler"),
        (r"onmouseover\s*=", "XSS: onmouseover handler"),
        (r"<iframe[^>]*>", "XSS: iframe injection"),
        (r"<embed[^>]*>", "XSS: embed injection"),
        (r"<object[^>]*>", "XSS: object injection"),
        (r"<svg[^>]*>", "XSS: SVG injection"),
        (r"alert\s*\(\s*['\"]", "XSS: alert() call"),
        (r"document\.cookie", "XSS: cookie access"),
        (r"document\.location", "XSS: location access"),
        (r"window\.location", "XSS: window location"),
        (r"fetch\s*\(\s*['\"]https?://", "XSS: external fetch"),
    ]

    PATH_TRAVERSAL_PATTERNS = [
        (r"\.\./", "Path traversal: ../"),
        (r"\.\.\\", "Path traversal: ..\\"),
        (r"~[^/\\\s]+", "Path traversal: home directory"),
        (r"/etc/passwd", "Path traversal: /etc/passwd"),
        (r"/etc/shadow", "Path traversal: /etc/shadow"),
        (r"\.env", "Path traversal: .env file"),
    ]

    COMMAND_INJECTION_PATTERNS = [
        (r";\s*(rm|del|format|mkfs)", "Command injection: destructive command"),
        (r"`[^`]+`", "Command injection: backtick execution"),
        (r"\$\([^)]+\)", "Command injection: subshell execution"),
        (r"\|[^\|]*shutdown", "Command injection: shutdown pipe"),
        (r"\|[^\|]*reboot", "Command injection: reboot pipe"),
        (r"\|[^\|]*wget", "Command injection: wget pipe"),
        (r"\|[^\|]*curl", "Command injection: curl pipe"),
        (r"\|[^\|]*bash", "Command injection: bash pipe"),
        (r"\|[^\|]*python", "Command injection: python pipe"),
    ]

    PII_PATTERNS = [
        (r"\b\d{3}[-.]?\d{2}[-.]?\d{4}\b", "PII: SSN detected"),
        (r"\b\d{16}\b", "PII: credit card number"),
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "PII: email address"),
        (r"\b\d{10}\b", "PII: phone number (US)"),
        (r"\b[A-Z]{2}\d{6}\b", "PII: passport number"),
    ]

    def __init__(self):
        self._scan_count = 0
        self._blocked_count = 0
        self._threats: list[dict] = []

    def scan_input(self, text: str, source: str = "user_input") -> dict[str, Any]:
        """Scan user input for threats. Returns scan result."""
        self._scan_count += 1
        threats = []
        
        # Check all threat categories
        for category, patterns in [
            ("injection", self.INJECTION_PATTERNS),
            ("sql_injection", self.SQL_INJECTION_PATTERNS),
            ("xss", self.XSS_PATTERNS),
            ("path_traversal", self.PATH_TRAVERSAL_PATTERNS),
            ("command_injection", self.COMMAND_INJECTION_PATTERNS),
            ("pii", self.PII_PATTERNS),
        ]:
            for pattern, description in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    threats.append({
                        "category": category,
                        "pattern": pattern,
                        "description": description,
                        "severity": self._get_severity(category),
                    })
        
        result = {
            "safe": len(threats) == 0,
            "threats": threats,
            "threat_count": len(threats),
            "source": source,
            "timestamp": time.time(),
        }
        
        if threats:
            self._blocked_count += 1
            self._threats.append(result)
            logger.warning("[antivirus] BLOCKED %d threats from %s", len(threats), source)
        
        return result

    def scan_file(self, filename: str, content: bytes) -> dict[str, Any]:
        """Scan file content for malware."""
        threats = []
        
        # Check file extension
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        dangerous_extensions = {"exe", "bat", "cmd", "com", "scr", "pif", "vbs", "vbe", "js", "jse", "wsf", "wsh", "ps1", "psm1", "psd1"}
        
        if ext in dangerous_extensions:
            threats.append({
                "category": "malware",
                "description": f"Dangerous file extension: .{ext}",
                "severity": "CRITICAL",
            })
        
        # Check for executable content
        if content[:2] == b"MZ":
            threats.append({
                "category": "malware",
                "description": "Windows executable detected",
                "severity": "CRITICAL",
            })
        
        if content[:4] == b"\x7fELF":
            threats.append({
                "category": "malware",
                "description": "Linux executable detected",
                "severity": "CRITICAL",
            })
        
        # Check for macros in office documents
        if ext in {"doc", "docm", "xls", "xlsm", "ppt", "pptm"}:
            if b"AutoOpen" in content or b"AutoExec" in content or b"AutoClose" in content:
                threats.append({
                    "category": "malware",
                    "description": "Office macro detected",
                    "severity": "HIGH",
                })
        
        return {
            "safe": len(threats) == 0,
            "threats": threats,
            "threat_count": len(threats),
            "filename": filename,
        }

    def sanitize_output(self, text: str) -> str:
        """Sanitize output to prevent XSS and data leakage."""
        # Strip HTML tags
        text = re.sub(r"<[^>]*>", "", text)
        # Strip script content
        text = re.sub(r"javascript\s*:", "", text, flags=re.IGNORECASE)
        # Strip potential PII
        text = re.sub(r"\b\d{16}\b", "[REDACTED]", text)  # Credit cards
        text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[REDACTED]", text)  # Emails
        return text

    def _get_severity(self, category: str) -> str:
        severities = {
            "injection": "CRITICAL",
            "sql_injection": "CRITICAL",
            "xss": "HIGH",
            "path_traversal": "HIGH",
            "command_injection": "CRITICAL",
            "pii": "MEDIUM",
        }
        return severities.get(category, "MEDIUM")

    def get_stats(self) -> dict[str, Any]:
        """Get antivirus statistics."""
        return {
            "total_scans": self._scan_count,
            "total_blocked": self._blocked_count,
            "recent_threats": self._threats[-10:] if self._threats else [],
            "threat_categories": self._count_categories(),
        }

    def _count_categories(self) -> dict[str, int]:
        counts = {}
        for threat in self._threats:
            for t in threat.get("threats", []):
                cat = t.get("category", "unknown")
                counts[cat] = counts.get(cat, 0) + 1
        return counts

# R-F1007 - wire to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="antivirus", summary="ARIA Antivirus Active", source_id="antivirus:R-F1007")

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

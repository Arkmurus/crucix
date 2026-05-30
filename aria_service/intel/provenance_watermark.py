"""R-F1134 — Provenance watermarking for all external content.

Every piece of external content that enters ARIA's processing pipeline gets a
tamper-evident provenance watermark — a metadata tag that follows it through
every transformation. The watermark is applied by ARIA's code at ingest time
and carried as metadata that the LLM-visible content cannot forge or strip.

When a prompt injection or security threat is detected, the provenance chain
traces it back to the exact source that delivered it, enabling source-level
blocking and forensic analysis.

Watermark structure:
    {
        "source_url": "https://example.com/doc.pdf",
        "source_type": "web_fetch|email_attachment|document_upload|chat_message",
        "fetched_at": "2026-05-30T16:00:00+00:00",
        "source_tier": "1a|1b|2|3",
        "content_hash": "sha256:abc123...",
        "passed_scan": true,
        "scan_results": [...],
        "chain": [  # Previous watermarks if this content was transformed
            {"source_url": "...", "content_hash": "..."}
        ]
    }

Usage:
    from aria_service.intel.provenance_watermark import (
        Watermark,
        apply_watermark,
        extract_watermark,
        trace_provenance,
        verify_integrity,
    )

    # At ingest:
    wm = await apply_watermark(
        content=pdf_bytes,
        source_url="https://example.com/doc.pdf",
        source_type="web_fetch",
        source_tier="2",
    )

    # When a threat is detected:
    chain = trace_provenance(wm)
    # -> [source_url, original_url, ...]
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("aria.provenance_watermark")

# The watermark tag that identifies provenance metadata in content
# This is placed OUT-OF-BAND from the content itself — it's metadata,
# not part of the LLM-visible text.
WATERMARK_HEADER = "---ARIA-PROVENANCE-BLOCK---"
WATERMARK_FOOTER = "---END-ARIA-PROVENANCE-BLOCK---"

# Regex to extract watermark from content
_WATERMARK_RE = re.compile(
    re.escape(WATERMARK_HEADER) + r"\n(.+?)\n" + re.escape(WATERMARK_FOOTER),
    re.DOTALL,
)

# Source tier labels
SOURCE_TIERS = {"1a", "1b", "2", "3", "4"}

# Source types
SOURCE_TYPES = {"web_fetch", "email_attachment", "document_upload",
                "chat_message", "api_response", "crawl_result"}


class Watermark:
    """Provenance watermark for external content.

    Tamper-evident: any modification to the watermark or the content it
    describes will cause verify_integrity() to fail.
    """

    def __init__(
        self,
        source_url: str,
        source_type: str,
        source_tier: str = "3",
        content_hash: str = "",
        passed_scan: bool = True,
        scan_results: Optional[list[dict[str, Any]]] = None,
        chain: Optional[list[dict[str, Any]]] = None,
        fetched_at: Optional[str] = None,
    ):
        self.source_url = source_url
        self.source_type = source_type
        self.source_tier = source_tier
        self.content_hash = content_hash
        self.passed_scan = passed_scan
        self.scan_results = scan_results or []
        self.chain = chain or []
        self.fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "source_type": self.source_type,
            "source_tier": self.source_tier,
            "content_hash": self.content_hash,
            "passed_scan": self.passed_scan,
            "scan_results": self.scan_results,
            "chain": self.chain,
            "fetched_at": self.fetched_at,
        }

    def to_block(self) -> str:
        """Render the watermark as an out-of-band metadata block."""
        data = json.dumps(self.to_dict(), indent=2)
        return f"{WATERMARK_HEADER}\n{data}\n{WATERMARK_FOOTER}"

    @classmethod
    def from_block(cls, block: str) -> Optional[Watermark]:
        """Parse a watermark from a metadata block string."""
        try:
            data = json.loads(block)
            return cls(
                source_url=data.get("source_url", ""),
                source_type=data.get("source_type", ""),
                source_tier=data.get("source_tier", "3"),
                content_hash=data.get("content_hash", ""),
                passed_scan=data.get("passed_scan", True),
                scan_results=data.get("scan_results", []),
                chain=data.get("chain", []),
                fetched_at=data.get("fetched_at"),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug("[provenance_watermark] Failed to parse block: %s", e)
            return None

    @classmethod
    def from_content(cls, content: str) -> Optional[Watermark]:
        """Extract a watermark from content that contains a provenance block."""
        m = _WATERMARK_RE.search(content)
        if m:
            return cls.from_block(m.group(1))
        return None


def _compute_content_hash(data: bytes) -> str:
    """Compute a SHA-256 hash of content."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _validate_source_type(source_type: str) -> str:
    """Validate and normalize source type."""
    if source_type not in SOURCE_TYPES:
        logger.warning(
            "[provenance_watermark] Unknown source type '%s', using 'web_fetch'",
            source_type,
        )
        return "web_fetch"
    return source_type


def _validate_source_tier(tier: str) -> str:
    """Validate and normalize source tier."""
    if tier not in SOURCE_TIERS:
        return "3"
    return tier


async def apply_watermark(
    content: bytes,
    source_url: str,
    source_type: str = "web_fetch",
    source_tier: str = "3",
    passed_scan: bool = True,
    scan_results: Optional[list[dict[str, Any]]] = None,
    parent_watermark: Optional[Watermark] = None,
) -> Watermark:
    """Apply a provenance watermark to external content.

    Args:
        content: The raw bytes of the content being ingested.
        source_url: The URL or source identifier where this content came from.
        source_type: Type of source (web_fetch, email_attachment, etc.).
        source_tier: Source tier (1a, 1b, 2, 3, 4).
        passed_scan: Whether the content passed security scanning.
        scan_results: Results from content scanning (if any threats found).
        parent_watermark: If this content was derived from another piece of
            content, the parent's watermark (for chain tracking).

    Returns:
        Watermark object that can be attached to the content.
    """
    content_hash = _compute_content_hash(content)
    source_type = _validate_source_type(source_type)
    source_tier = _validate_source_tier(source_tier)

    chain: list[dict[str, Any]] = []
    if parent_watermark:
        chain = list(parent_watermark.chain)
        chain.append({
            "source_url": parent_watermark.source_url,
            "content_hash": parent_watermark.content_hash,
            "fetched_at": parent_watermark.fetched_at,
        })

    wm = Watermark(
        source_url=source_url,
        source_type=source_type,
        source_tier=source_tier,
        content_hash=content_hash,
        passed_scan=passed_scan,
        scan_results=scan_results or [],
        chain=chain,
    )

    # Wire to brain
    try:
        from .engine_wiring import wire_success
        wire_success(
            module="provenance_watermark",
            summary=f"Watermark applied: {source_url[:80]}",
            detail=(
                f"Type: {source_type}, Tier: {source_tier}, "
                f"Hash: {content_hash[:20]}, "
                f"Chain length: {len(chain)}"
            ),
            confidence="CONFIRMED",
            source_id=f"provenance_watermark:{source_url[:40]}",
        )
    except Exception:
        logger.debug("[provenance_watermark] brain wiring failed", exc_info=True)

    return wm


def extract_watermark(content: str) -> Optional[Watermark]:
    """Extract a provenance watermark from content.

    Searches for the provenance block in the content and parses it.
    Returns None if no valid watermark is found.
    """
    return Watermark.from_content(content)


def trace_provenance(watermark: Watermark) -> list[dict[str, str]]:
    """Trace the full provenance chain of a piece of content.

    Returns an ordered list from most recent to original source.
    """
    chain = [{
        "source_url": watermark.source_url,
        "source_type": watermark.source_type,
        "source_tier": watermark.source_tier,
        "content_hash": watermark.content_hash,
        "fetched_at": watermark.fetched_at,
    }]
    chain.extend(watermark.chain)
    return chain


def verify_integrity(content: bytes, watermark: Watermark) -> bool:
    """Verify that the content matches the watermark's hash.

    Returns True if the content hash matches, False if the content was
    modified after the watermark was applied (tamper evidence).
    """
    current_hash = _compute_content_hash(content)
    return current_hash == watermark.content_hash


def strip_watermark(content: str) -> str:
    """Remove the provenance watermark block from content.

    This is used before presenting content to the LLM — the watermark
    is metadata, not part of the analysis text.
    """
    return _WATERMARK_RE.sub("", content).strip()


async def report_injection_source(
    watermark: Watermark,
    injection_type: str = "prompt_injection",
) -> None:
    """When a prompt injection is detected, trace it to its source.

    Records the source in the brain and optionally blocks the source
    from future fetches.

    Args:
        watermark: The watermark of the content that contained the injection.
        injection_type: Type of injection detected.
    """
    chain = trace_provenance(watermark)

    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="provenance_watermark",
            detail=(
                f"Injection source traced: {watermark.source_url} "
                f"(type: {injection_type}, tier: {watermark.source_tier}, "
                f"chain: {' -> '.join(c['source_url'][:40] for c in chain)})"
            ),
            gap_type="security_threat",
            source=f"provenance_watermark:{watermark.source_url[:40]}",
        )
    except Exception:
        logger.debug("[provenance_watermark] brain wiring failed", exc_info=True)

    logger.warning(
        "[provenance_watermark] Injection detected from %s (type=%s, tier=%s)",
        watermark.source_url, injection_type, watermark.source_tier,
    )

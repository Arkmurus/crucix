"""Citation validator — deterministic post-synthesis check.

R-F1633 (2026-06-17): after the LLM generates a response, extract every
inline '[from <url>]' / '[from <source>]' marker and verify each cited
source was actually gathered THIS turn. Any citation pointing to a source
not in the allowed set is stripped or downgraded.

This is a DETERMINISTIC guard — it cannot be prompted around. It directly
fixes the hallucinated-citation problem (e.g. LLM attaching a random
Routledge DOI to a claim about its own ghost-score methodology).

Usage:
    from .citation_validator import validate_citations
    cleaned = validate_citations(response_text, gathered_sources)
"""

from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import logging
import re
from typing import Set

logger = logging.getLogger("aria.citation_validator")

# Matches [from <url-or-source>] markers — case-insensitive prefix.
_CITATION_MARKER = re.compile(
    r"\[from\s+(.+?)\]",
    re.IGNORECASE,
)


def _extract_gathered_sources(
    tool_context: str = "",
    attached_doc: str = "",
    rag_hits: list[str] | None = None,
    dd_press_urls: list[str] | None = None,
    dd_citation_urls: list[str] | None = None,
) -> set[str]:
    """Build the set of ALLOWED sources for this turn.

    These are sources the system actually gathered — tool results,
    attached documents, RAG hits, DD layer outputs. Any citation
    pointing outside this set is unsupported.

    Args:
        tool_context: The raw tool context block (contains fetched URLs).
        attached_doc: The attached document body (if any).
        rag_hits: URLs of RAG documents actually retrieved.
        dd_press_urls: URLs of press articles gathered by DD layer.
        dd_citation_urls: URLs of citation sources gathered by DD layer.

    Returns:
        Set of allowed URL/source strings (lowercased for matching).
    """
    allowed: set[str] = set()

    # Tool context — extract all http URLs
    if tool_context:
        for m in re.finditer(r"https?://[^\s\"'>)\]]+", tool_context):
            allowed.add(m.group().rstrip(".,;:!?)").lower())

    # Attached document marker — match any [from ATTACHED DOCUMENT: ...] variant
    if attached_doc:
        allowed.add("[attached document]")
        allowed.add("attached document")
        allowed.add("attached document:")

    # RAG hits
    for url in (rag_hits or []):
        allowed.add(url.strip().lower())

    # DD press coverage URLs
    for url in (dd_press_urls or []):
        allowed.add(url.strip().lower())

    # DD citation URLs
    for url in (dd_citation_urls or []):
        allowed.add(url.strip().lower())

    return allowed


def validate_citations(
    response: str,
    *,
    tool_context: str = "",
    attached_doc: str = "",
    rag_hits: list[str] | None = None,
    dd_press_urls: list[str] | None = None,
    dd_citation_urls: list[str] | None = None,
) -> str:
    """Strip or downgrade unsupported citations from a response.

    Every '[from <url>]' marker in the response is checked against the
    set of sources actually gathered this turn. If the cited source is
    not in the allowed set, the marker is replaced with
    '[UNVERIFIED — source not in gathered evidence]'.

    Args:
        response: The LLM-generated response text.
        tool_context: Raw tool context block (contains fetched URLs).
        attached_doc: Attached document body (if any).
        rag_hits: URLs of RAG documents actually retrieved.
        dd_press_urls: URLs of press articles gathered by DD layer.
        dd_citation_urls: URLs of citation sources gathered by DD layer.

    Returns:
        Response with unsupported citations stripped/downgraded.
    """
    if not response or not response.strip():
        return response

    allowed = _extract_gathered_sources(
        tool_context=tool_context,
        attached_doc=attached_doc,
        rag_hits=rag_hits,
        dd_press_urls=dd_press_urls,
        dd_citation_urls=dd_citation_urls,
    )

    if not allowed:
        # No sources gathered this turn — any citation is unsupported.
        # Strip all citation markers.
        cleaned = _CITATION_MARKER.sub("", response)
        if cleaned != response:
            logger.info(
                "citation_validator: stripped %d citation(s) — no sources gathered this turn",
                len(_CITATION_MARKER.findall(response)),
            )
        return cleaned

    def _replace(m: re.Match) -> str:
        cited = m.group(1).strip().lower()
        # Check if the cited source is in the allowed set
        # Try exact match first, then domain-level match for URLs
        if cited in allowed:
            return m.group(0)  # Keep the citation

        # Attached document citation — check if it starts with an allowed prefix
        if cited.startswith("attached document"):
            for prefix in ("[attached document]", "attached document", "attached document:"):
                if prefix in allowed:
                    return m.group(0)  # Keep it — attached doc was provided

        # For URLs, check if the domain matches or is a subdomain of an allowed URL
        if cited.startswith("http"):
            cited_domain = cited.split("/")[2] if "://" in cited else ""
            if cited_domain:
                # Strip leading www. for comparison
                cited_domain_stripped = cited_domain.removeprefix("www.")
                for allowed_url in allowed:
                    if allowed_url.startswith("http"):
                        allowed_domain = allowed_url.split("/")[2] if "://" in allowed_url else ""
                        if allowed_domain:
                            allowed_stripped = allowed_domain.removeprefix("www.")
                            # Exact match OR cited is a subdomain of allowed
                            if cited_domain_stripped == allowed_stripped or cited_domain_stripped.endswith("." + allowed_stripped):
                                return m.group(0)  # Domain match — keep it

        # Unsupported citation — downgrade
        logger.info(
            "citation_validator: stripped unsupported citation [from %s]",
            m.group(1)[:100],
        )
        return "[UNVERIFIED \u2014 source not in gathered evidence]"

    cleaned = _CITATION_MARKER.sub(_replace, response)
    return cleaned

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="citation_validator",
                     summary="citation_validator module active",
                     source_id="citation_validator:init")
    except Exception:
        pass

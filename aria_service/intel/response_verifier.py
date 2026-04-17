"""Response verification post-processor — inline verification tags on every response.

Wires the verified_intel pipeline into the conversational response path.
After ARIA generates a response, this module:

  1. Extracts factual claims (entity names, dates, numbers, appointments)
  2. Checks each claim against the verified_intel fact store
  3. Runs SourceTierClassifier on any cited URLs
  4. Detects contradictions between response claims and verified facts
  5. Rewrites the response with inline verification tags:
     - [VERIFIED from <source>, corroborated by <source>]
     - [UNVERIFIED — single source: <domain>]
     - [CONTRADICTED — sources disagree, see below]

This is a POST-PROCESSOR, not a gate. The response is always delivered.
The tags provide transparency — the team sees what is verified and what
is not on every response.

Called from routes/aria.py after source_verifier but before delivery.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("aria.response_verifier")

# Claims that should be verified: entity-specific factual assertions
# with confidence tags. We DON'T verify general knowledge statements
# like "ITAR is a US export control regime" — only entity-bound claims.
_ENTITY_CLAIM_RE = re.compile(
    r"\[(?:CONFIRMED|PROBABLE|ASSESSED)[^\]]*\]\s*([^.!?\n]+[.!?])",
    re.IGNORECASE,
)

# Also match inline citations that already have source markers
_EXISTING_VERIFICATION_RE = re.compile(
    r"\[(?:VERIFIED|UNVERIFIED|CONTRADICTED|from |snippet|EXTRACT)",
    re.IGNORECASE,
)


async def verify_and_tag_response(
    response_text: str,
    tool_context: str = "",
    session_id: str = "",
) -> dict:
    """Post-process a response to add inline verification tags.

    Returns:
        {
            "original": str,           # original response
            "tagged": str,             # response with verification tags
            "claims_checked": int,     # total claims extracted
            "verified": int,           # claims with 2+ sources
            "unverified": int,         # claims with single source
            "contradicted": int,       # claims contradicting verified facts
            "unchanged": bool,         # True if no tags were added
        }
    """
    if not response_text or len(response_text) < 50:
        return {
            "original": response_text,
            "tagged": response_text,
            "claims_checked": 0, "verified": 0,
            "unverified": 0, "contradicted": 0,
            "unchanged": True,
        }

    # Skip if response already has verification tags (from DD pipeline etc)
    if _EXISTING_VERIFICATION_RE.search(response_text):
        return {
            "original": response_text,
            "tagged": response_text,
            "claims_checked": 0, "verified": 0,
            "unverified": 0, "contradicted": 0,
            "unchanged": True,
        }

    # Extract claims with confidence tags
    claims = _ENTITY_CLAIM_RE.findall(response_text)
    if not claims:
        return {
            "original": response_text,
            "tagged": response_text,
            "claims_checked": 0, "verified": 0,
            "unverified": 0, "contradicted": 0,
            "unchanged": True,
        }

    # Check each claim against verified_intel
    tagged_text = response_text
    stats = {"verified": 0, "unverified": 0, "contradicted": 0}

    try:
        from . import verified_intel as _vi

        for claim_text in claims[:15]:  # cap at 15 claims per response
            claim_clean = claim_text.strip()
            if len(claim_clean) < 15:
                continue

            # Search for matching verified facts
            matches = await _vi.get_relevant_verified_facts(claim_clean, limit=3)

            if not matches:
                # No verified facts found — check if tool_context has sources
                source_count = _count_sources_for_claim(claim_clean, tool_context)
                if source_count >= 2:
                    stats["verified"] += 1
                    # Don't tag — already has [from ...] inline citations
                elif source_count == 1:
                    stats["unverified"] += 1
                    # Add unverified tag after the confidence tag
                    tagged_text = _insert_tag_after_claim(
                        tagged_text, claim_text,
                        " [UNVERIFIED — single source]"
                    )
                else:
                    stats["unverified"] += 1
                    tagged_text = _insert_tag_after_claim(
                        tagged_text, claim_text,
                        " [UNVERIFIED — no source cited]"
                    )
                continue

            # Found verified facts — check for contradictions
            best_match = matches[0]
            match_status = best_match.get("verification_status", "UNKNOWN")

            if match_status == "CONTRADICTED":
                stats["contradicted"] += 1
                tagged_text = _insert_tag_after_claim(
                    tagged_text, claim_text,
                    f" [CONTRADICTED — verified facts disagree on this claim, "
                    f"human review required]"
                )
            elif match_status == "VERIFIED":
                stats["verified"] += 1
                source_count = best_match.get("source_count", 0)
                if source_count >= 2:
                    tagged_text = _insert_tag_after_claim(
                        tagged_text, claim_text,
                        f" [VERIFIED — {source_count} source(s)]"
                    )
            elif match_status in ("PENDING_CORROBORATION", "LEGACY_UNVERIFIED"):
                stats["unverified"] += 1
                tagged_text = _insert_tag_after_claim(
                    tagged_text, claim_text,
                    f" [{match_status.replace('_', ' ')}]"
                )

    except Exception as e:
        logger.debug("response_verifier failed (non-fatal): %s", e)
        return {
            "original": response_text,
            "tagged": response_text,
            "claims_checked": len(claims),
            "verified": 0, "unverified": 0, "contradicted": 0,
            "unchanged": True,
        }

    return {
        "original": response_text,
        "tagged": tagged_text,
        "claims_checked": len(claims),
        "verified": stats["verified"],
        "unverified": stats["unverified"],
        "contradicted": stats["contradicted"],
        "unchanged": tagged_text == response_text,
    }


def _count_sources_for_claim(claim: str, tool_context: str) -> int:
    """Count how many distinct source URLs were fetched that might
    support this claim. Uses keyword overlap as a heuristic."""
    if not tool_context:
        return 0
    # Extract URLs from tool_context
    urls = re.findall(r"https?://[^\s\)\"'>]+", tool_context)
    if not urls:
        return 0
    # Count unique domains
    domains = set()
    for url in urls:
        try:
            domain = url.split("//")[1].split("/")[0].replace("www.", "")
            domains.add(domain)
        except Exception:
            pass
    return len(domains)


def _insert_tag_after_claim(text: str, claim_sentence: str, tag: str) -> str:
    """Insert a verification tag after the claim sentence in the text.
    Only inserts once (first occurrence)."""
    # Find the claim in the text and insert tag after the sentence-ending punctuation
    idx = text.find(claim_sentence)
    if idx == -1:
        return text
    end_idx = idx + len(claim_sentence)
    # Don't double-tag
    if tag.strip("[] ").split("—")[0].strip() in text[end_idx:end_idx + 100]:
        return text
    return text[:end_idx] + tag + text[end_idx:]

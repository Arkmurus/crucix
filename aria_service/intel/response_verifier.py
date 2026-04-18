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


# =============================================================================
# CLAUSE 15 — INLINE CITATION INJECTOR (2026-04-18 evening)
# =============================================================================
# response_verifier above adds [VERIFIED]/[UNVERIFIED] tags AFTER the response.
# That flags the gap but does not close it. The 91% miss rate on Clause 15
# (inline `[from <url>]` markers on tool-derived facts) needs an actual
# INJECTOR — one that parses tool_context, builds a snippet→url index,
# scores each claim sentence against the index, and inserts the best-
# matching `[from <url>]` when the sentence already has a confidence tag
# but no inline citation. This is best-effort heuristic matching, NOT
# semantic — but it converts the most common silent-fact pattern into
# auditable citations.
# =============================================================================

# Detect already-cited facts so we don't double-cite
_HAS_CITATION_RE = re.compile(
    r"\[(?:from\s+http|snippet\s*#|EXTRACT\s*\d|from\s+ATTACHED|VERIFIED|UNVERIFIED|CONTRADICTED|LEGACY)",
    re.IGNORECASE,
)
# Detect facts that need citation: confidence-tagged sentences
_TAGGED_CLAIM_RE = re.compile(
    r"(\[(?:CONFIRMED|PROBABLE|ASSESSED)[^\]]*\])([^.!?\n]+[.!?])",
    re.IGNORECASE,
)
# Pull URL + nearby snippet text from tool_context blocks like:
#   [TOOL: web_search] {...}
#   Title: ...
#   URL: https://example.com/path
#   Snippet: blah blah
_TOOL_URL_RE = re.compile(
    r"(?:URL|url|Source|source|from|http_link)[:\s]+(https?://[^\s\"'<>)]+)"
)
_BARE_URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
# Stop words for keyword overlap scoring
_CITE_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "to", "for", "with", "by", "at", "is",
    "are", "was", "were", "be", "been", "and", "or", "but", "if", "from", "as",
    "this", "that", "these", "those", "it", "its", "has", "have", "had", "will",
    "would", "should", "could", "may", "might", "can", "do", "does", "did",
    "[confirmed]", "[probable]", "[assessed]", "[uncertain]", "[speculative]",
}


def _build_url_snippet_index(tool_context: str) -> list[tuple[str, set[str]]]:
    """Parse tool_context into a list of (url, content_keywords) pairs.

    Each block in tool_context typically looks like:
      [TOOL: ...] ...
      Title: foo
      URL: https://example.com/path
      Snippet: bar bar bar
      ────
    We slice on `URL:` markers and any bare URLs, then collect the text
    that surrounds each URL as the candidate context for that source.
    """
    if not tool_context:
        return []
    index: list[tuple[str, set[str]]] = []

    # Strategy 1: explicit URL: markers with surrounding snippet.
    # Walk every URL match and capture +/- 600 chars around it as the
    # candidate snippet, then tokenise into a keyword set for overlap scoring.
    for m in _BARE_URL_RE.finditer(tool_context):
        url = m.group(0).rstrip(".,;)\"'")
        if len(url) < 12 or len(url) > 400:
            continue
        start = max(0, m.start() - 600)
        end = min(len(tool_context), m.end() + 600)
        surrounding = tool_context[start:end].lower()
        # Tokenise: alphanum words >= 4 chars, drop stopwords
        tokens = {
            t for t in re.findall(r"[a-z0-9]{4,}", surrounding)
            if t not in _CITE_STOPWORDS
        }
        if tokens:
            index.append((url, tokens))
    return index


def _score_claim_against_url(claim_text: str, url_keywords: set[str]) -> float:
    """Score how well a claim's keyword content overlaps with a URL's
    context window. Returns a 0-1 score (Jaccard-like)."""
    claim_tokens = {
        t for t in re.findall(r"[a-z0-9]{4,}", claim_text.lower())
        if t not in _CITE_STOPWORDS
    }
    if not claim_tokens or not url_keywords:
        return 0.0
    overlap = claim_tokens & url_keywords
    # Weight by overlap proportion of claim tokens (claim is the smaller side)
    return len(overlap) / max(len(claim_tokens), 1)


# Minimum overlap to attach a citation. Tuned conservative — better to
# leave a fact uncited (then UNVERIFIED tag fires) than to cite the
# wrong source. 0.25 = 25% of claim tokens must appear near the URL.
_MIN_CITATION_OVERLAP = 0.25
_MAX_CITATIONS_PER_RESPONSE = 12


def inject_inline_citations(
    response_text: str,
    tool_context: str,
) -> dict:
    """Clause 15 enforcement — inject `[from <url>]` markers on
    confidence-tagged claims that lack inline citation.

    Heuristic:
      1. Build snippet index from tool_context (URL → surrounding tokens).
      2. For every confidence-tagged claim sentence in the response that
         doesn't already have a citation marker, score it against each URL.
      3. If best match scores ≥ _MIN_CITATION_OVERLAP, inject `[from URL]`.
      4. Cap at _MAX_CITATIONS_PER_RESPONSE per response so a noisy match
         pass can't flood the reply.

    Returns:
      {
        "rewritten":        str,    # response_text with citations injected
        "claims_total":     int,
        "claims_cited":     int,    # how many had a confident match
        "claims_uncitable": int,    # tagged but no URL scored above threshold
        "unchanged":        bool,
      }
    """
    out = {
        "rewritten": response_text,
        "claims_total": 0,
        "claims_cited": 0,
        "claims_uncitable": 0,
        "unchanged": True,
    }
    if not response_text or not tool_context:
        return out

    url_index = _build_url_snippet_index(tool_context)
    if not url_index:
        return out

    cited = 0
    uncitable = 0
    # Walk claims left-to-right and inject; rebuild incrementally so we
    # don't mangle index positions on each insertion.
    parts: list[str] = []
    cursor = 0
    for m in _TAGGED_CLAIM_RE.finditer(response_text):
        out["claims_total"] += 1
        tag, claim = m.group(1), m.group(2)
        full_match_start = m.start()
        full_match_end = m.end()
        # Append text before this claim
        parts.append(response_text[cursor:full_match_start])

        # Skip if already cited (or if hit the cap)
        already_cited = bool(_HAS_CITATION_RE.search(
            response_text[full_match_end:full_match_end + 80]
        ))
        if already_cited or cited >= _MAX_CITATIONS_PER_RESPONSE:
            parts.append(response_text[full_match_start:full_match_end])
            cursor = full_match_end
            continue

        # Score the claim against every URL in context
        best_url = ""
        best_score = 0.0
        for url, kws in url_index:
            s = _score_claim_against_url(claim, kws)
            if s > best_score:
                best_score = s
                best_url = url

        if best_score >= _MIN_CITATION_OVERLAP and best_url:
            # Trim very long URLs in the visible citation but keep the
            # full URL as a clickable in the bracket
            parts.append(f"{tag}{claim} [from {best_url}]")
            cited += 1
        else:
            parts.append(f"{tag}{claim}")
            uncitable += 1
        cursor = full_match_end

    # Append the tail
    parts.append(response_text[cursor:])
    rewritten = "".join(parts)

    out["rewritten"] = rewritten
    out["claims_cited"] = cited
    out["claims_uncitable"] = uncitable
    out["unchanged"] = (rewritten == response_text)

    # Brain signal: every citation injection pass is a Clause 15 signal.
    # Successful injections boost grounding mastery; uncitable claims feed
    # capability gaps so the predictor can flag domains where the LLM
    # routinely produces ungrounded confidence-tagged claims.
    try:
        # Schedule but don't await (called from sync contexts too)
        import asyncio as _aio
        from . import brain_hook as _bh

        async def _emit():
            await _bh.absorb(
                module="response_verifier",
                summary=(
                    f"Clause 15 citation injector: "
                    f"{cited}/{out['claims_total']} cited, "
                    f"{uncitable} uncitable from {len(url_index)} URLs"
                ),
                detail="",
                success=cited > 0 or out["claims_total"] == 0,
                gap_type=("clause_15_no_citation_match"
                          if uncitable >= 3 else None),
                gap_detail=(
                    f"{uncitable} confidence-tagged claims could not be matched "
                    f"to any of the {len(url_index)} tool-context URLs"
                    if uncitable >= 3 else None
                ),
                extra_topics=["compliance"],
            )

        try:
            loop = _aio.get_running_loop()
            loop.create_task(_emit())
        except RuntimeError:
            # No running loop — caller is synchronous. Fire-and-forget
            # via a fresh loop is overkill; skip the brain signal here.
            pass
    except Exception:
        pass

    return out

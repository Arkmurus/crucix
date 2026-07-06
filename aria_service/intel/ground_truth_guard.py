"""Ground-truth guard — enforce that factual claims trace back to the extract.

Past incident 2026-04-18 — CSG Group
────────────────────────────────────
User asked about csg.com/en. extract_url ran and pulled the verbatim
page text, which clearly said "Czechoslovak Group" and "heart of
Europe". The LLM synthesis confidently wrote:

    Jurisdiction: Turkey (HQ). [CONFIRMED — from website content
    describing Turkish operations and sectors]

Pure confabulation. The acronym "CSG" triggered a pattern-match in
training to "Çelikler Savunma Grubu" (a different, Turkish company),
and the LLM overrode the extract. The prompt footer even explicitly
said "Do NOT invent jurisdictions" (aria.py:3221, Clause 14) — the
LLM ignored it. Prompts alone don't prevent this class of bug.

What this guard does
────────────────────
Post-generation, it:
  1. Scans the LLM's reply for specific factual claims — jurisdiction,
     HQ, headquartered-in, founded year, acronym expansions, revenue
     figures, named executives.
  2. For each claim that's tagged [CONFIRMED] or [from TOOL: ...] — i.e.
     claims the LLM says are grounded in extracted evidence — searches
     the ACTUAL extract text (tool_context) for the asserted value.
  3. If the value isn't present (fuzzy match with normalisation), the
     claim is rewritten inline to UNVERIFIED with a clear correction
     note. The mistake ledger records it as a hallucination so the
     predictor downgrades confidence on similar turns.
  4. If the extract CONTAINS a competing / contradictory value for the
     same slot (e.g. "Czechoslovak" present but LLM claimed "Turkey"),
     the correction explicitly calls that out.

Scope
─────
Only runs when a TOOL: extract block is present in the prompt context
AND the response includes [CONFIRMED] / [from TOOL:] tags. Pure-LLM
turns (no tools fired) are outside this guard's purview — those are
covered by the existing tool_claim_guard / commitment_guard.

Public API
──────────
  async verify(response_text, tool_context, user_message="") -> dict

Returns:
    {
        "original": str,
        "guarded": str,
        "violations_found": int,
        "violations": [{"pattern_type", "claim", "extract_has", "corrected"}],
        "changed": bool,
        "skipped_reason": str | None,
    }
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import re
from typing import Any

logger = logging.getLogger("aria.ground_truth_guard")


# ── Claim-extraction patterns ──────────────────────────────────────────────
# Each pattern captures:
#   (1) the slot name (e.g. "Jurisdiction", "HQ")
#   (2) the asserted value (the thing we need to verify against the extract)
#
# Patterns intentionally match claims tagged with confidence markers
# because those are the dangerous ones — an "I think" claim isn't
# pretending to be grounded. A "[CONFIRMED — from TOOL: read_article]"
# claim IS, so if the value isn't in the TOOL block, it's a lie.

_CLAIM_PATTERNS: list[tuple[str, re.Pattern]] = [
    # Jurisdiction: Turkey (HQ).
    # Jurisdiction — Czech Republic / Slovakia (Headquartered in "the heart of Europe")
    # Sentence-start OR line-start, allows list markers + markdown emphasis.
    ("jurisdiction", re.compile(
        r"(?:^|\n|[.!?]\s+)\s*[*_#>-]*\s*"
        r"(?:jurisdiction|country\s+of\s+origin|country\s+of\s+registration)"
        r"\s*[:—–-]\s*"
        r"([^\n.]+?)"
        r"(?=\s*\.\s|$|\n|\[|\()",
        re.IGNORECASE,
    )),
    # HQ: Istanbul
    # Headquartered in Istanbul, Turkey
    ("hq", re.compile(
        r"\b(?:hq|headquartered|headquarters)\s*(?:in|:|—|at|is)?\s*"
        r"([A-Z][A-Za-z .,-]{2,80}?)"
        r"(?=\s*[.\n\[]|$)",
    )),
    # Based in Turkey
    ("based_in", re.compile(
        r"\bbased\s+in\s+"
        r"([A-Z][A-Za-z .,-]{2,80}?)"
        r"(?=\s*[.\n\[]|$)",
    )),
    # Founded in 1899 / established 1985
    ("founded", re.compile(
        r"\b(?:founded|established|incorporated|formed)\s*(?:in)?\s*"
        r"(\d{4})"
        r"\b",
        re.IGNORECASE,
    )),
    # Acronym expansion: CSG (Çelikler Savunma Grubu)
    # CSG stands for "Czechoslovak Group"
    # CSG: Çelikler Savunma Grubu
    ("acronym_expansion", re.compile(
        r"\b([A-Z]{2,6})\b\s*"
        r"(?:\(|stands\s+for|:|—|is\s+an?\s+acronym\s+for|abbreviates)\s*[\"']?"
        r"([A-Z][A-Za-zÀ-ž][A-Za-zÀ-ž .&/-]{4,80}?)"
        r"[\"']?(?:\)|\.|$|\n)",
    )),
]

# Country/demonym list — extendable. Used to detect a specific
# jurisdiction swap: response says "Turkish" but extract says
# "Czechoslovak" and nothing about "Turkey".
# Aliases must be SPECIFIC — short/ambiguous tokens like " us " match any
# text containing "industrial" or "plus". Keep only word-bounded, fully
# unambiguous strings here. If we need " us " matching later, use a
# dedicated regex with word boundaries, not a substring search.
_COUNTRY_ALIASES: dict[str, list[str]] = {
    "turkey": ["turkey", "turkish", "türkiye", "ankara", "istanbul"],
    "czech_republic": ["czech", "czechia", "czechoslovak", "prague"],
    "slovakia": ["slovak", "slovakia", "bratislava"],
    "germany": ["germany", "german", "deutschland", "berlin", "munich"],
    "france": ["france", "french", "paris"],
    "uk": ["united kingdom", "britain", "british", "england", "english", "london"],
    "usa": ["united states", "america", "american", "washington", "pentagon"],
    "israel": ["israel", "israeli", "tel aviv", "jerusalem"],
    "south_korea": ["south korea", "korean", "seoul"],
    "russia": ["russia", "russian", "moscow", "kremlin"],
    "china": ["china", "chinese", "beijing", "shanghai"],
    "india": ["india", "indian", "delhi", "mumbai"],
    "brazil": ["brazil", "brazilian", "brasilia", "rio de janeiro"],
    "uae": ["united arab emirates", "emirati", "dubai", "abu dhabi"],
    "south_africa": ["south africa", "south african", "johannesburg", "pretoria"],
    "angola": ["angola", "angolan", "luanda"],
    "nigeria": ["nigeria", "nigerian", "abuja", "lagos"],
    "poland": ["poland", "polish", "warsaw"],
    "italy": ["italy", "italian", "rome", "milan"],
    "spain": ["spain", "spanish", "madrid"],
    "romania": ["romania", "romanian", "bucharest"],
    "norway": ["norway", "norwegian", "oslo"],
    "sweden": ["sweden", "swedish", "stockholm"],
    "finland": ["finland", "finnish", "helsinki"],
}


def _normalise(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace — so "türkiye" and
    "Turkey" compare equal after normalisation."""
    import unicodedata
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\s+", " ", t.lower()).strip()
    return t


def _extract_has_value(extract: str, value: str) -> bool:
    """True if value appears in extract after light normalisation. We
    deliberately don't fuzzy-match to 0.7 — a claim of "Turkey" needs
    the word Turkey (or Türkiye) in the extract, not just something
    vaguely similar."""
    if not extract or not value:
        return False
    e = _normalise(extract)
    v = _normalise(value)
    if len(v) < 3:
        return False
    # Exact substring
    if v in e:
        return True
    # Try each whitespace-separated token for multi-word values
    tokens = [t for t in v.split() if len(t) > 3]
    if tokens and all(t in e for t in tokens):
        return True
    return False


def _extract_has_country(extract: str, country_key: str) -> bool:
    """True if any alias of the country appears in extract as a WHOLE word.

    Critical: substring matching breaks on "oslo" ⊂ "czechoslovak",
    "paris" ⊂ "parisian-anything", etc. We compile a word-bounded regex
    per alias so "oslo" only matches the standalone word.
    """
    e = _normalise(extract)
    for alias in _COUNTRY_ALIASES.get(country_key, []):
        alias_n = _normalise(alias)
        # Word-bounded match — use regex because the alias may contain
        # spaces (e.g. "united kingdom"), which \b handles correctly.
        if re.search(rf"\b{re.escape(alias_n)}\b", e):
            return True
    return False


def _country_key_for_value(value: str) -> str | None:
    """Map 'Turkey (HQ)' / 'turkish' → 'turkey' key. None if no match."""
    v = _normalise(value)
    for key, aliases in _COUNTRY_ALIASES.items():
        for alias in aliases:
            alias_n = _normalise(alias)
            if re.search(rf"\b{re.escape(alias_n)}\b", v):
                return key
    return None


def _contradictory_countries(response_claim: str, extract: str) -> list[str]:
    """Return a list of country keys that ARE in the extract but are
    DIFFERENT from the one the response claims. Used for the "we said
    Turkey but extract only mentions Czechoslovak" correction."""
    claimed_key = _country_key_for_value(response_claim)
    if not claimed_key:
        return []
    found_in_extract: list[str] = []
    for key in _COUNTRY_ALIASES:
        if key == claimed_key:
            continue
        if _extract_has_country(extract, key):
            found_in_extract.append(key)
    return found_in_extract


# ── The guard ──────────────────────────────────────────────────────────────

_CONFIRMED_TAG_NEAR_RE = re.compile(
    r"\[(?:CONFIRMED|PROBABLE|from\s+TOOL)[^\]]*\]",
    re.IGNORECASE,
)

_SKIP_IF_EXTRACT_SHORTER_THAN = 150  # chars — below this, there's no real extract
_MIN_RESPONSE_LENGTH = 80  # don't run on trivial responses


async def verify(
    response_text: str,
    tool_context: str,
    user_message: str = "",
) -> dict:
    """Verify factual claims in response_text against tool_context.

    Args:
        response_text: The LLM's reply (what the user will see).
        tool_context: Full TOOL: block text that was in the prompt —
            this is the ground truth the LLM was supposed to cite.
        user_message: The user's question (for context in the mistake
            ledger + operator nudges).

    Returns the guard result dict. Caller applies `guarded` to the
    response if `changed` is True.
    """
    out = {
        "original": response_text,
        "guarded": response_text,
        "violations_found": 0,
        "violations": [],
        "changed": False,
        "skipped_reason": None,
    }

    if not response_text or len(response_text) < _MIN_RESPONSE_LENGTH:
        out["skipped_reason"] = "response-too-short"
        return out

    if not tool_context or len(tool_context) < _SKIP_IF_EXTRACT_SHORTER_THAN:
        # No meaningful extract to verify against — this guard doesn't
        # apply. (Pure-LLM turns are covered by tool_claim_guard instead.)
        out["skipped_reason"] = "no-tool-extract"
        return out

    guarded = response_text
    violations: list[dict] = []

    for slot, pattern in _CLAIM_PATTERNS:
        for match in pattern.finditer(guarded):
            full_match = match.group(0)
            # Only enforce on CONFIRMED/from-TOOL claims — "I think X"
            # claims are honest uncertainty, not hallucination.
            nearby = guarded[max(0, match.start() - 40):match.end() + 200]
            if not _CONFIRMED_TAG_NEAR_RE.search(nearby):
                continue

            asserted_value = (
                match.group(2) if slot == "acronym_expansion" else match.group(1)
            ).strip(" .,'\"()[]—–-")

            if not asserted_value or len(asserted_value) < 2:
                continue

            # Skip if the LLM already correctly cited the extract
            # containing this value
            if _extract_has_value(tool_context, asserted_value):
                continue

            # For jurisdiction / HQ / based_in — check if a DIFFERENT
            # country appears in the extract, so the correction can say
            # what the extract ACTUALLY supports.
            contradictory: list[str] = []
            if slot in ("jurisdiction", "hq", "based_in"):
                contradictory = _contradictory_countries(asserted_value, tool_context)

            # Build the correction
            if contradictory:
                found_names = ", ".join(
                    c.replace("_", " ").title() for c in contradictory[:3]
                )
                correction = (
                    f" [GROUND-TRUTH GUARD: claim '{asserted_value[:60]}' NOT "
                    f"found in extract. Extract actually mentions: "
                    f"{found_names}. Rewriting as UNVERIFIED.]"
                )
            else:
                correction = (
                    f" [GROUND-TRUTH GUARD: claim '{asserted_value[:60]}' NOT "
                    f"verifiable from the extract in this turn. Tagging "
                    f"UNVERIFIED — re-run extract_url or ask operator.]"
                )

            # Insert correction right after the claim. We keep the
            # original text so the reader can see what ARIA originally
            # said — that's important for learning and trust.
            start = match.end()
            guarded = guarded[:start] + correction + guarded[start:]

            violations.append({
                "slot": slot,
                "claim": asserted_value[:200],
                "full_match": full_match[:250],
                "extract_has_contradictory": contradictory,
                "corrected": True,
            })

            # Move cursor past the inserted correction so subsequent
            # iterations don't match the correction text itself
            # (finditer re-scans from the last match.end on the ORIGINAL
            # string buffer, so re-materialise the guarded text for
            # subsequent pattern runs).
            break  # re-scan from scratch for this pattern on next loop

    out["guarded"] = guarded
    out["violations_found"] = len(violations)
    out["violations"] = violations
    out["changed"] = guarded != response_text

    if out["changed"]:
        logger.warning(
            "[ground_truth_guard] %d unverifiable claim(s) corrected "
            "(user_msg head=%r)",
            len(violations),
            (user_message or "")[:80],
        )
        # Feed the brain — each unverifiable claim is a mini-fail that
        # should push mastery down on whichever domain the claim
        # targeted. If the guard keeps firing on 'jurisdiction' claims,
        # the predictor learns to downgrade CONFIRMED tags on
        # jurisdiction-heavy turns.
        try:
            from . import brain_hook as _bh
            slots = [v.get("slot", "unknown") for v in violations]
            await _bh.absorb(
                module="ground_truth_guard",
                summary=(
                    f"{len(violations)} unverifiable claim(s) rewritten: "
                    f"{', '.join(slots[:4])}"
                ),
                detail="\n".join(
                    f"  {v.get('slot')}: '{(v.get('claim') or '')[:80]}' "
                    f"(extract suggests: {v.get('extract_has_contradictory') or 'nothing'})"
                    for v in violations[:3]
                ),
                success=False,
                gap_type="knowledge_gap",
                gap_detail=f"LLM asserted facts not grounded in extract; slots={slots}",
                confidence="CONFIRMED",
            )
        except Exception:
            from .engine_wiring import wire_failure as _wf
            _wf(
                module="ground_truth_guard",
                detail="brain_hook.absorb failed in verify",
                gap_type="engine_failure",
                source="ground_truth_guard:verify",
            )
            pass

    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="ground_truth_guard",
        summary="Verify",
        source_id="ground_truth_guard:R-F1001",
    )

    return out

# R-F2119 §21a — wire failure handler for ground_truth_guard
try:
    wire_failure(module="ground_truth_guard", detail="module shutdown",
                gap_type="engine_failure", source="ground_truth_guard:shutdown")
except Exception:
    pass

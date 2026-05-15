"""R-F411 — prompt-injection guards for user-controlled content.

Pre-R-F411 multiple endpoints flowed user-controlled content into
LLM prompts without delimiter escape:
  - /api/aria/chat ChatRequest.group_context (WhatsApp last 5 msgs)
    → injected inside [GROUP CONTEXT ...] markers. Attacker who
    controls a group message can include `]\\n[NEW INSTRUCTIONS]`
    and break out of the delimiter block.
  - /api/aria/brain/absorb body.summary / detail / entity_name /
    gap_detail → routed to LLM via brain_hook.absorb without any
    field validation. entity_name in particular ends up as a
    canonical key in mastery / capability-gap tracking.

This module exposes two helpers:
  - escape_for_delimiter_block(text) — replace `[`/`]` with the
    Unicode mathematical bracket equivalents `⟦` / `⟧` so the
    text reads naturally to a human but cannot forge a closing
    delimiter. Also collapses suspicious meta-instruction patterns.
  - validate_entity_name(name) — allow alphanumeric + space +
    hyphen + dot + ampersand + apostrophe; reject otherwise.
    Returns (cleaned_name, ok_bool).

Both are conservative: real attacks get neutralised; legitimate
content (corporate names, prose, multilingual chat) passes through
visibly identical.
"""
from __future__ import annotations

import re
import unicodedata

# Mathematical white square brackets — visually similar to ASCII
# brackets but model can't use them to forge a delimiter close.
_MATH_LBRACKET = "⟦"  # ⟦
_MATH_RBRACKET = "⟧"  # ⟧

# Patterns that look like prompt-injection meta-instructions. We do
# NOT remove them silently (that would hide the attack from log
# review); we wrap them in a `[NEUTRALISED:...]` marker so the model
# sees the literal text but reads the marker as "ignore this".
_META_INSTRUCTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions?", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)", re.I),
    re.compile(r"you\s+are\s+now\s+(a\s+)?(different|new)", re.I),
    re.compile(r"system\s*[:=]\s*you\s+(are|must)", re.I),
    re.compile(r"</?\s*(system|instructions?|prompt)\s*>", re.I),
    re.compile(r"\[(?:system|admin|root|new\s+instructions?)\]", re.I),
)

# Validation regex for canonical entity names. Allows letters
# (any script, via unicodedata category L*), digits, space, hyphen,
# dot, ampersand, apostrophe. Rejects ANY other char including
# brackets, quotes, slashes, newlines, control chars.
_ENTITY_NAME_ALLOWED_PUNCT = " -.&'"
_ENTITY_NAME_MAX_LEN = 200


def escape_for_delimiter_block(text: str) -> str:
    """Make `text` safe to inline inside a `[DELIM ...] ... [/DELIM]`
    style block. Replaces ASCII brackets with Unicode equivalents and
    wraps meta-instruction patterns in a neutralisation marker.

    The output is visually almost identical (the math brackets look
    like brackets) and reads naturally to a human reviewer; the model
    cannot use it to forge a delimiter close or hijack the prompt.
    """
    if not text:
        return ""
    # R-F537 (2026-05-15) — neutralise BEFORE replacing ASCII brackets.
    # Pre-R-F537 the order was reversed: `[NEW INSTRUCTIONS]` became
    # `⟦NEW INSTRUCTIONS⟧` first, then the bracket-aware regex
    # `\[(?:system|admin|root|new\s+instructions?)\]` couldn't match
    # the math-bracketed version, so the NEUTRALISED marker never wrapped
    # and the literal phrase leaked into the LLM context. Live evidence:
    # `test_rf411_escape_neutralises_meta_instructions` failed for the
    # `[NEW INSTRUCTIONS]` attack. Neutralising first preserves the
    # delimiter-forge defence below (math-bracket replacement still runs
    # on every ASCII bracket the attack didn't claim).
    out = text
    for pat in _META_INSTRUCTION_PATTERNS:
        out = pat.sub(
            lambda m: f"⟦NEUTRALISED:{m.group(0)}⟧", out,
        )
    out = out.replace("[", _MATH_LBRACKET).replace("]", _MATH_RBRACKET)
    return out


def validate_entity_name(name: str) -> tuple[str, bool]:
    """Sanitise an entity name for canonical use (mastery keys,
    capability gaps, finding titles). Returns (cleaned, ok).

    ok=True only when:
      - non-empty after strip
      - length <= 200 chars
      - every char is alphanumeric (any script) OR a member of
        `_ENTITY_NAME_ALLOWED_PUNCT`
      - no control characters, no brackets, no newlines
    """
    if not name:
        return "", False
    cleaned = (name or "").strip()
    if not cleaned or len(cleaned) > _ENTITY_NAME_MAX_LEN:
        return cleaned, False
    for ch in cleaned:
        if ch in _ENTITY_NAME_ALLOWED_PUNCT:
            continue
        # Letters of any script + digits ok; everything else is out.
        if ch.isalnum():
            continue
        cat = unicodedata.category(ch)
        # Categories starting with L (Lu, Ll, Lt, Lm, Lo — letters)
        # or Nd (decimal digit) are accepted via isalnum above.
        # Anything else (P*=punctuation not in allowed set, S*=symbols,
        # C*=controls/formatting, Z*=separators other than space) is
        # rejected to keep the entity-name key canonical.
        if cat.startswith("L") or cat == "Nd":
            continue
        return cleaned, False
    return cleaned, True

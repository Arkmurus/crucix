"""Robust JSON parsing for LLM outputs.

LLMs (especially DeepSeek and Sonnet on long structured-output tasks)
emit JSON with predictable failure modes:

  1. Markdown code fences: ```json ... ```
  2. Newlines / control characters inside string values
  3. Unquoted dict keys (DeepSeek quirk):  key: value  instead of  "key": value
  4. Single-quoted strings:  'foo'  instead of  "foo"
  5. Truncated mid-token (max_tokens hit during generation)
  6. Trailing commas:  [1, 2, 3,]  or  {"a":1,}
  7. Trailing prose after the closing }

This helper applies a 5-strategy repair cascade modeled on the Node-side
`lib/self/bd_intelligence.mjs` `parseJSON()` function (which the BD module
hardened twice in 2026-04-26 after sweep-side analysis kept dying on
truncated DeepSeek output).

Usage:
    from .llm_json import parse_llm_json
    data = parse_llm_json(rr.text)         # dict on success, None on failure
    data = parse_llm_json(rr.text, default={"facts": []})  # custom default

The function NEVER raises -- a None return means even nuclear-strip
failed. Caller can treat None as "model output unusable, skip this
entry" rather than crashing the surrounding pipeline.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("aria.intel.llm_json")


def _strip_fences(s: str) -> str:
    """Drop ```json ... ``` wrapping that LLMs add even when told not to."""
    s = s.strip()
    s = re.sub(r"^```(?:json|JSON)?\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _extract_json_object(s: str) -> str | None:
    """Greedy-match the outermost JSON value -- {...} or [...]. Whichever
    opener appears first wins, matched against its corresponding closer.
    Returns the substring or None if no JSON shape is present.

    active_challenge_engine and other LLM sites return top-level arrays;
    the previous {-only regex silently dropped those into the default."""
    obj = re.search(r"\{[\s\S]*\}", s)
    arr = re.search(r"\[[\s\S]*\]", s)
    if obj and arr:
        return obj.group() if obj.start() <= arr.start() else arr.group()
    if obj:
        return obj.group()
    if arr:
        return arr.group()
    return None


def _escape_control_chars_in_strings(s: str) -> str:
    """Walk the string and escape \\n, \\r, \\t that appear inside
    double-quoted string values (which is invalid per JSON spec but
    routinely emitted by LLMs that copy newlines from source material).

    This is more careful than a blanket replace because newlines OUTSIDE
    strings are valid JSON formatting -- we only want to fix the ones
    that break parses.
    """
    out_chars: list[str] = []
    in_string = False
    escape = False
    for ch in s:
        if escape:
            out_chars.append(ch)
            escape = False
            continue
        if ch == "\\":
            out_chars.append(ch)
            escape = True
            continue
        if ch == '"':
            out_chars.append(ch)
            in_string = not in_string
            continue
        if in_string:
            if ch == "\n":
                out_chars.append("\\n")
                continue
            if ch == "\r":
                out_chars.append("\\r")
                continue
            if ch == "\t":
                out_chars.append("\\t")
                continue
        out_chars.append(ch)
    return "".join(out_chars)


def _quote_unquoted_keys(s: str) -> str:
    r"""Add quotes around bare dict keys (`foo:` -> `"foo":`). DeepSeek
    sometimes emits these. Matches keys that follow `{` or `,` and are
    a plain identifier."""
    return re.sub(r"([\{,]\s*)([a-zA-Z_]\w*)(\s*:)", r'\1"\2"\3', s)


def _single_to_double_quotes(s: str) -> str:
    """Convert single-quoted strings to double-quoted, preserving
    apostrophes inside existing double-quoted strings.

    A blanket replace would corrupt strings like "Angola's defence" by
    turning the apostrophe into a closing-quote. State-machine walk
    avoids this by only replacing single quotes outside double-quoted
    sections.
    """
    out_chars: list[str] = []
    in_string = False
    escape = False
    for ch in s:
        if escape:
            out_chars.append(ch)
            escape = False
            continue
        if ch == "\\":
            out_chars.append(ch)
            escape = True
            continue
        if ch == '"':
            out_chars.append(ch)
            in_string = not in_string
            continue
        if ch == "'" and not in_string:
            out_chars.append('"')
            continue
        out_chars.append(ch)
    return "".join(out_chars)


def _close_truncated(s: str) -> str | None:
    """Walk the string tracking `{[` opener stack + string state. If the
    LLM ran out of tokens mid-output, append the closing tokens needed
    to make the JSON syntactically valid.

    Handles: truncation inside a string (close the string first),
    trailing key with no value (fill with `null`), trailing comma
    (drop it). Returns the repaired string or None if no truncation
    repair was applicable.
    """
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]"):
            if stack:
                stack.pop()
    pre = s
    suffix = ""
    if in_string:
        suffix = '"'
    else:
        if re.search(r":\s*$", pre):
            suffix = "null"
        else:
            pre = re.sub(r",\s*$", "", pre)
    while stack:
        suffix += stack.pop()
    if suffix:
        return pre + suffix
    return None


def _strip_trailing_commas(s: str) -> str:
    """Remove `,` immediately before `}` or `]` -- valid JS, invalid JSON."""
    return re.sub(r",(\s*[}\]])", r"\1", s)


def _nuclear_clean(s: str) -> str:
    """Last resort: strip control chars + collapse whitespace.
    Matches what bd_intelligence.mjs Fix 5 does."""
    s = re.sub(r"[\x00-\x1f]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def parse_llm_json(text: str, *, default: Any = None) -> Any:
    """Parse LLM-emitted JSON with multi-strategy repair.

    Returns the parsed object on success, or `default` (None by default)
    when even the nuclear strip fails. Never raises.

    Logs a single WARNING with a 200-char preview when all strategies
    fail, so a recurring failure (the same article failing every spider
    pass) shows up in fly logs as one warning per attempt -- not the
    silent retry-forever loop the previous `try/except: pass` produced.
    """
    if not text:
        return default
    cleaned = _strip_fences(text)
    candidate = _extract_json_object(cleaned) or (cleaned if ("{" in cleaned or "[" in cleaned) else None)
    if candidate is None:
        return default

    # Strategy 0: as-is
    try:
        return json.loads(candidate)
    except Exception:
        pass

    # Strategy 1: escape control chars inside strings + drop trailing commas
    try:
        repaired = _strip_trailing_commas(_escape_control_chars_in_strings(candidate))
        return json.loads(repaired)
    except Exception:
        pass

    # Strategy 2: also quote unquoted keys
    try:
        repaired = _quote_unquoted_keys(repaired)
        return json.loads(repaired)
    except Exception:
        pass

    # Strategy 3: also single quotes -> double (state-machine, apostrophe-safe)
    try:
        repaired = _single_to_double_quotes(repaired)
        return json.loads(repaired)
    except Exception:
        pass

    # Strategy 4: stack-based truncation repair
    try:
        closed = _close_truncated(repaired)
        if closed is not None:
            return json.loads(closed)
    except Exception:
        pass

    # Strategy 5: nuclear -- strip control chars + collapse whitespace
    try:
        return json.loads(_nuclear_clean(repaired))
    except Exception as e:
        logger.warning(
            "[llm_json] all 5 repair strategies failed: %s. Preview: %r",
            str(e)[:120], candidate[:200],
        )
        return default

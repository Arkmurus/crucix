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


def _insert_missing_object_commas(s: str) -> str:
    """Insert commas between adjacent object/array boundaries that
    DeepSeek occasionally drops between elements of a list.

    Patterns repaired (none of which are valid JSON): `}{`, `}[`,
    `]{`, `][`. All of these only appear in real LLM output where the
    model dropped a separating comma between two siblings inside the
    same parent array.

    Live evidence 2026-04-29 15:08-15:09: 3 consecutive
    `_analyse_compliance_document` failures with
    `Expecting ',' delimiter: line 188 column 6 (char 7919)`-style
    errors. Walks character-by-character to skip occurrences inside
    string values (where `}{` would be valid content, not structure).
    """
    out: list[str] = []
    in_string = False
    escape = False
    n = len(s)
    for i, ch in enumerate(s):
        out.append(ch)
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
        if ch in ("}", "]"):
            # Find next non-whitespace char
            j = i + 1
            while j < n and s[j] in (" ", "\n", "\r", "\t"):
                j += 1
            if j < n and s[j] in ("{", "["):
                out.append(",")
    return "".join(out)


def _nuclear_clean(s: str) -> str:
    """Last resort: strip control chars + collapse whitespace.
    Matches what bd_intelligence.mjs Fix 5 does."""
    s = re.sub(r"[\x00-\x1f]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s


# R-F472 (2026-05-14): in-process counters for llm_json failure attribution.
# DD-audit P0 #4-extension: parse_llm_json logged a generic WARNING when all
# 5 strategies failed, but didn't surface a counter — so the operator
# couldn't tell which caller (researcher / deep_researcher / active_challenge /
# document_intelligence / correction_learner) was generating the bad JSON,
# nor how often. Now every caller can pass source="<module>" and we accumulate
# in-process tallies that expose() reads for /api/aria/health-style probes.
_R472_FAIL_BY_SOURCE: dict[str, int] = {}
_R472_TOTAL_FAILS: int = 0
_R472_TOTAL_ATTEMPTS: int = 0


def llm_json_stats() -> dict:
    """R-F472: in-process metrics for parse_llm_json. Surfaced via
    /api/aria/llm-json/stats so operators can attribute the WARNING flood."""
    return {
        "total_attempts":   _R472_TOTAL_ATTEMPTS,
        "total_fails":      _R472_TOTAL_FAILS,
        "fail_rate":        (_R472_TOTAL_FAILS / _R472_TOTAL_ATTEMPTS) if _R472_TOTAL_ATTEMPTS else 0,
        "fails_by_source":  dict(_R472_FAIL_BY_SOURCE),
    }


def parse_llm_json(text: str, *, default: Any = None, source: str = "") -> Any:
    """Parse LLM-emitted JSON with multi-strategy repair.

    Returns the parsed object on success, or `default` (None by default)
    when even the nuclear strip fails. Never raises.

    Logs a single WARNING with a 200-char preview when all strategies
    fail, so a recurring failure (the same article failing every spider
    pass) shows up in fly logs as one warning per attempt -- not the
    silent retry-forever loop the previous `try/except: pass` produced.

    R-F472: `source` kwarg attributes failures per caller. Callers should
    pass source="researcher", "deep_researcher", etc. Stats readable via
    llm_json_stats().
    """
    global _R472_TOTAL_ATTEMPTS
    _R472_TOTAL_ATTEMPTS += 1
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

    # Strategy 1b (F93 fix 2026-04-29): insert missing commas between
    # adjacent objects/arrays. DeepSeek's compliance-extraction prompt
    # occasionally drops the separator between two siblings inside the
    # same list (live: char 7919 / line 188-style errors). Run before
    # the unquoted-keys / single-quote strategies because those won't
    # fire on otherwise-clean DeepSeek output.
    try:
        repaired = _insert_missing_object_commas(repaired)
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
        global _R472_TOTAL_FAILS
        _R472_TOTAL_FAILS += 1
        _r472_key = source or "unknown"
        _R472_FAIL_BY_SOURCE[_r472_key] = _R472_FAIL_BY_SOURCE.get(_r472_key, 0) + 1
        logger.warning(
            "[llm_json] R-F472 all 5 repair strategies failed (source=%s, "
            "total_fails=%d): %s. Preview: %r",
            _r472_key, _R472_TOTAL_FAILS, str(e)[:120], candidate[:200],
        )
        return default

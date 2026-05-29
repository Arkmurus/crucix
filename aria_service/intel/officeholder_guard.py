"""ARIA officeholder claim guard.

Code-level enforcement of CONSTITUTION clause 10 (officeholder discipline).
The clause is in the system prompt but the LLM games it under pressure —
the round-4/5 smoke tests produced "[CONFIRMED] Hon. Dominic Nitiwul" for
"current" Ghana defence minister questions even though Mahama's December
2024 election replaced the entire cabinet.

Window-based matching (not sentence-based)
══════════════════════════════════════════
The first version of this module split the response into sentences and
checked each sentence for {title + name + tag} co-occurrence. That failed
on the round-5 test because the LLM wrote "...is *Hon. Dominic Nitiwul.
*[CONFIRMED]**" — the period after "Hon" caused the splitter to fragment
the claim across two pseudo-sentences, separating the title from the name.

Now we scan the full response for every [CONFIRMED] / [PROBABLE] tag, take
a ±200-char window around it, and check whether that window contains BOTH
an officeholder title AND a person-name pattern. Robust to honorifics,
quoted titles, and cross-line phrasing.

Demotion conditions
═══════════════════
A claim is demoted to [UNCERTAIN — <reason>] when EITHER:
  (a) No tool ran in this turn AND/OR no URLs were cited in the verification
      payload — i.e. nothing real backs the claim.
  (b) A calendar date older than 12 months is cited inside the window.

The post-processor also appends a structured WARNING block listing the
demotions so the user sees the enforcement explicitly. Inline replacement
provides the at-the-spot fix; the warning block provides the audit trail.

Behind ARIA_OFFICEHOLDER_GUARD env var (default ON). Disabled → no-op.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

logger = logging.getLogger("aria.officeholder_guard")

# Officeholder titles we recognize. Tight list — common defence/political
# roles only. Extend if false negatives appear in testing.
_OFFICEHOLDER_TITLES = (
    "president",
    "prime minister",
    "minister of defen[cs]e",
    "defen[cs]e minister",
    "minister for defen[cs]e",
    "minister of foreign affairs",
    "foreign minister",
    "minister of interior",
    "interior minister",
    "minister of finance",
    "finance minister",
    "chief of (?:army|navy|air force|defen[cs]e staff)",
    "commander[\\s-]in[\\s-]chief",
    "ambassador to",
    "director (?:of|general)",
    "head of (?:state|government)",
    "secretary of state",
    "secretary[\\s-]general",
    "ministro (?:da|de|do) defesa",
    "ministre de la d[ée]fense",
)
_TITLE_RE = re.compile(
    r"\b(" + "|".join(_OFFICEHOLDER_TITLES) + r")\b",
    re.IGNORECASE,
)

# A "named officeholder" sentence pattern: at least two consecutive
# capitalised tokens (a plausible person name) within 80 chars of an
# officeholder title. Conservative — false positives demote unnecessarily,
# false negatives miss the bug we're trying to catch. We bias toward more
# demotion since the prior risk is reputational.
_NAME_TOKEN = r"[A-Z][a-zà-ÿ'-]{1,30}"
_PERSON_NAME_RE = re.compile(
    r"(?:" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){1,4})"
)

# Confidence tags we may demote.
_DEMOTABLE_TAGS = ("CONFIRMED", "PROBABLE")

# Window radius around each confidence tag — we look this many chars in
# each direction for a title + name + date. ±350 chars covers the typical
# layout where the headline claim is on one line and the supporting
# "Tenure: since YYYY" detail is several bullets below.
_WINDOW_CHARS = 350

# Per-tag matcher: find every [CONFIRMED] or [PROBABLE] in the response.
# We process them in document order and rewrite the response in-place by
# tracking offsets.
_TAG_FIND_RE = re.compile(r"\[(CONFIRMED|PROBABLE)\]")

# Verification-date detection — looks for explicit dates the LLM may have
# cited as the source-of-truth for an officeholder claim. Demote if the
# date is older than 12 months from today.
_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|"
    r"Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{4})\b"
    r"|\b(\d{4})-(\d{1,2})-(\d{1,2})\b"
    r"|\b(20\d{2})\b",
    re.IGNORECASE,
)

# Explicit demotion markers — used by both the inline tag replacement and
# the appended warning block.
_DEMOTION_REASON_NO_TOOL = "no fresh source — pre-tool training data"
_DEMOTION_REASON_NO_URLS = "no cited URLs to back the claim"
_DEMOTION_REASON_STALE_DATE = "cited date older than 12 months"


def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_OFFICEHOLDER_GUARD=0 to disable."""
    val = os.getenv("ARIA_OFFICEHOLDER_GUARD", "1") or "1"
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="officeholder_guard",
        summary="Is Enabled",
        source_id="officeholder_guard:R-F1001",
    )

    return val.strip().lower() not in ("0", "false", "no", "off")


def _has_stale_cited_date(window: str, *, now_year: int) -> str | None:
    """If the window cites a calendar date >12 months in the past, return
    the matched date string for use in the demotion reason. Otherwise None.
    """
    for m in _DATE_RE.finditer(window):
        # Group 1 = "Month YYYY"; Group 2/3/4 = "YYYY-MM-DD"; Group 5 = bare year
        year = None
        if m.group(1):
            year = int(m.group(1))
        elif m.group(2):
            year = int(m.group(2))
        elif m.group(5):
            year = int(m.group(5))
        if year is None:
            continue
        # Anything in a calendar year >12 months back counts as stale.
        if year <= now_year - 1:
            return m.group(0)
    return None


def review_response(
    response_text: str,
    verification: dict | None,
    *,
    now: datetime | None = None,
) -> tuple[str, list[dict]]:
    """Scan the response for officeholder claims and demote unverified ones.

    Window-based: for each [CONFIRMED] / [PROBABLE] tag in the response,
    take a ±200-char window around the tag, check whether the window
    contains BOTH an officeholder title AND a person-name pattern, and
    demote the tag in place if the claim isn't backed by tool evidence.

    Returns (rewritten_response, demotions).

    `demotions` is a list of dicts each describing one demotion:
        {
            "window_preview": str,  # the matched ±200-char window (truncated)
            "person_name":    str,  # best-effort extracted name
            "title_matched":  str,  # the officeholder title that triggered the match
            "original_tag":   str,  # CONFIRMED or PROBABLE
            "reason":         str,  # NO_TOOL / NO_URLS / STALE_DATE
        }
    """
    if not is_enabled() or not response_text:
        return response_text, []

    now = now or datetime.now(timezone.utc)
    now_year = now.year

    # Tool-verification status. We treat as "unverified" when EITHER the
    # verdict is NO_TOOL (no tool ran at all) OR cited URL count is 0 (tool
    # ran but the response cited zero URLs that source_verifier could ground).
    # The round-5 incident hit the second case: auto-investigate fired but
    # the LLM still wrote "via cross-referenced government sources" without
    # citing any actual URLs, so verification.cited == 0 even though a tool
    # ran. Without this stricter check, the post-processor saw "tool ran"
    # and skipped the demotion.
    unverified = True
    if isinstance(verification, dict):
        verdict = (verification.get("verdict") or "").upper()
        cited = int(verification.get("cited", 0) or 0)
        if verdict and verdict != "NO_TOOL" and cited > 0:
            unverified = False

    # Walk every demotable tag in document order. We collect (start, end,
    # replacement) tuples and apply them at the end so we don't shift indices
    # mid-iteration.
    rewrites: list[tuple[int, int, str]] = []
    demotions: list[dict] = []
    seen_windows: set[tuple[int, int]] = set()

    for tag_match in _TAG_FIND_RE.finditer(response_text):
        tag = tag_match.group(1)
        tag_start = tag_match.start()
        tag_end = tag_match.end()

        # ±200-char window around the tag
        win_start = max(0, tag_start - _WINDOW_CHARS)
        win_end = min(len(response_text), tag_end + _WINDOW_CHARS)
        window = response_text[win_start:win_end]

        # Need BOTH a title and a person name in the window
        title_match = _TITLE_RE.search(window)
        if not title_match:
            continue
        name_match = _PERSON_NAME_RE.search(window)
        if not name_match:
            continue

        # Determine demotion reason
        reason: str | None = None
        if unverified:
            # Distinguish "no tool ran at all" from "tool ran but cited
            # nothing" so the reason is informative.
            if isinstance(verification, dict) and (verification.get("verdict") or "").upper() not in ("", "NO_TOOL"):
                reason = _DEMOTION_REASON_NO_URLS
            else:
                reason = _DEMOTION_REASON_NO_TOOL
        else:
            stale_date = _has_stale_cited_date(window, now_year=now_year)
            if stale_date:
                reason = f"{_DEMOTION_REASON_STALE_DATE} ({stale_date})"

        if not reason:
            continue

        # Avoid double-counting the same window across multiple nearby tags
        window_key = (win_start, win_end)
        already_seen = window_key in seen_windows
        seen_windows.add(window_key)

        replacement = f"[UNCERTAIN — {reason}]"
        rewrites.append((tag_start, tag_end, replacement))

        if not already_seen:
            demotions.append({
                "window_preview": window.strip()[:240],
                "person_name": name_match.group(0),
                "title_matched": title_match.group(0),
                "original_tag": tag,
                "reason": reason,
            })

    if not demotions:
        return response_text, []

    # Apply rewrites from end to start so earlier offsets stay valid.
    rewritten_text = response_text
    for start, end, replacement in sorted(rewrites, key=lambda r: -r[0]):
        rewritten_text = rewritten_text[:start] + replacement + rewritten_text[end:]

    # Append a visible WARNING block so the user sees the enforcement
    # explicitly. The footer comes from confidence_footer separately and
    # remains untouched.
    warning_lines = [
        "",
        "",
        "⚠️ *OFFICEHOLDER GUARD — DEMOTED CLAIMS*",
        f"_Constitution clause 10 enforcement at code level. {len(demotions)} officeholder claim(s) "
        f"demoted from [CONFIRMED]/[PROBABLE] to [UNCERTAIN] because they could not be verified in this turn._",
        "",
    ]
    for i, d in enumerate(demotions[:5], 1):
        warning_lines.append(
            f"{i}. *{d['person_name']}* ({d['title_matched']}) — was [{d['original_tag']}] — _{d['reason']}_"
        )
    if len(demotions) > 5:
        warning_lines.append(f"   _… and {len(demotions) - 5} more._")
    warning_lines.append("")
    warning_lines.append(
        "_To get a verified answer, ask me to investigate the position with a fresh source: "
        '"Aria, investigate current Ghana defence minister" — that will trigger a web search '
        "that source_verifier can ground against._"
    )

    return rewritten_text + "\n".join(warning_lines), demotions

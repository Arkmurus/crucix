"""ARIA officeholder claim guard.

Code-level enforcement of CONSTITUTION clause 10 (officeholder discipline).
The clause is in the system prompt but the LLM games it under pressure —
the round-4 smoke test produced "[CONFIRMED] Hon. Dominic Nitiwul ... since
his re-appointment in February 2023" for a question about Ghana's *current*
defence minister, fabricating a verification date to satisfy the rule.
Nitiwul served under Akufo-Addo; Mahama won the December 2024 election.

This module post-processes ARIA's reply text and:

  1. Detects sentences that name an officeholder (President / Minister /
     Director / Ambassador / Commander / etc) by FULL human name.
  2. If the sentence carries [CONFIRMED] OR [PROBABLE] AND no tool ran in
     this turn (verification.verdict == "NO_TOOL"), demote the tag to
     [UNCERTAIN — pre-tool knowledge, may have changed].
  3. If the sentence cites a verification date older than 12 months, also
     demote to [UNCERTAIN — last known YYYY-MM, may have changed].
  4. Append a structured WARNING block at the end of the reply listing all
     demoted claims so the user sees the demotion explicitly.

Why post-processing instead of upstream enforcement?
The LLM completes BEFORE source verification runs, so by the time we know
the verification verdict the response is already baked. Post-processing is
the only enforcement point that has access to BOTH the response text AND
the verification verdict.

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

# A claim sentence is a chunk of text containing both an officeholder title
# and a person-name match within reasonable proximity. We process the reply
# sentence by sentence so we can demote precisely.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*])")

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
_DEMOTION_REASON_STALE_DATE = "cited date older than 12 months"


def is_enabled() -> bool:
    """Feature flag — default ON. Set ARIA_OFFICEHOLDER_GUARD=0 to disable."""
    val = os.getenv("ARIA_OFFICEHOLDER_GUARD", "1") or "1"
    return val.strip().lower() not in ("0", "false", "no", "off")


def _looks_like_officeholder_claim(sentence: str) -> bool:
    """True if the sentence both names an officeholder title and a likely
    person name within reasonable proximity."""
    if not sentence:
        return False
    title_match = _TITLE_RE.search(sentence)
    if not title_match:
        return False
    # Look for a person-name match anywhere in the sentence — proximity is
    # implicit because we operate on a single sentence.
    return bool(_PERSON_NAME_RE.search(sentence))


def _earliest_demotable_tag_idx(sentence: str) -> tuple[int, str] | None:
    """Find the position of the first demotable confidence tag, if any.
    Returns (start_idx, tag_string) or None.
    """
    for tag in _DEMOTABLE_TAGS:
        marker = f"[{tag}]"
        idx = sentence.find(marker)
        if idx != -1:
            return idx, tag
    return None


def _has_stale_cited_date(sentence: str, *, now_year: int, now_month: int) -> str | None:
    """If the sentence cites a calendar date >12 months in the past, return
    the matched date string for use in the demotion reason. Otherwise None.
    """
    for m in _DATE_RE.finditer(sentence):
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
        # Months-ago calculation. We treat anything in a calendar year >12
        # months back as stale. Don't bother with month precision — the
        # purpose is a coarse "this is old" signal.
        if year < now_year - 1:
            return m.group(0)
        if year == now_year - 1:
            return m.group(0)
    return None


def review_response(
    response_text: str,
    verification: dict | None,
    *,
    now: datetime | None = None,
) -> tuple[str, list[dict]]:
    """Scan the response for officeholder claims and demote unverified ones.

    Returns (rewritten_response, demotions).

    `demotions` is a list of dicts each describing one demotion:
        {
            "sentence":      str,   # the original sentence (truncated)
            "person_name":   str,   # best-effort extracted name
            "original_tag":  str,   # CONFIRMED or PROBABLE
            "reason":        str,   # NO_TOOL or STALE_DATE
        }
    """
    if not is_enabled() or not response_text:
        return response_text, []

    now = now or datetime.now(timezone.utc)
    now_year = now.year
    now_month = now.month

    # Tool-verification status. NO_TOOL means no investigation was run for
    # this reply, so any officeholder claim is grounded in training data only.
    no_tool_run = True
    if isinstance(verification, dict):
        verdict = (verification.get("verdict") or "").upper()
        # NO_TOOL is the verdict source_verifier emits when no tool ran in
        # this request. PASS / FAIL / SUSPICIOUS all mean a tool DID run and
        # the response can at least be checked against fetched URLs.
        if verdict and verdict != "NO_TOOL":
            no_tool_run = False

    sentences = _SENTENCE_SPLIT_RE.split(response_text)
    rewritten_sentences: list[str] = []
    demotions: list[dict] = []

    for sentence in sentences:
        if not _looks_like_officeholder_claim(sentence):
            rewritten_sentences.append(sentence)
            continue

        tag_info = _earliest_demotable_tag_idx(sentence)
        if not tag_info:
            # Sentence names an officeholder but doesn't claim CONFIRMED /
            # PROBABLE — leave it alone. ASSESSED / UNCERTAIN / SPECULATIVE
            # are already appropriately hedged.
            rewritten_sentences.append(sentence)
            continue

        tag_idx, tag = tag_info

        reason: str | None = None
        if no_tool_run:
            reason = _DEMOTION_REASON_NO_TOOL
        else:
            stale_date = _has_stale_cited_date(sentence, now_year=now_year, now_month=now_month)
            if stale_date:
                reason = f"{_DEMOTION_REASON_STALE_DATE} ({stale_date})"

        if not reason:
            rewritten_sentences.append(sentence)
            continue

        # Build the replacement tag. Extract a person-name preview for the
        # demotions log so the user can see exactly what got flagged.
        name_match = _PERSON_NAME_RE.search(sentence)
        person_name = name_match.group(0) if name_match else "(name not extracted)"
        replacement = f"[UNCERTAIN — {reason}]"
        rewritten = sentence[:tag_idx] + replacement + sentence[tag_idx + len(f"[{tag}]"):]

        demotions.append({
            "sentence": sentence.strip()[:240],
            "person_name": person_name,
            "original_tag": tag,
            "reason": reason,
        })
        rewritten_sentences.append(rewritten)

    if not demotions:
        return response_text, []

    rewritten_text = " ".join(rewritten_sentences)

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
            f"{i}. *{d['person_name']}* (was [{d['original_tag']}]) — _{d['reason']}_"
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

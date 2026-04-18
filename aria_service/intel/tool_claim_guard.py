"""Tool-claim guard — catch fabricated "I'm running tool X" prose.

Context
───────
Past incident 2026-04-18 (Baykar): user asked ARIA to "crawl the known
OEM websites" without pasting any URL. Intent detection required a URL
to route to extract_url_deep, so it returned None. The LLM then wrote
very convincing prose:

  "I have begun the deep crawl of Baykar's official website
   (https://www.baykartech.com/) using the extract_url_deep tool.
   Stand by for the first data extract from Baykar."

Zero tool call fired. No background task was queued. The next turn had
no new data. Silent fabrication.

commitment_guard catches FUTURE-tense promises ("Within 24h I will…").
This module catches PRESENT-tense tool-execution claims ("I am crawling",
"tool is running", "crawl initiated", "stand by for results") when no
tool actually fired this turn.

How it decides fabrication
──────────────────────────
  - Caller passes `tool_used` — the string set by the intent router when
    a real tool ran (e.g. "extract_url", "deep_research"). None / empty
    means pure-LLM turn, so any tool-execution claim is fabricated.
  - Scan the response for the patterns below. Rewrite each match inline
    with a correction note, and record a pending_actions entry so:
      a) the operator sees "I owe you this" in the daily briefing, and
      b) the missing intent / resolver is made visible for us to wire.

Rewriting strategy
──────────────────
  - Replace the fabricated claim with an honest sentence: "I cannot run
    this inline — I've logged this as a pending action #<id> and will
    resolve it via <resolver or 'operator input required'>."
  - The original claim still ends up in the pending_actions queue so the
    work isn't lost.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("aria.tool_claim_guard")

# Phrases that imply ARIA is actively running a tool right now. Each is
# written to be specific enough that normal chat prose doesn't trip it.
_TOOL_CLAIM_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "I have begun the deep crawl of X" / "I have initiated the crawl"
    (re.compile(
        r"I\s+(?:have|'ve)\s+(?:begun|started|initiated|launched|kicked\s+off|fired|triggered)\s+"
        r"(?:the\s+)?(?:deep\s+)?(?:crawl|extraction|scrape|research|investigation|analysis|scan)\s+"
        r"(?:of|on|for|against)?\s*[^.!?]*[.!?]",
        re.IGNORECASE,
    ), "claimed-crawl-initiated"),

    # "The extract_url_deep tool is running" / "the crawler is currently fetching"
    (re.compile(
        r"(?:the\s+)?(?:extract_url_deep|extract_url|deep_research|web_search|crawl(?:_website)?|spider|researcher)\s+"
        r"(?:tool\s+)?(?:is|has\s+been)\s+"
        r"(?:running|executing|fetching|crawling|scraping|processing|active|in\s+progress)"
        r"[^.!?]*[.!?]",
        re.IGNORECASE,
    ), "claimed-tool-running"),

    # "Crawl initiated" / "Extraction started" as status line
    (re.compile(
        r"(?:^|\n|\.\s+)(?:crawl|extraction|deep\s+research|investigation|scraping|extract|web\s+search)\s+"
        r"(?:initiated|started|begun|launched|in\s+progress|executing|running)\s*[.!?:]?",
        re.IGNORECASE,
    ), "status-line-fabrication"),

    # "Stand by for the first data extract / results"
    (re.compile(
        r"[Ss]tand\s+by\s+(?:for|while)\s+[^.!?]*(?:extract|result|finding|crawl|fetch|data)[^.!?]*[.!?]",
    ), "stand-by-fabrication"),

    # "I am now crawling X" / "I am executing the extract"
    (re.compile(
        r"I\s+am\s+(?:now\s+)?(?:currently\s+)?"
        r"(?:crawling|extracting|scraping|fetching|researching|investigating|running|executing|processing|analy[sz]ing)\s+"
        r"[^.!?]*[.!?]",
        re.IGNORECASE,
    ), "claimed-active-execution"),

    # "The tool will return in N minutes / seconds"
    (re.compile(
        r"(?:the\s+)?(?:tool|crawl|extraction|research)\s+will\s+(?:return|complete|finish|deliver)\s+"
        r"(?:in|within)\s+[^.!?]+[.!?]",
        re.IGNORECASE,
    ), "false-completion-eta"),

    # "Once the crawl returns, I will..." / "when the extraction completes"
    (re.compile(
        r"[Oo]nce\s+(?:the\s+)?(?:crawl|extraction|tool|research|web\s+search)\s+"
        r"(?:returns|completes|finishes|is\s+done)\s*,?\s*I\s+will[^.!?]+[.!?]",
    ), "false-completion-chained"),

    # "Next OEMs in queue" / "queued for crawling" — only when no queue exists
    (re.compile(
        r"(?:next\s+\w+s?\s+in\s+queue|queued\s+for\s+crawling|in\s+the\s+research\s+queue)[^.!?]*[.!?]",
        re.IGNORECASE,
    ), "false-queue-claim"),

]

# ── Bracket-form fabricated tool calls — ALWAYS scanned, even when a
# real tool ran earlier in the same turn. Past incident 2026-04-19 web
# chat: the LLM ran a real web_search, presented results, then ALSO
# emitted a "[TOOL: web_search]\n... json query ..." block at the end
# proposing to run another search. tool_claim_guard early-returned
# because tool_used was set, so the bracket leaked. Now the bracket
# scan is in its own list (_BRACKET_PATTERNS) and runs after the
# tool_used early-return — every bracket-form fake-call is caught
# regardless of whether a real tool ran on this turn.
_BRACKET_PATTERNS: list[tuple[re.Pattern, str]] = [
    # [TOOL: name] ... [/TOOL] (with closing) or just [TOOL: name]
    (re.compile(
        r"\[\s*TOOL\s*(?:CALL)?\s*[:=]\s*[\w_-]+[^\]]*\]"
        r"(?:\s*[^\[]{0,800}\[\s*/\s*TOOL\s*\])?",
        re.IGNORECASE | re.DOTALL,
    ), "fabricated-tool-bracket"),
    # Stand-alone closing tag
    (re.compile(
        r"\[\s*/\s*TOOL\s*\]",
        re.IGNORECASE,
    ), "fabricated-tool-bracket-close"),
]

# Trigger words that indicate the response is REFERENCING tool execution.
# Used as a pre-filter so we don't run the full regex set on short replies
# that obviously don't mention tools.
#
# Also matches the literal "[TOOL" bracket so the bracket-form patterns
# above aren't bypassed by the pre-filter. Without this, the 2026-04-18
# 23:07 incident (LLM emitted [TOOL: deep_research] / [/TOOL] for a brain
# stats query) would have been skipped because none of the verbal
# trigger words appeared in the response.
_QUICK_TRIGGER = re.compile(
    r"\b(?:crawl|extract|scrape|fetch|research|investigat|spider|queue|stand\s+by|tool\s+is|initiated|executing)\b"
    r"|\[\s*(?:/\s*)?TOOL\s*[:\]=]",
    re.IGNORECASE,
)

_NOTE_TEMPLATE = (
    " [Clause 20(f): Tool-execution claim removed — I cannot fire {tool} "
    "inline for this turn. Logged as pending action {aid}; will resolve "
    "via {resolver}.]"
)


async def guard(
    response_text: str,
    *,
    tool_used: str | None,
    user_message: str = "",
    user_id: str = "",
    chat_id: str = "",
) -> dict:
    """Scan response for fabricated tool claims and rewrite them.

    Args:
        response_text: The LLM output to check.
        tool_used: The intent-router result. Truthy means a real tool ran
            this turn (so tool-execution prose is legitimate). Falsy means
            pure-LLM turn — any tool-execution prose is fabrication.
        user_message: The original user prompt (so we can enqueue a better
            pending-action description).
        user_id / chat_id: Context for the pending-actions record so
            proactive nudges know where to reply.

    Returns:
        {
          "original": str,
          "guarded": str,
          "violations_found": int,
          "violations": [{"pattern_type": str, "match": str, "action_id": str}],
          "changed": bool,
          "skipped_reason": str | None,
        }
    """
    out = {
        "original": response_text,
        "guarded": response_text,
        "violations_found": 0,
        "violations": [],
        "changed": False,
        "skipped_reason": None,
    }

    if not response_text or len(response_text) < 40:
        out["skipped_reason"] = "response-too-short"
        return out

    guarded = response_text
    violations: list[dict] = []

    # Inferred resolver — best guess based on the user's ask. The OEM case
    # (list of manufacturers with no URL) routes to the known_oems batch;
    # anything else is operator_action until we wire a better intent.
    resolver_kind, resolver_ref, operator_prompt = _infer_resolver(
        user_message=user_message, response_text=response_text,
    )

    # Lazy imports so a test env without redis_store still loads this module.
    try:
        from . import pending_actions as _pa
    except Exception as e:
        logger.warning("[tool_claim_guard] pending_actions import failed: %s", e)
        _pa = None  # type: ignore

    # ── Bracket-form scan — ALWAYS RUNS ──────────────────────────────────
    # [TOOL: name] / [/TOOL] are structured fake-call syntax that LLMs
    # emit when imitating tool-using assistants. They are NEVER honest
    # output regardless of whether a real tool ran this turn — the real
    # tool's output is in `tool_context`, not in a bracket block in the
    # response text. So we always strip these.
    for pattern, pattern_type in _BRACKET_PATTERNS:
        for match in pattern.findall(guarded):
            match_text = match.strip() if isinstance(match, str) else ""
            if not match_text:
                continue
            ix = guarded.find(match_text)
            if ix < 0:
                continue
            if "Clause 20(f)" in guarded[ix : ix + len(match_text) + 200]:
                continue
            aid = "pending"
            if _pa is not None:
                try:
                    entry = await _pa.record(
                        promise=match_text[:400],
                        reason=(
                            f"Bracket-form tool call emitted in response text "
                            f"(pattern: {pattern_type}). The LLM is imitating "
                            f"tool-call syntax — real tool calls go through "
                            f"_execute_tool, not response brackets. tool_used="
                            f"{tool_used or 'None'} (irrelevant — bracket form "
                            f"is fake-call syntax regardless)."
                        ),
                        resolver_kind=resolver_kind,
                        resolver_ref=resolver_ref,
                        severity="HIGH",
                        user_id=user_id,
                        chat_id=chat_id,
                        source="tool_claim_guard",
                        operator_prompt=operator_prompt,
                        metadata={
                            "pattern_type": pattern_type,
                            "user_message_head": (user_message or "")[:200],
                            "tool_used_this_turn": tool_used or "",
                        },
                    )
                    aid = entry.get("action_id", "pending")
                except Exception as e:
                    logger.debug("[tool_claim_guard] record failed: %s", e)
            note = _NOTE_TEMPLATE.format(
                tool="the named tool (bracket-form fake call)",
                aid=aid,
                resolver=resolver_ref or "operator input required",
            )
            guarded = guarded.replace(
                match_text,
                match_text.rstrip(".!?") + "." + note,
                1,
            )
            violations.append({
                "pattern_type": pattern_type,
                "match": match_text[:200],
                "action_id": aid,
            })

    # ── Prose-form scan — gated on no-tool-ran ──────────────────────────
    # Natural-language tool prose ("I have begun the crawl") is only
    # fabrication when NO real tool ran this turn. If a tool ran, prose
    # describing it is honest. Bracket-form is structured fake syntax so
    # it's checked unconditionally above.
    if tool_used:
        out["skipped_reason"] = f"prose-skipped:tool-ran:{tool_used}"
    elif not _QUICK_TRIGGER.search(guarded):
        out["skipped_reason"] = "prose-skipped:no-tool-keywords"
    else:
        for pattern, pattern_type in _TOOL_CLAIM_PATTERNS:
            for match in pattern.findall(guarded):
                match_text = match.strip() if isinstance(match, str) else ""
                if not match_text or len(match_text) < 15:
                    continue
                # Don't double-tag
                ix = guarded.find(match_text)
                if ix < 0:
                    continue
                if "Clause 20(f)" in guarded[ix : ix + len(match_text) + 200]:
                    continue

                aid = "pending"
                if _pa is not None:
                    try:
                        entry = await _pa.record(
                            promise=match_text[:400],
                            reason=(
                                f"Tool-execution prose emitted but no intent fired "
                                f"this turn (tool_used=None). Pattern: {pattern_type}."
                            ),
                            resolver_kind=resolver_kind,
                            resolver_ref=resolver_ref,
                            severity="HIGH",
                            user_id=user_id,
                            chat_id=chat_id,
                            source="tool_claim_guard",
                            operator_prompt=operator_prompt,
                            metadata={
                                "pattern_type": pattern_type,
                                "user_message_head": (user_message or "")[:200],
                            },
                        )
                        aid = entry.get("action_id", "pending")
                    except Exception as e:
                        logger.debug("[tool_claim_guard] record failed: %s", e)

                note = _NOTE_TEMPLATE.format(
                    tool=_infer_tool_name(pattern_type),
                    aid=aid,
                    resolver=resolver_ref or "operator input required",
                )
                guarded = guarded.replace(
                    match_text,
                    match_text.rstrip(".!?") + "." + note,
                    1,
                )
                violations.append({
                    "pattern_type": pattern_type,
                    "match": match_text[:200],
                    "action_id": aid,
                })

    out["guarded"] = guarded
    out["violations_found"] = len(violations)
    out["violations"] = violations
    out["changed"] = guarded != response_text

    if out["changed"]:
        logger.info(
            "[tool_claim_guard] %d tool-claim fabrication(s) rewritten (tool_used=None)",
            len(violations),
        )
        # Feed the brain — pattern-tracking on tool-claim fabrications
        # so the predictor learns which phrasings correlate with the
        # LLM pretending to run a tool it doesn't have access to.
        try:
            from . import brain_hook as _bh
            patterns = [v.get("pattern_type", "unknown") for v in violations]
            await _bh.absorb(
                module="tool_claim_guard",
                summary=(
                    f"{len(violations)} tool-claim fabrication(s) rewritten: "
                    f"{', '.join(patterns[:4])}"
                ),
                detail="\n".join(
                    f"  {v.get('pattern_type')}: {(v.get('match') or '')[:120]}"
                    for v in violations[:3]
                ),
                success=False,
                gap_type="knowledge_gap",
                gap_detail=f"LLM claimed tool execution with no tool fired; patterns={patterns}",
                confidence="CONFIRMED",
            )
        except Exception:
            pass

    return out


def _infer_tool_name(pattern_type: str) -> str:
    """Best-effort tool name for the correction note."""
    mapping = {
        "claimed-crawl-initiated": "extract_url_deep",
        "claimed-tool-running": "extract_url_deep / deep_research",
        "status-line-fabrication": "the named tool",
        "stand-by-fabrication": "the research pipeline",
        "claimed-active-execution": "the tool",
        "false-completion-eta": "the tool",
        "false-completion-chained": "the tool",
        "false-queue-claim": "the research queue",
    }
    return mapping.get(pattern_type, "the tool")


def _infer_resolver(*, user_message: str, response_text: str) -> tuple[str, str, str]:
    """Guess the right resolver for a pending-action entry.

    Returns (resolver_kind, resolver_ref, operator_prompt).

    The heuristic is intentionally narrow — we only auto-route the cases
    we know how to action. Anything else goes to pure_promise + an
    operator prompt, which is the honest outcome.
    """
    msg = (user_message or "").lower() + " " + (response_text or "")[:500].lower()

    # OEM batch intent — user asked about OEMs without pasting URLs.
    if any(k in msg for k in (
        "oem", "manufacturer", "baykar", "roketsan", "rheinmetall",
        "elbit", "iai", "kia", "kmw", "otokar",
    )) and any(k in msg for k in (
        "crawl", "research", "investigate", "capabilit", "product",
    )):
        return (
            "background_job",
            "known_oems_batch",
            "No action needed — a batch OEM crawl will run via the "
            "autonomous engine. Confirm it fired in the next briefing.",
        )

    # Generic "research X" without a URL
    if any(k in msg for k in ("research", "investigate", "look into")):
        return (
            "operator_action",
            "paste-url-or-confirm-batch",
            "Reply with a specific URL or entity list so I can route this "
            "through extract_url_deep / deep_research.",
        )

    # Fallback — no auto-path available
    return (
        "pure_promise",
        "",
        "I emitted a tool-execution claim without a resolver path. Review "
        "this pending action and either (a) ask me again with a URL, or "
        "(b) let me know which autonomous task should cover it.",
    )

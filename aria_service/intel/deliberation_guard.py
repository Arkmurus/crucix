"""R-F3615 (2026-08-01) — deliberation is never a fact.

WHAT WENT WRONG
Between 2026-07-25 and 2026-07-31, R-F3033 deliberately SERVED a reasoning
model's `reasoning_content` as the answer when `content` came back empty. That
was reversed by R-F3591 ("reasoning is a diagnostic, never the answer"), but for
those six days every module that absorbed its own LLM output could absorb raw
chain-of-thought as knowledge.

It did. From the operator's live WhatsApp transcript on 2026-08-01, served back
as "[ARIA KNOWLEDGE BASE — verified facts]":

    [ASSESSED] contract_intelligence:detail: ── Self-review window 1/1 ──
    We need answer audit. Need follow instructions. Need inspect window text
    and ARIA draft. Need determine if any issues in this window...

That is the model thinking out loud, stored and re-served as an established
fact. R-F3608 closed the chat path specifically; this closes the CLASS, at the
two chokepoints every module shares:

    write:  brain_hook.absorb()          — nothing deliberation-shaped is stored
    read:   knowledge._rank_knowledge_facts() — nothing already stored is served

§7 — INFINITE MEMORY, SO NOTHING IS DELETED. ARIA never forgets: no TTL, no
prune, no eviction (CLAUDE.md §7, aria_infinite_memory.md). The existing
poisoned rows are therefore QUARANTINED, not removed — they stay on disk and
stay auditable, they simply stop being served as verified facts. That is
reversible; a deletion would not be, and would breach a binding rule to fix a
presentation bug.

DESIGN — TWO MARKERS, NOT ONE.
A single "we need to" appears in legitimate analytic prose ("we need to verify
the UBO chain"), so one hit cannot condemn a row. Deliberation is recognisable
by its DENSITY: the narrator plans, second-guesses and addresses itself
repeatedly. Requiring two DISTINCT markers keeps real intel out of the net while
still catching the transcript above (which trips five). A small set of
unambiguous phrases — ones that only occur when a model is narrating its own
process — count on their own.
"""
from __future__ import annotations

import re

# Phrases that, alone, mean the text is a model narrating its own process.
# Each was observed in real leaked output (R-F3591's live capture, and the
# operator's 2026-08-01 transcript).
_UNAMBIGUOUS = (
    re.compile(r"──\s*self-review window", re.IGNORECASE),
    re.compile(r"\bbut wait\b[\s,—-]*can i\b", re.IGNORECASE),
    re.compile(r"\bi need to answer from the snippets\b", re.IGNORECASE),
)

# Individually weak, jointly decisive. Two DISTINCT hits classify as
# deliberation — see the density rationale in the module docstring.
_MARKERS = (
    re.compile(r"\bwe need (?:to|answer)\b", re.IGNORECASE),
    re.compile(r"\blet me (?:look|check|think|see|inspect)\b", re.IGNORECASE),
    re.compile(r"\bbut wait\b", re.IGNORECASE),
    re.compile(r"\bthe user (?:asks|is asking|wants)\b", re.IGNORECASE),
    re.compile(r"\bwe are asked\b", re.IGNORECASE),
    re.compile(r"\bi (?:should|must|need to) (?:answer|check|look|verify)\b",
               re.IGNORECASE),
    # Telegraphic article-dropping is highly characteristic of reasoning
    # traces: "Need follow instructions. Need determine if any issues."
    re.compile(r"\bNeed (?:follow|determine|inspect|check|output|answer|verify|"
               r"ensure|produce)\b"),
    re.compile(r"\bactually,? (?:we|i) (?:should|need|must)\b", re.IGNORECASE),
    re.compile(r"\bso the answer (?:is|should be)\b", re.IGNORECASE),
)


def looks_like_deliberation(text: str) -> bool:
    """True when `text` is a model's chain of thought rather than a finding.

    Conservative by construction: needs either one unambiguous self-narration
    phrase, or two DISTINCT weaker markers. A false negative leaves one noisy
    row in recall; a false positive would suppress a genuine intel fact, which
    is the more expensive error — so the threshold favours keeping data.
    """
    if not text or not isinstance(text, str):
        return False
    t = text.strip()
    if len(t) < 25:          # too short to establish a pattern either way
        return False
    for rx in _UNAMBIGUOUS:
        if rx.search(t):
            return True
    hits = sum(1 for rx in _MARKERS if rx.search(t))
    return hits >= 2


def quarantine_reason(text: str) -> str:
    """Short machine-readable reason, for logs and the audit trail."""
    return "deliberation" if looks_like_deliberation(text) else ""

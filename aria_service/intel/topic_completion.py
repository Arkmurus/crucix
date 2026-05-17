"""topic_completion — R-F660 per-topic completion criteria (read-only metric).

Slice 2 of the learning-framework buildout (2026-05-17). Phase A symptom-
fix: pure measurement, no control, zero LLM calls. The Phase B learning
controller (R-F662) will consume this metric to decide when to stop a
learning session on a given topic.

Design-analysis directive (2026-05-17):

    "Per-topic completion criteria: 'I know enough about X when 5
     verified facts cite ≥3 tier-1 sources and no critical sub-question
     is OPEN.'"

This module computes that exact judgement on demand from existing local
data sources:

  - verified_intel.facts        — Redis key `crucix:verified_intel:facts`
                                  Each fact has claim, entity_name,
                                  verification_status, sources[].
  - dialogue_state              — SQLite-backed open-question tracker.
                                  Status 'open' or 'chasing' counts as
                                  unanswered.

NOTHING here calls an LLM. The metric is fully deterministic — same
inputs → same `complete:bool` — which is what the controller needs to
make stop decisions without driving up cloud-LLM spend.

Public surface
──────────────
    DEFAULT_THRESHOLDS  : dict
    TIER1_TIERS         : set[str]
    topic_matches(topic, fact)               -> bool
    fact_is_verified(fact)                   -> bool
    count_tier1_sources(facts)               -> int  (distinct domains)
    async topic_completion(topic, **thresholds) -> dict
"""
from __future__ import annotations

import logging
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.topic_completion")


# Defaults straight from the design-analysis directive. Operator can
# override per-call via topic_completion(...) kwargs once the controller
# wants different thresholds per topic-class (e.g. higher bar for
# sanctions evidence than for tender-monitor evidence).
DEFAULT_THRESHOLDS: dict[str, int] = {
    "verified_facts_min": 5,
    "tier1_sources_min": 3,
    "open_questions_max": 0,
}

# Source tiers that count as "tier-1" for the completion criterion.
# verified_intel.SourceTier defines 1a (official primary) and 1b (official
# secondary). Both satisfy the design analysis's "tier-1" bar.
TIER1_TIERS: frozenset[str] = frozenset({"1a", "1b"})


# ──────────────────────────────────────────────────────────────────────────
# Pure helpers (no I/O — easy to unit-test)
# ──────────────────────────────────────────────────────────────────────────

def topic_matches(topic: str, fact: dict) -> bool:
    """Return True iff this fact relates to `topic`.

    Match rules (cheap, deterministic, no LLM):
      1. Exact topic-tag match: if fact has a 'topics' list (some writers
         tag facts via student.detect_topics), check membership.
      2. Substring match on claim or entity_name (case-insensitive).

    Both rules permissive on purpose — false-positives are bounded by the
    verification_status filter (only VERIFIED facts count toward the
    metric), and false-negatives would mean topic-mastery NEVER closes."""
    if not topic or not isinstance(fact, dict):
        return False
    t = topic.strip().lower()
    if not t:
        return False
    # 1. Tagged topics
    tagged = fact.get("topics") or []
    if isinstance(tagged, list) and t in {str(x).lower() for x in tagged}:
        return True
    # 2. Substring on claim or entity_name
    claim = (fact.get("claim") or "").lower()
    entity = (fact.get("entity_name") or "").lower()
    if t in claim or t in entity:
        return True
    return False


def fact_is_verified(fact: dict) -> bool:
    """Only facts with verification_status == 'VERIFIED' count toward
    the completion threshold. Pre-verification candidates and
    contradicted facts are excluded."""
    if not isinstance(fact, dict):
        return False
    status = (fact.get("verification_status") or "").strip().upper()
    return status == "VERIFIED"


def count_tier1_sources(facts: list[dict]) -> int:
    """Count DISTINCT tier-1 source domains across a list of facts.

    Two facts citing the same Reuters article count as ONE tier-1 source
    for the topic. The metric measures *breadth* of coverage, not the
    raw number of citations."""
    domains: set[str] = set()
    for f in facts:
        if not isinstance(f, dict):
            continue
        for s in (f.get("sources") or []):
            if not isinstance(s, dict):
                continue
            tier = str(s.get("tier") or "").strip().lower()
            if tier not in TIER1_TIERS:
                continue
            domain = (s.get("domain") or "").strip().lower()
            if not domain:
                # Fall back to URL host if the writer didn't pre-extract.
                url = s.get("url") or ""
                if url:
                    try:
                        from urllib.parse import urlparse
                        domain = (urlparse(url).hostname or "").lower()
                        if domain.startswith("www."):
                            domain = domain[4:]
                    except Exception:
                        domain = ""
            if domain:
                domains.add(domain)
    return len(domains)


# ──────────────────────────────────────────────────────────────────────────
# Open-question count (touches dialogue_state — pure read)
# ──────────────────────────────────────────────────────────────────────────

async def count_open_critical_for_topic(topic: str) -> int:
    """Count open or chasing dialogue questions whose question_text
    mentions `topic`. dialogue_state has no per-topic index yet, so we
    pull list_all_open() and filter. Capped at 500 entries — if the
    topic has more, the metric saturates at 'not complete' anyway."""
    try:
        from . import dialogue_state
    except Exception as e:
        logger.debug("topic_completion: dialogue_state import failed: %s", e)
        return 0
    try:
        rows = await dialogue_state.list_all_open(limit=500)
    except Exception as e:
        logger.debug("topic_completion: list_all_open failed: %s", e)
        return 0
    t = topic.strip().lower()
    if not t:
        return 0
    count = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        # Only 'critical' questions block completion. The dialogue_state
        # schema doesn't yet flag criticality — treat ALL open questions
        # as critical for now (the conservative choice; the controller
        # can override via kwargs once criticality is wired).
        text = (row.get("question_text") or "").lower()
        if t in text:
            count += 1
    return count


# ──────────────────────────────────────────────────────────────────────────
# Top-level entry point
# ──────────────────────────────────────────────────────────────────────────

async def topic_completion(
    topic: str,
    *,
    verified_facts_min: int = DEFAULT_THRESHOLDS["verified_facts_min"],
    tier1_sources_min: int = DEFAULT_THRESHOLDS["tier1_sources_min"],
    open_questions_max: int = DEFAULT_THRESHOLDS["open_questions_max"],
) -> dict:
    """Return per-topic completion measurement.

    Output shape:
        {
            "topic": "<echoed input>",
            "complete": bool,
            "criteria": {
                "verified_facts":            int,
                "verified_facts_min":        int,
                "tier1_sources":             int,
                "tier1_sources_min":         int,
                "open_critical_questions":   int,
                "open_questions_max":        int,
            },
            "blockers": [<reason strings>],
        }

    `complete` is True iff ALL three gates pass:
        verified_facts >= verified_facts_min
        AND tier1_sources >= tier1_sources_min
        AND open_critical_questions <= open_questions_max

    `blockers` lists the gates that are currently failing, in a form the
    learning controller (R-F662) can surface to the operator or use to
    pick the next reading-list entry. Empty list when complete=True.
    """
    topic = (topic or "").strip()
    if not topic:
        return {
            "topic": "",
            "complete": False,
            "criteria": {
                "verified_facts": 0,
                "verified_facts_min": verified_facts_min,
                "tier1_sources": 0,
                "tier1_sources_min": tier1_sources_min,
                "open_critical_questions": 0,
                "open_questions_max": open_questions_max,
            },
            "blockers": ["topic_empty"],
        }

    # ── 1. Verified facts about this topic ────────────────────────
    try:
        facts_raw = await rs.get_json("crucix:verified_intel:facts") or []
    except Exception as e:
        logger.debug("topic_completion: get_json failed: %s", e)
        facts_raw = []

    matching = [
        f for f in facts_raw
        if isinstance(f, dict) and topic_matches(topic, f) and fact_is_verified(f)
    ]
    verified_facts_n = len(matching)
    tier1_n = count_tier1_sources(matching)

    # ── 2. Open critical sub-questions ────────────────────────────
    open_q = await count_open_critical_for_topic(topic)

    # ── 3. Compute gate verdict ───────────────────────────────────
    blockers: list[str] = []
    if verified_facts_n < verified_facts_min:
        blockers.append(
            f"verified_facts {verified_facts_n} < {verified_facts_min}"
        )
    if tier1_n < tier1_sources_min:
        blockers.append(
            f"tier1_sources {tier1_n} < {tier1_sources_min}"
        )
    if open_q > open_questions_max:
        blockers.append(
            f"open_critical_questions {open_q} > {open_questions_max}"
        )
    complete = not blockers

    return {
        "topic": topic,
        "complete": complete,
        "criteria": {
            "verified_facts": verified_facts_n,
            "verified_facts_min": verified_facts_min,
            "tier1_sources": tier1_n,
            "tier1_sources_min": tier1_sources_min,
            "open_critical_questions": open_q,
            "open_questions_max": open_questions_max,
        },
        "blockers": blockers,
    }

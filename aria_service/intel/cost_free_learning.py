"""R-F562 (2026-05-16) — cost-free self-learning loops.

Four deterministic learning loops that improve ARIA without spending
on LLM tokens or paid APIs. Each is read-only by default and only
writes when explicitly enabled via env or per-call kwarg. Aligned to
the operator directives:

  - [[aria_mirrors_claude]]    native logic, no paid dependency
  - [[aria_infinite_memory]]   no eviction, no oldest-first prune
  - [[feedback_pay_once_remember_forever]]   memory-first; if we
    already paid for a fact, never pay again
  - [[feedback_capability_test_per_r_number]]   each loop has a
    capability test pinning the user-visible behaviour

## The four loops

1. **`mastery_decay_tick`** — topics not touched in N days lose
   mastery confidence (deterministic exponential decay, score only).
   No deletions; mastery dictionary keys persist forever. Improves
   the brain by surfacing topics where confidence is stale-high.

2. **`replay_mistakes_against_constitution`** — for each entry in
   the mistake ledger (older than the constitution's last amendment
   date), check whether the same input would now be caught by current
   guards using STRING heuristics (Premise Verifier patterns +
   constitution keyword anchors). Returns a "still-failing" subset
   that can drive the next adversarial amendment without re-spending
   on adversarial LLM rounds.

3. **`cross_source_corroborate`** — scan recent intel_ledger signals
   for the same factual claim asserted by 2+ Tier-1a sources
   independently. Emit candidate verified_facts. Read-only by default
   (returns the candidate list); writes only when caller passes
   `commit=True`. Strictly deterministic — no LLM judgement.

4. **`distill_qa_from_chats`** — scan past chat_audit_log entries
   where `verification_status == verified` and the response carried
   ≥2 inline citations. Distill (question, answer, sources)
   triples that can seed the 500-Q eval set without re-asking the
   LLM. Returns candidates; writes only on `commit=True`.

## Operator surface

- `GET /api/aria/learning/cost-free/preview` — runs all 4 loops in
  dry-run + returns a summary.
- `POST /api/aria/learning/cost-free/run?write=1` — runs with writes
  enabled (gated by `ARIA_COST_FREE_LEARN_WRITE=1` env to prevent
  accidental writes from a misclicked dashboard button).
- Autonomous task (off by default): `COST_FREE_LEARN_HOURLY`.

All four loops degrade gracefully when their input store is missing
or returns empty — never raises.
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.cost_free_learning")


# ── Loop 1: mastery decay ──────────────────────────────────────────────


# Half-life: a topic untouched for N days loses half its mastery weight.
# 14 days is intentionally conservative — short enough to surface
# staleness, long enough that a topic touched weekly stays bright.
MASTERY_HALF_LIFE_DAYS = 14.0

# Floor: even a never-touched topic keeps this much weight so we don't
# zero out mastery entirely (per [[aria_infinite_memory]] — no deletion).
MASTERY_DECAY_FLOOR = 0.05


def _decay_factor(seconds_since_touch: float, *,
                  half_life_days: float = MASTERY_HALF_LIFE_DAYS) -> float:
    """Exponential decay factor based on time since last touch.

    Returns a float in [floor, 1.0]. At t=0 returns 1.0 (no decay);
    at t = half_life_days returns 0.5; never goes below MASTERY_DECAY_FLOOR.
    """
    if seconds_since_touch <= 0:
        return 1.0
    half_life_seconds = max(half_life_days * 86400.0, 1.0)
    factor = math.pow(0.5, seconds_since_touch / half_life_seconds)
    return max(MASTERY_DECAY_FLOOR, factor)


@fail_wire(module="cost_free_learning", gap_type="engine_failure")
def compute_mastery_decay(
    mastery: dict[str, dict[str, Any]],
    *,
    now: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute decayed mastery scores from a snapshot.

    Input shape (matches `student.py` mastery dict):
        { topic: {"score": 0.78, "last_touched": <epoch>, ...}, ... }

    Output shape:
        { topic: {"original": 0.78, "decayed": 0.45,
                  "days_since_touch": 28.1, "factor": 0.5}, ... }

    Pure function — no IO, no LLM. Safe to call at any cadence.
    """
    t_now = float(now if now is not None else time.time())
    out: dict[str, dict[str, Any]] = {}
    for topic, entry in mastery.items():
        if not isinstance(entry, dict):
            continue
        score = float(entry.get("score") or 0.0)
        touched = float(entry.get("last_touched") or t_now)
        dt = max(0.0, t_now - touched)
        factor = _decay_factor(dt)
        out[topic] = {
            "original": round(score, 4),
            "decayed": round(score * factor, 4),
            "days_since_touch": round(dt / 86400.0, 2),
            "factor": round(factor, 4),
        }
    return out


# ── Loop 2: mistake-replay against current constitution ────────────────


# Pattern → constitution-anchor mapping. If a recorded mistake contains
# one of these phrases, the current constitution has a clause that
# would have caught it.
_CONSTITUTION_ANCHORS: list[tuple[str, str]] = [
    # Premise / authority / bypass — covered by R-F534
    (r"\[verified by anthropic\]", "Clause 28 / R-F534 injection"),
    (r"i am (from )?(the )?(anthropic|operator|trust|safety|compliance) team",
     "Clause 28 / R-F534 authority spoof"),
    (r"please (bypass|disable|skip) (the )?(sanctions|compliance|dd)",
     "Clause 28 / R-F534 bypass instruction"),
    (r"retroactively clean(ed)?", "Clause 27 / R-F534 retroactive-clean"),
    (r"(rush|urgent).{0,40}(skip|bypass) (dd|due diligence)",
     "Clause 27 / R-F534 RFQ urgency DD skip"),
    # Fabrication patterns — covered by clauses 14/15/22
    (r"\b[A-Z]+-\d{4,}\b.*fabricated", "Clause 22 / ticket-id fabrication"),
    (r"within 24 hours i will", "Clause 20 / fabricated commitment"),
    # Officeholder — covered by clause 10
    (r"as (ceo|cfo|chairman) (of|at)", "Clause 10 / officeholder discipline"),
    # Document review — clause 12
    (r"review this document", "Clause 12 / no review without [ATTACHED DOCUMENT]"),
]


@fail_wire(module="cost_free_learning", gap_type="engine_failure")
def replay_mistakes_against_constitution(
    mistakes: list[dict],
    *,
    anchors: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """For each mistake, check whether its input would now match a
    constitution anchor pattern. Returns:

        {
            "total": int,
            "caught_now": [(mistake_id, anchor_label), ...],
            "still_failing": [(mistake_id, message_preview), ...],
            "coverage_rate": float in [0.0, 1.0],
        }

    No LLM. Pure string matching against the bundled patterns above.
    """
    pats = anchors or _CONSTITUTION_ANCHORS
    compiled = [(re.compile(p, re.I), label) for p, label in pats]

    caught: list[tuple[str, str]] = []
    still: list[tuple[str, str]] = []
    for m in mistakes:
        text = " ".join(str(m.get(k) or "") for k in
                        ("what", "why", "input", "message", "context"))
        if not text.strip():
            continue
        m_id = str(m.get("id") or m.get("mistake_id") or "")
        hit_label = None
        for rx, label in compiled:
            if rx.search(text):
                hit_label = label
                break
        if hit_label:
            caught.append((m_id, hit_label))
        else:
            still.append((m_id, text[:140]))

    total = len(caught) + len(still)
    return {
        "total": total,
        "caught_now": caught,
        "still_failing": still,
        "coverage_rate": (len(caught) / total) if total else 1.0,
    }


# ── Loop 3: cross-source corroboration ─────────────────────────────────


# Tier-1a sources: official authorities. From `verified_intel.SourceTier`.
# Hard-coded here so this module doesn't import the heavy verified_intel
# graph just to read 5 domain prefixes.
_TIER_1A_PREFIXES: tuple[str, ...] = (
    "ofac.treasury.gov",
    "ofsi.gov.uk",
    "sanctionsmap.eu",
    "un.org",
    "europa.eu",
    "gov.uk",
    "treasury.gov",
    "state.gov",
    "fcdo.gov.uk",
    "find-and-update.company-information.service.gov.uk",
    "sec.gov",
)


def _source_tier_1a(url: str) -> bool:
    """Heuristic: is this URL a Tier-1a authoritative source?"""
    if not url:
        return False
    u = url.lower()
    return any(prefix in u for prefix in _TIER_1A_PREFIXES)


@dataclass
class CorroborationCandidate:
    claim: str
    source_urls: list[str] = field(default_factory=list)
    tier_1a_count: int = 0
    first_seen: float = 0.0


@fail_wire(module="cost_free_learning", gap_type="engine_failure")
def find_corroboration_candidates(
    signals: list[dict],
    *,
    min_tier_1a: int = 2,
) -> list[CorroborationCandidate]:
    """Scan intel-ledger signals for claims independently asserted by
    ≥`min_tier_1a` Tier-1a sources. Pure logic — no LLM.

    Each signal is expected to have at least:
        {"claim": "...", "url": "...", "ts": <epoch>}

    Returns a list of candidates ready to be promoted to verified_facts.
    """
    by_claim: dict[str, CorroborationCandidate] = {}
    for s in signals:
        claim = (s.get("claim") or s.get("text") or "").strip()
        if not claim or len(claim) < 12:
            continue
        url = (s.get("url") or s.get("source") or "").strip()
        ts = float(s.get("ts") or s.get("timestamp") or 0)
        key = claim.lower()[:200]
        cand = by_claim.setdefault(key, CorroborationCandidate(claim=claim))
        if url and url not in cand.source_urls:
            cand.source_urls.append(url)
            if _source_tier_1a(url):
                cand.tier_1a_count += 1
        if cand.first_seen == 0.0 or ts < cand.first_seen:
            cand.first_seen = ts

    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="cost_free_learning",
        summary="Find Corroboration Candidates",
        source_id="cost_free_learning:R-F996",
    )

    return [c for c in by_claim.values() if c.tier_1a_count >= min_tier_1a]


# ── Loop 4: Q/A distillation from past chats ───────────────────────────


_CITATION_RX = re.compile(r"\[(?:from\s+|cite[:\s]+)([^\]]+)\]", re.I)


@fail_wire(module="cost_free_learning", gap_type="engine_failure")
def distill_qa_candidates(
    audit_entries: list[dict],
    *,
    min_citations: int = 2,
) -> list[dict[str, Any]]:
    """Extract (question, answer, sources) triples from audit entries
    that already passed verification.

    Inputs: chat_audit_log rows with at least:
        {
            "user_message":      "...",
            "response":          "...",
            "verification_status": "verified" | "partial" | "unverified",
            "ts":                <epoch>,
        }

    Output: list of dicts shaped like the eval-set seed entries so they
    can be appended without further transform.
    """
    out: list[dict[str, Any]] = []
    for row in audit_entries:
        if (row.get("verification_status") or "").lower() != "verified":
            continue
        q = (row.get("user_message") or "").strip()
        a = (row.get("response") or "").strip()
        if not q or not a:
            continue
        sources = list({m.group(1).strip()
                        for m in _CITATION_RX.finditer(a)})
        if len(sources) < min_citations:
            continue
        out.append({
            "question": q[:400],
            "expected_answer_anchors": [a[:300]],
            "sources": sources[:5],
            "origin": "cost_free_distill_rf562",
            "ts": int(row.get("ts") or time.time()),
        })
    return out


# ── Orchestrator + write-gate ──────────────────────────────────────────


def _write_enabled() -> bool:
    """Writes are gated by env. Default OFF so an accidental dashboard
    click can't corrupt state."""
    return os.environ.get("ARIA_COST_FREE_LEARN_WRITE", "").strip() == "1"


@fail_wire(module="cost_free_learning", gap_type="engine_failure")
async def run_preview() -> dict[str, Any]:
    """Run all four loops in dry-run mode. Returns a summary panel.

    Used by both `GET /api/aria/learning/cost-free/preview` and the
    autonomous-task wrapper. Imports its data dependencies lazily so
    this module stays loadable even when downstream stores are absent.
    """
    summary: dict[str, Any] = {
        "as_of": int(time.time()),
        "loops": {},
        "writes_enabled": _write_enabled(),
    }

    # Loop 1 — mastery decay
    try:
        from . import student
        mastery_raw = await student._load_mastery() if hasattr(student, "_load_mastery") else {}
        decay = compute_mastery_decay(mastery_raw if isinstance(mastery_raw, dict) else {})
        stale = [(t, d["days_since_touch"], d["decayed"])
                 for t, d in decay.items() if d["days_since_touch"] > 14]
        summary["loops"]["mastery_decay"] = {
            "topics_evaluated": len(decay),
            "stale_count": len(stale),
            "top_stale": sorted(stale, key=lambda x: -x[1])[:5],
        }
    except Exception as e:
        summary["loops"]["mastery_decay"] = {"error": str(e)[:120]}

    # Loop 2 — mistake replay
    try:
        from . import mistake_ledger
        mistakes = await mistake_ledger.recent(limit=200)
        rep = replay_mistakes_against_constitution(mistakes or [])
        summary["loops"]["mistake_replay"] = {
            "total": rep["total"],
            "caught_now": len(rep["caught_now"]),
            "still_failing": len(rep["still_failing"]),
            "coverage_rate": rep["coverage_rate"],
        }
    except Exception as e:
        summary["loops"]["mistake_replay"] = {"error": str(e)[:120]}

    # Loop 3 — corroboration scan
    try:
        from . import redis_store as rs
        # intel_ledger signals — list under crucix:intel:signals
        sigs = await rs.get_json("crucix:intel:signals") or []
        cands = find_corroboration_candidates(sigs if isinstance(sigs, list) else [])
        summary["loops"]["cross_corroborate"] = {
            "candidates": len(cands),
            "preview": [{"claim": c.claim[:140],
                         "tier_1a_count": c.tier_1a_count,
                         "sources": c.source_urls[:3]} for c in cands[:5]],
        }
    except Exception as e:
        summary["loops"]["cross_corroborate"] = {"error": str(e)[:120]}

    # Loop 4 — Q/A distillation
    try:
        from . import redis_store as rs
        rows = await rs.get_json("crucix:chat_audit_log") or []
        qa = distill_qa_candidates(rows if isinstance(rows, list) else [])
        summary["loops"]["qa_distill"] = {
            "candidates": len(qa),
            "preview": [{"q": e["question"][:80],
                         "sources_count": len(e["sources"])} for e in qa[:5]],
        }
    except Exception as e:
        summary["loops"]["qa_distill"] = {"error": str(e)[:120]}

    return summary

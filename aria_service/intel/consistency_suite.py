"""Consistency test suite — same question, different phrasing, same answer?

Why this exists
───────────────
A recognised AI system gives substantively the same answer to the same
question regardless of how it's phrased. ARIA has never been tested for
this. If she says "30%" when asked formally about the Angola offset
obligation and "it depends" when asked colloquially, her knowledge is
not reliable enough to act on — regardless of what any single-shot
test says.

This module runs canonical-question-plus-variants tests weekly and
produces a consistency score per domain. The score feeds the sixth
quality signal on the dashboard alongside grounded_rate, mastery,
adversarial_score, and others.

How scoring works
─────────────────
For each test: ask ARIA the canonical question + every variant. Compare:

  1. Each answer to the ground_truth — "accuracy"
  2. Each answer to the canonical answer — "internal consistency"

The final score is the minimum of the two — a test passes only if
ARIA is BOTH right AND consistent. A consistent wrong answer is not
better than an inconsistent right one.

Scoring uses semantic similarity via the existing sentence-transformers
embedder in rag_store (no extra LLM call). A match is a cosine >= 0.75
on a normalised vector. If embedding fails (fallback mode), we drop to
a normalised-substring check which is stricter but still functional.

Public API
──────────
  load_tests() -> list[ConsistencyTest]       # from YAML
  async run_all(llm, limit=None) -> dict       # fire the suite
  async run_one(test, llm) -> ConsistencyScore # fire a single test
  async get_latest_scores() -> dict            # read Redis state
  async summary_for_dashboard() -> dict        # tile payload

Autonomous cadence: CONSISTENCY-WEEKLY task in tasks.yaml fires on
Sunday 05:00 UTC. Results go to Redis + mistake_ledger.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.consistency_suite")

_YAML_PATH = Path(__file__).parent / "consistency_tests.yaml"
_KEY_LATEST = "crucix:consistency:latest"
_KEY_HISTORY = "crucix:consistency:history"


@dataclass
class ConsistencyTest:
    test_id: str
    domain: str
    canonical_question: str
    variants: list[str]
    ground_truth: str
    notes: str = ""


@dataclass
class VariantScore:
    variant: str
    response: str
    accuracy: float          # vs ground_truth
    consistency: float       # vs canonical answer
    response_time_ms: int


@dataclass
class ConsistencyScore:
    test_id: str
    domain: str
    overall_score: float         # min(accuracy_avg, consistency_avg)
    accuracy_avg: float
    consistency_avg: float
    variant_scores: list[VariantScore] = field(default_factory=list)
    fired_at: str = ""
    passed: bool = False


# ── Similarity ─────────────────────────────────────────────────────────────

async def _similarity(a: str, b: str) -> float:
    """Cosine similarity between two strings.

    Primary path: sentence-transformers embedder (warm, already loaded
    for RAG). Fallback: token-overlap jaccard — less accurate but
    functional so the test suite never hard-fails if embeddings break.
    """
    if not a or not b:
        return 0.0
    a_n = a.strip().lower()
    b_n = b.strip().lower()
    if a_n == b_n:
        return 1.0

    try:
        # R-F870 — embedder lives in semantic_search, not rag_store (the old
        # import raised ImportError every call → _similarity silently fell back
        # to Jaccard). Use the async accessor so the cold-load runs off-loop.
        from .semantic_search import aget_embedder  # reuse the warm instance
        embedder = await aget_embedder()
        if embedder is not None:
            import numpy as np
            # R-F703 (2026-05-18) — wrap sync encode in to_thread. The
            # _similarity helper is async but the underlying
            # sentence_transformers encode() is a GIL-holding C call;
            # before this fix every consistency-suite pair invocation
            # blocked the event loop for the encode duration (~50-100ms
            # warm, 22-28s on a cold model). On run_all() with N pairs
            # the loop stalled N × ~50ms straight, and the recurring
            # fly /health/live timeout pattern at 19:52:34 (autonomy_
            # surface 4-way 8s wait_for() expiring simultaneously) is
            # the symptom of exactly this kind of synchronous CPU
            # squatting on the loop. Use the process-wide _safe_encode
            # lock-aware wrapper so we honour R-F530's lock semantics.
            import asyncio as _aio
            from .semantic_search import _safe_encode
            vecs = await _aio.to_thread(
                _safe_encode, embedder, [a_n[:2000], b_n[:2000]],
            )
            v1 = vecs[0] / max(np.linalg.norm(vecs[0]), 1e-9)
            v2 = vecs[1] / max(np.linalg.norm(vecs[1]), 1e-9)
            return float(np.clip(v1 @ v2, -1.0, 1.0))
    except Exception as e:
        logger.debug("[consistency] embedder fallback: %s", e)

    # Fallback: token jaccard
    a_tokens = set(a_n.split())
    b_tokens = set(b_n.split())
    if not a_tokens or not b_tokens:
        return 0.0
    inter = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)
    return inter / max(union, 1)


# ── Test loading ───────────────────────────────────────────────────────────

def _default_tests() -> list[dict]:
    """Seed tests covering ARIA's established domains. YAML overrides
    this if present. Each domain has at least one test so we surface
    weak areas immediately."""
    return [
        # ── Angola procurement (a core Arkmurus domain) ──
        {
            "test_id": "angola_offset_pct",
            "domain": "angola_procurement",
            "canonical_question": "What is the minimum offset percentage required for defence contracts in Angola?",
            "variants": [
                "How much local content must an Angola defence contract include?",
                "Angola procurement — what's the industrial participation obligation?",
                "If we sign a USD 10M defence supply contract with SIMPORTEX, what offset applies?",
                "What does Angolan law require in terms of local industry participation for defence?",
                "SIMPORTEX contract offset requirements — what percentage?",
            ],
            "ground_truth": "30 percent industrial participation under the Angolan Industrial Participation Policy, administered by MINDCOM",
        },
        # ── UK Bribery Act ──
        {
            "test_id": "uk_bribery_s7",
            "domain": "uk_compliance",
            "canonical_question": "What is the Section 7 offence under the UK Bribery Act 2010?",
            "variants": [
                "Tell me about the UK Bribery Act's corporate offence",
                "What's a corporate failure to prevent bribery under UK law?",
                "UK Bribery Act — what's the company-level offence for not preventing bribery?",
                "If our agent pays a bribe abroad, what UK offence applies to Arkmurus?",
                "Section 7 UK Bribery Act — explain",
            ],
            "ground_truth": "Section 7 is the corporate offence of failure to prevent bribery. Strict liability applies when an associated person commits bribery to benefit the commercial organisation; adequate procedures defence",
        },
        # ── Export control (UK SIEL) ──
        {
            "test_id": "uk_siel_timeline",
            "domain": "uk_export_control",
            "canonical_question": "How long does a UK SIEL application typically take to process?",
            "variants": [
                "UK defence export licence processing time",
                "How long for ECJU to decide on a SIEL?",
                "When we apply for a standard individual export licence in the UK, what's the lead time?",
                "Export control — UK SIEL how many weeks to approve?",
                "Arkmurus is planning a UK defence export; what's the licence processing timeline?",
            ],
            "ground_truth": "20 working days target published by ECJU but in practice 4-8 weeks depending on destination and goods; more for sensitive destinations or Category A items",
        },
        # ── Sanctions compliance ──
        {
            "test_id": "ofac_50pct_rule",
            "domain": "sanctions",
            "canonical_question": "What is the OFAC 50 Percent Rule?",
            "variants": [
                "If a designated person owns 50% of a company, is the company sanctioned?",
                "Explain the Treasury 50 percent ownership rule",
                "OFAC sanctions cascade — when does subsidiary ownership trigger restrictions?",
                "SDN subsidiary rule — what ownership threshold?",
                "We're screening a counterparty; what ownership stake by an SDN triggers sanctions?",
            ],
            "ground_truth": "Entities 50% or more owned by one or more SDN-designated persons are themselves treated as blocked even if not individually listed; applies in aggregate across multiple SDN owners",
        },
        # ── NATO STANAG ──
        {
            "test_id": "nato_stanag_4172",
            "domain": "nato_standards",
            "canonical_question": "What does NATO STANAG 4172 standardise?",
            "variants": [
                "NATO 155mm ammunition standard — which STANAG?",
                "What's the standardisation agreement for NATO 155mm artillery rounds?",
                "STANAG 4172 scope?",
                "Artillery ammunition interoperability in NATO — which STANAG governs it?",
                "What does 4172 cover in the NATO standards framework?",
            ],
            "ground_truth": "STANAG 4172 covers the 155mm NATO modular artillery ammunition including the M109 and M107-compatible projectiles; interoperability specification for NATO-standard calibre 155mm ammunition",
        },
        # ── Turkey ≠ NATO ──
        {
            "test_id": "turkey_nato_status",
            "domain": "strategic_geography",
            "canonical_question": "Is Turkey treated as part of the NATO target market for Arkmurus purposes?",
            "variants": [
                "Does Turkey count as a NATO market for Arkmurus strategy?",
                "When we map NATO regions, should Turkey be included?",
                "Arkmurus target markets — is Turkey in the NATO bucket?",
                "Strategic geography: where does Turkey fit in Arkmurus's region model?",
                "Is Turkey grouped under NATO in our go-to-market?",
            ],
            "ground_truth": "No. Turkey is a NATO member politically but is treated as a standalone market by Arkmurus — not grouped under NATO. This is an operator-set hard rule",
        },
    ]


def load_tests() -> list[ConsistencyTest]:
    """Load tests from YAML if present, else use the seed set."""
    raw: list[dict] = []
    if _YAML_PATH.exists():
        try:
            import yaml
            with open(_YAML_PATH, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                raw = data.get("tests") or []
        except Exception as e:
            logger.warning("[consistency] YAML load failed: %s — using seed", e)
    if not raw:
        raw = _default_tests()

    tests: list[ConsistencyTest] = []
    for t in raw:
        try:
            tests.append(ConsistencyTest(
                test_id=str(t["test_id"]),
                domain=str(t["domain"]),
                canonical_question=str(t["canonical_question"]),
                variants=list(t.get("variants") or []),
                ground_truth=str(t["ground_truth"]),
                notes=str(t.get("notes", "")),
            ))
        except Exception as e:
            logger.warning("[consistency] skipping malformed test %r: %s", t, e)
    return tests


# ── Runner ─────────────────────────────────────────────────────────────────

async def _ask_aria(llm, question: str, timeout: float = 45.0) -> tuple[str, int]:
    """Fire a one-shot question through the LLM chain. Returns (response, ms)."""
    from ..aria_engine import aria_chat
    t0 = time.time()
    try:
        # R-F870 — correct signature is aria_chat(message, session_id, llm, ...).
        # The previous call passed (llm, question, group_context=, auto_tools=)
        # which (a) put the provider object in `message` and the question in
        # `session_id`, and (b) used two kwargs that don't exist → TypeError on
        # EVERY call. The except below then returned "<error: ...>" as the
        # response for every variant, which is why the suite scored ~0.01
        # across all domains (identical error strings → consistency≈1.0,
        # accuracy≈0.0). aria_chat has no auto-tools toggle; a pure-knowledge
        # mode would be a separate feature, not a kwarg here.
        result = await aria_chat(
            message=question, session_id="consistency", llm=llm,
        )
        elapsed = int((time.time() - t0) * 1000)
        if isinstance(result, dict):
            return (result.get("response") or result.get("answer") or ""), elapsed
        return str(result), elapsed
    except Exception as e:
        logger.warning("[consistency] aria_chat failed: %s", e)
        return f"<error: {e}>", int((time.time() - t0) * 1000)


async def run_one(test: ConsistencyTest, llm) -> ConsistencyScore:
    """Fire one canonical + all variants and compute scores."""
    from datetime import datetime, timezone

    # Canonical first — this is the reference answer for consistency
    canonical_resp, canonical_ms = await _ask_aria(llm, test.canonical_question)

    variant_scores: list[VariantScore] = []
    # Include the canonical itself in the scoreboard for completeness
    accuracy_can = await _similarity(canonical_resp, test.ground_truth)
    variant_scores.append(VariantScore(
        variant=test.canonical_question,
        response=canonical_resp[:500],
        accuracy=round(accuracy_can, 3),
        consistency=1.0,  # canonical is its own reference
        response_time_ms=canonical_ms,
    ))

    for v in test.variants:
        resp, ms = await _ask_aria(llm, v)
        acc = await _similarity(resp, test.ground_truth)
        cons = await _similarity(resp, canonical_resp)
        variant_scores.append(VariantScore(
            variant=v,
            response=resp[:500],
            accuracy=round(acc, 3),
            consistency=round(cons, 3),
            response_time_ms=ms,
        ))

    accuracy_avg = sum(v.accuracy for v in variant_scores) / max(len(variant_scores), 1)
    # Consistency excludes the canonical-vs-itself 1.0 (would skew up)
    cons_list = [v.consistency for v in variant_scores[1:]]
    consistency_avg = sum(cons_list) / max(len(cons_list), 1) if cons_list else 1.0

    overall = min(accuracy_avg, consistency_avg)

    return ConsistencyScore(
        test_id=test.test_id,
        domain=test.domain,
        overall_score=round(overall, 3),
        accuracy_avg=round(accuracy_avg, 3),
        consistency_avg=round(consistency_avg, 3),
        variant_scores=variant_scores,
        fired_at=datetime.now(timezone.utc).isoformat(),
        passed=overall >= 0.65,
    )


async def run_all(llm, limit: int | None = None) -> dict:
    """Fire the full suite. Persists results to Redis. Returns summary."""
    tests = load_tests()
    if limit is not None:
        tests = tests[:limit]

    scores: list[ConsistencyScore] = []
    for t in tests:
        try:
            s = await run_one(t, llm)
            scores.append(s)
        except Exception as e:
            logger.warning("[consistency] test %s raised: %s", t.test_id, e)

    # Aggregate
    overall = (
        sum(s.overall_score for s in scores) / max(len(scores), 1)
        if scores else 0.0
    )
    by_domain: dict[str, list[float]] = {}
    for s in scores:
        by_domain.setdefault(s.domain, []).append(s.overall_score)
    domain_scores = {d: round(sum(v) / len(v), 3) for d, v in by_domain.items()}

    weakest = (
        min(domain_scores.items(), key=lambda kv: kv[1])
        if domain_scores else (None, 0.0)
    )
    passed = sum(1 for s in scores if s.passed)

    from datetime import datetime, timezone
    summary = {
        "fired_at": datetime.now(timezone.utc).isoformat(),
        "tests_run": len(scores),
        "tests_passed": passed,
        "overall_score": round(overall, 3),
        "by_domain": domain_scores,
        "weakest_domain": weakest[0],
        "weakest_score": weakest[1],
        "per_test": [
            {
                "test_id": s.test_id,
                "domain": s.domain,
                "overall": s.overall_score,
                "accuracy": s.accuracy_avg,
                "consistency": s.consistency_avg,
                "passed": s.passed,
            }
            for s in scores
        ],
    }

    try:
        from . import redis_store as rs
        await rs.set_json(_KEY_LATEST, summary)
        await rs.lpush(_KEY_HISTORY, _safe_json(summary))
        await rs.ltrim(_KEY_HISTORY, 0, 51)  # keep last year of weekly runs
    except Exception as e:
        logger.debug("[consistency] persist failed: %s", e)

    # Log failing tests to mistake ledger so predictor learns
    try:
        from . import mistake_ledger as _ml
        for s in scores:
            if not s.passed:
                await _ml.record(
                    category="false_confidence",
                    task_type="chat",
                    domain=s.domain,
                    what=f"Consistency test {s.test_id} failed",
                    why=(
                        f"accuracy_avg={s.accuracy_avg:.2f} "
                        f"consistency_avg={s.consistency_avg:.2f} "
                        f"(threshold 0.65)"
                    ),
                    fix="Review domain knowledge / re-seed corpus or constitution",
                    what_class=f"consistency_{s.domain}",
                )
    except Exception:
        pass
        pass

    # Feed the brain — success=overall >= 0.65 globally, topic attribution
    # per domain so weak domains downgrade their mastery automatically.
    try:
        from . import brain_hook as _bh
        passed_all = summary["tests_passed"] == summary["tests_run"] and summary["tests_run"] > 0
        domains_touched = list(summary.get("by_domain", {}).keys())
        await _bh.absorb(
            module="consistency_suite",
            summary=(
                f"Consistency suite: {summary['tests_passed']}/{summary['tests_run']} passed "
                f"(overall {summary['overall_score']:.2f}). "
                f"Weakest: {summary.get('weakest_domain') or 'n/a'} "
                f"at {summary.get('weakest_score', 0):.2f}"
            ),
            detail="\n".join(
                f"  {d}: {score:.2f}"
                for d, score in summary.get("by_domain", {}).items()
            ),
            success=passed_all,
            extra_topics=domains_touched,
            confidence="CONFIRMED",
        )
    except Exception as e:
        logger.debug("[consistency] brain_hook feed failed: %s", e)

    # R-F1304 — wire to brain (§21a)
    try:
        from .engine_wiring import wire_success, wire_failure
        wire_success(
            module="consistency_suite",
            summary=f"Consistency suite: {summary['tests_passed']}/{summary['tests_run']} passed (overall {summary['overall_score']:.2f})",
            source_id="consistency_suite:run_all",
        )
    except Exception:
        pass
    return summary


def _safe_json(obj: Any) -> str:
    """Serialise dict → JSON string with a 200kB cap."""
    import json
    try:
        s = json.dumps(obj, default=str)
        return s[:200_000]
    except Exception:
        pass
        return "{}"


# ── Read-side API ──────────────────────────────────────────────────────────

async def get_latest_scores() -> dict | None:
    from . import redis_store as rs
    try:
        return await rs.get_json(_KEY_LATEST)
    except Exception:
        pass


        return None


async def summary_for_dashboard() -> dict:
    """Compact payload for the dashboard tile."""
    latest = await get_latest_scores()
    if not latest:
        return {
            "ok": False,
            "reason": "not_yet_run",
            "message": "Consistency suite has not run yet — first fire due Sunday 05:00 UTC",
        }
    return {
        "ok": True,
        "overall_score": latest.get("overall_score"),
        "tests_passed": latest.get("tests_passed"),
        "tests_run": latest.get("tests_run"),
        "by_domain": latest.get("by_domain", {}),
        "weakest_domain": latest.get("weakest_domain"),
        "weakest_score": latest.get("weakest_score"),
        "fired_at": latest.get("fired_at"),
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

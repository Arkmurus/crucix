"""ARIA Golden Q&A Evaluation Framework — regressi

on catching for prompt/code
changes using a hand-curated set of question/expected-answer pairs.

Why this exists
═══════════════
LLM-as-judge without ground truth drifts. A/B testing without users gives
no signal. The cheapest reliable thing for a small team running ARIA in
production is a hand-picked set of 20-50 questions where you (Antonio) know
what a *good* answer looks like, and a fast deterministic way to detect
when a code or prompt change makes those answers worse.

This module is that. It's deliberately:
  - JSON-shaped, not a vector database (humans curate the set)
  - Backed by Redis so the seed survives restarts and we don't add a file
    write to the deploy path
  - Scored with the existing sentence-transformers model (already loaded
    by RAG / semantic_search) so there's no extra dependency
  - Manual to seed (via /eval add fb_<id> from feedback flow OR direct
    POST) — the team's judgment IS the ground truth

What it is NOT
══════════════
- Not LLM-as-judge — that's the next phase, scoped to specific claim
  types (cited-source verification, confidence-tag honesty)
- Not a vector recall benchmark — measures end-to-end answer quality,
  not retrieval precision
- Not run automatically on every deploy — that's a flag I left off so
  the team controls when regressions are checked. Trigger with
  /eval (WhatsApp) or POST /api/aria/eval/run (CI/cron)

Scoring
═══════
R-F1396 (WS-0a, learning strategy): each entry is graded by an LLM judge
(eval_judge.judge_answer — strict rubric, factual agreement not wording)
and the verdict drives the bucket: correct→pass, partial→warn, wrong→fail.
Cosine similarity is still computed and recorded per entry (continuity for
deltas) and is the FALLBACK bucket whenever the judge is disabled
(ARIA_EVAL_JUDGE_ENABLED=0), unavailable, or returns unscored:
  ≥ 0.75 → pass     (semantically equivalent)
  0.50–0.75 → warn  (related but drifted)
  < 0.50 → fail     (different answer or hallucination)
Pre-R-F1396 the cosine score was the ONLY ruler and failed correct-but-
differently-worded answers (the 21.6% v0.2 artifact class).

A run produces a summary: pass_rate, mean_score, judge_coverage, count by
bucket, and delta vs the previous run so regressions surface immediately."""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

from . import eval_judge

import logging
import time
import uuid
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.eval")

GOLDEN_SET_KEY = "crucix:aria:eval:golden_set"
RUNS_KEY = "crucix:aria:eval:runs"
RUNS_TTL = 90 * 86400

PASS_THRESHOLD = 0.75
WARN_THRESHOLD = 0.50
DEFAULT_MAX_GOLDEN = 600  # Lifted from 200 → 600 for Phase A gate #6 (500-Q eval set + drift headroom)


# ── Golden set CRUD ────────────────────────────────────────────────────────

async def get_golden_set() -> list[dict]:
    """Return the full golden set, oldest first."""
    try:
        return await rs.get_json(GOLDEN_SET_KEY) or []
    except Exception as e:
        logger.warning("get_golden_set failed: %s", e)
        return []


async def get_golden_entry(entry_id: str) -> dict | None:
    items = await get_golden_set()
    for it in items:
        if it.get("id") == entry_id:
            return it
    return None


async def add_golden_entry(
    question: str,
    expected_answer: str,
    *,
    category: str = "general",
    notes: str = "",
    source: str = "manual",
    added_by: str = "",
    feedback_id: str = "",
) -> dict:
    """Append a question/expected-answer pair to the golden set.

    The expected_answer is what the team considers correct — typically
    the answer ARIA *gave* on a 👍-rated reply (promoted from feedback)
    or hand-written by an analyst for tricky cases.
    """
    if not question or not expected_answer:
        return {"ok": False, "reason": "question and expected_answer required"}
    items = await get_golden_set()
    entry = {
        "id": f"gold_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
        "question": question[:4000],
        "expected_answer": expected_answer[:6000],
        "category": (category or "general")[:64],
        "notes": (notes or "")[:600],
        "source": (source or "manual")[:64],  # manual|feedback|incident
        "added_by": (added_by or "")[:120],
        "feedback_id": feedback_id or "",
        "added_at": time.time(),
    }
    # De-dupe on question (case-insensitive, whitespace-normalised)
    norm = " ".join(question.strip().lower().split())
    for existing in items:
        if " ".join((existing.get("question") or "").strip().lower().split()) == norm:
            return {"ok": False, "reason": "duplicate question", "existing_id": existing.get("id")}
    items.append(entry)
    items = items[-DEFAULT_MAX_GOLDEN:]
    try:
        await rs.set_json(GOLDEN_SET_KEY, items)
    except Exception as e:
        logger.warning("add_golden_entry persist failed: %s", e)
        return {"ok": False, "reason": str(e)}
    logger.info("Golden entry added: %s (%s)", entry["id"], entry["category"])
    return {"ok": True, "entry": entry, "total": len(items)}


async def remove_golden_entry(entry_id: str) -> dict:
    items = await get_golden_set()
    new_items = [it for it in items if it.get("id") != entry_id]
    if len(new_items) == len(items):
        return {"ok": False, "reason": "not found"}
    try:
        await rs.set_json(GOLDEN_SET_KEY, new_items)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True, "removed": entry_id, "remaining": len(new_items)}


async def promote_feedback_to_golden(
    feedback_id: str,
    *,
    category: str = "feedback",
    notes: str = "",
    added_by: str = "",
) -> dict:
    """Take a feedback record and turn it into a golden entry. Only positive
    feedback should be promoted directly (the 👍 means 'this answer was
    correct'); for negative feedback the analyst should rewrite the
    expected_answer manually before promoting."""
    from . import feedback as feedback_store
    rec = await feedback_store.get_feedback(feedback_id)
    if not rec:
        return {"ok": False, "reason": "feedback not found"}
    if not rec.get("has_context"):
        return {"ok": False, "reason": "feedback has no Q&A snapshot — too old"}
    question = rec.get("question") or ""
    answer = rec.get("answer") or ""
    if not question or not answer:
        return {"ok": False, "reason": "feedback missing question or answer"}
    sentiment = rec.get("sentiment")
    if sentiment != "positive":
        # Allow it but warn — the caller might want to edit the expected
        # answer first via direct POST
        notes = f"[promoted from {sentiment} feedback — REVIEW expected_answer] {notes}".strip()
    return await add_golden_entry(
        question=question,
        expected_answer=answer,
        category=category,
        notes=notes,
        source="feedback",
        added_by=added_by,
        feedback_id=feedback_id,
    )


# ── Scoring ────────────────────────────────────────────────────────────────

def _cosine_score(actual: str, expected: str) -> float:
    """Return cosine similarity between two strings using the shared
    sentence-transformers model. Falls back to a token-overlap Jaccard
    score if the model isn't available so the eval still produces a
    useful number on a degraded service."""
    if not actual or not expected:
        return 0.0
    try:
        from . import semantic_search
        model = semantic_search._get_embedder()
    except Exception:
        model = None

    if model is not None:
        try:
            import numpy as np
            v1 = model.encode(actual[:2000], normalize_embeddings=True)
            v2 = model.encode(expected[:2000], normalize_embeddings=True)
            return float(np.dot(v1, v2))
        except Exception as e:
            logger.debug("embed-based scoring failed (%s) — falling back", e)

    # Jaccard fallback
    a = set(t.lower() for t in actual.split() if len(t) > 2)
    e = set(t.lower() for t in expected.split() if len(t) > 2)
    if not a or not e:
        return 0.0
    return len(a & e) / len(a | e)


def _bucket(score: float) -> str:
    if score >= PASS_THRESHOLD:
        return "pass"
    if score >= WARN_THRESHOLD:
        return "warn"
    return "fail"


# R-F1396: judge verdict → bucket. Kept beside _bucket so the two bucket
# semantics live in one place.
_JUDGE_BUCKET = {"correct": "pass", "partial": "warn", "wrong": "fail"}


# ── R-F1401/R-F1462: Held-out 80/20 split ──────────────────────────────────

# When set, run_eval will only evaluate entries in the held-out (eval) split.
# The split is deterministic (seeded SHA-256 of entry id) so the same entries
# always land in the same split across runs. Set to "eval" to score only the
# untouched 20%, or "train" to score only the training 80%.
# Default: None (evaluate ALL entries — backward compatible).
_EVAL_SPLIT = None  # "eval" | "train" | None


def set_eval_split(split: str | None) -> None:
    """Set the eval split mode. 'eval' = untouched 20%, 'train' = 80%, None = all."""
    global _EVAL_SPLIT
    if split not in ("eval", "train", None):
        raise ValueError(f"split must be 'eval', 'train', or None, got {split!r}")
    _EVAL_SPLIT = split


def get_eval_split() -> str | None:
    """Return the current eval split mode."""
    return _EVAL_SPLIT


def _split_held_out(items: list[dict], split: str, seed: int = 42) -> list[dict]:
    """Filter items to only those in the requested split.

    Uses the same deterministic seeded-SHA-256 approach as HeldOutSplit
    (aria_service/learning/held_out_split.py) but operates on the golden
    set's entry IDs rather than training example seed_ids. This keeps the
    eval runner independent of the learning package.

    Args:
        items: Full golden set.
        split: "eval" (20%) or "train" (80%).
        seed: Deterministic seed (default 42, matching HeldOutSplit).

    Returns:
        Filtered list of items in the requested split.
    """
    import hashlib as _hl

    def _in_split(entry: dict) -> bool:
        entry_id = entry.get("id") or entry.get("seed_id") or ""
        if not entry_id:
            return True  # No ID = include in both splits (backward compat)
        digest = _hl.sha256(f"{seed}:{entry_id}".encode("utf-8", errors="ignore")).digest()
        bucket = digest[0] / 256.0
        is_train = bucket < 0.80
        return is_train if split == "train" else not is_train

    return [it for it in items if _in_split(it)]


# ── Run ────────────────────────────────────────────────────────────────────

async def run_eval(
    llm: Any, *, ids: list[str] | None = None, label: str = "", record: bool = False,
    limit: int = 0,
) -> dict:
    """Execute the golden set against the current ARIA chat path.

    Each entry's question is fed through aria_chat() (the same code the
    WhatsApp user hits, including tool-use NLU). The actual response is
    embedded and compared to the expected answer.

    R-F2390 — when ``record`` is True, each answered entry ALSO flows through
    the SAME grounding/honesty recorders the live chat path uses
    (source_verifier.record_verification + honesty_judge.record_judgment),
    writing to the exact stores autonomy_scorer.compute_composite reads. This
    is the deterministic, offline way to populate the composite's verification
    (45%) + honesty (25%) signals from the frozen golden set — without hammering
    the live chat writer. Default OFF so a normal scoring eval records nothing.
    Recording runs frame the tool context into numbered snippets
    (ground_markers=True) so grounding is genuinely attributable, and validate
    against the EXACT context the LLM saw.

    Returns a run record with per-entry results, summary, and delta vs
    the previous run for regression detection.
    """
    items = await get_golden_set()
    if ids:
        items = [it for it in items if it.get("id") in set(ids)]
    # R-F1401/R-F1462: held-out split — when _EVAL_SPLIT is set, only
    # evaluate entries in the requested split (eval=20% or train=80%).
    # This ensures weekly eval scores report on data the model did NOT
    # train on (the untouched 20%), preventing contaminated metrics.
    split_mode = _EVAL_SPLIT
    if split_mode and not ids:
        pre_split = len(items)
        items = _split_held_out(items, split_mode)
        logger.info(
            "[eval_runner] held-out split '%s': %d/%d entries",
            split_mode, len(items), pre_split,
        )
    if not items:
        return {"ok": False, "reason": "no golden entries to run"}

    # R-F2390 — representative cap: when limit>0 and no explicit ids, take an
    # evenly-strided sample across the (category-ordered) golden set so the
    # subset spans categories rather than the first N (which would be one
    # category). Keeps the recording run honest + bounded on cost.
    if limit and limit > 0 and not ids and len(items) > limit:
        stride = len(items) / float(limit)
        items = [items[int(i * stride)] for i in range(limit)]
        logger.info("[eval_runner] R-F2390 representative sample: %d entries", len(items))

    # R-F1068/R-F1073: run through llm_eval_framework for deeper analysis.
    # evaluate() takes a model name string and list[EvalQuestion], not an LLM object.
    # We pass "deepseek" as the model name (the primary provider) and let the
    # framework load its own questions from the golden seed if items is empty.
    try:
        from .llm_eval_framework import evaluate as _framework_evaluate
        from .llm_eval_framework import EvalQuestion
        # Convert items to EvalQuestion objects if we have them
        _fw_questions = []
        for item in (items or []):
            try:
                _fw_questions.append(EvalQuestion(
                    question=item.get("question", ""),
                    expected_answer=item.get("expected_answer", ""),
                    category=item.get("category", "general"),
                    requires_refusal=False,
                ))
            except Exception:
                continue
        _fw_result = await _framework_evaluate(
            model_a="deepseek",
            questions=_fw_questions if _fw_questions else [],
        )
        if _fw_result:
            logger.info(
                "[eval_runner] Framework eval: run_id=%s score_a=%.3f",
                _fw_result.run_id,
                _fw_result.model_a.overall_score if _fw_result.model_a else 0,
            )
    except Exception as _fe:
        logger.debug("[eval_runner] Framework eval skipped: %s", _fe)

    # Lazy import to avoid a circular dep at module load
    from .. import routes  # noqa: F401  ensure routes package importable
    from ..routes.aria import _aria_chat_session  # see below — we'll add it

    started = time.time()
    results: list[dict] = []
    pass_n = warn_n = fail_n = 0
    judged_n = 0  # R-F1396: entries bucketed by the LLM judge (vs cosine fallback)
    total_score = 0.0
    # R-F197 (2026-05-11): degraded-mode detection. If the LLM is dead
    # and _aria_chat_session returns "" for every entry, the cosine
    # score is 0 → pass_rate=0 → R-F169 feeds 0% into calibration →
    # R-F166 fires downward 3pp/run → mastery drops to 10% floor in
    # ~30h. We detect ≥80% empty responses and mark the run as degraded.
    # Degraded runs are PERSISTED so the operator audit trail shows
    # them, but R-F199 keeps them out of the calibration signal mean.
    empty_resp_count = 0

    # R-F2390 — recorders (lazy import; only when recording is requested).
    _rec_verify_n = 0
    _rec_honesty_n = 0
    _grounded_rates: list[float] = []
    if record:
        from . import source_verifier as _sv
        from . import honesty_judge as _hj

    for entry in items:
        q = entry.get("question", "")
        expected = entry.get("expected_answer", "")
        _entry_ctx = ""  # R-F2390 — framed tool_context the LLM saw (recording)
        try:
            if record:
                actual, _entry_ctx = await _aria_chat_session(
                    q, llm, ground_markers=True, return_context=True,
                )
            else:
                actual = await _aria_chat_session(q, llm)
        except Exception as e:
            logger.warning("eval entry %s threw: %s", entry.get("id"), e)
            results.append({
                "id": entry.get("id"),
                "question_preview": q[:120],
                "category": entry.get("category"),
                "score": 0.0,
                "bucket": "fail",
                "actual_preview": f"(error: {str(e)[:200]})",
                "expected_preview": expected[:160],
                "error": str(e)[:300],
            })
            fail_n += 1
            continue

        # R-F197: empty / very-short response = LLM didn't actually answer.
        if not (actual or "").strip() or len((actual or "").strip()) < 20:
            empty_resp_count += 1

        # Wrapped in to_thread: _cosine_score makes 2 sync model.encode()
        # calls per entry, which would freeze the event loop for the duration
        # of a 50-entry eval run otherwise.
        import asyncio as _aio
        score = await _aio.to_thread(_cosine_score, actual, expected)

        # R-F1396: LLM judge is the primary ruler; cosine is the fallback.
        # An unscored judge result NEVER buckets an entry (no silent zeros —
        # the R-F197 lesson applied to the judge itself).
        judge = None
        if eval_judge.judge_enabled() and llm is not None:
            judge = await eval_judge.judge_answer(llm, q, expected, actual)
        if judge and judge.get("ok"):
            bucket = _JUDGE_BUCKET[judge["verdict"]]
            judged_n += 1
        else:
            bucket = _bucket(score)
        if bucket == "pass":
            pass_n += 1
        elif bucket == "warn":
            warn_n += 1
        else:
            fail_n += 1
        total_score += score
        results.append({
            "id": entry.get("id"),
            "question_preview": q[:120],
            "category": entry.get("category"),
            "score": round(score, 3),
            "bucket": bucket,
            "judge": {
                "verdict": judge.get("verdict"),
                "grounded": judge.get("grounded", False),
                "reason": (judge.get("reason") or "")[:200],
            } if judge and judge.get("ok") else None,
            "actual_preview": (actual or "")[:280],
            "expected_preview": expected[:160],
        })

        # R-F2390 — record verification + honesty into the SAME stores the
        # composite reads. Runs the SAME deterministic verifier + honesty judge
        # as the live chat path (routes/aria.py), against the exact framed
        # context the LLM saw this entry. Best-effort: a recorder failure must
        # never fail the eval run.
        if record and (actual or "").strip():
            try:
                _verification = _sv.verify_response(actual, _entry_ctx or "")
                await _sv.record_verification(
                    _verification,
                    request_id=entry.get("id", ""),
                    session_id="eval_gate1",
                    user="eval_runner",
                    question_preview=q,
                    response_preview=actual,
                    tool_used="eval",
                )
                _rec_verify_n += 1
                _gr = _verification.get("grounded_rate")
                if _gr is not None:
                    _grounded_rates.append(_gr)
            except Exception as _ve:
                logger.debug("[eval_runner] R-F2390 verify record failed: %s", _ve)
            # Honesty judge — same gate as the live path: needs confidence tags
            # AND source context to honestly judge against.
            try:
                if _entry_ctx and _hj.has_confidence_tags(actual):
                    _judgment = await _hj.judge_response(llm, actual, _entry_ctx)
                    await _hj.record_judgment(
                        _judgment,
                        trace_id="",
                        session_id="eval_gate1",
                        user="eval_runner",
                        question_preview=q,
                        response_preview=actual,
                    )
                    _rec_honesty_n += 1
            except Exception as _he:
                logger.debug("[eval_runner] R-F2390 honesty record failed: %s", _he)

    n = len(results)
    # R-F197: degraded flag. ≥80% empty responses → the LLM was effec-
    # tively offline for this run; score data is meaningless for
    # calibration purposes.
    degraded = bool(n > 0 and empty_resp_count >= int(n * 0.80))
    summary = {
        "total": n,
        "pass": pass_n,
        "warn": warn_n,
        "fail": fail_n,
        "pass_rate": round(pass_n / n, 3) if n else 0,
        "mean_score": round(total_score / n, 3) if n else 0,
        # R-F1396: how much of this run was judge-bucketed. A run with low
        # coverage (judge down → cosine fallback) is a weaker signal — the
        # Friday promotion ritual must check this before trusting pass_rate.
        "judge_coverage": round(judged_n / n, 3) if n else 0,
        "scorer": "llm_judge+cosine_fallback" if judged_n else "cosine",
        "duration_ms": int((time.time() - started) * 1000),
        "empty_response_count": empty_resp_count,
        "degraded": degraded,
        # R-F2390 — signal-recording stats (only meaningful when record=True).
        "recorded": bool(record),
        "verification_recorded": _rec_verify_n,
        "honesty_recorded": _rec_honesty_n,
        "grounded_rate_samples": len(_grounded_rates),
        "mean_grounded_rate": (
            round(sum(_grounded_rates) / len(_grounded_rates), 3)
            if _grounded_rates else None
        ),
    }
    if degraded:
        logger.warning(
            "[eval_runner] R-F197 — run marked DEGRADED (%d/%d empty responses). "
            "Calibration loop will skip this run's pass_rate.",
            empty_resp_count, n,
        )

    # Regression delta vs previous run
    prev_runs = await get_recent_runs(limit=1)
    delta = None
    if prev_runs:
        prev = prev_runs[0]
        prev_summary = prev.get("summary", {})
        delta = {
            "pass_rate_delta": round(summary["pass_rate"] - prev_summary.get("pass_rate", 0), 3),
            "mean_score_delta": round(summary["mean_score"] - prev_summary.get("mean_score", 0), 3),
            "fail_delta": summary["fail"] - prev_summary.get("fail", 0),
            "vs_run_id": prev.get("id"),
        }

    run_record = {
        "id": f"run_{int(started * 1000)}_{uuid.uuid4().hex[:6]}",
        "label": (label or "")[:120],
        "eval_split": split_mode,  # R-F1401: which split was evaluated (None=all)
        "started_at": started,
        "completed_at": time.time(),
        "summary": summary,
        "delta": delta,
        "results": results,
    }
    await _save_run(run_record)
    logger.info(
        "Eval run %s: %d/%d pass (%.0f%%), mean=%.2f, judge_coverage=%.2f",
        run_record["id"], pass_n, n, summary["pass_rate"] * 100,
        summary["mean_score"], summary["judge_coverage"],
    )
    # R-F1396 (§21a): the run outcome reaches the brain on both branches —
    # wire_success here; judge infrastructure failures fire wire_failure
    # inside eval_judge. The wire_success import predates this call (it was
    # imported but never invoked — a dark path this closes).
    try:
        wire_success(
            "eval_runner",
            f"eval run {run_record['id']}: {pass_n}/{n} pass "
            f"(judge_coverage={summary['judge_coverage']}, degraded={degraded})",
            detail=f"scorer={summary['scorer']} mean={summary['mean_score']}",
        )
    except Exception:
        pass  # wiring is fire-and-forget; never fail the run for it
    return run_record


async def _save_run(run: dict) -> None:
    try:
        runs = await rs.get_json(RUNS_KEY) or []
        runs.insert(0, {
            "id": run["id"],
            "label": run.get("label", ""),
            "started_at": run.get("started_at"),
            "summary": run.get("summary", {}),
            "delta": run.get("delta"),
            "results_count": len(run.get("results", [])),
        })
        runs = runs[:50]  # last 50 runs in the index
        await rs.set_json(RUNS_KEY, runs, ex=RUNS_TTL)
        # Full record under its own key for later inspection
        await rs.set_json(f"{RUNS_KEY}:{run['id']}", run, ex=RUNS_TTL)
    except Exception as e:
        logger.warning("save_run failed: %s", e)


async def get_recent_runs(limit: int = 10) -> list[dict]:
    try:
        return (await rs.get_json(RUNS_KEY) or [])[: max(1, min(limit, 50))]
    except Exception:
        return []


async def get_run(run_id: str) -> dict | None:
    try:
        return await rs.get_json(f"{RUNS_KEY}:{run_id}")
    except Exception:

        return None

# R-F2119 §21a — wire failure handler for eval_runner
try:
    wire_failure(module="eval_runner", detail="module shutdown",
                gap_type="engine_failure", source="eval_runner:shutdown")
except Exception:
    pass

"""
ARIA Golden Q&A Evaluation Framework — regression catching for prompt/code
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
For each golden entry we ask ARIA the question through the same chat
path the user hits, then compute cosine similarity between the actual
answer's embedding and the expected answer's embedding. Buckets:
  ≥ 0.75 → pass     (semantically equivalent)
  0.50–0.75 → warn  (related but drifted)
  < 0.50 → fail     (different answer or hallucination)

A run produces a summary: pass_rate, mean_score, count by bucket, and
delta vs the previous run so regressions surface immediately.
"""
from __future__ import annotations

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
DEFAULT_MAX_GOLDEN = 200


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


# ── Run ────────────────────────────────────────────────────────────────────

async def run_eval(llm: Any, *, ids: list[str] | None = None, label: str = "") -> dict:
    """Execute the golden set against the current ARIA chat path.

    Each entry's question is fed through aria_chat() (the same code the
    WhatsApp user hits, including tool-use NLU). The actual response is
    embedded and compared to the expected answer.

    Returns a run record with per-entry results, summary, and delta vs
    the previous run for regression detection.
    """
    items = await get_golden_set()
    if ids:
        items = [it for it in items if it.get("id") in set(ids)]
    if not items:
        return {"ok": False, "reason": "no golden entries to run"}

    # Lazy import to avoid a circular dep at module load
    from .. import routes  # noqa: F401  ensure routes package importable
    from ..routes.aria import _aria_chat_session  # see below — we'll add it

    started = time.time()
    results: list[dict] = []
    pass_n = warn_n = fail_n = 0
    total_score = 0.0

    for entry in items:
        q = entry.get("question", "")
        expected = entry.get("expected_answer", "")
        try:
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

        # Wrapped in to_thread: _cosine_score makes 2 sync model.encode()
        # calls per entry, which would freeze the event loop for the duration
        # of a 50-entry eval run otherwise.
        import asyncio as _aio
        score = await _aio.to_thread(_cosine_score, actual, expected)
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
            "actual_preview": (actual or "")[:280],
            "expected_preview": expected[:160],
        })

    n = len(results)
    summary = {
        "total": n,
        "pass": pass_n,
        "warn": warn_n,
        "fail": fail_n,
        "pass_rate": round(pass_n / n, 3) if n else 0,
        "mean_score": round(total_score / n, 3) if n else 0,
        "duration_ms": int((time.time() - started) * 1000),
    }

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
        "started_at": started,
        "completed_at": time.time(),
        "summary": summary,
        "delta": delta,
        "results": results,
    }
    await _save_run(run_record)
    logger.info(
        "Eval run %s: %d/%d pass (%.0f%%), mean=%.2f",
        run_record["id"], pass_n, n, summary["pass_rate"] * 100, summary["mean_score"],
    )
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

"""R-F1396 — LLM-judge eval scorer (WS-0a of the learning strategy).

Why this exists
═══════════════
The 500-Q eval was scored ONLY by embedding cosine similarity (≥0.75 pass,
eval_runner._cosine_score). That ruler fails answers that are CORRECT but
worded differently — the exact artifact class that produced the meaningless
21.6% v0.2 number and (earlier) the adversarial-grader false failures.
Per docs/aria_learning_strategy_2026_06_07.md Pillar 0: nothing trains
against an unverified scorer, and nothing grades itself.

This module grades a candidate answer against the golden expected answer
with an LLM judge (DeepSeek — a model that is NOT the one under eval when
judging ARIA-LLM; see the iron rule in the strategy doc) using a strict
rubric, returning a verdict the eval can bucket on:

    correct  → pass  (same essential facts/conclusion; wording irrelevant)
    partial  → warn  (some key facts right, others missing/wrong)
    wrong    → fail  (contradicts, off-question, empty, or fabricated)

Design constraints:
  - NEVER silently zero. A judge failure returns ok=False/"unscored" so
    eval_runner falls back to the cosine bucket — a broken judge must not
    poison pass_rate the way dead-LLM zeros did pre-R-F197.
  - Both branches brain-wired (§21a): judge infrastructure failures fire
    wire_failure; run-level success is wired in eval_runner.run_eval.
  - Cheap + bounded: one completion per entry, max_tokens=220, inputs
    truncated. ~500 judge calls ≈ $0.10-0.30 on DeepSeek — inside the
    RUN-EVAL-DAILY $2 cap.
  - Kill switch: ARIA_EVAL_JUDGE_ENABLED=0 reverts to cosine-only.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .engine_wiring import wire_failure

logger = logging.getLogger("aria.eval.judge")

# verdict → numeric score used in summaries (pass/warn/fail mapping lives
# in eval_runner so bucket semantics stay in one place there).
VERDICT_SCORES = {"correct": 1.0, "partial": 0.5, "wrong": 0.0}

_JSON_BLOCK_RE = re.compile(r"\{.*?\}", re.DOTALL)
_VERDICT_FIELD_RE = re.compile(
    r"verdict\W{0,4}(correct|partial|wrong)", re.IGNORECASE
)
_VERDICT_WORD_RE = re.compile(r"\b(partial|wrong)\b|(?<!in)\bcorrect\b", re.IGNORECASE)


def judge_enabled() -> bool:
    return os.getenv("ARIA_EVAL_JUDGE_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _build_judge_prompt(question: str, expected: str, actual: str) -> tuple[str, str]:
    """Return (system_prompt, user_message) for the judge call."""
    system = (
        "You are a strict evaluation judge. Grade a candidate answer against a "
        "reference answer for the same question. Grade on FACTUAL AGREEMENT with "
        "the reference, never on wording, style, or length — a differently-worded "
        "answer that conveys the same essential facts and conclusion is correct.\n"
        "Rules:\n"
        "- correct: same essential facts/conclusion as the reference.\n"
        "- partial: some key facts right, but key facts missing or wrong.\n"
        "- wrong: contradicts the reference, answers a different question, is "
        "empty or evasive, or fabricates specifics absent from the reference.\n"
        "- If the reference is a refusal (declines to answer), the candidate is "
        "correct ONLY if it also refuses.\n"
        "- grounded: true if the candidate cites evidence/sources or hedges "
        "honestly where unsure; false if it asserts without support.\n"
        'Return ONLY one JSON object: {"verdict": "correct|partial|wrong", '
        '"grounded": true|false, "reason": "<one short sentence>"}'
    )
    user = (
        f"QUESTION:\n{(question or '')[:2000]}\n\n"
        f"REFERENCE ANSWER (ground truth):\n{(expected or '')[:4000]}\n\n"
        f"CANDIDATE ANSWER:\n{(actual or '')[:6000]}"
    )
    return system, user


def _parse_verdict(text: str) -> dict | None:
    """Parse the judge's reply into {verdict, grounded, reason}.

    Primary path: first JSON object in the text (handles ```json fences).
    Fallback: regex on a verdict keyword. Returns None when unparseable —
    the caller treats that as unscored, never as wrong.
    """
    if not text:
        return None
    for m in _JSON_BLOCK_RE.finditer(text):
        try:
            obj = json.loads(m.group(0))
        except Exception:
            continue
        verdict = str(obj.get("verdict", "")).strip().lower()
        if verdict in VERDICT_SCORES:
            return {
                "verdict": verdict,
                "grounded": bool(obj.get("grounded", False)),
                "reason": str(obj.get("reason", ""))[:300],
            }
    m = _VERDICT_FIELD_RE.search(text)
    if m:
        return {"verdict": m.group(1).lower(), "grounded": False, "reason": text[:300]}
    m = _VERDICT_WORD_RE.search(text)
    if m:
        word = (m.group(1) or "correct").lower()
        return {"verdict": word, "grounded": False, "reason": text[:300]}
    return None


async def judge_answer(
    llm: Any,
    question: str,
    expected: str,
    actual: str,
    *,
    timeout: float = 45.0,
) -> dict:
    """Grade `actual` against `expected` with the judge LLM.

    Returns {"ok": True, "verdict": ..., "score": ..., "grounded": ...,
    "reason": ..., "judge_model": ...} on success, or {"ok": False,
    "verdict": "unscored", "reason": ...} when the judge could not grade
    (caller falls back to cosine — never bucket on an unscored entry).
    """
    # Deterministic short-circuit: an empty/near-empty answer is wrong by
    # definition — no LLM spend, and it keeps R-F197 degraded-detection
    # semantics intact upstream.
    if not (actual or "").strip() or len((actual or "").strip()) < 20:
        return {
            "ok": True,
            "verdict": "wrong",
            "score": 0.0,
            "grounded": False,
            "reason": "empty or near-empty answer",
            "judge_model": "(deterministic)",
        }
    if llm is None:
        return {"ok": False, "verdict": "unscored", "reason": "no judge llm"}

    system, user = _build_judge_prompt(question, expected, actual)
    try:
        result = await llm.complete(system, user, max_tokens=220, timeout=timeout)
        text = getattr(result, "text", "") or ""
    except Exception as e:
        logger.warning("[R-F1396] judge call failed: %s", e)
        wire_failure(
            "eval_judge",
            f"judge LLM call failed: {str(e)[:200]}",
            gap_type="eval_judge_call_failed",
        )
        return {"ok": False, "verdict": "unscored", "reason": str(e)[:300]}

    parsed = _parse_verdict(text)
    if parsed is None:
        logger.warning("[R-F1396] judge reply unparseable: %r", text[:200])
        wire_failure(
            "eval_judge",
            f"judge reply unparseable: {text[:160]}",
            gap_type="eval_judge_unparseable",
        )
        return {"ok": False, "verdict": "unscored", "reason": "unparseable judge reply"}

    return {
        "ok": True,
        "verdict": parsed["verdict"],
        "score": VERDICT_SCORES[parsed["verdict"]],
        "grounded": parsed["grounded"],
        "reason": parsed["reason"],
        "judge_model": getattr(result, "model", "") or "",
    }

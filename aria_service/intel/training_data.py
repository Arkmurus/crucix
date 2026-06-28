"""
Training Data Collection — records ARIA interactions for future LLM fine-tuning.
Ported from lib/aria/training_data.mjs.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.intel.training")

TRAINING_DIR = Path("runs/training")
META_KEY = "crucix:training:meta"

_meta: dict = {"conversations": 0, "outcomes": 0, "brain_assessments": 0, "knowledge": 0, "corrections": 0, "think_responses": 0}


def _ensure_dir() -> None:
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)


def _append_jsonl(filename: str, obj: dict) -> None:
    _ensure_dir()
    path = TRAINING_DIR / filename
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, default=str) + "\n")


async def _save_meta() -> None:
    await rs.set_json(META_KEY, _meta)


async def init() -> None:
    global _meta
    saved = await rs.get_json(META_KEY)
    if saved:
        _meta.update(saved)
    _ensure_dir()
    logger.info(f"Training data: {_meta.get('conversations',0)} conversations recorded")


# ── Recording Functions ──────────────────────────────────────────────────────

async def record_conversation(
    system_prompt: str,
    user_message: str,
    aria_response: str,
    metadata: dict | None = None,
) -> None:
    """Record a chat interaction for supervised fine-tuning."""
    meta = metadata or {}
    # Extract confidence tags from response
    tags = re.findall(r"\[(CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE)\]", aria_response)

    obj = {
        "messages": [
            {"role": "system", "content": system_prompt[:4000]},
            {"role": "user", "content": user_message[:2000]},
            {"role": "assistant", "content": aria_response[:6000]},
        ],
        "meta": {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "conversation",
            "market": meta.get("market", ""),
            "topic": meta.get("topic", ""),
            "hadIntelContext": meta.get("hadIntelContext", False),
            "contextLength": meta.get("contextLength", 0),
            "layersInjected": meta.get("layersInjected", []),
            "confidenceTags": tags,
        },
    }
    _append_jsonl("conversations.jsonl", obj)
    _meta["conversations"] = _meta.get("conversations", 0) + 1
    await _save_meta()


async def record_think_response(question: str, think_output: dict) -> None:
    """Record a 6-step reasoning chain."""
    obj = {
        "messages": [
            {"role": "system", "content": "ARIA deep reasoning protocol — 6-step analysis"},
            {"role": "user", "content": question[:2000]},
            {"role": "assistant", "content": think_output.get("full_text", "")[:8000]},
        ],
        "meta": {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "think",
            "hasOrientation": bool(think_output.get("orientation")),
            "hasReasoning": bool(think_output.get("reasoning")),
            "confidence": think_output.get("conclusion", {}).get("confidence", 0) if isinstance(think_output.get("conclusion"), dict) else 0,
            "selfGrade": think_output.get("metacognition", {}).get("self_grade", "") if isinstance(think_output.get("metacognition"), dict) else "",
            "durationMs": think_output.get("duration_ms", 0),
        },
    }
    _append_jsonl("conversations.jsonl", obj)
    _meta["think_responses"] = _meta.get("think_responses", 0) + 1
    await _save_meta()


async def record_outcome(
    market: str,
    product: str,
    outcome: str,
    brain_recommendation: str = "",
    approach: str = "",
    win_prob: float = 0,
) -> None:
    """Record deal outcome for calibration training."""
    obj = {
        "messages": [
            {"role": "system", "content": "ARIA outcome training — learn which recommendations lead to wins"},
            {"role": "user", "content": f"Market: {market}, Product: {product}, Brain said: {brain_recommendation}, Approach: {approach}"},
            {"role": "assistant", "content": f"Outcome: {outcome}. Win probability was {win_prob:.0%}."},
        ],
        "meta": {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "outcome",
            "market": market,
            "product": product,
            "outcome": outcome,
            "winProb": win_prob,
        },
    }
    _append_jsonl("outcomes.jsonl", obj)
    _meta["outcomes"] = _meta.get("outcomes", 0) + 1
    await _save_meta()


async def record_correction(
    original_query: str,
    original_response: str,
    correction: str,
    correct_answer: str = "",
) -> None:
    """Record user correction — negative example for unlearning."""
    obj = {
        "messages": [
            {"role": "system", "content": "ARIA correction — this response was wrong"},
            {"role": "user", "content": original_query[:2000]},
            {"role": "assistant", "content": original_response[:4000]},
        ],
        "meta": {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "correction",
            "correction": correction[:1000],
            "correctAnswer": correct_answer[:2000],
            "isNegativeExample": True,
        },
    }
    _append_jsonl("conversations.jsonl", obj)
    _meta["corrections"] = _meta.get("corrections", 0) + 1
    await _save_meta()


async def record_knowledge_fact(topic: str, content: str, confidence: str) -> None:
    """Record a verified domain fact."""
    obj = {
        "messages": [
            {"role": "system", "content": "ARIA knowledge fact"},
            {"role": "user", "content": f"Topic: {topic}"},
            {"role": "assistant", "content": f"[{confidence}] {content}"},
        ],
        "meta": {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "knowledge",
            "topic": topic,
            "confidence": confidence,
        },
    }
    _append_jsonl("knowledge.jsonl", obj)
    _meta["knowledge"] = _meta.get("knowledge", 0) + 1
    await _save_meta()


# ── Export ────────────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="training_data",
        summary="Get Stats",
        source_id="training_data:R-F996",
    )

    return {**_meta}


# ── Confidence calibration ──────────────────────────────────────────────────
# Compares ARIA's self-tagged confidence ([CONFIRMED] / [PROBABLE] / [ASSESSED] /
# [UNCERTAIN] / [SPECULATIVE]) against actual outcomes — corrections (negative
# examples) and recorded deal outcomes (won/lost).
#
# A well-calibrated agent should be wrong ~5% of the time on [CONFIRMED] claims,
# ~25% on [PROBABLE], etc. If [CONFIRMED] claims are wrong 40% of the time, the
# agent is overconfident and we recommend tightening the threshold.

_CONFIDENCE_LEVELS = ["CONFIRMED", "PROBABLE", "ASSESSED", "UNCERTAIN", "SPECULATIVE"]
_EXPECTED_ERROR_RATE = {
    "CONFIRMED":   0.05,
    "PROBABLE":    0.25,
    "ASSESSED":    0.45,
    "UNCERTAIN":   0.65,
    "SPECULATIVE": 0.80,
}


async def get_calibration() -> dict:
    """Compute confidence calibration over the entire training log.

    Walks conversations.jsonl and outcomes.jsonl. For each [CONFIRMED] /
    [PROBABLE] / etc tag found in an ARIA response, checks whether the same
    interaction was later corrected (the response was wrong) or whether the
    associated outcome was a loss.

    Returns per-tag stats {samples, errors, error_rate, expected, calibration_delta}
    plus an overall calibration score and recommendations.
    """
    _ensure_dir()
    conv_path = TRAINING_DIR / "conversations.jsonl"
    outcomes_path = TRAINING_DIR / "outcomes.jsonl"

    # Build a per-tag counter
    counts: dict[str, dict[str, int]] = {
        lvl: {"samples": 0, "errors": 0} for lvl in _CONFIDENCE_LEVELS
    }

    # ── Pass 1: read all conversation records and build tag → record map ──
    records: list[dict] = []
    if conv_path.exists():
        try:
            with open(conv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning("calibration: failed to read conversations.jsonl: %s", e)

    # Build a quick "was this query later corrected?" lookup keyed by user_message[:200]
    correction_keys: set[str] = set()
    for r in records:
        meta = r.get("meta") or {}
        if meta.get("type") == "correction" or meta.get("isNegativeExample"):
            msgs = r.get("messages") or []
            user_msg = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
            if user_msg:
                correction_keys.add(user_msg[:200].strip().lower())

    # ── Pass 2: count tag samples and mark errors when corrections exist ──
    for r in records:
        meta = r.get("meta") or {}
        if meta.get("type") not in (None, "conversation", "think"):
            continue
        tags = meta.get("confidenceTags") or []
        if not tags:
            # Re-extract tags from the assistant response (legacy records)
            msgs = r.get("messages") or []
            asst = next((m.get("content", "") for m in msgs if m.get("role") == "assistant"), "")
            tags = re.findall(r"\[(CONFIRMED|PROBABLE|ASSESSED|UNCERTAIN|SPECULATIVE)\]", asst or "")
        if not tags:
            continue

        msgs = r.get("messages") or []
        user_msg = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
        was_corrected = bool(user_msg and user_msg[:200].strip().lower() in correction_keys)

        for tag in tags:
            if tag not in counts: continue
            counts[tag]["samples"] += 1
            if was_corrected:
                counts[tag]["errors"] += 1

    # ── Pass 3: outcomes.jsonl — count losses against confidence ──────────
    if outcomes_path.exists():
        try:
            with open(outcomes_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    meta = rec.get("meta") or {}
                    outcome = (meta.get("outcome") or "").lower()
                    win_prob = float(meta.get("winProb") or 0)
                    is_loss = "lost" in outcome or "lose" in outcome or "fail" in outcome
                    if is_loss and win_prob > 0:
                        # If win_prob ≥ 70% but we lost, that's a CONFIRMED-tier overconfidence
                        if win_prob >= 0.7:
                            counts["CONFIRMED"]["samples"] += 1
                            counts["CONFIRMED"]["errors"] += 1
                        elif win_prob >= 0.5:
                            counts["PROBABLE"]["samples"] += 1
                            counts["PROBABLE"]["errors"] += 1
                        else:
                            counts["ASSESSED"]["samples"] += 1
                            counts["ASSESSED"]["errors"] += 1
        except Exception as e:
            logger.warning("calibration: failed to read outcomes.jsonl: %s", e)

    # ── Build per-level report ────────────────────────────────────────────
    per_level = {}
    overconfident_tags: list[str] = []
    underconfident_tags: list[str] = []
    for lvl in _CONFIDENCE_LEVELS:
        c = counts[lvl]
        samples = c["samples"]
        errors = c["errors"]
        rate = (errors / samples) if samples > 0 else 0.0
        expected = _EXPECTED_ERROR_RATE[lvl]
        delta = rate - expected
        per_level[lvl] = {
            "samples": samples,
            "errors": errors,
            "error_rate": round(rate, 3),
            "expected_error_rate": expected,
            "calibration_delta": round(delta, 3),
            "status": (
                "overconfident" if delta > 0.15 else
                "underconfident" if delta < -0.15 else
                "well-calibrated"
            ) if samples >= 5 else "insufficient_data",
        }
        if samples >= 5 and delta > 0.15:
            overconfident_tags.append(lvl)
        if samples >= 5 and delta < -0.15:
            underconfident_tags.append(lvl)

    # Overall calibration score: 1.0 = perfect, lower = worse
    total_samples = sum(c["samples"] for c in counts.values())
    total_weighted_error = 0.0
    if total_samples > 0:
        for lvl, stats in per_level.items():
            if stats["samples"] >= 5:
                total_weighted_error += abs(stats["calibration_delta"]) * (stats["samples"] / total_samples)
    score = max(0.0, 1.0 - total_weighted_error * 2)

    # Build recommendations
    recommendations = []
    for tag in overconfident_tags:
        recommendations.append(
            f"Tighten threshold for [{tag}]: error rate "
            f"{per_level[tag]['error_rate']*100:.0f}% exceeds expected "
            f"{int(_EXPECTED_ERROR_RATE[tag]*100)}% — downgrade marginal claims to next tier."
        )
    for tag in underconfident_tags:
        recommendations.append(
            f"Loosen threshold for [{tag}]: error rate "
            f"{per_level[tag]['error_rate']*100:.0f}% is well below expected "
            f"{int(_EXPECTED_ERROR_RATE[tag]*100)}% — promote claims to higher tier."
        )
    if not recommendations:
        if total_samples < 50:
            recommendations.append(
                f"Calibration is provisional ({total_samples} samples). "
                "Need at least 50 tagged interactions per level for reliable assessment."
            )
        else:
            recommendations.append("Calibration is healthy across all confidence levels.")

    return {
        "total_samples": total_samples,
        "calibration_score": round(score, 3),
        "per_level": per_level,
        "overconfident_levels": overconfident_tags,
        "underconfident_levels": underconfident_tags,
        "recommendations": recommendations,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


async def export_training_data() -> dict:
    """Export all training data for fine-tuning."""
    _ensure_dir()
    data: list[dict] = []

    for fname in ["conversations.jsonl", "outcomes.jsonl", "knowledge.jsonl"]:
        path = TRAINING_DIR / fname
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            data.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

    return {
        "format": "jsonl",
        "compatible_with": ["llama3", "mistral", "qwen", "openai"],
        "total_examples": len(data),
        "breakdown": {**_meta},
        "data": data,
        "export_date": datetime.now(timezone.utc).isoformat(),
        "instructions": {
            "llama3": "python train.py --model meta-llama/Llama-3-8B --data training_data.jsonl --epochs 3",
            "mistral": "python train.py --model mistralai/Mistral-7B-v0.3 --data training_data.jsonl --epochs 3",
            "ollama": 'ollama create aria-v1 -f Modelfile # then set LLM_PROVIDER=ollama LLM_MODEL=aria-v1',
        },
    }

# R-F2119 §21a — wire failure handler for training_data
try:
    wire_failure(module="training_data", detail="module shutdown",
                gap_type="engine_failure", source="training_data:shutdown")
except Exception:
    pass

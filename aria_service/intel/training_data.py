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
    return {**_meta}


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

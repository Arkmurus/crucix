"""deepseek_clients — live DeepSeek implementations of the data-engine
generation interfaces (R-F1469).

These plug into data_engine_generate.py's injectable interfaces
(QuestionGenerator / AnswerGenerator) with real DeepSeek API calls, so the
generation pipeline can produce actual distillation training data. The
pipeline + interfaces (ARIA, R-F1467) stay model-agnostic and mock-testable;
THIS module is the concrete live wiring (Claude lane).

Distillation note (per the validated strategy): DeepSeek writes the questions
AND the strong reference answers -> ARIA learns to mimic DeepSeek = distillation
to ~parity, the correct bootstrap off 14%. NOT sovereign-beyond-teacher.

V02AnswerProvider (DPO rejected = v0.2's actual answers) is NOT here — it needs
the RunPod pod serving v0.2; that's a separate pod-side client.

Env: DEEPSEEK_API_KEY (or ARIA_DEEPSEEK_API_KEY).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from aria_service.learning.data_engine_generate import (
    AnswerGenerator,
    QuestionGenerator,
)
from aria_service.intel.engine_wiring import wire_success, wire_failure  # R-F2489 §21a

logger = logging.getLogger("aria.learning.deepseek_clients")

_DEEPSEEK_URL = os.environ.get("ARIA_DEEPSEEK_URL", "https://api.deepseek.com/v1")
_DEEPSEEK_MODEL = os.environ.get("ARIA_DEEPSEEK_MODEL", "deepseek-chat")


def _api_key() -> str:
    return (os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("ARIA_DEEPSEEK_API_KEY") or "").strip()


async def _chat(messages: list[dict], *, max_tokens: int = 800,
                temperature: float = 0.4) -> str:
    """One DeepSeek chat completion. Per-call timeout (no hung runs)."""
    import httpx
    key = _api_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {key}"}
    body = {"model": _DEEPSEEK_MODEL, "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await asyncio.wait_for(
                client.post(f"{_DEEPSEEK_URL}/chat/completions",
                            headers=headers, json=body),
                timeout=100.0,
            )
    except asyncio.TimeoutError:
        raise RuntimeError("deepseek call timeout after 100s")
    if resp.status_code != 200:
        raise RuntimeError(f"deepseek http_{resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


class DeepSeekQuestionGenerator(QuestionGenerator):
    """Generate N due-diligence questions for a topic via DeepSeek."""

    async def generate_questions(self, topic: str, n: int) -> list[str]:
        system = (
            "You generate due-diligence / compliance evaluation questions for "
            "training an analyst model. Questions must be specific, answerable "
            "from public knowledge, and varied (no near-duplicates)."
        )
        user = (
            f"Generate exactly {n} distinct, specific due-diligence questions on "
            f"the topic: '{topic}'. Vary the angle (entities, jurisdictions, "
            f"procedures, red flags). Return ONLY a JSON array of {n} question "
            f'strings, e.g. ["...", "..."]. No prose, no numbering.'
        )
        try:
            raw = await _chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                max_tokens=900, temperature=0.7,
            )
        except Exception as e:
            logger.warning("[deepseek_clients] question-gen failed for %s: %s", topic, e)
            # R-F2489 §21a — DeepSeek failure (was log-only/dark) reaches the brain.
            wire_failure(module="deepseek_clients",
                         detail=f"question-gen failed for {topic!r}: {e}",
                         gap_type="engine_failure", source="deepseek_clients")
            return []
        qs = _parse_questions(raw, n)
        wire_success(module="deepseek_clients", summary=f"generated {len(qs)} questions")
        return qs

    async def generate_questions_async(self, topic: str, n: int) -> list[str]:  # alias safety
        return await self.generate_questions(topic, n)


class DeepSeekAnswerGenerator(AnswerGenerator):
    """Generate a strong reference answer for a question via DeepSeek."""

    async def generate_answer(self, question: str, topic: str) -> str:
        system = (
            "You are a senior due-diligence / compliance analyst. Answer "
            "accurately and concisely. State uncertainty plainly; do NOT "
            "fabricate specific figures, names, or sources you are not sure of."
        )
        try:
            answer = (await _chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": question}],
                max_tokens=700, temperature=0.3,
            )).strip()
        except Exception as e:
            logger.warning("[deepseek_clients] answer-gen failed: %s", e)
            # R-F2489 §21a — DeepSeek failure (was log-only/dark) reaches the brain.
            wire_failure(module="deepseek_clients",
                         detail=f"answer-gen failed: {e}",
                         gap_type="engine_failure", source="deepseek_clients")
            return ""
        wire_success(module="deepseek_clients", summary="generated reference answer")
        return answer


def _parse_questions(raw: str, n: int) -> list[str]:
    """Parse a model reply into a list of question strings.

    Prefers a JSON array; falls back to line/numbered parsing.
    """
    raw = (raw or "").strip()
    # Strip ```json fences if present.
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
    # Try JSON array first.
    try:
        arr = json.loads(raw)
        if isinstance(arr, list):
            qs = [str(x).strip() for x in arr if str(x).strip()]
            if qs:
                return qs[:n]
    except Exception:
        pass
    # Fallback: split lines, drop numbering / bullets.
    out: list[str] = []
    for line in raw.splitlines():
        s = re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", line).strip().strip('"')
        if len(s) > 12 and "?" in s:
            out.append(s)
    return out[:n]

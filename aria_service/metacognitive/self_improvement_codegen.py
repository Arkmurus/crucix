"""ARIA Self-Improvement Code Generation — Layer 4 of the metacognitive stack.

When ARIA identifies a persistent capability gap — for example, she cannot
currently parse AIS vessel data, or she lacks a specific scraper — she can
generate the code to close that gap.

All generated code has status PENDING_REVIEW. Nothing auto-deploys.
This is the most advanced layer: ARIA is not just self-aware but
self-modifying (within safe boundaries).

Safety: triple-gated
  1. Only fires during the monthly gap-closure sprint (not on every query)
  2. Generated code is stored with PENDING_REVIEW — human must approve
  3. The autonomous engine's safety gates (rate/cost/dry_run) still apply
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..intel import redis_store as rs

if TYPE_CHECKING:
    from ..llm.provider import LLMProvider

logger = logging.getLogger("aria.metacognitive.codegen")

_PROPOSALS_LIST = "crucix:metacog:codegen:proposals"
_MAX_PROPOSALS = 50


_CODEGEN_SYSTEM = (
    "You are ARIA generating code to close an identified capability gap in "
    "your own research pipeline. Write production-ready Python that integrates "
    "with ARIA's existing stack: Fly.io, Redis, ChromaDB, Anthropic API, "
    "Brave Search. Include error handling, logging, and rate limiting. "
    "Be ambitious — close the gap completely, not partially."
)

_CODEGEN_USER = """CAPABILITY GAP TO CLOSE:
{gap_description}

SPECIFIC REQUIREMENTS:
{requirements}

EXISTING ARCHITECTURE:
- Python running on Fly.io (FastAPI + uvicorn)
- Redis for caching, queuing, and state (aria_service/intel/redis_store.py)
- ChromaDB for RAG vector storage
- Anthropic API via llm/provider.py abstraction (async complete())
- Brave Search for web queries
- Knowledge base via knowledge.store_fact()
- Cost tracking via intel/cost_tracker.py

Write the complete, deployable Python module with:
1. Module docstring explaining what gap this closes
2. How it integrates with existing architecture
3. What new capability this unlocks
4. Error handling and logging
5. A test function that validates the capability"""


async def generate_improvement_code(
    gap_description: str,
    requirements: str,
    domain: str,
    llm: "LLMProvider | None",
) -> dict:
    """Generate code to close an identified capability gap.

    Returns dict with generated code, status, and deployment notes.
    All proposals start as PENDING_REVIEW.
    """
    t0 = time.time()
    result = {"ok": False, "skipped": False, "duration_ms": 0}

    if not llm or not getattr(llm, "is_configured", False):
        result["skipped"] = True
        result["skipped_reason"] = "no_llm"
        return result

    prompt = _CODEGEN_USER.format(
        gap_description=gap_description[:2000],
        requirements=requirements[:1000],
    )

    try:
        from ..intel import cost_tracker
        with cost_tracker.feature("metacognitive"):
            llm_result = await llm.complete(
                _CODEGEN_SYSTEM,
                prompt,
                max_tokens=4096,
                timeout=60.0,
            )
        code = (getattr(llm_result, "text", "") or "").strip()
    except Exception as e:
        logger.warning("Code generation LLM call failed: %s", e)
        result["skipped"] = True
        result["skipped_reason"] = f"llm_error: {str(e)[:80]}"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    ts = datetime.now(timezone.utc).isoformat()
    proposal = {
        "gap_description": gap_description[:500],
        "domain": domain,
        "generated_code": code,
        "generated_at": ts,
        "status": "PENDING_REVIEW",
        "deployment_notes": (
            "Review this code before deployment. "
            "Test in staging environment first. "
            "Validate Redis and ChromaDB integration before going live."
        ),
    }

    await rs.lpush(_PROPOSALS_LIST, json.dumps(proposal))
    await rs.ltrim(_PROPOSALS_LIST, 0, _MAX_PROPOSALS - 1)

    result["ok"] = True
    result["proposal"] = proposal
    result["duration_ms"] = int((time.time() - t0) * 1000)
    return result


async def get_pending_proposals(limit: int = 10) -> list[dict]:
    raw = await rs.lrange(_PROPOSALS_LIST, 0, limit * 2)
    pending = []
    for r in raw:
        try:
            p = json.loads(r)
            if p.get("status") == "PENDING_REVIEW":
                pending.append(p)
                if len(pending) >= limit:
                    break
        except Exception:
            continue
    return pending

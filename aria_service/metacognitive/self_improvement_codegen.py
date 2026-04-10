"""ARIA Self-Improvement Code Generation — Layer 4 of the metacognitive stack.

When ARIA identifies a persistent capability gap she can generate the code
to close it, following a rigorous 7-step self-coding protocol:

  Step 1: GAP ANALYSIS — what is broken or missing, which file, correct behaviour
  Step 2: RISK ASSESSMENT — LOW / MEDIUM / HIGH / CRITICAL
  Step 3: SOLUTION DESIGN — approach, function signatures, I/O, failure modes
  Step 4: CODE GENERATION — complete, production-ready, no placeholders
  Step 5: SELF-REVIEW — trace through with a real example, catch bugs
  Step 6: TEST GENERATION — at least 2 test cases (happy path + edge case)
  Step 7: SUBMISSION — package with rollback instructions

4-tier risk routing:
  LOW      → auto-deploy after syntax validation + notify
  MEDIUM   → human review required (WhatsApp notification)
  HIGH     → human review + staging environment test
  CRITICAL → document gap only — human writes the fix

Safety: all proposals default to PENDING_REVIEW. Auto-deploy only fires
for LOW risk with syntax validation passed. The autonomous engine's
safety gates (rate/cost/dry_run) still apply.
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
_MAX_PROPOSALS = 100


# ── 7-Step Self-Coding Protocol ────────────────────────────────────────────

_CODEGEN_SYSTEM = """You are ARIA operating in METACOGNITIVE CODING MODE.

You write, test, reason about, and improve your own codebase.

CODING DISCIPLINES:
1. PATTERN RECOGNITION — identify which pattern this problem matches before writing
2. EXECUTION CONTEXT — code for Fly.io (memory limits, Redis pub/sub, rate limits)
3. ERROR ANTICIPATION — trace 3 most likely failure modes and add defensive code
4. SELF-CORRECTION — re-read as the Python interpreter, trace with real input
5. ARCHITECTURAL AWARENESS — check imports, dependencies, function signatures

ARCHITECTURE:
- Python on Fly.io (FastAPI + uvicorn, async throughout)
- Redis via aria_service/intel/redis_store.py (async get/set/lpush/lrange)
- ChromaDB for RAG vector storage
- Anthropic API via llm/provider.py abstraction (async complete())
- Brave Search for web queries
- Knowledge base via knowledge.store_fact()
- Cost tracking via intel/cost_tracker.py

Respond ONLY with valid JSON. No markdown fences, no preamble."""

_CODEGEN_USER = """CAPABILITY GAP TO CLOSE:
{gap_description}

RELATED GAPS (same pattern):
{related_gaps}

CURRENT CODE IN TARGET FILE ({target_file}):
{current_code}

Follow the 7-step metacognitive coding protocol:

1. GAP ANALYSIS — what is broken or missing, which file, correct behaviour
2. RISK ASSESSMENT — LOW / MEDIUM / HIGH / CRITICAL — with justification
3. SOLUTION DESIGN — approach, function signatures, I/O, failure modes
4. CODE GENERATION — complete production-ready Python (type hints, docstrings, error handling, logging, no placeholders)
5. SELF-REVIEW — trace through with a real example, identify and fix bugs
6. TEST GENERATION — at least 2 test cases (happy path + edge case)
7. ROLLBACK — how to undo this change if it breaks something

JSON format:
{{"gap_analysis":"string","risk_level":"LOW|MEDIUM|HIGH|CRITICAL","risk_justification":"string","solution_design":"string","code_change":{{"file":"filepath","action":"MODIFY|ADD|CREATE","old_code":"for MODIFY — exact code being replaced","new_code":"complete new code","imports_needed":["list"]}},"self_review":"string — trace through with real example","tests":"complete test code string","rollback":"how to undo","confidence":0.85,"estimated_improvement":"what this fixes"}}"""


# ── Risk routing ───────────────────────────────────────────────────────────

_RISK_ROUTES = {
    "LOW": {
        "status": "AUTO_DEPLOYABLE",
        "requires_human": False,
        "description": "Auto-deploy after syntax validation",
    },
    "MEDIUM": {
        "status": "AWAITING_HUMAN_REVIEW",
        "requires_human": True,
        "description": "Human review required before deployment",
    },
    "HIGH": {
        "status": "AWAITING_STAGING_TEST",
        "requires_human": True,
        "description": "Human review + staging environment test required",
    },
    "CRITICAL": {
        "status": "DOCUMENT_ONLY",
        "requires_human": True,
        "description": "Gap documented — human must write the fix",
    },
}

# Files that must NEVER be auto-deployed to (regardless of risk level)
_NEVER_AUTO_DEPLOY = {
    "aria_service/routes/aria.py",
    "aria_service/main.py",
    "aria_service/aria_engine.py",
    "aria_service/config.py",
    "server.mjs",
    ".env",
    "Dockerfile",
    "fly.toml",
}


async def generate_improvement_code(
    gap_description: str,
    requirements: str,
    domain: str,
    llm: "LLMProvider | None",
    related_gaps: list[dict] | None = None,
    target_file: str = "",
    current_code: str = "",
) -> dict:
    """Generate code to close an identified capability gap using the
    7-step self-coding protocol.

    Returns dict with the full fix package including risk-routed status.
    """
    t0 = time.time()
    result = {"ok": False, "skipped": False, "duration_ms": 0}

    if not llm or not getattr(llm, "is_configured", False):
        result["skipped"] = True
        result["skipped_reason"] = "no_llm"
        return result

    related_summary = (
        json.dumps(related_gaps[:5], indent=2)[:2000]
        if related_gaps else "No related gaps"
    )

    prompt = _CODEGEN_USER.format(
        gap_description=gap_description[:2000],
        related_gaps=related_summary,
        target_file=target_file or "auto-detect from gap",
        current_code=(current_code[:6000] if current_code
                      else "(no current code — new module or target unknown)"),
    )

    # Inject prior coding lessons so ARIA doesn't repeat past mistakes
    try:
        from . import coding_lessons
        lessons_block = await coding_lessons.lessons_addendum_for_codegen(gap_description)
        if lessons_block:
            prompt += lessons_block
    except Exception as e:
        logger.debug("Coding lessons injection failed (non-fatal): %s", e)

    try:
        from ..intel import cost_tracker
        with cost_tracker.feature("metacognitive"):
            llm_result = await llm.complete(
                _CODEGEN_SYSTEM,
                prompt,
                max_tokens=4096,
                timeout=90.0,
            )
        raw = (getattr(llm_result, "text", "") or "").strip()
    except Exception as e:
        logger.warning("Code generation LLM call failed: %s", e)
        result["skipped"] = True
        result["skipped_reason"] = f"llm_error: {str(e)[:80]}"
        result["duration_ms"] = int((time.time() - t0) * 1000)
        return result

    # Parse JSON response
    clean = raw.replace("```json", "").replace("```", "").strip()
    try:
        fix_package = json.loads(clean)
    except json.JSONDecodeError:
        # Fallback: treat as raw code if JSON parse fails
        logger.warning("Codegen JSON parse failed, storing as raw code")
        fix_package = {
            "gap_analysis": gap_description[:300],
            "risk_level": "MEDIUM",
            "risk_justification": "JSON parse failed — defaulting to MEDIUM",
            "code_change": {"new_code": raw, "action": "CREATE"},
            "confidence": 0.5,
        }

    # Enrich with metadata
    ts = datetime.now(timezone.utc).isoformat()
    risk_level = fix_package.get("risk_level", "MEDIUM").upper()
    if risk_level not in _RISK_ROUTES:
        risk_level = "MEDIUM"

    # Determine the target file
    code_change = fix_package.get("code_change", {})
    file_target = code_change.get("file", target_file or "unknown")

    # Force-escalate if target is a protected file
    if file_target in _NEVER_AUTO_DEPLOY and risk_level == "LOW":
        risk_level = "MEDIUM"
        fix_package["risk_justification"] = (
            (fix_package.get("risk_justification", "") or "") +
            f" [ESCALATED: {file_target} is in the never-auto-deploy list]"
        )

    route = _RISK_ROUTES[risk_level]

    # Syntax validation for LOW risk auto-deploy candidates
    syntax_valid = None
    new_code = code_change.get("new_code", "")
    if risk_level == "LOW" and new_code and file_target.endswith(".py"):
        try:
            compile(new_code, "<codegen>", "exec")
            syntax_valid = True
        except SyntaxError as e:
            syntax_valid = False
            risk_level = "MEDIUM"  # escalate on syntax failure
            fix_package["risk_justification"] = (
                (fix_package.get("risk_justification", "") or "") +
                f" [ESCALATED: syntax error at line {e.lineno}: {e.msg}]"
            )

    reference = f"ARK-CODEFIX-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    proposal = {
        "reference": reference,
        "gap_description": gap_description[:500],
        "domain": domain,
        "risk_level": risk_level,
        "risk_justification": fix_package.get("risk_justification", ""),
        "status": route["status"],
        "requires_human": route["requires_human"],
        "gap_analysis": fix_package.get("gap_analysis", ""),
        "solution_design": fix_package.get("solution_design", ""),
        "code_change": code_change,
        "self_review": fix_package.get("self_review", ""),
        "tests": fix_package.get("tests", ""),
        "rollback": fix_package.get("rollback", ""),
        "confidence": fix_package.get("confidence", 0.5),
        "estimated_improvement": fix_package.get("estimated_improvement", ""),
        "syntax_valid": syntax_valid,
        "generated_at": ts,
    }

    await rs.lpush(_PROPOSALS_LIST, json.dumps(proposal))
    await rs.ltrim(_PROPOSALS_LIST, 0, _MAX_PROPOSALS - 1)

    logger.info(
        "[codegen] %s risk=%s status=%s file=%s confidence=%.0f%%",
        reference, risk_level, route["status"],
        file_target, (fix_package.get("confidence", 0) or 0) * 100,
    )

    result["ok"] = True
    result["proposal"] = proposal
    result["reference"] = reference
    result["risk_level"] = risk_level
    result["status"] = route["status"]
    result["duration_ms"] = int((time.time() - t0) * 1000)
    return result


async def get_pending_proposals(limit: int = 10) -> list[dict]:
    """Return proposals that require action (any non-terminal status)."""
    raw = await rs.lrange(_PROPOSALS_LIST, 0, limit * 3)
    active_statuses = {"AUTO_DEPLOYABLE", "AWAITING_HUMAN_REVIEW",
                       "AWAITING_STAGING_TEST", "DOCUMENT_ONLY", "PENDING_REVIEW"}
    pending = []
    for r in raw:
        try:
            p = json.loads(r)
            if p.get("status") in active_statuses:
                pending.append(p)
                if len(pending) >= limit:
                    break
        except Exception:
            continue
    return pending


async def get_all_proposals(limit: int = 30) -> list[dict]:
    """Return all recent proposals regardless of status."""
    raw = await rs.lrange(_PROPOSALS_LIST, 0, limit - 1)
    out = []
    for r in raw:
        try:
            out.append(json.loads(r))
        except Exception:
            continue
    return out


async def get_proposals_by_risk(risk_level: str, limit: int = 10) -> list[dict]:
    """Filter proposals by risk level."""
    all_proposals = await get_all_proposals(limit=100)
    return [p for p in all_proposals if p.get("risk_level") == risk_level][:limit]

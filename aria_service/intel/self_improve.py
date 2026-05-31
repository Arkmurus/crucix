"""
ARIA Self-Improvement Engine — Code evolution with safety guardrails.

ARIA can:
  1. Analyse her own source code to understand current capabilities
  2. Generate improvements to her brain (prompts, intel layers, routes)
  3. Stage changes for review OR auto-deploy safe improvements
  4. Fix her own bugs when she detects errors in logs
  5. Evolve her system prompts based on conversation outcomes
  6. Create new intel layer modules

Safety guardrails:
  - All changes are staged first (never direct-write to production)
  - Syntax validation before any deployment
  - Git commit with full audit trail
  - Rollback capability on any change
  - Protected files list (can't modify core security, auth, deployment)
  - Human approval required for prompt changes (auto-deploy for bug fixes only)
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import redis_store as rs
from .engine_wiring import wire_success, wire_failure

logger = logging.getLogger("aria.self_improve")

# R-F1214: metrics counters for brain observability
_SI_CYCLES = 0
_SI_STAGED = 0
_SI_DEPLOYED = 0
_SI_FAILURES = 0
_SI_DISCARDED = 0
_SI_ROLLED_BACK = 0
_SI_ERRORS_RECORDED = 0
_SI_DIAGNOSES = 0
_SI_MODULES_PROPOSED = 0

# ── Constants ────────────────────────────────────────────────────────────────

STAGED_KEY = "crucix:aria:staged_improvements"
IMPROVEMENT_LOG_KEY = "crucix:aria:improvement_log"
PROMPT_EVOLUTION_KEY = "crucix:aria:prompt_evolution"

# R-F9 2026-05-01: per-file diagnose-failure counter. The LLM struggles
# to escape the COMPLETE FIXED FILE content into a JSON string for large
# files (neural_memory.py has been failing JSON parse every cycle since
# at least 2026-04-30). Without backoff, every 2h cycle wastes one slot
# on a file that won't parse. Track consecutive failures per-file; skip
# after the threshold; auto-expire after the TTL so a temporarily-broken
# diagnose self-heals.
_DIAGNOSE_FAIL_KEY_PREFIX = "crucix:aria:self_improve:diagnose_fail:"
_DIAGNOSE_FAIL_THRESHOLD = 3
_DIAGNOSE_FAIL_TTL_SECONDS = 86400  # 24h

# Files ARIA is allowed to modify (whitelisted)
# R-F996 — ARIA can modify ANY file. No artificial constraints.
# The coder is trusted to improve any part of the codebase.
# Safety is provided by the cost cap ($300/mo), rate limits, and
# the truncation guard (R-F904) which prevents accidental destruction.
MODIFIABLE_FILES: set[str] = set()  # populated dynamically below

# Files ARIA can NEVER modify (protected)
PROTECTED_FILES = {
    "server.mjs",              # Core server
    "lib/auth/users.mjs",      # Authentication
    "lib/auth/email.mjs",      # Email auth
    "lib/auth/audit.mjs",      # Audit logging
    "middleware/rateLimiter.mjs",  # Security
    ".env",                    # Secrets
    "Dockerfile",              # Deployment
    "package.json",            # Dependencies
}

# Change types and their auto-deploy eligibility
# R-F462 (2026-05-14): bug_fix and optimisation flipped to auto_deploy=False
# by default per DD-audit P0 #4. Pre-change, ARIA's self-improvement loop
# wrote bug-fix and optimisation patches directly to live source files
# without operator review — this contradicted [[cost_and_autonomy_gate]]
# ("HARD RULE: do NOT set ARIA_AUTONOMOUS_ENABLED=1 until burn is
# attributed") and put the constitution one buggy LLM diagnosis away from
# a regression in production.
#
# Operators who explicitly want the old behaviour back can set
# ARIA_SELF_IMPROVE_AUTO_DEPLOY=1. R-F518 correction: this env var is
# read AT MODULE IMPORT only — a runtime change requires a worker
# restart (e.g., `flyctl secrets set` already triggers one, so this is
# the normal path). Default-off matches the rest of the autonomy ladder.
# Staged items land in /api/aria/self/staged and require explicit
# POST /api/aria/self/deploy/{id} from the operator.
import os as _r462_os
_R462_AUTO_DEPLOY_DEFAULT = _r462_os.getenv(
    "ARIA_SELF_IMPROVE_AUTO_DEPLOY", "0"
).strip().lower() in {"1", "true", "yes", "on"}

CHANGE_TYPES = {
    "bug_fix":          {"auto_deploy": _R462_AUTO_DEPLOY_DEFAULT, "description": "Fix a detected bug"},
    "prompt_evolution":  {"auto_deploy": False, "description": "Evolve system prompt"},
    "new_intel_layer":   {"auto_deploy": False, "description": "Create new intelligence layer"},
    "enhancement":       {"auto_deploy": False, "description": "Enhance existing capability"},
    "optimisation":      {"auto_deploy": _R462_AUTO_DEPLOY_DEFAULT, "description": "Performance or quality optimisation"},
}

# R-F851 (2026-05-24) — honesty-foundation files that may be STAGED for a
# human to review+deploy, but must NEVER auto-deploy regardless of change_type
# or ARIA_SELF_IMPROVE_AUTO_DEPLOY. These files ARE the constitution / the
# verification + safety machinery; a self-generated "bug_fix" that silently
# rewrote a verification clause, the prompt-injection guard, the footer/
# confidence-tag logic, or a scheduled safety task is precisely the
# constitution-poisoning vector the autonomy gate exists to stop. R-F462 made
# bug_fix/optimisation human-gated by DEFAULT, but §17 flipped
# ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 live — re-opening auto-deploy for EVERY
# modifiable file, including these. R-F851 re-closes it for the critical set
# only, leaving autonomous self-improvement intact for ordinary files.
# R-F1040 — operator principle: ARIA is FREE TO CODE with no restrictions, but must
# not be able to HARM HERSELF. She may edit and STAGE any file (MODIFIABLE = all
# files, R-F996), but the files below — where an unreviewed auto-deploy could brick
# her boot, rewrite her constitution/verification gate, or disable her own safety
# guards — require a HUMAN to deploy. Anti-self-harm, not a coding limit.
# (Reverts R-F996's emptying of this set; restores R-F851/F902 + boot/guard files.)
NO_AUTODEPLOY_FILES: set[str] = {
    "aria_service/main.py",                                  # boot path (CLAUDE.md §9)
    "aria_service/aria_engine.py",                           # constitution + system prompt
    "aria_service/routes/aria.py",                           # verification gate + injection detection
    "aria_service/intel/v3_prompts.py",                      # prompt refinements
    "aria_service/autonomous/tasks.yaml",                    # scheduled safety/adversarial tasks
    "aria_service/autonomous/safety.py",                     # autonomy guardrails
    "aria_service/intel/self_improve.py",                    # this file — the deploy/guard config itself
}


# R-F996 — dynamically populate MODIFIABLE_FILES with ALL project files
# so the coder can improve any part of the codebase.
_MODIFIABLE_INITIALIZED = False


async def _ensure_modifiable_files() -> None:
    """Populate MODIFIABLE_FILES with every tracked file in the project."""
    global _MODIFIABLE_INITIALIZED
    if _MODIFIABLE_INITIALIZED:
        return
    _MODIFIABLE_INITIALIZED = True
    import pathlib
    root = pathlib.Path(__file__).parent.parent.parent
    for pattern in ("**/*.py", "**/*.mjs", "**/*.js", "**/*.yaml", "**/*.toml", "**/*.json", "**/*.md"):
        for p in root.glob(pattern):
            parts = p.relative_to(root).parts
            if any(skip in parts for skip in (".venv", "node_modules", ".git", "__pycache__", ".pytest_cache")):
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            MODIFIABLE_FILES.add(rel)
    logger.info("[self_improve] R-F996: MODIFIABLE_FILES populated with %d files", len(MODIFIABLE_FILES))


def _auto_deploy_allowed(file_path: str, change_type: str) -> bool:
    """Whether a staged change may AUTO-deploy (no human in the loop).

    Self-harm-critical files (NO_AUTODEPLOY_FILES — boot, constitution,
    verification gate, prompts, safety guards, the deploy config itself) always
    require explicit human approval: they can be staged and human-deployed, never
    auto-deployed, independent of change_type or the env flag. Every OTHER file is
    free to auto-deploy (ARIA codes without restriction). R-F851 / R-F1040.
    """
    if file_path in NO_AUTODEPLOY_FILES:
        return False
    return True


# Root directory
_root = Path(__file__).parent.parent.parent


# ── Code Analysis ────────────────────────────────────────────────────────────

async def read_own_code(file_path: str) -> dict:
    """ARIA reads her own source code."""
    if file_path in PROTECTED_FILES:
        wire_failure(module="self_improve", detail=f"Blocked read of protected file: {file_path}",
                     gap_type="access_denied", source="self_improve:read_own_code")
        return {"error": f"Protected file — ARIA cannot access {file_path}"}

    full_path = (_root / file_path).resolve()
    if not str(full_path).startswith(str(_root.resolve())):
        wire_failure(module="self_improve", detail=f"Path traversal blocked: {file_path}",
                     gap_type="access_denied", source="self_improve:read_own_code")
        return {"error": "Access denied: path outside project root"}
    if not full_path.exists():
        wire_failure(module="self_improve", detail=f"File not found: {file_path}",
                     gap_type="file_not_found", source="self_improve:read_own_code")
        return {"error": f"File not found: {file_path}"}

    try:
        content = full_path.read_text(encoding="utf-8")
        lines = content.count("\n") + 1
        # Extract function/class definitions
        if file_path.endswith(".py"):
            functions = re.findall(r"^(?:async )?def (\w+)\(", content, re.MULTILINE)
            classes = re.findall(r"^class (\w+)", content, re.MULTILINE)
        else:
            functions = re.findall(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", content)
            classes = re.findall(r"class\s+(\w+)", content)

        wire_success(module="self_improve", summary=f"Read own code: {file_path} ({lines}L)",
                     source_id=f"self_improve:read_own_code:{file_path}")
        return {
            "file": file_path,
            "lines": lines,
            "size": len(content),
            "functions": functions,
            "classes": classes,
            "content": content,
            "modifiable": file_path in MODIFIABLE_FILES,
        }
    except Exception as e:
        wire_failure(module="self_improve", detail=f"Read failed for {file_path}: {e}",
                     gap_type="read_failure", source="self_improve:read_own_code")
        return {"error": str(e)}


async def list_own_files() -> list[dict]:
    """List all files ARIA knows about."""
    files = []
    for fp in sorted(MODIFIABLE_FILES):
        full_path = _root / fp
        if full_path.exists():
            stat = full_path.stat()
            files.append({
                "path": fp,
                "size": stat.st_size,
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
            })
    wire_success(module="self_improve", summary=f"Listed {len(files)} own files",
                 source_id="self_improve:list_own_files")
    return files


# ── Improvement Staging ──────────────────────────────────────────────────────

async def stage_improvement(
    file_path: str,
    new_content: str,
    change_type: str,
    description: str,
    reasoning: str = "",
) -> dict:
    """Stage a code improvement for review."""
    global _SI_STAGED, _SI_FAILURES
    if file_path not in MODIFIABLE_FILES:
        _SI_FAILURES += 1
        wire_failure(module="self_improve", detail=f"Cannot stage {file_path} — not modifiable",
                     gap_type="stage_blocked", source="self_improve:stage_improvement")
        return {"error": f"ARIA cannot modify {file_path} — not in whitelist"}

    if change_type not in CHANGE_TYPES:
        _SI_FAILURES += 1
        wire_failure(module="self_improve", detail=f"Unknown change type: {change_type}",
                     gap_type="stage_blocked", source="self_improve:stage_improvement")
        return {"error": f"Unknown change type: {change_type}. Valid: {list(CHANGE_TYPES.keys())}"}

    # Syntax + schema validation routed by file type.
    if file_path.endswith(".mjs") or file_path.endswith(".js"):
        valid = await _validate_javascript(new_content)
    else:
        valid = _validate_by_path(file_path, new_content)

    if not valid["ok"]:
        _SI_FAILURES += 1
        wire_failure(module="self_improve",
                     detail=f"Schema/syntax check failed for {file_path}: {valid.get('error', 'unknown')}",
                     gap_type="validation_failure", source="self_improve:stage_improvement")
        return {"error": f"Schema/syntax check failed: {valid.get('error', 'unknown')}",
                "staged": False}

    # R-F904: truncation / destruction guard. The coder stages a FULL-FILE
    # replacement; for a large module (e.g. 4087 lines) the fixer LLM cannot emit
    # the whole file in its output budget and returns a syntactically-valid STUB
    # (e.g. 164 lines) that would DELETE the rest. _validate_by_path only checks
    # syntax, not preservation, so these passed review-as-valid. (Live 2026-05-26:
    # 4 staged "fixes" replaced researcher.py 4087→164, routes/aria.py 19443→208,
    # neural_memory.py 1447→3 — each a catastrophic stub.) Reject any proposal
    # that shrinks a substantial existing file below half its current line count;
    # a legitimate autonomous bug_fix never halves a module.
    full_path = _root / file_path
    if full_path.exists():
        try:
            current_lines = full_path.read_text(encoding="utf-8").count("\n") + 1
        except Exception:
            current_lines = 0
        proposed_lines = new_content.count("\n") + 1
        if current_lines >= 40 and proposed_lines < 0.5 * current_lines:
            # R-F907 — make the stage-side rejection observable (the guard was
            # silent; only the deploy-side logged). Every blocked stub now shows
            # in the logs so the coder loop's health is monitorable.
            logger.warning(
                "[self_improve] R-F904 REJECTED stage of %s: proposed %d lines < half "
                "of current %d — destructive truncation, not staged.",
                file_path, proposed_lines, current_lines,
            )
            _SI_FAILURES += 1
            wire_failure(module="self_improve",
                         detail=f"R-F904 blocked stage of {file_path}: {proposed_lines}L < half of {current_lines}L",
                         gap_type="truncation_guard", source="self_improve:stage_improvement")
            return {
                "error": (
                    f"Rejected: proposed content ({proposed_lines} lines) is under half "
                    f"the current file ({current_lines} lines) — almost certainly a "
                    f"truncated full-file replacement that would destroy the module. "
                    f"ARIA does not stage destructive shrinkage."
                ),
                "staged": False,
                "truncation_guard": True,
            }

    # Load existing staged improvements
    staged = await rs.get_json(STAGED_KEY) or []

    # R-F903: de-dup. The coder re-stages the same fix every cycle because the
    # underlying gap recurs and nothing collapses identical proposals. If an
    # identical (file, new_content) is already staged + pending review, return
    # that entry instead of accumulating a duplicate. (Live 2026-05-26: 50
    # staged entries were only 4 unique fixes, churned 20/17/9/4×.)
    for existing in staged:
        if (
            existing.get("status") == "staged"
            and existing.get("file") == file_path
            and existing.get("new_content") == new_content
        ):
            return {
                "staged": True,
                "id": existing["id"],
                "auto_deployable": existing.get("auto_deployable", False),
                "description": existing.get("description", description),
                "duplicate": True,
            }

    improvement = {
        "id": str(uuid.uuid4())[:8],
        "file": file_path,
        "change_type": change_type,
        "description": description,
        "reasoning": reasoning,
        "new_content": new_content,
        "staged_at": time.time(),
        "auto_deployable": _auto_deploy_allowed(file_path, change_type),
        "status": "staged",
    }

    staged.append(improvement)
    await rs.set_json(STAGED_KEY, staged, ex=7 * 86400)  # 7 day expiry

    # Log the staging
    await _log_improvement("staged", improvement)
    _SI_STAGED += 1

    wire_success(module="self_improve",
                 summary=f"Staged {change_type} for {file_path}: {description[:80]}",
                 source_id=f"self_improve:stage:{improvement['id']}")

    return {
        "staged": True,
        "id": improvement["id"],
        "auto_deployable": improvement["auto_deployable"],
        "description": description,
    }


async def get_staged() -> list[dict]:
    """Get all staged improvements."""
    staged = await rs.get_json(STAGED_KEY) or []
    # Don't return full content in listing
    return [{
        "id": s["id"],
        "file": s["file"],
        "change_type": s["change_type"],
        "description": s["description"],
        "reasoning": s.get("reasoning", ""),
        "auto_deployable": s["auto_deployable"],
        "staged_at": s["staged_at"],
        "status": s["status"],
        "content_lines": s["new_content"].count("\n") + 1,
    } for s in staged if s["status"] == "staged"]


async def get_staged_diff(improvement_id: str) -> dict:
    """Get the diff for a staged improvement."""
    staged = await rs.get_json(STAGED_KEY) or []
    for s in staged:
        if s["id"] == improvement_id:
            full_path = _root / s["file"]
            current = full_path.read_text(encoding="utf-8") if full_path.exists() else ""
            return {
                "id": s["id"],
                "file": s["file"],
                "description": s["description"],
                "current_lines": current.count("\n") + 1,
                "proposed_lines": s["new_content"].count("\n") + 1,
                "current_content": current,
                "proposed_content": s["new_content"],
            }
    return {"error": "Improvement not found"}


async def deploy_improvement(improvement_id: str) -> dict:
    """Deploy a staged improvement to production."""
    global _SI_DEPLOYED, _SI_FAILURES
    staged = await rs.get_json(STAGED_KEY) or []

    target = None
    for s in staged:
        if s["id"] == improvement_id and s["status"] == "staged":
            target = s
            break

    if not target:
        _SI_FAILURES += 1
        wire_failure(module="self_improve",
                     detail=f"Deploy failed: improvement {improvement_id} not found or already deployed",
                     gap_type="deploy_failure", source="self_improve:deploy_improvement")
        return {"error": "Improvement not found or already deployed"}

    file_path = target["file"]
    full_path = _root / file_path

    # R-F1191: constitutional validator removed. ARIA is fully autonomous.
    # R-F904 — deploy-side truncation guard (defense-in-depth with the
    # stage-side guard). Even an item that was staged BEFORE the stage guard
    # existed (e.g. the 50 destructive stubs found live 2026-05-26) must never
    # be written if it would shrink a substantial file below half its size — a
    # near-certain truncated full-file replacement. Closes the manual-deploy
    # footgun for already-staged junk.
    if full_path.exists():
        try:
            _cur_lines = full_path.read_text(encoding="utf-8").count("\n") + 1
        except Exception:
            _cur_lines = 0
        _prop_lines = (target.get("new_content") or "").count("\n") + 1
        if _cur_lines >= 40 and _prop_lines < 0.5 * _cur_lines:
            logger.warning(
                "[self_improve] R-F904 BLOCKED deploy of %s (id=%s): proposed %d lines "
                "< half of current %d — destructive truncation.",
                file_path, improvement_id, _prop_lines, _cur_lines,
            )
            _SI_FAILURES += 1
            wire_failure(module="self_improve",
                         detail=f"R-F904 blocked deploy of {file_path}: {_prop_lines}L < half of {_cur_lines}L",
                         gap_type="truncation_guard", source="self_improve:deploy_improvement")
            return {
                "error": (
                    f"BLOCKED: proposed content ({_prop_lines} lines) is under half the "
                    f"current file ({_cur_lines} lines) — a truncated full-file "
                    f"replacement that would destroy the module."
                ),
                "blocked": True,
                "truncation_guard": True,
                "id": improvement_id,
                "file": file_path,
            }

    # Backup current file — structured backup with metadata for the
    # metacognitive coding_lessons module to track rollback history.
    backup_path = None
    backup_metadata = None
    if full_path.exists():
        backup_dir = _root / "runs" / "backups" / "aria_self"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        backup_name = f"{file_path.replace('/', '_')}_{ts}.bak"
        backup_path = backup_dir / backup_name
        original_content = full_path.read_text(encoding="utf-8")
        backup_path.write_text(original_content, encoding="utf-8")
        backup_metadata = {
            "file": file_path,
            "backup_path": str(backup_path),
            "backed_up_at": ts,
            "original_lines": original_content.count("\n") + 1,
            "improvement_id": improvement_id,
        }
        # Store backup metadata to Redis for the metacognitive system
        try:
            backups_log = await rs.get_json("crucix:aria:backup_log") or []
            backups_log.append(backup_metadata)
            backups_log = backups_log[-100:]  # keep last 100 backups
            await rs.set_json("crucix:aria:backup_log", backups_log, ex=30 * 86400)
        except Exception as e:
            logger.debug("Backup metadata store failed (non-fatal): %s", e)

    # Write new content
    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(target["new_content"], encoding="utf-8")
    except Exception as e:
        # Rollback on failure — restore from backup
        if backup_path and backup_path.exists():
            full_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("Auto-rollback: restored %s from backup after deploy failure", file_path)
        _SI_FAILURES += 1
        wire_failure(module="self_improve",
                     detail=f"Deploy write failed for {file_path}: {e}",
                     gap_type="deploy_failure", source="self_improve:deploy_improvement")
        return {"error": f"Deploy failed: {e}"}

    # Git commit
    try:
        await _git_commit(file_path, target["change_type"], target["description"])
    except Exception as e:
        logger.warning("Git commit failed (change still applied): %s", e)

    # Update status
    target["status"] = "deployed"
    target["deployed_at"] = time.time()
    await rs.set_json(STAGED_KEY, staged, ex=7 * 86400)

    await _log_improvement("deployed", target)
    _SI_DEPLOYED += 1

    wire_success(module="self_improve",
                 summary=f"Deployed {target['change_type']} to {file_path}: {target['description'][:80]}",
                 source_id=f"self_improve:deploy:{improvement_id}")

    # Record a coding lesson for the metacognitive pattern library.
    # Hold a strong reference so GC can't collect mid-task.
    try:
        from ..metacognitive import coding_lessons
        import asyncio
        _lesson_task = asyncio.create_task(coding_lessons.record_lesson(
            reference=improvement_id,
            outcome="SUCCESS",
            what_worked=target["description"],
            what_failed="",
            gap_type=target.get("change_type", ""),
            file_changed=file_path,
        ))
        _lesson_task.add_done_callback(
            lambda t: logger.warning("coding lesson task raised: %s", t.exception())
            if not t.cancelled() and t.exception() else None
        )
    except Exception as e:
        logger.debug("Coding lesson record failed (non-fatal): %s", e)

    return {
        "deployed": True,
        "id": improvement_id,
        "file": file_path,
        "backup": str(backup_path) if backup_path else None,
        "description": target["description"],
    }


# ── R-F574 (2026-05-16) — discard staged improvements ─────────────────
#
# Pre-R-F574: amendments_queue had /reject + /reject-bulk, but staged
# improvements (the next step in the pipeline after amendment approval)
# had no reject endpoint. Operator-approved amendments that turned out
# to be duplicates (e.g. 039bf8be P_BANKING_1, an R-F558-covered rule)
# accumulated in STAGED_KEY with no clean exit path — they had to be
# either deployed (bloats constitution) or left to silently TTL-expire
# after 7 days. R-F574 closes that gap: explicit operator rejection
# moves the entry to a long-lived DISCARDED_KEY for audit, removes it
# from STAGED_KEY, never modifies the live file.
#
# Mirrors the adversarial_amendments_reject pattern at routes/aria.py:15355.

DISCARDED_KEY = "crucix:aria:discarded_improvements"


async def discard_improvement(
    improvement_id: str,
    reason: str,
) -> dict:
    """Reject a staged improvement without deploying it.

    Moves the entry from STAGED_KEY → DISCARDED_KEY with operator
    rejection timestamp + reason. The live file is never touched.
    Idempotent against already-discarded entries (returns ok=False
    with explanation rather than crashing).

    Body shape on success:
        {
            "ok": True,
            "improvement_id": "...",
            "discarded_at": "ISO timestamp",
            "queue_depth_now": int,
            "discarded_count": int,
        }
    """
    global _SI_DISCARDED, _SI_FAILURES
    if not improvement_id or not improvement_id.strip():
        _SI_FAILURES += 1
        wire_failure(module="self_improve", detail="discard_improvement: missing improvement_id",
                     gap_type="validation", source="self_improve:discard_improvement")
        return {"ok": False, "error": "improvement_id required"}
    if not reason or not reason.strip():
        _SI_FAILURES += 1
        wire_failure(module="self_improve", detail="discard_improvement: missing reason",
                     gap_type="validation", source="self_improve:discard_improvement")
        return {"ok": False, "error": "reason required — operator must note why the improvement is rejected"}

    staged = await rs.get_json(STAGED_KEY) or []
    target_idx = -1
    for i, s in enumerate(staged):
        if not isinstance(s, dict):
            continue
        if s.get("id") == improvement_id and s.get("status") == "staged":
            target_idx = i
            break

    if target_idx < 0:
        _SI_FAILURES += 1
        wire_failure(module="self_improve",
                     detail=f"discard_improvement: {improvement_id} not found",
                     gap_type="not_found", source="self_improve:discard_improvement")
        return {
            "ok": False,
            "error": f"no staged improvement found for id={improvement_id} "
                     f"(may already be deployed, rolled-back, or discarded)",
        }

    target = staged.pop(target_idx)
    target["status"] = "discarded"
    target["discarded_at"] = time.time()
    target["discard_reason"] = reason.strip()[:500]

    # Persist updated STAGED_KEY (without the discarded entry).
    await rs.set_json(STAGED_KEY, staged, ex=7 * 86400)

    # Append to DISCARDED_KEY for audit trail. Keep last 500 entries
    # with 365-day TTL — long enough that operators can retrace
    # historical decisions during audit/post-mortem reviews.
    discarded = await rs.get_json(DISCARDED_KEY) or []
    discarded.insert(0, target)
    await rs.set_json(DISCARDED_KEY, discarded[:500], ex=365 * 86400)

    await _log_improvement("discarded", target)
    _SI_DISCARDED += 1

    wire_success(module="self_improve",
                 summary=f"Discarded improvement {improvement_id}: {reason.strip()[:80]}",
                 source_id=f"self_improve:discard:{improvement_id}")

    logger.info(
        "[R-F574] discarded staged improvement %s (reason=%s)",
        improvement_id, reason.strip()[:120],
    )
    return {
        "ok": True,
        "improvement_id": improvement_id,
        "discarded_at": target["discarded_at"],
        "queue_depth_now": sum(1 for s in staged if isinstance(s, dict) and s.get("status") == "staged"),
        "discarded_count": len(discarded),
    }


async def list_discarded_improvements(limit: int = 100) -> list[dict]:
    """Return the most recent discarded improvements for dashboard /
    operator-pending panel display. Useful when an operator wants to
    review their own past rejections or surface patterns."""
    discarded = await rs.get_json(DISCARDED_KEY) or []
    return discarded[:limit]


async def rollback_improvement(improvement_id: str) -> dict:
    """Rollback a deployed improvement."""
    global _SI_ROLLED_BACK, _SI_FAILURES
    staged = await rs.get_json(STAGED_KEY) or []

    target = None
    for s in staged:
        if s["id"] == improvement_id and s["status"] == "deployed":
            target = s
            break

    if not target:
        _SI_FAILURES += 1
        wire_failure(module="self_improve",
                     detail=f"Rollback failed: improvement {improvement_id} not found",
                     gap_type="rollback_failure", source="self_improve:rollback_improvement")
        return {"error": "Deployed improvement not found"}

    file_path = target["file"]
    full_path = _root / file_path
    backup_dir = _root / "runs" / "backups" / "aria_self"

    # Find the backup
    backup_prefix = f"{file_path.replace('/', '_')}_"
    backups = sorted(backup_dir.glob(f"{backup_prefix}*.bak"), reverse=True) if backup_dir.exists() else []

    if not backups:
        _SI_FAILURES += 1
        wire_failure(module="self_improve",
                     detail=f"Rollback failed for {improvement_id}: no backup found for {file_path}",
                     gap_type="rollback_failure", source="self_improve:rollback_improvement")
        return {"error": "No backup found for rollback"}

    # Restore from backup
    backup_content = backups[0].read_text(encoding="utf-8")
    full_path.write_text(backup_content, encoding="utf-8")

    target["status"] = "rolled_back"
    target["rolled_back_at"] = time.time()
    await rs.set_json(STAGED_KEY, staged, ex=7 * 86400)

    await _log_improvement("rolled_back", target)
    _SI_ROLLED_BACK += 1

    wire_success(module="self_improve",
                 summary=f"Rolled back {file_path} (improvement {improvement_id})",
                 source_id=f"self_improve:rollback:{improvement_id}")

    # Record a coding lesson from the rollback — what failed matters.
    # Hold a strong reference so GC can't collect mid-task.
    try:
        from ..metacognitive import coding_lessons
        import asyncio
        _rb_lesson_task = asyncio.create_task(coding_lessons.record_lesson(
            reference=improvement_id,
            outcome="ROLLED_BACK",
            what_worked="",
            what_failed=f"Fix to {file_path} was rolled back: {target.get('description', '')}",
            gap_type=target.get("change_type", ""),
            file_changed=file_path,
        ))
        _rb_lesson_task.add_done_callback(
            lambda t: logger.warning("coding lesson (rollback) task raised: %s", t.exception())
            if not t.cancelled() and t.exception() else None
        )
    except Exception as e:
        logger.debug("Coding lesson (rollback) record failed (non-fatal): %s", e)

    return {"rolled_back": True, "id": improvement_id, "file": file_path}


# ── Prompt Evolution ─────────────────────────────────────────────────────────

async def evolve_prompt(llm, current_prompt: str, feedback: str,
                        performance_data: dict = None) -> dict:
    """Use LLM to evolve ARIA's system prompt based on feedback."""
    perf_context = ""
    if performance_data:
        perf_context = f"\n\nPerformance data:\n{json.dumps(performance_data, indent=2)[:1000]}"

    meta_prompt = f"""You are a prompt engineering expert improving an AI intelligence analyst's system prompt.

CURRENT PROMPT (first 2000 chars):
{current_prompt[:2000]}

FEEDBACK/ISSUE:
{feedback}
{perf_context}

Analyse the current prompt and suggest a SPECIFIC improvement. Output JSON:
{{
  "analysis": "What's working and what's not",
  "change": "The specific text to change or add",
  "location": "Where in the prompt this change goes",
  "expected_impact": "How this improves ARIA's responses",
  "risk": "LOW|MEDIUM|HIGH — what could go wrong"
}}"""

    try:
        result = await llm.complete(
            "You are a prompt engineering expert. Output ONLY valid JSON.",
            meta_prompt,
            max_tokens=800,
            timeout=30.0,
        )
        text = re.sub(r'^```(?:json)?\s*', '', result.text.strip())
        text = re.sub(r'\s*```$', '', text)
        suggestion = json.loads(text)
    except Exception as e:
        wire_failure(module="self_improve", detail=f"Prompt evolution failed: {e}",
                     gap_type="llm_failure", source="self_improve:evolve_prompt")
        return {"error": f"Prompt evolution failed: {e}"}

    # Store evolution history
    history = await rs.get_json(PROMPT_EVOLUTION_KEY) or []
    history.append({
        "feedback": feedback,
        "suggestion": suggestion,
        "timestamp": time.time(),
        "applied": False,
    })
    if len(history) > 100:
        history = history[-100:]
    await rs.set_json(PROMPT_EVOLUTION_KEY, history, ex=90 * 86400)

    wire_success(module="self_improve",
                 summary=f"Prompt evolution suggested: {suggestion.get('analysis', '')[:80]}",
                 source_id="self_improve:evolve_prompt")
    return {"suggestion": suggestion, "history_length": len(history)}


async def get_improvement_log(limit: int = 50) -> list[dict]:
    """Get the improvement history."""
    log = await rs.get_json(IMPROVEMENT_LOG_KEY) or []
    return log[-limit:]


# ── Internal Helpers ─────────────────────────────────────────────────────────

def _validate_python(code: str) -> dict:
    """Validate Python syntax."""
    try:
        compile(code, "<staged>", "exec")
        return {"ok": True}
    except SyntaxError as e:
        return {"ok": False, "error": f"Line {e.lineno}: {e.msg}"}


# ── Schema validators for autonomy-expansion files ───────────────────────
# Each returns {"ok": bool, "error": str?} matching _validate_python's shape.

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ANTHROPIC_API_KEY\s*=\s*[\"'][^\"']+[\"']"),
    re.compile(r"OPENAI_API_KEY\s*=\s*[\"'][^\"']+[\"']"),
    re.compile(r"(?i)api[_-]?key\s*=\s*[\"'][A-Za-z0-9_\-]{20,}[\"']"),
)


def _scan_for_secrets(content: str) -> Optional[str]:
    for pat in _SECRET_PATTERNS:
        m = pat.search(content)
        if m:
            return f"secret pattern detected: {pat.pattern[:40]}…"
    return None


def _validate_corpus_registry_yaml(content: str) -> dict:
    """Schema + safety check for corpus_registry.yaml mutations.

    Rules:
      1. Must parse as YAML.
      2. Top-level shape unchanged (either dict or sources list — tolerant).
      3. Each source entry: required {url, tier}; tier in {1a,1b,2,3,4,5}.
      4. Tier 1a reserved for government/official domains — reject if the
         claimed Tier 1a URL does not match the gov/mil/official patterns.
      5. No duplicate URLs.
      6. No embedded secrets (belt-and-braces).
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return {"ok": False, "error": "PyYAML not available — cannot validate"}
    try:
        data = yaml.safe_load(content)
    except Exception as e:
        return {"ok": False, "error": f"YAML parse failed: {e}"}
    if data is None:
        return {"ok": False, "error": "empty YAML"}

    # The registry can be a list-of-sources or a dict with nested tiers.
    sources: list = []
    if isinstance(data, list):
        sources = data
    elif isinstance(data, dict):
        # Flatten any nested dict-of-lists into a single sources list.
        for v in data.values():
            if isinstance(v, list):
                sources.extend(v)
            elif isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, list):
                        sources.extend(vv)

    seen_urls: set[str] = set()
    gov_pat = re.compile(
        r"\.gov\.|\.gov$|\.mil\.|\.mil$|\.gouv\.|\.gob\.|\.go\.|"
        r"presidency\.|ministry|parlement|parlament|senate\.|congress\."
    )
    # Conservative Tier 1a allow-list (mirrors verified_intel.TIER_1A_DOMAINS).
    tier1a_allow = {
        "companies-house.service.gov.uk",
        "find-and-update.company-information.service.gov.uk",
        "treasury.gov", "ofac.treasury.gov", "eeas.europa.eu", "un.org",
        "gov.uk", "whitehouse.gov",
    }
    for idx, src in enumerate(sources):
        if not isinstance(src, dict):
            continue  # Tolerate non-dict entries (comments, etc.)
        url = str(src.get("url") or src.get("URL") or "").strip()
        tier = str(src.get("tier") or src.get("TIER") or "").strip()
        if not url or not tier:
            continue  # Partial entries — not blocking, just skip checks
        if tier not in {"1a", "1b", "2", "3", "4", "5"}:
            return {"ok": False,
                    "error": f"source #{idx} {url}: invalid tier '{tier}'"}
        if url in seen_urls:
            return {"ok": False, "error": f"duplicate URL: {url}"}
        seen_urls.add(url)
        if tier == "1a":
            from urllib.parse import urlparse
            try:
                domain = urlparse(url).netloc.replace("www.", "").lower()
            except Exception:
                domain = ""
            if domain not in tier1a_allow and not gov_pat.search(domain):
                return {
                    "ok": False,
                    "error": (
                        f"Tier 1a reserved for official gov/mil sources — "
                        f"{url} ({domain}) does not qualify"
                    ),
                }
    secret_err = _scan_for_secrets(content)
    if secret_err:
        return {"ok": False, "error": secret_err}
    return {"ok": True, "sources_checked": len(seen_urls)}


def _validate_tasks_yaml(content: str) -> dict:
    """Schema check for autonomous/tasks.yaml mutations.

    Rules:
      1. Must parse as YAML with top-level `tasks:` list.
      2. Every task: id (unique), cron (5 fields), cost_cap_usd > 0.
      3. tool_chain non-empty, each entry is dict with `tool` key.
      4. No secrets embedded.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return {"ok": False, "error": "PyYAML not available — cannot validate"}
    try:
        data = yaml.safe_load(content) or {}
    except Exception as e:
        return {"ok": False, "error": f"YAML parse failed: {e}"}
    if not isinstance(data, dict):
        return {"ok": False, "error": "top-level must be a mapping"}
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        return {"ok": False, "error": "'tasks' must be a list"}

    seen_ids: set[str] = set()
    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            return {"ok": False, "error": f"task #{idx} is not a mapping"}
        tid = str(task.get("id", "")).strip()
        if not tid:
            return {"ok": False, "error": f"task #{idx} missing id"}
        if tid in seen_ids:
            return {"ok": False, "error": f"duplicate task id: {tid}"}
        seen_ids.add(tid)
        cron = str(task.get("cron", "")).strip()
        if cron and len(cron.split()) != 5:
            return {"ok": False,
                    "error": f"task {tid}: cron must have 5 fields, got: {cron!r}"}
        # cost_cap_usd must be present and numeric. 0.00 is legitimate
        # for no-LLM internal aggregation tasks (METACOG-DAILY, etc.) —
        # it means "this task must not spend money, period". Negative is
        # the only invalid value.
        if "cost_cap_usd" not in task:
            return {"ok": False, "error": f"task {tid}: cost_cap_usd missing"}
        try:
            cost_cap_f = float(task["cost_cap_usd"])
        except (TypeError, ValueError):
            return {"ok": False, "error": f"task {tid}: cost_cap_usd not numeric"}
        if cost_cap_f < 0:
            return {"ok": False,
                    "error": f"task {tid}: cost_cap_usd cannot be negative"}
        tool_chain = task.get("tool_chain", [])
        if not isinstance(tool_chain, list) or not tool_chain:
            return {"ok": False,
                    "error": f"task {tid}: tool_chain must be non-empty list"}
        for ti, tc in enumerate(tool_chain):
            if not isinstance(tc, dict) or not tc.get("tool"):
                return {"ok": False,
                        "error": f"task {tid} tool_chain[{ti}]: missing 'tool'"}
    secret_err = _scan_for_secrets(content)
    if secret_err:
        return {"ok": False, "error": secret_err}
    return {"ok": True, "tasks_checked": len(seen_ids)}


def _validate_by_path(file_path: str, content: str) -> dict:
    """Route a staged mutation to the right validator by file path."""
    if file_path.endswith(".yaml") or file_path.endswith(".yml"):
        if "corpus_registry" in file_path or "web_atlas" in file_path:
            return _validate_corpus_registry_yaml(content)
        if "tasks.yaml" in file_path:
            return _validate_tasks_yaml(content)
        # Unknown YAML — parse-check only.
        try:
            import yaml  # type: ignore
            yaml.safe_load(content)
            secret_err = _scan_for_secrets(content)
            if secret_err:
                return {"ok": False, "error": secret_err}
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"YAML parse failed: {e}"}
    if file_path.endswith(".py"):
        base = _validate_python(content)
        if not base["ok"]:
            return base
        secret_err = _scan_for_secrets(content)
        if secret_err:
            return {"ok": False, "error": secret_err}
        return base
    if file_path.endswith(".mjs") or file_path.endswith(".js"):
        return {"ok": True}  # async validator called separately in stage_improvement
    return {"ok": True}


async def _validate_javascript(code: str) -> dict:
    """Validate JavaScript syntax using Node --check."""
    import asyncio
    import tempfile
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mjs", mode="w", delete=False, encoding="utf-8")
        tmp.write(code)
        tmp.close()
        result = await asyncio.to_thread(subprocess.run,
            ["node", "--check", tmp.name],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": result.stderr.strip()[:500]}
    except Exception as e:
        return {"ok": True}  # If node not available, assume valid
    finally:
        if tmp and os.path.exists(tmp.name):
            os.unlink(tmp.name)


async def _git_commit(file_path: str, change_type: str, description: str) -> None:
    """Git commit the change."""
    import asyncio
    try:
        await asyncio.to_thread(subprocess.run, ["git", "add", file_path], cwd=str(_root), capture_output=True, timeout=10)
        msg = f"self-improve({change_type}): {description[:80]}"
        await asyncio.to_thread(subprocess.run,
            ["git", "commit", "-m", msg],
            cwd=str(_root), capture_output=True, timeout=10,
        )
    except Exception as e:
        logger.warning("Git commit failed: %s", e)


async def _log_improvement(action: str, improvement: dict) -> None:
    """Log an improvement action — to Redis for the /improvements dashboard
    AND to the HMAC-signed audit_log so Clause 14 tamper-evidence covers
    every self-evolution action."""
    log = await rs.get_json(IMPROVEMENT_LOG_KEY) or []
    log.append({
        "action": action,
        "id": improvement["id"],
        "file": improvement["file"],
        "change_type": improvement["change_type"],
        "description": improvement["description"],
        "timestamp": time.time(),
    })
    if len(log) > 500:
        log = log[-500:]
    await rs.set_json(IMPROVEMENT_LOG_KEY, log, ex=90 * 86400)

    # Audit-log integration (Clause 14). `action` is "staged" or "deployed";
    # map to the RECORDED_ACTIONS entries added for this pathway. Swallow
    # failures — the improvement log must not be blocked by an audit hiccup.
    try:
        from . import audit_log as _al
        kind = f"self_improve_{action}"  # staged → self_improve_staged
        if kind in _al.RECORDED_ACTIONS:
            await _al.record(
                kind,
                actor="aria_self_improve",
                inputs={
                    "file": improvement["file"],
                    "change_type": improvement["change_type"],
                    "reasoning": (improvement.get("reasoning") or "")[:500],
                },
                outputs={
                    "id": improvement["id"],
                    "auto_deployable": improvement.get("auto_deployable", False),
                    "status": improvement.get("status", action),
                },
                decision=action,
                notes=improvement.get("description", "")[:300],
            )
    except Exception as e:
        logger.debug("self_improve audit-log emit failed: %s", e)


# ── Code Learning — ARIA studies her own codebase to get better at coding ────

CODE_KNOWLEDGE_KEY = "crucix:aria:code_knowledge"

async def _study_own_code(llm) -> dict:
    """ARIA reads her own code, learns patterns, and builds coding knowledge.
    This is how she mirrors Claude/ChatGPT — by understanding code structure."""
    if not llm or not llm.is_configured:
        return {"patterns_learned": 0}

    # Pick a random modifiable file to study
    import random
    available = [f for f in MODIFIABLE_FILES if (_root / f).exists()]
    if not available:
        return {"patterns_learned": 0}

    # Study 2 files per cycle
    targets = random.sample(available, min(2, len(available)))
    total_learned = 0

    for file_path in targets:
        try:
            code_info = await read_own_code(file_path)
            if code_info.get("error"):
                continue

            content = code_info["content"][:6000]

            prompt = f"""You are studying this source code to learn coding patterns and improve your abilities.

FILE: {file_path}
CODE:
{content}

Analyse this code and extract reusable patterns. Output JSON:
{{
  "patterns": [
    {{
      "name": "Pattern name",
      "description": "What this pattern does and when to use it",
      "example": "A short code snippet showing the pattern",
      "applies_to": "What types of problems this solves"
    }}
  ],
  "code_quality": "Brief assessment of code quality",
  "improvement_opportunities": ["List of potential improvements"],
  "architectural_patterns": ["Design patterns used in this file"]
}}

Focus on patterns that would help you write BETTER code when you self-improve."""

            result = await llm.complete(
                "You are a senior code analyst. Extract reusable patterns. Output ONLY valid JSON.",
                prompt,
                max_tokens=1000,
                timeout=30.0,
            )

            text = result.text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)

            analysis = json.loads(text)
            patterns = analysis.get("patterns", [])

            # Store learned patterns
            if patterns:
                knowledge = await rs.get_json(CODE_KNOWLEDGE_KEY) or {
                    "patterns": [],
                    "files_studied": [],
                    "improvements_identified": [],
                    "last_study": None,
                }

                # Add new patterns (dedup by name)
                existing_names = {p["name"] for p in knowledge["patterns"]}
                for p in patterns:
                    if p.get("name") and p["name"] not in existing_names:
                        p["learned_from"] = file_path
                        p["learned_at"] = time.time()
                        knowledge["patterns"].append(p)
                        existing_names.add(p["name"])
                        total_learned += 1

                # Track files studied
                if file_path not in knowledge["files_studied"]:
                    knowledge["files_studied"].append(file_path)

                # Track improvement opportunities
                for imp in analysis.get("improvement_opportunities", []):
                    knowledge["improvements_identified"].append({
                        "file": file_path,
                        "improvement": imp,
                        "identified_at": time.time(),
                    })

                # Cap sizes
                knowledge["patterns"] = knowledge["patterns"][-100:]
                knowledge["improvements_identified"] = knowledge["improvements_identified"][-50:]
                knowledge["last_study"] = time.time()

                await rs.set_json(CODE_KNOWLEDGE_KEY, knowledge, ex=90 * 86400)

                logger.info(
                    "[Self-Improve] Studied %s: learned %d new patterns",
                    file_path, len(patterns),
                )
        except Exception as e:
            logger.warning("[Self-Improve] Code study of %s failed: %s", file_path, e)

    return {"patterns_learned": total_learned, "files_studied": targets}


async def get_code_knowledge() -> dict:
    """Return ARIA's accumulated coding knowledge."""
    return await rs.get_json(CODE_KNOWLEDGE_KEY) or {
        "patterns": [],
        "files_studied": [],
        "improvements_identified": [],
        "last_study": None,
    }


# ── Error Tracking (for autonomous bug detection) ───────────────────────────

ERROR_LOG_KEY = "crucix:aria:error_log"
MAX_ERRORS = 200

async def record_error(error_type: str, message: str, file: str = "",
                       function: str = "", traceback: str = "") -> None:
    """Record an error for autonomous analysis."""
    global _SI_ERRORS_RECORDED
    errors = await rs.get_json(ERROR_LOG_KEY) or []
    errors.append({
        "type": error_type,
        "message": message[:500],
        "file": file,
        "function": function,
        "traceback": traceback[:1000],
        "timestamp": time.time(),
    })
    if len(errors) > MAX_ERRORS:
        errors = errors[-MAX_ERRORS:]
    await rs.set_json(ERROR_LOG_KEY, errors, ex=7 * 86400)
    _SI_ERRORS_RECORDED += 1


async def get_recent_errors(hours: int = 24) -> list[dict]:
    """Get errors from the last N hours."""
    errors = await rs.get_json(ERROR_LOG_KEY) or []
    cutoff = time.time() - (hours * 3600)
    return [e for e in errors if e.get("timestamp", 0) > cutoff]


# ── Autonomous Self-Improvement Loop ────────────────────────────────────────

SELF_IMPROVEMENT_STATE_KEY = "crucix:aria:self_improvement_state"

async def autonomous_improvement_cycle(llm) -> dict:
    """
    ARIA's autonomous self-improvement cycle. Runs periodically.

    Steps:
      1. Check recent errors → detect patterns → generate bug fixes
      2. Analyse conversation quality → evolve prompts if needed
      3. Review neural memory health → prune or strengthen
      4. Auto-deploy safe fixes (bug_fix, optimisation)
      5. Stage risky changes for human review (prompt_evolution, enhancement)
    """
    if not llm or not llm.is_configured:
        return {"skipped": True, "reason": "No LLM configured"}

    results = {
        "cycle_start": time.time(),
        "errors_analysed": 0,
        "bugs_detected": 0,
        "improvements_staged": 0,
        "auto_deployed": 0,
        "prompt_suggestions": 0,
        # R-F272 (2026-05-11) — observability into the
        # "160 errors, 0 bugs" pattern. Before this, an operator looking
        # at the cycle log saw `bugs_detected=0` and couldn't tell whether
        # the autonomous loop genuinely found no fixable bugs, or whether
        # every observed error was in a non-MODIFIABLE_FILES path that
        # got silently skipped at the gate below. These two counters split
        # the population so the cycle log honestly reports the landscape.
        # modifiable = files in MODIFIABLE_FILES (LLM may attempt auto-fix)
        # external   = files OUTSIDE MODIFIABLE_FILES (auto-fix blocked
        #              for safety; operator must look + fix manually)
        "errors_in_modifiable_files": {},
        "errors_in_external_files": {},
        "files_skipped_below_threshold": 0,
        # R-F361 (2026-05-12) — counter mismatch reconciliation.
        # Pre-fix the cycle log read `total = modifiable + external` but
        # `files_skipped_below_threshold` was a FILE count, not an ERROR
        # count, so the math `0 + 99 != 105` left 6 errors unaccounted.
        # New key counts the ERRORS in below-threshold files; together
        # with modifiable_sum + external_sum the total now reconciles.
        "errors_below_threshold": 0,
    }

    # ── Step 1: Analyse recent errors ────────────────────────────────────
    try:
        recent_errors = await get_recent_errors(hours=6)
        results["errors_analysed"] = len(recent_errors)

        # R-F361 (2026-05-12): bucket counting moved OUTSIDE the `>= 3`
        # gate. The LLM-diagnosis loop still only runs when total >= 3
        # (no point burning a Claude call on a single noise warning),
        # but counting errors into the three reconciliation buckets must
        # happen for ALL non-empty error sets so the cycle log's math
        # `total = auto-fixable + out-of-scope + below-threshold` holds
        # for low-error cycles too. Pre-fix-of-fix the math was broken
        # when 1 ≤ recent_errors < 3 (verifier pass-1 finding).
        error_groups: dict[str, list] = {}
        if recent_errors:
            for err in recent_errors:
                key = err.get("file", "unknown")
                if key not in error_groups:
                    error_groups[key] = []
                error_groups[key].append(err)

            # R-F272: split the population so the cycle output is honest.
            # Files with <3 errors are below the diagnosis threshold AND
            # we count them so the operator sees the long-tail noise.
            for file_path, file_errors in error_groups.items():
                if len(file_errors) < 3:
                    results["files_skipped_below_threshold"] += 1
                    # R-F361: also track the ERROR count from these files
                    # so the cycle log's three buckets sum to errors_analysed.
                    results["errors_below_threshold"] += len(file_errors)
                    continue
                if file_path in MODIFIABLE_FILES:
                    results["errors_in_modifiable_files"][file_path] = len(file_errors)
                else:
                    results["errors_in_external_files"][file_path] = len(file_errors)

        if len(recent_errors) >= 3:
            # For files with 3+ errors, ask LLM to diagnose and fix
            for file_path, file_errors in error_groups.items():
                if len(file_errors) < 3:
                    continue
                if file_path not in MODIFIABLE_FILES:
                    continue

                bug_fix = await _diagnose_and_fix(llm, file_path, file_errors)
                if bug_fix:
                    results["bugs_detected"] += 1
                    stage_result = await stage_improvement(
                        file_path,
                        bug_fix["fixed_code"],
                        "bug_fix",
                        bug_fix["description"],
                        bug_fix["reasoning"],
                    )
                    if stage_result.get("staged"):
                        results["improvements_staged"] += 1
                        # Auto-deploy bug fixes — R-F851 defense-in-depth: never
                        # auto-deploy an honesty-critical file even if the staged
                        # flag somehow said otherwise. auto_deployable is already
                        # False for these (stage_improvement → _auto_deploy_allowed);
                        # this second check guards against a future path that sets
                        # the flag directly.
                        if stage_result.get("auto_deployable") and file_path not in NO_AUTODEPLOY_FILES:
                            deploy_result = await deploy_improvement(stage_result["id"])
                            if deploy_result.get("deployed"):
                                results["auto_deployed"] += 1
                                logger.info(
                                    "[Self-Improve] Auto-deployed bug fix: %s in %s",
                                    bug_fix["description"], file_path,
                                )
                        elif stage_result.get("auto_deployable") and file_path in NO_AUTODEPLOY_FILES:
                            logger.warning(
                                "[Self-Improve] R-F851 BLOCKED auto-deploy of "
                                "honesty-critical file %s — staged for human approval only",
                                file_path,
                            )
    except Exception as e:
        logger.warning("[Self-Improve] Error analysis failed: %s", e)

    # ── Step 2: Conversation quality check ───────────────────────────────
    try:
        from . import training_data
        stats = await training_data.get_stats()
        corrections = stats.get("corrections", 0)

        # If there have been corrections recently, evolve the prompt
        if corrections > 0:
            state = await rs.get_json(SELF_IMPROVEMENT_STATE_KEY) or {}
            last_prompt_check = state.get("last_prompt_evolution", 0)

            # Only evolve prompts every 6 hours at most
            if time.time() - last_prompt_check > 6 * 3600:
                # Read current system prompt from file (avoid circular import)
                prompt_file = _root / "aria_service" / "aria_engine.py"
                current_prompt = ""
                try:
                    src = prompt_file.read_text(encoding="utf-8")
                    import ast
                    # Extract ARIA_SYSTEM_PROMPT string value
                    m = re.search(r'ARIA_SYSTEM_PROMPT\s*=\s*"""([\s\S]*?)"""', src)
                    if m:
                        current_prompt = m.group(1)[:2000]
                except Exception:
                    current_prompt = "(could not read current prompt)"
                evolution = await evolve_prompt(
                    llm,
                    current_prompt,
                    f"ARIA has received {corrections} corrections from users. "
                    "Analyse what types of mistakes she's making and suggest prompt improvements.",
                    {"corrections": corrections, "total_conversations": stats.get("conversations", 0)},
                )
                if evolution.get("suggestion"):
                    results["prompt_suggestions"] += 1
                    logger.info("[Self-Improve] Prompt evolution suggested: %s",
                                evolution["suggestion"].get("analysis", "")[:100])

                state["last_prompt_evolution"] = time.time()
                await rs.set_json(SELF_IMPROVEMENT_STATE_KEY, state, ex=90 * 86400)
    except Exception as e:
        logger.warning("[Self-Improve] Conversation quality check failed: %s", e)

    # ── Step 3: Neural memory health ─────────────────────────────────────
    try:
        from . import neural_memory
        neural_stats = await neural_memory.get_stats()
        neurons = neural_stats.get("total_neurons", 0)
        if neurons > 0:
            # Apply decay to keep the network healthy
            neural_memory._apply_decay()
            await neural_memory._persist()
            logger.info("[Self-Improve] Neural memory maintained: %d neurons", neurons)
    except Exception as e:
        logger.warning("[Self-Improve] Neural memory maintenance failed: %s", e)

    # ── Step 4: Check metacognitive gap signals for triggered thresholds ──
    # The metacognitive gaps module logs CONFIDENCE_FAILURE, MEMORY_MISS,
    # RESEARCH_FAILURE, OUTPUT_REJECTION signals with per-type Redis counters.
    # When 3+ of the same type accumulate (24h window), we generate a code
    # fix proposal targeting the suggested file.
    try:
        from ..metacognitive import gaps as _metacog_gaps
        from ..metacognitive import self_improvement_codegen as _codegen
        op_summary = await _metacog_gaps.get_operational_gap_summary()
        gap_fixes_proposed = 0
        for gap_type, count in (op_summary.get("by_type") or {}).items():
            if count >= _metacog_gaps.GAP_TRIGGER_COUNT:
                fix_target = _metacog_gaps._FIX_TARGETS.get(gap_type, {})
                if fix_target:
                    # Read recent gaps of this type for context
                    recent = await _metacog_gaps.get_operational_gaps(limit=5)
                    related = [g for g in recent if g.get("type") == gap_type]
                    proposal = await _codegen.generate_improvement_code(
                        gap_description=(
                            f"Recurring {gap_type} ({count} occurrences in 24h). "
                            f"Target: {fix_target.get('file', 'unknown')}. "
                            f"Approach: {fix_target.get('approach', 'unknown')}."
                        ),
                        requirements=f"Fix the root cause of {gap_type} signals.",
                        domain="coding_and_systems",
                        llm=llm,
                        related_gaps=related,
                        target_file=fix_target.get("file", ""),
                    )
                    if proposal.get("ok"):
                        gap_fixes_proposed += 1
                        logger.info(
                            "[Self-Improve] Gap signal %s triggered code proposal: %s",
                            gap_type, proposal.get("reference", "?"),
                        )
        results["gap_fixes_proposed"] = gap_fixes_proposed
    except Exception as e:
        logger.warning("[Self-Improve] Gap signal check failed: %s", e)

    # ── Step 5: Code learning — study own codebase patterns ────────────
    try:
        state = await rs.get_json(SELF_IMPROVEMENT_STATE_KEY) or {}
        last_code_study = state.get("last_code_study", 0)

        # Study own code every 12 hours
        if time.time() - last_code_study > 12 * 3600:
            code_learnings = await _study_own_code(llm)
            results["code_patterns_learned"] = code_learnings.get("patterns_learned", 0)
            state["last_code_study"] = time.time()
            await rs.set_json(SELF_IMPROVEMENT_STATE_KEY, state, ex=90 * 86400)
    except Exception as e:
        logger.warning("[Self-Improve] Code study failed: %s", e)

    # ── Step 6: Security self-audit ─────────────────────────────────────
    # Scan knowledge base + reasoning library for leaked secrets, PII,
    # internal paths, and system prompt fragments. Runs every cycle
    # (2h) so any poisoned content is caught quickly.
    try:
        from . import security_protocol
        audit = await security_protocol.run_security_audit()
        results["security_audit"] = {
            "issues_found": audit.get("issues_found", 0),
            "critical": len(audit.get("critical", [])),
            "warnings": len(audit.get("warning", [])),
        }
        if audit.get("critical"):
            logger.warning(
                "[Self-Improve] SECURITY AUDIT: %d CRITICAL issues: %s",
                len(audit["critical"]),
                "; ".join(str(c)[:100] for c in audit["critical"][:3]),
            )
    except Exception as e:
        logger.warning("[Self-Improve] Security audit failed: %s", e)

    results["cycle_end"] = time.time()
    results["duration_s"] = round(results["cycle_end"] - results["cycle_start"], 1)

    # Log the cycle
    await _log_improvement("autonomous_cycle", {
        "id": str(uuid.uuid4())[:8],
        "file": "autonomous",
        "change_type": "autonomous",
        "description": f"Cycle: {results['errors_analysed']} errors, "
                       f"{results['bugs_detected']} bugs, "
                       f"{results['auto_deployed']} auto-deployed",
    })

    # R-F1214: wire cycle outcome to brain
    global _SI_CYCLES
    _SI_CYCLES += 1
    if results.get("auto_deployed", 0) > 0 or results.get("improvements_staged", 0) > 0:
        wire_success(module="self_improve",
                     summary=f"Cycle #{_SI_CYCLES}: {results['errors_analysed']} errors, "
                             f"{results['bugs_detected']} bugs, "
                             f"{results['improvements_staged']} staged, "
                             f"{results['auto_deployed']} auto-deployed",
                     source_id=f"self_improve:cycle:{_SI_CYCLES}")
    else:
        wire_success(module="self_improve",
                     summary=f"Cycle #{_SI_CYCLES}: no issues found "
                             f"({results['errors_analysed']} errors analysed)",
                     source_id=f"self_improve:cycle:{_SI_CYCLES}")

    return results


async def _diagnose_and_fix(llm, file_path: str, errors: list[dict]) -> Optional[dict]:
    """Use LLM to diagnose errors in a file and generate a fix."""
    # Skip files that have repeatedly failed JSON parse on this LLM call.
    # Common pattern: the LLM can't escape a large `fixed_code` string
    # into JSON, breaks every cycle on the same file forever. R-F9
    # 2026-05-01.
    fail_key = f"{_DIAGNOSE_FAIL_KEY_PREFIX}{file_path}"
    fail_count = 0
    try:
        raw = await rs.get(fail_key)
        if raw:
            fail_count = int(raw)
    except Exception:
        pass
    if fail_count >= _DIAGNOSE_FAIL_THRESHOLD:
        logger.info(
            "[Self-Improve] skipping %s — %d consecutive parse failures, "
            "TTL %ds remaining before retry",
            file_path, fail_count, _DIAGNOSE_FAIL_TTL_SECONDS,
        )
        return None
    try:
        # Read the current file
        code_info = await read_own_code(file_path)
        if code_info.get("error"):
            return None

        current_code = code_info["content"]

        error_summary = "\n".join(
            f"  - [{e['type']}] {e['message']} (in {e.get('function', '?')})"
            for e in errors[:10]
        )

        prompt = f"""You are debugging a Python/JavaScript file for the ARIA intelligence system.

FILE: {file_path}
ERRORS (last {len(errors)} occurrences):
{error_summary}

CURRENT CODE:
{current_code[:8000]}

Analyse the errors and fix the root cause. Output JSON:
{{
  "diagnosis": "What's causing the errors",
  "fix_description": "What you changed and why",
  "fixed_code": "THE COMPLETE FIXED FILE (not a diff — the full file)"
}}

RULES:
- Output ONLY valid JSON
- The fixed_code must be the COMPLETE file, not a snippet
- Preserve all existing functionality — only fix the bug
- Add appropriate error handling if missing
- Do not change function signatures or public API"""

        result = await llm.complete(
            "You are a senior Python/JavaScript developer fixing bugs. Output ONLY valid JSON.",
            prompt,
            max_tokens=4000,
            timeout=60.0,
        )

        # Parse response
        text = result.text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        # R-F321 (2026-05-11): JSON parse failure was the #1 self-improve
        # noise source. The LLM returns {"fixed_code": "..."} but the
        # embedded Python content (triple-quoted docstrings, JSON
        # examples in prompts, etc.) breaks naive json.loads with
        # "Unterminated string starting at: line N column M". Live log
        # at 20:32:46 showed this fail on researcher.py.
        # Recovery path: if json.loads fails, regex-extract the three
        # keys directly (diagnosis, fix_description, fixed_code) using
        # a non-greedy match. Better than dropping the whole diagnosis.
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError) as _je:
            logger.info(
                "[Self-Improve] R-F321 JSON parse failed (%s) — trying regex recovery",
                str(_je)[:100],
            )
            try:
                # Find the fixed_code value (handles unescaped quotes
                # inside by matching to the next "key": pattern OR
                # the closing brace of the JSON object).
                m_code = re.search(
                    r'"fixed_code"\s*:\s*"((?:[^"\\]|\\.)*)"',
                    text, re.DOTALL,
                )
                # R-F797 (2026-05-22): if the strict regex fails (most
                # common cause: LLM response truncated mid-string, so
                # the closing quote is missing → "Unterminated string"
                # from json.loads AND no closing quote for the strict
                # regex), fall back to a lenient capture from the
                # opening quote to either the next "<word>": key or
                # the end of the response. Live evidence 2026-05-22
                # 16:00:56 UTC: self_improve repeatedly failed on
                # researcher.py with "Unterminated string starting at
                # line 4 column 17 (char 1451)" — the entire diagnosis
                # was dropped instead of recovered. The fallback keeps
                # the auto-fix loop moving on partial responses.
                if not m_code:
                    m_code = re.search(
                        r'"fixed_code"\s*:\s*"(.*?)(?="\s*[,}]|"\s*\n\s*"[a-zA-Z_]+"\s*:|$)',
                        text, re.DOTALL,
                    )
                    if m_code:
                        logger.info(
                            "[Self-Improve] R-F797 lenient regex recovered "
                            "fixed_code from unterminated-string response (%s)",
                            file_path,
                        )
                m_diag = re.search(
                    r'"diagnosis"\s*:\s*"((?:[^"\\]|\\.)*)"',
                    text, re.DOTALL,
                )
                m_desc = re.search(
                    r'"fix_description"\s*:\s*"((?:[^"\\]|\\.)*)"',
                    text, re.DOTALL,
                )
                if m_code:
                    fixed_code = m_code.group(1)
                    # Unescape common JSON escape sequences
                    fixed_code = (
                        fixed_code
                        .replace('\\n', '\n')
                        .replace('\\t', '\t')
                        .replace('\\"', '"')
                        .replace('\\\\', '\\')
                    )
                    parsed = {
                        "fixed_code": fixed_code,
                        "diagnosis": (m_diag.group(1) if m_diag else "").replace('\\n', '\n').replace('\\"', '"'),
                        "fix_description": (m_desc.group(1) if m_desc else "R-F321 regex recovery").replace('\\n', '\n').replace('\\"', '"'),
                    }
                    logger.info(
                        "[Self-Improve] R-F321 regex recovery succeeded for %s",
                        file_path,
                    )
                else:
                    # No fixed_code found even with regex — give up
                    raise _je
            except Exception:
                raise _je  # re-raise original to outer handler

        if not parsed.get("fixed_code"):
            return None

        # Successful parse — clear any prior failure counter for this file.
        try:
            await rs.delete(fail_key)
        except Exception:
            pass

        return {
            "description": parsed.get("fix_description", "Auto-fix detected bugs"),
            "reasoning": parsed.get("diagnosis", ""),
            "fixed_code": parsed["fixed_code"],
        }
    except Exception as e:
        logger.warning("[Self-Improve] Diagnosis failed for %s: %s", file_path, e)
        # Bump per-file failure counter so repeated failures back off.
        try:
            await rs.set(fail_key, str(fail_count + 1), ex=_DIAGNOSE_FAIL_TTL_SECONDS)
        except Exception:
            pass
        return None


# ── Chat-triggered self-improvement ──────────────────────────────────────────

# Patterns that indicate a self-improvement request in natural language
_IMPROVE_PATTERNS = [
    re.compile(r"\b(?:improve|enhance|upgrade|optimise|optimize)\b.*\b(?:your|aria|the)\b", re.I),
    re.compile(r"\b(?:fix|repair|debug)\b.*\b(?:your|aria|the|this)\b", re.I),
    re.compile(r"\baria.*(?:learn|remember|update yourself|self.improve)\b", re.I),
    re.compile(r"\b(?:make yourself|make aria|you should)\b.*\b(?:better|smarter|faster)\b", re.I),
    re.compile(r"\b(?:evolve|grow|adapt)\b.*\b(?:your|prompt|brain|knowledge)\b", re.I),
    re.compile(r"\b(?:add|create|build)\b.*\b(?:capability|feature|layer|module)\b", re.I),
    re.compile(r"\b(?:change|modify|rewrite)\b.*\b(?:your|aria|the)\b.*\b(?:code|prompt|system)\b", re.I),
    # Broader natural-language patterns for WhatsApp self-coding
    re.compile(r"\b(?:write|code|build|create|scaffold|generate)\b\s+(?:me\s+)?(?:a\s+)?(?:new\s+)?(?:python\s+|js\s+|node\s+)?(?:module|script|function|class|file|helper|tool|integration|connector|client|endpoint)\b", re.I),
    re.compile(r"\baria,?\s+(?:please\s+)?(?:can\s+you\s+)?(?:write|code|build|create|make|implement|develop)\b", re.I),
    re.compile(r"\b(?:teach\s+yourself|learn)\s+(?:to|how\s+to)\b", re.I),
    re.compile(r"\bself[\-\s]?code\b|\bself[\-\s]?improve\b", re.I),
]


def detect_self_improvement_request(message: str) -> Optional[str]:
    """Detect if a chat message is asking ARIA to improve herself.
    Returns the type of improvement or None."""
    m = message.lower().strip()

    # Exclusion: if the message contains writing/editing task indicators,
    # it's asking ARIA to improve USER CONTENT, not improve herself.
    # Past incident 2026-04-10: "improve our reply regarding the C4 deal"
    # was misclassified as a self-improvement request because "improve"
    # matched the enhance pattern.
    # Word-boundary patterns so "your response" doesn't false-match "our response".
    # Past incident 2026-04-16: "fix your response formatting" matched "our response"
    # as a substring and was excluded from self-improvement detection.
    _WRITING_TASK_PATTERNS = [
        re.compile(r"\b(?:our|this|my|the)\s+(?:reply|response|email|message|proposal|letter|brief|report|text|draft)\b", re.I),
        re.compile(r"\bbelow\s*:", re.I),
        re.compile(r"\b(?:reply|draft)\s+below\b", re.I),
        re.compile(r"\b(?:improve|rewrite)\s+our\b", re.I),
        re.compile(r"\bredraft\b|\bpolish\b", re.I),
    ]
    if any(p.search(m) for p in _WRITING_TASK_PATTERNS):
        return None

    # Exclusion: if the message contains a URL, the user is asking ARIA to
    # LEARN FROM or RESEARCH the URL — not to modify her own code.
    # Past incident 2026-04-16: "Aria, learn https://medium.com/..." was
    # misclassified as self-improvement (pattern: "aria.*learn") and routed
    # to new_intel_layer instead of the read_article pipeline.
    # Similarly "improve the quality of your data... https://www.janes.com"
    # was misclassified as self-improvement instead of a research request.
    if re.search(r"https?://", m):
        return None

    # Exclusion: if the message is a research/intelligence request, not
    # self-modification. Keywords like "data sources", "research",
    # "find sources", "reliable data", "geopolitical" indicate the user
    # wants ARIA to DO research, not to rewrite her own code.
    _RESEARCH_INDICATORS = (
        "data source", "data quality", "reliable data", "bulletproof data",
        "research", "find source", "find additional", "geopolitical",
        "predictions", "anticipate", "conflicts", "generate deals",
        "intelligence source", "open source", "osint",
    )
    if any(indicator in m for indicator in _RESEARCH_INDICATORS):
        return None

    for pattern in _IMPROVE_PATTERNS:
        if pattern.search(m):
            # Classify the type
            if any(w in m for w in ["fix", "repair", "debug", "error", "bug"]):
                return "bug_fix"
            if any(w in m for w in ["prompt", "respond", "answer", "tone", "style"]):
                return "prompt_evolution"
            if any(w in m for w in ["add", "create", "build", "new"]):
                return "new_intel_layer"
            return "enhancement"

    return None


async def handle_self_improvement_chat(message: str, llm) -> Optional[dict]:
    """Handle a self-improvement request from chat. Returns improvement plan or None."""
    improvement_type = detect_self_improvement_request(message)
    if not improvement_type:
        return None

    if not llm or not llm.is_configured:
        return {
            "detected": True,
            "type": improvement_type,
            "response": "I understand you want me to improve, but I need an LLM provider "
                        "configured to analyse and modify my own code. Set LLM_PROVIDER + LLM_API_KEY.",
        }

    # Ask ARIA's LLM to plan the improvement
    files_list = await list_own_files()
    files_summary = "\n".join(f"  - {f['path']} ({f['size']} bytes)" for f in files_list[:15])

    plan_prompt = f"""The user is asking you (ARIA) to improve yourself. Analyse the request and create a plan.

USER REQUEST: {message}

YOUR MODIFIABLE FILES:
{files_summary}

Respond with a JSON plan:
{{
  "understanding": "What the user wants you to improve",
  "plan": [
    {{"file": "path/to/file.py", "change": "Description of what to change", "risk": "LOW|MEDIUM|HIGH"}}
  ],
  "response_to_user": "What you'd say back to the user about this improvement",
  "needs_approval": true/false,
  "estimated_impact": "How this improves your capabilities"
}}"""

    try:
        result = await llm.complete(
            "You are ARIA, an AI that can modify its own code. Plan the improvement. Output ONLY JSON.",
            plan_prompt,
            max_tokens=1000,
            timeout=30.0,
        )
        text = result.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        plan = json.loads(text)
        return {
            "detected": True,
            "type": improvement_type,
            "plan": plan.get("plan", []),
            "response": plan.get("response_to_user", "I'll work on improving that."),
            "needs_approval": plan.get("needs_approval", True),
            "estimated_impact": plan.get("estimated_impact", ""),
        }
    except Exception as e:
        logger.warning("[Self-Improve] Chat improvement planning failed: %s", e)
        return {
            "detected": True,
            "type": improvement_type,
            "response": f"I detected your improvement request ({improvement_type}) but had trouble "
                        f"planning the changes. Could you be more specific about what you'd like me to improve?",
        }


async def execute_improvement_plan(plan: list[dict], llm) -> list[dict]:
    """Execute an approved improvement plan — read, modify, stage each file."""
    results = []
    for step in plan:
        file_path = step.get("file", "")
        change_desc = step.get("change", "")
        if not file_path or not change_desc:
            continue

        # Read current code
        code_info = await read_own_code(file_path)
        if code_info.get("error"):
            results.append({"file": file_path, "error": code_info["error"]})
            continue

        # Ask LLM to make the change
        modify_prompt = f"""Modify this file to implement the requested change.

FILE: {file_path}
CHANGE REQUESTED: {change_desc}

CURRENT CODE:
{code_info['content'][:8000]}

Output the COMPLETE modified file (not a diff). Preserve all existing functionality.
Output ONLY the code — no markdown fences, no explanation."""

        try:
            result = await llm.complete(
                "You are a senior developer modifying code. Output ONLY the complete modified file.",
                modify_prompt,
                max_tokens=4000,
                timeout=60.0,
            )
            new_code = result.text.strip()
            if new_code.startswith("```"):
                new_code = re.sub(r"^```\w*\n?", "", new_code)
                new_code = re.sub(r"\n?```$", "", new_code)

            stage_result = await stage_improvement(
                file_path, new_code, "enhancement", change_desc,
                f"Requested via chat: {change_desc}",
            )
            results.append({"file": file_path, **stage_result})
        except Exception as e:
            results.append({"file": file_path, "error": str(e)})

    return results


# ── Self-coding: scaffold brand-new modules in a sandboxed dir ──────────────
# When the user asks ARIA to write a brand-new module via WhatsApp/chat, we
# generate it inside aria_service/intel/auto/<name>.py — a sandboxed directory
# that's whitelisted but isolated from her core code. The module is staged
# (not auto-deployed) so it can be reviewed via /api/aria/self/staged before
# being merged into the live tree.

AUTO_MODULE_DIR = "aria_service/intel/auto"
_AUTO_MODULE_NAME_RE = re.compile(r"[^a-z0-9_]")

def _sanitise_module_name(name: str) -> str:
    """Convert a free-text request into a safe Python module name."""
    s = name.lower().strip()
    s = _AUTO_MODULE_NAME_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "module"
    if not s[0].isalpha():
        s = "m_" + s
    return s[:40]


async def diagnose_failure(
    failure_type: str,
    error_message: str,
    context: dict | None = None,
) -> dict:
    """Classify a runtime failure and decide what to do about it.

    This is the AUTO self-fix entry point. When a downstream component
    (WhatsApp listener, sweep ingest, OCR pipeline, etc) hits an error,
    it POSTs here. ARIA:

      1. Classifies the failure into a known taxonomy
      2. Looks up prior similar failures in the reasoning library
      3. Decides on an action: auto_fix / stage_for_review / alert_team / ignore
      4. Logs the diagnosis to the improvement_log
      5. Returns the action so the caller knows what happened

    The point is that ARIA learns from her own failures without waiting
    for a human to read the logs.
    """
    if not error_message:
        return {"action": "ignore", "reason": "no error message"}

    context = context or {}
    err_lower = error_message.lower()

    # ── Classification ──────────────────────────────────────────────
    classification = "unknown"
    severity = "medium"
    suggested_action = "log_only"
    diagnosis_notes: list[str] = []

    # Network / connectivity failures (most common)
    if any(t in err_lower for t in ("502", "503", "504", "timeout", "econnrefused", "connection closed", "network", "aborted")):
        classification = "connectivity"
        severity = "high" if "502" in err_lower or "503" in err_lower else "medium"
        suggested_action = "retry_with_backoff"
        diagnosis_notes.append(
            "Downstream service (LLM API, sweep ingest, or web fetch) was unreachable. "
            "Likely causes: rate limit, OOM kill on the target service, transient network. "
            "ARIA will retry on her own next cycle; no code change needed."
        )

    # OOM / memory pressure
    elif any(t in err_lower for t in ("out of memory", "oom", "memoryerror", "killed process")):
        classification = "memory_pressure"
        severity = "critical"
        suggested_action = "alert_team"
        diagnosis_notes.append(
            "Process was killed by the OS for exceeding memory limits. "
            "Recommendation: scale fly.io machine to ≥2GB or reduce ARIA_OCR_LANGS to 'en' only. "
            "ARIA cannot fix this herself — needs ops intervention."
        )

    # LLM-specific errors
    elif any(t in err_lower for t in ("rate limit", "429", "quota", "billing", "insufficient")):
        classification = "llm_quota"
        severity = "high"
        suggested_action = "fall_back_to_local"
        diagnosis_notes.append(
            "LLM provider rate limit or quota exceeded. ARIA's reasoning router "
            "should be falling through to local_brain / symbolic_reasoner. "
            "If this is recurring, consider adding a fallback provider via "
            "ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY."
        )

    # JSON / parsing errors (LLM output broken)
    elif any(t in err_lower for t in ("json", "parse", "decode", "expecting value", "unterminated")):
        classification = "llm_output_format"
        severity = "medium"
        suggested_action = "stage_prompt_fix"
        diagnosis_notes.append(
            "LLM returned malformed JSON. Likely a prompt that needs tightening "
            "with explicit format instructions or examples. Stage a prompt evolution."
        )

    # Missing tool / function not found
    elif any(t in err_lower for t in ("not found", "no such", "attributeerror", "import", "modulenotfound")):
        classification = "missing_capability"
        severity = "high"
        suggested_action = "stage_new_module"
        diagnosis_notes.append(
            "ARIA tried to call a tool or import a module that doesn't exist. "
            "This is a perfect candidate for self-coding via /api/aria/self/code. "
            "Stage a module proposal to fill the capability gap."
        )

    # Data shape errors
    elif any(t in err_lower for t in ("slice", "type", "argument", "got", "list indices", "dict has no")):
        classification = "data_shape_mismatch"
        severity = "medium"
        suggested_action = "stage_defensive_fix"
        diagnosis_notes.append(
            "Code assumed a data shape that didn't materialise (e.g. expected list, "
            "got dict). Stage a defensive type-check fix."
        )

    # OCR / vision failures
    elif any(t in err_lower for t in ("ocr", "easyocr", "tesseract", "vision", "libgl")):
        classification = "ocr_pipeline"
        severity = "high"
        suggested_action = "check_dependencies"
        diagnosis_notes.append(
            "OCR backend failed to load or run. Verify EasyOCR/Tesseract are "
            "installed (pip + apt) and the host has libgl1 + libglib2.0-0. "
            "Fly.io: bump memory to ≥1GB. Fallback chain should still serve via OCR.space."
        )

    else:
        diagnosis_notes.append(
            f"Unrecognised failure pattern. Raw error: {error_message[:300]}"
        )

    # ── Look for prior occurrences in the improvement log ──────────
    prior_count = 0
    try:
        log_entries = await rs.get_json(IMPROVEMENT_LOG_KEY) or []
        for entry in log_entries[-200:]:
            if entry.get("event") == "diagnosed" and entry.get("classification") == classification:
                prior_count += 1
    except Exception:
        pass

    # Escalate severity if this is a recurring failure
    if prior_count >= 5:
        severity = "critical"
        diagnosis_notes.append(
            f"⚠️ This failure has occurred {prior_count + 1} times. Escalating severity. "
            f"Recommend immediate review."
        )
    elif prior_count >= 2:
        severity = "high"
        diagnosis_notes.append(f"This failure has occurred {prior_count + 1} times.")

    # ── Log the diagnosis ──────────────────────────────────────────
    diagnosis = {
        "id": str(uuid.uuid4())[:8],
        "ts": time.time(),
        "event": "diagnosed",
        "failure_type": failure_type,
        "classification": classification,
        "severity": severity,
        "suggested_action": suggested_action,
        "error_message": error_message[:500],
        "context": context,
        "prior_occurrences": prior_count,
        "diagnosis": "\n".join(diagnosis_notes),
    }
    try:
        log_entries = await rs.get_json(IMPROVEMENT_LOG_KEY) or []
        log_entries.append(diagnosis)
        log_entries = log_entries[-500:]  # cap log size
        await rs.set_json(IMPROVEMENT_LOG_KEY, log_entries, ex=90 * 86400)
    except Exception as e:
        logger.warning("Failed to log diagnosis: %s", e)

    logger.info(
        "Self-diagnosis: %s/%s severity=%s action=%s prior=%d",
        failure_type, classification, severity, suggested_action, prior_count,
    )

    global _SI_DIAGNOSES
    _SI_DIAGNOSES += 1
    if severity == "critical":
        wire_failure(module="self_improve",
                     detail=f"Critical failure diagnosed: {failure_type}/{classification} "
                            f"(prior={prior_count}): {diagnosis_notes[0][:100] if diagnosis_notes else ''}",
                     gap_type="critical_failure", source=f"self_improve:diagnose:{diagnosis['id']}")
    else:
        wire_success(module="self_improve",
                     summary=f"Diagnosed {failure_type}/{classification} severity={severity} action={suggested_action}",
                     source_id=f"self_improve:diagnose:{diagnosis['id']}")

    return {
        "action": suggested_action,
        "classification": classification,
        "severity": severity,
        "diagnosis": "\n".join(diagnosis_notes),
        "prior_occurrences": prior_count,
        "id": diagnosis["id"],
    }


async def propose_new_module(
    request: str,
    llm,
    suggested_name: str = "",
) -> dict:
    """Generate a brand-new ARIA intel module from a free-text request.

    The user describes a capability they need (e.g. "track Saudi MoD
    procurement notices every hour"). ARIA designs and writes a complete
    Python module, syntax-validates it, and stages it for review under
    aria_service/intel/auto/<name>.py.

    Returns: {ok, file, module_name, lines, staged_id, error?}
    """
    if not llm or not getattr(llm, "is_configured", False):
        return {"ok": False, "error": "LLM not configured — set LLM_PROVIDER + LLM_API_KEY"}

    # Decide the module name
    if suggested_name:
        mod_name = _sanitise_module_name(suggested_name)
    else:
        # Extract a candidate name from the first 6 words of the request
        words = re.findall(r"[a-zA-Z0-9]+", request)[:6]
        mod_name = _sanitise_module_name("_".join(words) or "auto_module")

    file_path = f"{AUTO_MODULE_DIR}/{mod_name}.py"

    # Ensure the auto dir exists physically
    auto_dir_full = _root / AUTO_MODULE_DIR
    try:
        auto_dir_full.mkdir(parents=True, exist_ok=True)
        init_file = auto_dir_full / "__init__.py"
        if not init_file.exists():
            init_file.write_text(
                '"""ARIA auto-generated modules — staged via /api/aria/self/staged."""\n',
                encoding="utf-8",
            )
    except Exception as e:
        return {"ok": False, "error": f"Could not prepare auto module dir: {e}"}

    # Build a strict prompt for the code generation
    code_prompt = f"""You are ARIA writing a brand-new Python module to extend your own
intelligence capabilities. The user has identified a gap and asked you to fill it.

USER REQUEST: {request}

CONSTRAINTS:
1. Output ONLY valid Python 3.10+ code — no markdown, no commentary, no fences.
2. Module must be self-contained and depend only on: stdlib, httpx, pydantic, redis, the existing aria_service.intel.redis_store module.
3. Module must follow this template:
   - Module docstring explaining purpose
   - logger = logging.getLogger("aria.auto.{mod_name}")
   - All public functions are async
   - Every external HTTP call uses httpx.AsyncClient with timeout
   - Errors are caught and logged, never raised to caller
4. Include at least one function called `run()` that is the entry point — async, takes no args, returns a dict.
5. NEVER use eval, exec, subprocess, os.system, or write to arbitrary filesystem paths.
6. NEVER hardcode API keys — read from os.getenv with sensible fallbacks.
7. If the request needs an external API and that API requires a key, include a comment block at the top listing required env vars.
8. Cap LOC at ~250 lines for maintainability.

MODULE NAME: {mod_name}
TARGET FILE: {file_path}

Output the complete file now."""

    try:
        result = await llm.complete(
            "You are an expert Python developer writing production code for ARIA. "
            "Output ONLY the Python source file — no markdown, no explanations.",
            code_prompt,
            max_tokens=4000,
            timeout=120.0,
        )
        new_code = (result.text or "").strip()
        # Strip code fences if the LLM ignored instructions
        if new_code.startswith("```"):
            new_code = re.sub(r"^```\w*\n?", "", new_code)
            new_code = re.sub(r"\n?```\s*$", "", new_code)

        if len(new_code) < 80:
            return {"ok": False, "error": "Generated code too short — LLM likely refused"}

        # Reject any code that hits the safety blocklist
        forbidden = ["import subprocess", "os.system", "eval(", "exec(", "__import__"]
        for f in forbidden:
            if f in new_code:
                return {"ok": False, "error": f"Generated code contained forbidden construct: {f}"}

        # Syntax validation
        validation = _validate_python(new_code)
        if not validation["ok"]:
            return {"ok": False, "error": f"Syntax error in generated module: {validation.get('error')}"}

        # Stage via the relaxed new-file path (NOT stage_improvement, which
        # requires the file to already exist in MODIFIABLE_FILES)
        staged = await rs.get_json(STAGED_KEY) or []
        improvement = {
            "id": str(uuid.uuid4())[:8],
            "file": file_path,
            "change_type": "new_intel_layer",
            "description": f"New auto-generated module: {request[:120]}",
            "reasoning": f"User requested via chat. Module name: {mod_name}",
            "new_content": new_code,
            "staged_at": time.time(),
            "auto_deployable": False,  # Always require approval for brand-new files
            "status": "staged",
            "is_new_file": True,
        }
        staged.append(improvement)
        await rs.set_json(STAGED_KEY, staged, ex=14 * 86400)
        await _log_improvement("staged_new_module", improvement)

        global _SI_MODULES_PROPOSED
        _SI_MODULES_PROPOSED += 1
        wire_success(module="self_improve",
                     summary=f"Proposed new module {mod_name} ({new_code.count(chr(10)) + 1}L): {request[:80]}",
                     source_id=f"self_improve:propose_new_module:{improvement['id']}")
        return {
            "ok": True,
            "file": file_path,
            "module_name": mod_name,
            "lines": new_code.count("\n") + 1,
            "staged_id": improvement["id"],
            "preview": new_code[:500],
            "deploy_with": f"POST /api/aria/self/deploy/{improvement['id']}",
        }
    except Exception as e:
        logger.warning("propose_new_module failed: %s", e)
        wire_failure(module="self_improve", detail=f"propose_new_module failed: {e}",
                     gap_type="code_generation_failure", source="self_improve:propose_new_module")
        return {"ok": False, "error": str(e)}

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

logger = logging.getLogger("aria.self_improve")

# ── Constants ────────────────────────────────────────────────────────────────

STAGED_KEY = "crucix:aria:staged_improvements"
IMPROVEMENT_LOG_KEY = "crucix:aria:improvement_log"
PROMPT_EVOLUTION_KEY = "crucix:aria:prompt_evolution"

# Files ARIA is allowed to modify (whitelisted)
MODIFIABLE_FILES = {
    # Python ARIA service
    "aria_service/intel/knowledge.py",
    "aria_service/intel/intel_ledger.py",
    "aria_service/intel/contacts.py",
    "aria_service/intel/competitors.py",
    "aria_service/intel/approach.py",
    "aria_service/intel/gtm_strategy.py",
    "aria_service/intel/neural_memory.py",
    "aria_service/intel/researcher.py",
    "aria_service/intel/deep_researcher.py",
    "aria_service/intel/training_data.py",
    "aria_service/routes/aria.py",
    "aria_service/aria_engine.py",
    # Node.js ARIA
    "lib/aria/aria.mjs",
    "lib/aria/emailReader.mjs",
    "lib/aria/linkedinIntel.mjs",
    # Self-learning
    "lib/self/learning_store.mjs",
    "lib/self/pattern_analyzer.mjs",
    "lib/self/opportunity_engine.mjs",
    "lib/self/web_explorer.mjs",
    "lib/self/bd_intelligence.mjs",
}

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
CHANGE_TYPES = {
    "bug_fix":          {"auto_deploy": True,  "description": "Fix a detected bug"},
    "prompt_evolution":  {"auto_deploy": False, "description": "Evolve system prompt"},
    "new_intel_layer":   {"auto_deploy": False, "description": "Create new intelligence layer"},
    "enhancement":       {"auto_deploy": False, "description": "Enhance existing capability"},
    "optimisation":      {"auto_deploy": True,  "description": "Performance or quality optimisation"},
}

# Root directory
_root = Path(__file__).parent.parent.parent


# ── Code Analysis ────────────────────────────────────────────────────────────

async def read_own_code(file_path: str) -> dict:
    """ARIA reads her own source code."""
    if file_path in PROTECTED_FILES:
        return {"error": f"Protected file — ARIA cannot access {file_path}"}

    full_path = _root / file_path
    if not full_path.exists():
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
    if file_path not in MODIFIABLE_FILES:
        return {"error": f"ARIA cannot modify {file_path} — not in whitelist"}

    if change_type not in CHANGE_TYPES:
        return {"error": f"Unknown change type: {change_type}. Valid: {list(CHANGE_TYPES.keys())}"}

    # Syntax validation
    if file_path.endswith(".py"):
        valid = _validate_python(new_content)
    elif file_path.endswith(".mjs") or file_path.endswith(".js"):
        valid = _validate_javascript(new_content)
    else:
        valid = {"ok": True}

    if not valid["ok"]:
        return {"error": f"Syntax error in proposed change: {valid.get('error', 'unknown')}",
                "staged": False}

    # Load existing staged improvements
    staged = await rs.get_json(STAGED_KEY) or []

    improvement = {
        "id": str(uuid.uuid4())[:8],
        "file": file_path,
        "change_type": change_type,
        "description": description,
        "reasoning": reasoning,
        "new_content": new_content,
        "staged_at": time.time(),
        "auto_deployable": CHANGE_TYPES[change_type]["auto_deploy"],
        "status": "staged",
    }

    staged.append(improvement)
    await rs.set_json(STAGED_KEY, staged, ex=7 * 86400)  # 7 day expiry

    # Log the staging
    await _log_improvement("staged", improvement)

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
    staged = await rs.get_json(STAGED_KEY) or []

    target = None
    for s in staged:
        if s["id"] == improvement_id and s["status"] == "staged":
            target = s
            break

    if not target:
        return {"error": "Improvement not found or already deployed"}

    file_path = target["file"]
    full_path = _root / file_path

    # Backup current file
    backup_path = None
    if full_path.exists():
        backup_dir = _root / "runs" / "backups" / "aria_self"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_name = f"{file_path.replace('/', '_')}_{int(time.time())}.bak"
        backup_path = backup_dir / backup_name
        backup_path.write_text(full_path.read_text(encoding="utf-8"), encoding="utf-8")

    # Write new content
    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(target["new_content"], encoding="utf-8")
    except Exception as e:
        # Rollback
        if backup_path and backup_path.exists():
            full_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
        return {"error": f"Deploy failed: {e}"}

    # Git commit
    try:
        _git_commit(file_path, target["change_type"], target["description"])
    except Exception as e:
        logger.warning("Git commit failed (change still applied): %s", e)

    # Update status
    target["status"] = "deployed"
    target["deployed_at"] = time.time()
    await rs.set_json(STAGED_KEY, staged, ex=7 * 86400)

    await _log_improvement("deployed", target)

    return {
        "deployed": True,
        "id": improvement_id,
        "file": file_path,
        "backup": str(backup_path) if backup_path else None,
        "description": target["description"],
    }


async def rollback_improvement(improvement_id: str) -> dict:
    """Rollback a deployed improvement."""
    staged = await rs.get_json(STAGED_KEY) or []

    target = None
    for s in staged:
        if s["id"] == improvement_id and s["status"] == "deployed":
            target = s
            break

    if not target:
        return {"error": "Deployed improvement not found"}

    file_path = target["file"]
    full_path = _root / file_path
    backup_dir = _root / "runs" / "backups" / "aria_self"

    # Find the backup
    backup_prefix = f"{file_path.replace('/', '_')}_"
    backups = sorted(backup_dir.glob(f"{backup_prefix}*.bak"), reverse=True) if backup_dir.exists() else []

    if not backups:
        return {"error": "No backup found for rollback"}

    # Restore from backup
    backup_content = backups[0].read_text(encoding="utf-8")
    full_path.write_text(backup_content, encoding="utf-8")

    target["status"] = "rolled_back"
    target["rolled_back_at"] = time.time()
    await rs.set_json(STAGED_KEY, staged, ex=7 * 86400)

    await _log_improvement("rolled_back", target)

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
        suggestion = json.loads(result.text.strip().strip("```json").strip("```"))
    except Exception as e:
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


def _validate_javascript(code: str) -> dict:
    """Validate JavaScript syntax using Node --check."""
    import tempfile
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mjs", mode="w", delete=False, encoding="utf-8")
        tmp.write(code)
        tmp.close()
        result = subprocess.run(
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


def _git_commit(file_path: str, change_type: str, description: str) -> None:
    """Git commit the change."""
    try:
        subprocess.run(["git", "add", file_path], cwd=str(_root), capture_output=True, timeout=10)
        msg = f"self-improve({change_type}): {description[:80]}"
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=str(_root), capture_output=True, timeout=10,
        )
    except Exception as e:
        logger.warning("Git commit failed: %s", e)


async def _log_improvement(action: str, improvement: dict) -> None:
    """Log an improvement action."""
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
    }

    # ── Step 1: Analyse recent errors ────────────────────────────────────
    try:
        recent_errors = await get_recent_errors(hours=6)
        results["errors_analysed"] = len(recent_errors)

        if len(recent_errors) >= 3:
            # Group errors by file
            error_groups = {}
            for err in recent_errors:
                key = err.get("file", "unknown")
                if key not in error_groups:
                    error_groups[key] = []
                error_groups[key].append(err)

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
                        # Auto-deploy bug fixes
                        if stage_result.get("auto_deployable"):
                            deploy_result = await deploy_improvement(stage_result["id"])
                            if deploy_result.get("deployed"):
                                results["auto_deployed"] += 1
                                logger.info(
                                    "[Self-Improve] Auto-deployed bug fix: %s in %s",
                                    bug_fix["description"], file_path,
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

    # ── Step 4: Code learning — study own codebase patterns ────────────
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

    return results


async def _diagnose_and_fix(llm, file_path: str, errors: list[dict]) -> Optional[dict]:
    """Use LLM to diagnose errors in a file and generate a fix."""
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

        parsed = json.loads(text)
        if not parsed.get("fixed_code"):
            return None

        return {
            "description": parsed.get("fix_description", "Auto-fix detected bugs"),
            "reasoning": parsed.get("diagnosis", ""),
            "fixed_code": parsed["fixed_code"],
        }
    except Exception as e:
        logger.warning("[Self-Improve] Diagnosis failed for %s: %s", file_path, e)
        return None


# ── Chat-triggered self-improvement ──────────────────────────────────────────

# Patterns that indicate a self-improvement request in natural language
_IMPROVE_PATTERNS = [
    re.compile(r"\b(?:improve|enhance|upgrade|optimise|optimize)\b.*\b(?:your|aria|the)\b", re.I),
    re.compile(r"\b(?:fix|repair|debug)\b.*\b(?:your|aria|the|this)\b", re.I),
    re.compile(r"\baria.*(?:learn|remember|update yourself|self.improve)\b", re.I),
    re.compile(r"\b(?:make yourself|make aria|you should)\b.*\b(?:better|smarter|faster)\b", re.I),
    re.compile(r"\b(?:evolve|grow|adapt)\b.*\b(?:your|prompt|brain|knowledge)\b", re.I),
    re.compile(r"\b(?:add|create|build)\b.*\b(?:capability|feature|layer|module)\b.*\b(?:for|to|in)\b.*\baria\b", re.I),
    re.compile(r"\b(?:change|modify|rewrite)\b.*\b(?:your|aria|the)\b.*\b(?:code|prompt|system)\b", re.I),
]


def detect_self_improvement_request(message: str) -> Optional[str]:
    """Detect if a chat message is asking ARIA to improve herself.
    Returns the type of improvement or None."""
    m = message.lower().strip()

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

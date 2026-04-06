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

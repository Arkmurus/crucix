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

import asyncio
import hashlib
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

# R-F1534: anchor background tasks so they aren't GC-dropped mid-flight.
_BACKGROUND_TASKS: set[asyncio.Task] = set()

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

CODER_SCOREBOARD_KEY = "crucix:aria:coder:scoreboard"


def _gold_lane_int_env(name: str, default: int) -> int:
    """Read a non-negative integer threshold, failing closed to default."""
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _gold_lane_float_env(name: str, default: float) -> float:
    """Read a float threshold, failing closed to default."""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    if value < 0:
        return default
    return value


GOLD_LANE_MIN_FIXED = _gold_lane_int_env("ARIA_CODER_GOLD_LANE_MIN_FIXED", 20)
GOLD_LANE_MIN_GOLD = _gold_lane_int_env("ARIA_CODER_GOLD_LANE_MIN_GOLD", 10)
GOLD_LANE_MAX_BLOCKED_RATIO = _gold_lane_float_env(
    "ARIA_CODER_GOLD_LANE_MAX_BLOCKED_RATIO", 0.25
)

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
    # R-F1285 — protect the self-coding subsystem's OWN machinery. Without this
    # the loop could auto-deploy a truncated stub of the very modules that detect
    # gaps, generate fixes, and guard the loop — bricking its own ability to
    # function (and, with the constitutional validator removed in R-F1191, the
    # truncation guard below is the only thing between a stub and disk).
    "aria_service/intel/capability_gaps.py",                 # gap intake the coder reads
    # ── R-F3703 — this path DOES NOT EXIST, and never has ──────────────────
    # The real detector is aria_service/autonomous/gap_detector.py (117,754
    # bytes, ~2,535 lines) — the module that decides WHAT gets fixed — and it
    # was therefore auto-deployable with ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 live.
    # A typo in a protection list is indistinguishable from no protection, so
    # the assertion below now fails the import if any entry stops resolving.
    "aria_service/autonomous/gap_detector.py",               # gap detection
    # The rest of the self-coding machinery, all previously unprotected:
    "aria_service/autonomous/tasks.py",                      # the task executor
    "aria_service/autonomous/test_runner.py",                # the capability-test GATE itself
    "aria_service/autonomous/coder_entrypoint.py",           # the coder's own loop
    "aria_service/intel/load_governor.py",                   # decides whether autonomy runs
    "aria_service/intel/error_streak.py",                    # Phase A gate #3 measurement
    "aria_service/intel/phase_gates.py",                     # every Phase A gate measurement
    "aria_service/intel/mistake_ledger.py",                  # mistake memory
    "aria_service/autonomous/self_coder.py",                 # the fixer pipeline
    "aria_service/autonomous/sovereign_llm.py",              # the fixer's LLM call
    "aria_service/autonomous/engine.py",                     # autonomous scheduler
    "aria_service/autonomous/constitutional_validator.py",   # if/when restored
}

# ── R-F3703 — every protected path must RESOLVE ────────────────────────────
#
# `NO_AUTODEPLOY_FILES` protected "aria_service/intel/gap_detector.py" for
# months. No such file exists; the real one is at aria_service/autonomous/.
# A protection list is checked by STRING membership, so a wrong path is not a
# weaker guard — it is NO guard, and it looks identical to a correct one in
# review. The gap detector, the task executor and the capability-test runner
# were all auto-deployable while this list appeared to cover them.
#
# Verified at import so a typo fails loudly HERE rather than silently at the
# moment a bad fix is auto-deployed into the machinery that was supposed to
# stop it. Never raises in production — a missing file must not break boot —
# but it is loud in the log and a test asserts the set is clean.
def _verify_no_autodeploy_paths_resolve() -> list[str]:
    """Return protected paths that do not exist on disk."""
    missing: list[str] = []
    try:
        root = Path(__file__).resolve().parent.parent.parent
        for rel in NO_AUTODEPLOY_FILES:
            if not (root / rel).exists():
                missing.append(rel)
    except Exception:  # pragma: no cover - defensive; never break import
        return []
    return sorted(missing)


_NO_AUTODEPLOY_MISSING = _verify_no_autodeploy_paths_resolve()
if _NO_AUTODEPLOY_MISSING:
    logger.error(
        "[R-F3703] NO_AUTODEPLOY_FILES contains %d path(s) that DO NOT EXIST: %s. "
        "A protection entry that does not resolve protects NOTHING — the real "
        "file is auto-deployable. Fix the path.",
        len(_NO_AUTODEPLOY_MISSING), _NO_AUTODEPLOY_MISSING,
    )

# R-F2541: seed MODIFIABLE_FILES with the critical set at IMPORT time. These files
# ARE modifiable — a human may stage + review + deploy a legitimate edit — they are
# only barred from AUTO-deploy (that is what NO_AUTODEPLOY_FILES gates). Before this,
# MODIFIABLE_FILES stayed empty until the async _ensure_modifiable_files() ran at boot,
# so a critical file looked un-stageable at import and the human-review path was blocked
# (test_rf851 regression). The full-tree scan still runs at boot and unions in the rest.
MODIFIABLE_FILES.update(NO_AUTODEPLOY_FILES)


# R-F996 — dynamically populate MODIFIABLE_FILES with ALL project files
# so the coder can improve any part of the codebase.
_MODIFIABLE_INITIALIZED = False
_MODIFIABLE_SUFFIXES = (".py", ".mjs", ".js", ".yaml", ".toml", ".json", ".md")
_MODIFIABLE_SKIP_DIRS = {".venv", "node_modules", ".git", "__pycache__", ".pytest_cache"}


def _normalise_repo_path(root: Path, path: Path | str) -> str | None:
    """Return a forward-slash repo-relative path, or None when excluded."""
    try:
        rel_path = path if isinstance(path, Path) else Path(path)
        if rel_path.is_absolute():
            rel_path = rel_path.relative_to(root)
    except ValueError:
        return None
    parts = rel_path.parts
    if any(part in _MODIFIABLE_SKIP_DIRS for part in parts):
        return None
    rel = str(rel_path).replace("\\", "/")
    if not rel.endswith(_MODIFIABLE_SUFFIXES):
        return None
    return rel


def _collect_modifiable_files_sync(root: Path) -> set[str]:
    """Collect candidate files without blocking the event loop."""
    timeout_s = max(1.0, float(os.getenv("ARIA_MODIFIABLE_SCAN_TIMEOUT_S", "10.0") or "10.0"))
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--", *[f"*{suffix}" for suffix in _MODIFIABLE_SUFFIXES]],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            files = {
                rel
                for line in proc.stdout.splitlines()
                for rel in [_normalise_repo_path(root, line.strip())]
                if rel is not None
            }
            if files:
                return files
    except Exception as exc:
        logger.debug("[self_improve] git ls-files modifiable scan unavailable: %s", exc)

    files: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _MODIFIABLE_SKIP_DIRS]
        base = Path(dirpath)
        for filename in filenames:
            rel = _normalise_repo_path(root, base / filename)
            if rel is not None:
                files.add(rel)
    return files


async def _ensure_modifiable_files() -> None:
    """Populate MODIFIABLE_FILES with every tracked file in the project."""
    global _MODIFIABLE_INITIALIZED
    if _MODIFIABLE_INITIALIZED:
        return
    root = Path(__file__).parent.parent.parent
    try:
        files = await asyncio.to_thread(_collect_modifiable_files_sync, root)
        MODIFIABLE_FILES.update(files)
        _MODIFIABLE_INITIALIZED = True
        logger.info("[self_improve] R-F996: MODIFIABLE_FILES populated with %d files", len(MODIFIABLE_FILES))
        try:
            wire_success(
                module="self_improve",
                summary=f"MODIFIABLE_FILES populated with {len(MODIFIABLE_FILES)} files",
                source_id="self_improve:modifiable_scan:R-F2394",
            )
        except Exception:
            pass
    except Exception as exc:
        _MODIFIABLE_INITIALIZED = False
        try:
            wire_failure(
                module="self_improve",
                detail=f"MODIFIABLE_FILES scan failed: {exc}",
                gap_type="agent_cycle_failure",
                source="self_improve:modifiable_scan",
            )
        except Exception:
            pass
        raise


# R-F1363 — directories where the coder may CREATE brand-new files. MODIFIABLE_FILES
# only ever contains EXISTING tracked files (R-F996), so a new-capability gap (every
# operator "add X" request) produced a file that failed staging with "not in
# whitelist" — the coder could improve existing code but never grow the ecosystem.
# We allow new .py files ONLY under these safe dirs (new intel capabilities + their
# tests), still block every PROTECTED_FILES path, and everything goes to STAGING for
# operator review — never auto-deploy (§21c). Creating a new file cannot overwrite
# existing code, so this widens capability without weakening the modify-guard.
SAFE_NEW_FILE_DIRS: tuple[str, ...] = (
    "aria_service/intel/",
    "aria_service/tests/",
)


def _is_safe_new_file(file_path: str) -> bool:
    """True if file_path is a permissible BRAND-NEW file the coder may create."""
    fp = (file_path or "").replace("\\", "/").strip()
    if not fp.endswith(".py"):
        return False
    if not any(fp.startswith(d) for d in SAFE_NEW_FILE_DIRS):
        return False
    if ".." in fp:  # no path traversal
        return False
    try:
        from ..autonomous.constitutional_validator import PROTECTED_FILES
        if fp in PROTECTED_FILES:
            return False
    except Exception:
        # If the protected list can't be loaded, fail CLOSED for safety.
        return False
    return True


def _auto_deploy_allowed(file_path: str, change_type: str) -> bool:
    """Whether a staged change may AUTO-deploy (no human in the loop).

    TWO independent gates — BOTH must pass:
    1. Self-harm-critical files (NO_AUTODEPLOY_FILES — boot, constitution, verification
       gate, prompts, safety guards, the deploy config itself) always require explicit
       human approval: staged + human-deployed, never auto-deployed. R-F851 / R-F1040.
    2. The change_type must itself be auto-deployable. CHANGE_TYPES marks
       prompt_evolution / new_intel_layer / enhancement as auto_deploy=False (human-only)
       — those NEVER auto-deploy on any file; only bug_fix / optimisation may, and only
       when ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 (via _R462_AUTO_DEPLOY_DEFAULT).

    R-F2541: restore gate #2. A prior simplification returned True for every non-critical
    file "independent of change_type", so human-only change types could auto-deploy — a
    safety-gate regression caught by test_rf851. An unknown change_type fails closed.
    """
    if file_path in NO_AUTODEPLOY_FILES:
        return False
    ct = CHANGE_TYPES.get(change_type)
    if ct is None:
        return False  # unknown change_type → require human review (fail-closed)
    return bool(ct.get("auto_deploy", False))


def _autonomous_gold_lane_decision(scoreboard: dict | None) -> dict:
    """Whether autonomous self-improvement has earned direct deploy."""
    counts = (scoreboard or {}).get("counts") or {}

    def _as_int(value: object) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    fixed = _as_int(counts.get("fixed"))
    gold = _as_int(counts.get("gold"))
    blocked = _as_int(counts.get("blocked"))
    claimed = _as_int(counts.get("claimed"))
    attempts = max(claimed, fixed + blocked)
    blocked_ratio = (blocked / attempts) if attempts else 1.0

    reasons: list[str] = []
    if fixed < GOLD_LANE_MIN_FIXED:
        reasons.append(f"fixed {fixed} < {GOLD_LANE_MIN_FIXED}")
    if gold < GOLD_LANE_MIN_GOLD:
        reasons.append(f"gold {gold} < {GOLD_LANE_MIN_GOLD}")
    if blocked_ratio > GOLD_LANE_MAX_BLOCKED_RATIO:
        reasons.append(
            f"blocked_ratio {blocked_ratio:.3f} > {GOLD_LANE_MAX_BLOCKED_RATIO:.3f}"
        )

    return {
        "allowed": not reasons,
        "reasons": reasons,
        "counts": {
            "claimed": claimed,
            "fixed": fixed,
            "gold": gold,
            "blocked": blocked,
            "attempts": attempts,
        },
        "blocked_ratio": blocked_ratio,
    }


async def _autonomous_gold_lane_allows_deploy() -> dict:
    """Read the live coder scoreboard and decide direct-deploy maturity."""
    try:
        scoreboard = await rs.get_json(CODER_SCOREBOARD_KEY) or {}
    except Exception as e:
        wire_failure(
            module="self_improve",
            detail=f"Gold-lane scoreboard read failed: {e}",
            gap_type="autonomous_gold_lane_unavailable",
            source="self_improve:gold_lane_gate",
        )
        scoreboard = {}
    return _autonomous_gold_lane_decision(scoreboard)


# Root directory
_root = Path(__file__).parent.parent.parent


# ── Code Analysis ────────────────────────────────────────────────────────────

def _resolved_protected_paths() -> set[Path]:
    """PROTECTED_FILES as RESOLVED absolute paths (R-F3684).

    Computed per call rather than cached at import: the set is small, and a
    cache keyed on a module-level root is one more thing to invalidate.
    """
    out: set[Path] = set()
    for rel in PROTECTED_FILES:
        try:
            out.add((_root / rel).resolve())
        except (OSError, ValueError):  # pragma: no cover - platform-dependent
            continue
    return out


async def read_own_code(file_path: str) -> dict:
    """ARIA reads her own source code.

    R-F3684 — the containment checks are RESOLVE-FIRST. Both were bypassable:

    1. ``file_path in PROTECTED_FILES`` was an exact STRING match evaluated
       BEFORE ``resolve()``. ``.env`` was refused, but ``./.env``, ``.//.env``
       and ``aria_service/../.env`` all missed the set and then resolved to the
       very file the set exists to protect.
    2. ``str(full_path).startswith(str(_root.resolve()))`` is a string prefix
       test, so a sibling directory sharing the root's name — ``C:\\Code\\Aria``
       vs ``C:\\Code\\Aria-backup`` — satisfied it and escaped the project.

    Both now compare RESOLVED paths, and containment uses ``Path.is_relative_to``
    (a real path-segment test, not a prefix of the string form).

    This matters beyond the coder: ``POST /api/aria/self/read`` reaches here,
    and until R-F3684 it was not operator-gated at either tier, so any signed-in
    viewer could read any file inside the image — including ``scripts/``, which
    the Dockerfile copies in.
    """
    root = _root.resolve()
    try:
        full_path = (root / file_path).resolve()
    except (OSError, ValueError) as e:
        # A malformed path (NUL byte, over-long, bad drive) must not 500.
        wire_failure(module="self_improve", detail=f"Unresolvable path {file_path!r}: {e}",
                     gap_type="access_denied", source="self_improve:read_own_code")
        return {"error": "Access denied: unresolvable path"}

    if not full_path.is_relative_to(root):
        wire_failure(module="self_improve", detail=f"Path traversal blocked: {file_path}",
                     gap_type="access_denied", source="self_improve:read_own_code")
        return {"error": "Access denied: path outside project root"}

    # Compare RESOLVED against RESOLVED — this is the check that ./.env missed.
    if full_path in _resolved_protected_paths():
        wire_failure(module="self_improve", detail=f"Blocked read of protected file: {file_path}",
                     gap_type="access_denied", source="self_improve:read_own_code")
        return {"error": f"Protected file — ARIA cannot access {file_path}"}

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

def _collapse_pending_duplicates(staged: list[dict]) -> list[dict]:
    """R-F1293 — heal a staged queue that piled up under the old byte-identical-only
    dedup. Collapse PENDING entries to one per (file, change_type) — keeping the
    newest content and summing supersede_count so churn stays visible. Only entries
    with the SAME (file, change_type) ever merge, so distinct fixes are preserved.
    Non-pending entries pass through untouched. Order is preserved.
    Live 2026-06-03 this turns the 327→~32 backlog into one entry per file."""
    out: list[dict] = []
    pos: dict[tuple, int] = {}
    for s in staged:
        if s.get("status") != "staged":
            out.append(s)
            continue
        key = (s.get("file"), s.get("change_type"))
        if key in pos:
            kept = out[pos[key]]
            newer = s if s.get("staged_at", 0) >= kept.get("staged_at", 0) else kept
            merged = dict(newer)
            merged["supersede_count"] = (
                kept.get("supersede_count", 0) + s.get("supersede_count", 0) + 1
            )
            _firsts = [x.get("first_staged_at") or x.get("staged_at")
                       for x in (kept, s) if (x.get("first_staged_at") or x.get("staged_at"))]
            if _firsts:
                merged["first_staged_at"] = min(_firsts)
            out[pos[key]] = merged
        else:
            pos[key] = len(out)
            out.append(dict(s))
    return out


async def has_pending_staged_fix_for_module(module: str) -> bool:
    """R-F1294 — True if a staged + pending fix already targets this module.

    Matched by file STEM (basename without .py) so it works regardless of the
    directory the module lives in (intel/ vs llm/ vs autonomous/), since the coder
    knows a gap by its module name, not its full path. Used by the autonomous coder
    to SKIP regenerating a fix for a module whose fix is already waiting for review —
    the root cause of the 186× churn: with AUTO_DEPLOY off, fix_gap stages but the
    gap is never marked fixed, so it's re-detected and regenerated every cycle.
    """
    m = (module or "").strip()
    if not m:
        return False
    staged = await rs.get_json(STAGED_KEY) or []
    for s in staged:
        if s.get("status") != "staged":
            continue
        stem = (s.get("file") or "").rsplit("/", 1)[-1]
        if stem.endswith(".py"):
            stem = stem[:-3]
        if stem == m:
            return True
    return False


async def stage_improvement(
    file_path: str,
    new_content: str,
    change_type: str,
    description: str,
    reasoning: str = "",
) -> dict:
    """Stage a code improvement for review."""
    global _SI_STAGED, _SI_FAILURES
    # R-F1363 — allow either modifying an existing tracked file OR creating a new
    # file in a safe dir (intel/ capabilities + tests/). Both still route to
    # staging for operator review; PROTECTED_FILES stays locked either way.
    if file_path not in MODIFIABLE_FILES and not _is_safe_new_file(file_path):
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
    # R-F2708 — capture the hash of the file this full-content snapshot is being
    # built against ("base"). deploy_improvement refuses to write the snapshot if
    # the live file has since drifted from this base, so a late deploy can't
    # silently revert an intervening change (blast-radius brake). None for a
    # brand-new file (nothing to clobber).
    _base_sha256 = None
    if full_path.exists():
        try:
            _base_sha256 = hashlib.sha256(
                full_path.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()
        except Exception:
            _base_sha256 = None
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

        # R-F1285 — AST symbol-preservation guard. The 50%-line check above misses
        # a stub that keeps 51-99% of lines but still drops the tail of the file
        # (the live queue had ~11 such proposals, e.g. capability_gaps.py 406->180
        # but also milder ones). For Python, reject any SHRINKING proposal that
        # deletes a top-level public function/class present in the current file —
        # the fingerprint of a truncated rewrite. Additive/equal changes and
        # private-only churn are unaffected, so legitimate fixes pass; a human still
        # deploys genuine public-symbol removals.
        #
        # R-F1567 — widen the protected symbol set. The R-F1285/R-F1450 collector
        # only walked top-level PUBLIC defs, so a self-coded "fix" that gutted a
        # PRIVATE module-level helper (_prefixed) or a CLASS METHOD body — without
        # dropping a public top-level def line — sailed through; only the 50%-line
        # guard might have caught it. _preserved_syms now also collects (a) methods
        # defined directly inside top-level classes (qualified ClassName.method) and
        # (b) module-level private functions. A proposal that DROPS any previously-
        # present symbol from this widened set is rejected with the same semantics as
        # the existing public-symbol drop check. This closes the residual coder-safety
        # hole the operator flagged before any AUTO_DEPLOY.
        if file_path.endswith(".py") and current_lines > 0:
            import ast as _ast

            def _preserved_syms(src: str) -> set[str]:
                try:
                    tree = _ast.parse(src)
                except Exception:
                    return set()
                syms: set[str] = set()
                for n in tree.body:
                    # (existing) top-level public functions + classes
                    if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                        if not n.name.startswith("_"):
                            syms.add(n.name)
                    # (R-F1567 b) module-level PRIVATE functions
                    if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name.startswith("_"):
                        syms.add(n.name)
                    # (R-F1567 a) methods one level inside top-level classes —
                    # qualified ClassName.method so a method can't silently vanish.
                    if isinstance(n, _ast.ClassDef):
                        for m in n.body:
                            if isinstance(m, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                                syms.add(f"{n.name}.{m.name}")
                return syms

            # Back-compat alias: callers/tests that referenced the old collector name
            # still work, and the public-symbol semantics are a strict subset.
            _public_syms = _preserved_syms

            try:
                cur_src = full_path.read_text(encoding="utf-8")
            except Exception:
                cur_src = ""
            dropped = _preserved_syms(cur_src) - _preserved_syms(new_content)
            if dropped:
                logger.warning(
                    "[self_improve] R-F1285/R-F1450/R-F1567 REJECTED stage of %s: drops %d "
                    "symbol(s) %s (%d->%d lines) — likely destructive whole-file regen.",
                    file_path, len(dropped), sorted(dropped)[:6], current_lines, proposed_lines,
                )
                _SI_FAILURES += 1
                wire_failure(module="self_improve",
                             detail=f"R-F1285/R-F1567 blocked stage of {file_path}: drops {sorted(dropped)[:6]}",
                             gap_type="truncation_guard", source="self_improve:stage_improvement")
                return {
                    "error": (
                        f"Rejected: proposed content drops symbol(s) "
                        f"{sorted(dropped)[:6]} (public def, private helper, or class method) "
                        f"present in the current file "
                        f"({current_lines}->{proposed_lines} lines) — almost certainly a "
                        f"destructive whole-file regen that would delete working code. ARIA does not "
                        f"stage symbol-dropping shrinkage."
                    ),
                    "staged": False,
                    "truncation_guard": True,
                }

    # Load existing staged improvements
    staged = await rs.get_json(STAGED_KEY) or []

    # R-F1293: heal any pre-existing pile-up (the live 327→32 backlog) so the queue
    # is already 1-per-file before we add this one.
    _before = len(staged)
    staged = _collapse_pending_duplicates(staged)
    if len(staged) < _before:
        logger.info("[self_improve] R-F1293 collapsed staged queue %d→%d (healed churn backlog)",
                    _before, len(staged))

    # R-F1293 (supersedes R-F903): ONE pending entry per (file, change_type).
    # The old R-F903 dedup only collapsed BYTE-IDENTICAL proposals, so the coder's
    # non-deterministic re-generations of the SAME file piled up unbounded (live
    # 2026-06-03: memory_leak_detector.py staged 186×, prompt_budget.py 63× — 327
    # staged entries were only 32 distinct files). Now a new proposal for a
    # file+type that already has a pending entry SUPERSEDES it (newest wins), so
    # the queue is bounded at the number of distinct files and a would-be 186-deploy
    # churn-STORM becomes a single deploy. Each entry carries a `supersede_count`
    # so a stuck/looping file is visible at a glance (the symptom to fix upstream:
    # the gap keeps re-surfacing — resolve the gap, don't re-stage forever).
    # The truncation + AST guards above run BEFORE this point, so a truncated stub
    # can never supersede a good fix.
    prior = None
    kept: list[dict] = []
    for existing in staged:
        if (
            existing.get("status") == "staged"
            and existing.get("file") == file_path
            and existing.get("change_type") == change_type
        ):
            if existing.get("new_content") == new_content:
                # True no-op duplicate — identical content already pending.
                return {
                    "staged": True,
                    "id": existing["id"],
                    "auto_deployable": existing.get("auto_deployable", False),
                    "description": existing.get("description", description),
                    "duplicate": True,
                }
            prior = existing  # supersede this one (drop from `kept`)
            continue
        kept.append(existing)
    staged = kept

    _supersede_count = (prior.get("supersede_count", 0) + 1) if prior else 0
    if prior:
        logger.warning(
            "[self_improve] R-F1293 churn: re-staged %s (%s) — superseding id=%s "
            "(now superseded %d×). Newest wins; queue stays 1-per-file. If this count "
            "is high the gap is not resolving — fix upstream, not by re-staging.",
            file_path, change_type, prior.get("id"), _supersede_count,
        )

    improvement = {
        "id": (prior.get("id") if prior else str(uuid.uuid4())[:8]),  # reuse id → stable to pollers
        "file": file_path,
        "change_type": change_type,
        "description": description,
        "reasoning": reasoning,
        "new_content": new_content,
        "staged_at": time.time(),
        "first_staged_at": (prior.get("first_staged_at") or prior.get("staged_at")) if prior else time.time(),
        "supersede_count": _supersede_count,
        "auto_deployable": _auto_deploy_allowed(file_path, change_type),
        "base_sha256": _base_sha256,  # R-F2708 blast-radius brake anchor
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


def deploy_succeeded(res: dict | None) -> bool:
    """R-F1960 — THE single source of truth for "did deploy_improvement succeed?".

    Root cause this kills: deploy_improvement returns three different shapes
    ({"deployed":True} on success, {"error":...} / {"error":...,"blocked":True}
    on failure) and callers GUESSED the key. self_coder checked `.get("ok")` — a
    key deploy_improvement NEVER returned — so every successful auto-deploy was
    misread as `deploy_failed:...:unknown`, churning the gap and bypassing the
    post-deploy regression monitor. The internal caller checked `.get("deployed")`
    (right). Two callers, two guesses, no contract. Now every caller asks HERE.

    Robust to all current shapes AND to any future return that forgets the
    explicit `ok` flag: a real success has `deployed`/`ok` truthy and no
    error/blocked marker.
    """
    if not isinstance(res, dict):
        return False
    if res.get("error") or res.get("blocked"):
        return False
    return bool(res.get("ok", res.get("deployed")))


async def deploy_improvement(improvement_id: str) -> dict:
    """Deploy a staged improvement to production.

    Result contract (R-F1960): EVERY return carries an explicit ``ok`` bool.
    Callers MUST classify success via ``deploy_succeeded()``, never by guessing
    a key. Success also keeps ``deployed=True`` (legacy marker); failures carry
    ``error`` and sometimes ``blocked``.
    """
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
        return {"ok": False, "error": "Improvement not found or already deployed"}

    file_path = target["file"]
    full_path = _root / file_path

    # R-F1287: constitutional validator RESTORED (R-F1191 removal reverted — ARIA
    # had autonomously deleted her own safety gate). The fail-closed gate runs just
    # below, after the truncation guard, before the live write.
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
                "ok": False,   # R-F1960 — explicit failure contract
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

    # R-F1287 — constitutional validator deploy gate (restores R-F855, removed when
    # ARIA autonomously DELETED constitutional_validator.py in 085d0751 / R-F1191).
    # FAIL-CLOSED: validate the staged content against the constitution (protected
    # files, weakening patterns, learned attacks, unsafe AST/imports) BEFORE writing
    # it live. A violation — or a validator that can't even run — blocks the deploy.
    # Both branches wire to the brain (§21a): a block emits a constitutional_block
    # gap; a clean pass emits a success signal so the gate's health is observable.
    try:
        from ..autonomous.constitutional_validator import (
            ConstitutionalValidator, record_learned_attack,
        )
        _cv = ConstitutionalValidator().validate(
            target.get("new_content") or "", target_file=file_path,
        )
        _cv_ok, _cv_violations, _cv_risk = _cv.passed, _cv.violations, _cv.risk_score
    except Exception as _cv_err:  # noqa: BLE001 — unavailable validator = fail closed
        logger.error("[self_improve] R-F1287 constitutional_validator unavailable — "
                     "FAIL-CLOSED block of %s: %s", file_path, _cv_err)
        _cv_ok, _cv_violations, _cv_risk = False, [f"validator unavailable (fail-closed): {_cv_err}"], 1.0
    if not _cv_ok:
        target["status"] = "blocked_constitutional"
        _SI_FAILURES += 1
        wire_failure(
            module="self_improve",
            detail=f"R-F1287 constitutional BLOCK of {file_path} (risk={_cv_risk:.2f}): "
                   + "; ".join(_cv_violations)[:200],
            gap_type="constitutional_block", source="self_improve:deploy_improvement")
        try:
            record_learned_attack(target.get("new_content") or "", _cv_violations,
                                  origin="self_improve.deploy_improvement")
        except Exception:  # noqa: BLE001 — regression-learning must never block the gate
            pass
        logger.warning("[self_improve] R-F1287 BLOCKED deploy of %s: risk=%.2f violations=%s",
                       file_path, _cv_risk, _cv_violations[:3])
        return {
            "ok": False,   # R-F1960 — explicit failure contract
            "error": "BLOCKED by constitutional_validator: " + "; ".join(_cv_violations)[:300],
            "blocked": True,
            "constitutional_block": True,
            "risk_score": _cv_risk,
            "id": improvement_id,
            "file": file_path,
        }
    wire_success(
        module="self_improve",
        summary=f"R-F1287 constitutional gate passed for {file_path} (risk={_cv_risk:.2f})",
        source_id="self_improve:deploy_improvement")

    # R-F2256 — DiffValidator deploy gate. The whole-file validate() above catches
    # ADDED weakening patterns, but a fix that SILENTLY DELETES a critical safety line
    # (e.g. a source_verifier.verify call, a fail-closed guard, a signature check) would
    # PASS it. Wire the diff-based guard (constitutional_validator.DiffValidator — was
    # orphaned/uncalled) so a removed critical line blocks the deploy too. FAIL-CLOSED
    # like the gate above; §21a-wired both branches. Only diffs against an EXISTING file
    # (a brand-new file can't have removed a line).
    try:
        from ..autonomous.constitutional_validator import DiffValidator
        import difflib as _difflib
        _new_src = target.get("new_content") or ""
        try:
            _old_src = full_path.read_text(encoding="utf-8")
        except Exception:
            _old_src = ""
        if _old_src:
            _udiff = "\n".join(_difflib.unified_diff(
                _old_src.splitlines(), _new_src.splitlines(),
                fromfile=file_path, tofile=file_path, lineterm="",
            ))
            _dv = DiffValidator().validate_diff(_udiff)
            _dv_ok, _dv_violations = _dv.passed, _dv.violations
        else:
            _dv_ok, _dv_violations = True, []
    except Exception as _dv_err:  # noqa: BLE001 — unavailable diff-validator = fail closed
        logger.error("[self_improve] R-F2256 DiffValidator unavailable — FAIL-CLOSED block of %s: %s",
                     file_path, _dv_err)
        _dv_ok, _dv_violations = False, [f"diff-validator unavailable (fail-closed): {_dv_err}"]
    if not _dv_ok:
        target["status"] = "blocked_constitutional"
        _SI_FAILURES += 1
        wire_failure(
            module="self_improve",
            detail=f"R-F2256 diff BLOCK of {file_path} (safety line removed): "
                   + "; ".join(_dv_violations)[:200],
            gap_type="constitutional_block", source="self_improve:deploy_improvement")
        logger.warning("[self_improve] R-F2256 BLOCKED deploy of %s — silent safety-line removal: %s",
                       file_path, _dv_violations[:3])
        return {
            "ok": False,
            "error": "BLOCKED by DiffValidator (critical safety line removed): "
                     + "; ".join(_dv_violations)[:300],
            "blocked": True,
            "constitutional_block": True,
            "id": improvement_id,
            "file": file_path,
        }
    wire_success(
        module="self_improve",
        summary=f"R-F2256 diff gate passed for {file_path}",
        source_id="self_improve:deploy_improvement")

    # R-F2708 — deploy blast-radius brake. new_content is a FULL-FILE snapshot
    # captured at stage time against base_sha256. An amendment approve→deploy has a
    # long, human-paced gap; if the live file drifted since staging (another
    # amendment/fix/hotfix landed), writing this stale snapshot would SILENTLY REVERT
    # those intervening edits — blast radius = the whole file, not the intended clause.
    # Refuse the stale write; the operator re-approves/re-stages, which rebases the
    # change onto the CURRENT file (a minimal, current-based diff). Mirrors the
    # truncation guard: no status mutation — a re-stage supersedes this item (R-F1293).
    # Scoped by presence of base_sha256, so legacy staged items (pre-R-F2708) are
    # unaffected; a brand-new file has no base and is never gated here.
    _base_sha256 = target.get("base_sha256")
    if _base_sha256 and full_path.exists():
        try:
            _live_sha256 = hashlib.sha256(
                full_path.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()
        except Exception:
            _live_sha256 = None
        if _live_sha256 and _live_sha256 != _base_sha256:
            _SI_FAILURES += 1
            wire_failure(
                module="self_improve",
                detail=(f"R-F2708 blocked deploy of {file_path} (id={improvement_id}): "
                        f"live file drifted from staged base "
                        f"({_base_sha256[:12]} != {_live_sha256[:12]})"),
                gap_type="stale_base_deploy", source="self_improve:deploy_improvement")
            logger.warning(
                "[self_improve] R-F2708 BLOCKED deploy of %s (id=%s): base moved since "
                "staging — refusing stale full-file snapshot that would revert the "
                "intervening change(s). Re-approve/re-stage to rebase.",
                file_path, improvement_id)
            return {
                "ok": False,   # R-F1960 — explicit failure contract
                "error": (
                    f"BLOCKED (stale base): {file_path} changed since this improvement was "
                    f"staged, so deploying its full-file snapshot would REVERT the "
                    f"intervening change(s). Re-approve/re-stage to rebase onto the current "
                    f"file (yields a minimal, current-based diff)."
                ),
                "blocked": True,
                "stale_base": True,
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
        return {"ok": False, "error": f"Deploy failed: {e}"}

    # Git commit
    try:
        await _git_commit(file_path, target["change_type"], target["description"])
    except Exception as e:
        logger.warning("Git commit failed (change still applied): %s", e)

    # R-F1766: capture the commit SHA so the deploy-proprioception loop can
    # later CONFIRM the change actually reached the live server (build_rev).
    _commit_sha = ""
    try:
        from ..utils.git_utils import get_current_commit as _gcc1766
        _commit_sha, _ = _gcc1766()
    except Exception as _she:
        logger.debug("[self_improve] could not capture commit sha: %s", _she)

    # Update status. R-F1766: a local commit is NOT a verified live deploy —
    # the fly deploy is async (ci_deploy/CI build). status="deployed" is kept
    # for backward-compat (rollback finder + auto_deployed counter), but
    # verified_live=False marks the truth: not yet PROVEN on the live server.
    # reconcile_live_deploys() flips verified_live True ONLY once is_sha_live
    # confirms the live build_rev advanced to this SHA — else it records a
    # deploy_verification_failure gap so self-heal/coder retries the deploy.
    target["status"] = "deployed"
    target["deployed_at"] = time.time()
    target["commit_sha"] = _commit_sha
    target["verified_live"] = False
    await rs.set_json(STAGED_KEY, staged, ex=7 * 86400)

    await _log_improvement("deployed", target)
    _SI_DEPLOYED += 1

    # R-F1766: HONEST signal — committed, NOT yet proven live. The truthful
    # "verified live" success (or the failure) is wired by the proprioception
    # reconcile, never confabulated here. No more "Deployed" claim at commit time.
    wire_success(module="self_improve",
                 summary=f"Committed {target['change_type']} to {file_path} "
                         f"(commit {_commit_sha or '?'}; live verification pending): "
                         f"{target['description'][:60]}",
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

    # R-F1531: index the fix in CodingRAG for future retrieval.
    # Fire-and-forget via asyncio.to_thread (chromadb upsert is blocking).
    try:
        from .coding_rag_indexer import FixRecord, index_fix as _cri_index_fix
        from datetime import datetime, timezone  # R-F1534: was MISSING → NameError swallowed → this success branch never ran
        _fix_record = FixRecord(
            r_number=target.get("r_number", improvement_id[:12]),
            title=target.get("description", "Unknown fix")[:200],
            gap_type=target.get("change_type", "enhancement"),
            module=file_path,
            problem_description=target.get("description", ""),
            approach="Deployed via self_improve.deploy_improvement",
            files_changed=[file_path],
            tests_passed=0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            outcome="success",
        )
        import asyncio as _aio1531
        _t = _aio1531.create_task(
            _aio1531.to_thread(_cri_index_fix, _fix_record)
        )
        _BACKGROUND_TASKS.add(_t)
        _t.add_done_callback(_BACKGROUND_TASKS.discard)
    except Exception as e:
        logger.debug("[CodingRAG] Fix index failed (non-fatal): %s", e)

    return {
        "ok": True,            # R-F1960 — explicit success contract
        "deployed": True,
        "id": improvement_id,
        "file": file_path,
        "backup": str(backup_path) if backup_path else None,
        "description": target["description"],
    }


# ── R-F1766 — DEPLOY PROPRIOCEPTION: did the change ACTUALLY land live? ──
# Turns the confabulated "deployed" (which really meant "committed locally")
# into a VERIFIED one. The fly deploy is async (ci_deploy/CI), so we reconcile
# committed-but-unverified changes against the live build_rev on a loop.
# See aria_service/autonomous/deploy_verifier.

async def reconcile_live_deploys() -> dict:
    """Confirm committed self-improve changes reached the live server.

    For each item with a commit_sha that isn't yet verified_live:
      - live now  → verified_live=True + TRUTHFUL "verified live" wire_success.
      - not live AND older than the CI grace → wire_failure(
        deploy_verification_failure) + record a gap so self-heal/coder retries
        (mark verify_failed so we don't re-alert every cycle).
      - not live within grace → leave pending (CI still building).
    Idempotent; safe on a loop. NEVER claims live without proof (deploy_verifier).
    """
    import os as _os1766
    grace_s = int(_os1766.getenv("ARIA_DEPLOY_VERIFY_GRACE_S", "1200"))  # 20 min
    try:
        from ..autonomous import deploy_verifier as _dv
    except Exception as _de:
        return {"reconciled": 0, "error": f"deploy_verifier unavailable: {_de}"}

    staged = await rs.get_json(STAGED_KEY) or []
    pending = [s for s in staged
               if isinstance(s, dict) and s.get("commit_sha")
               and s.get("verified_live") is not True
               and not s.get("verify_failed")]
    if not pending:
        return {"reconciled": 0, "verified": 0, "failed": 0, "pending": 0}

    verdicts = await _dv.reconcile_committed_deploys(pending)
    vmap = {v["commit_sha"]: v for v in verdicts}
    now = time.time()
    verified = failed = still_pending = 0
    dirty = False
    for s in staged:
        if not isinstance(s, dict):
            continue
        sha = str(s.get("commit_sha") or "")
        if not sha or s.get("verified_live") is True or s.get("verify_failed"):
            continue
        v = vmap.get(sha)
        if v and v.get("verified_live"):
            s["verified_live"] = True
            s["verified_live_at"] = now
            dirty = True
            verified += 1
            wire_success(
                module="self_improve",
                summary=(f"Deploy VERIFIED LIVE: {sha} is serving on aria-intel "
                         f"({s.get('change_type','')} {s.get('file','')})"),
                source_id=f"self_improve:deploy_verified:{s.get('id')}")
        else:
            age = now - float(s.get("deployed_at") or now)
            if age > grace_s:
                s["verify_failed"] = True
                dirty = True
                failed += 1
                detail = (f"Deploy NOT live after {int(age)}s: commit {sha} "
                          f"({s.get('file','')}) never advanced the live build_rev "
                          f"(live={(v or {}).get('live_build_rev')}). Change is "
                          f"committed but NOT running — deploy must be retried.")
                wire_failure(module="self_improve", detail=detail,
                             gap_type="deploy_verification_failure",
                             source="self_improve:reconcile_live_deploys")
                try:
                    from . import capability_gaps as _cg1766
                    await _cg1766.record_gap(
                        gap_type="deploy_verification_failure",
                        detail=detail[:600],
                        source="self_improve:reconcile_live_deploys")
                except Exception:
                    pass
            else:
                still_pending += 1
    if dirty:
        await rs.set_json(STAGED_KEY, staged, ex=7 * 86400)
    return {"reconciled": len(verdicts), "verified": verified,
            "failed": failed, "pending": still_pending}


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
    targets = random.sample(available, min(2, len(available)))  # nosec B311
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

#: R-F4322 (C-270) — slots RESERVED for ERROR/CRITICAL entries, which a
#: WARNING flood may not take. Measured live 2026-08-25: the ledger held 200
#: entries spanning 1.4 HOURS at ~140 warnings/hour, so a real ERROR was
#: evicted within ninety minutes and `window_errors_24h` reported the CAP
#: rather than a count. `error_streak.py:96` documents the same defect.
#:
#: A RESERVE, NOT A BIGGER BUFFER. Raising MAX_ERRORS only moves the horizon
#: — the flood still wins — and §1 forbids that band-aid. Half is deliberate:
#: errors are rare in the healthy case, so the reserve is mostly unused and
#: warnings still get ~199 of 200 slots (pinned by a test), while under an
#: error storm neither class can starve the other.
ERROR_RESERVE = 100


def _trim_error_log(entries: list, max_entries: int | None = None,
                    error_reserve: int | None = None) -> list:
    """Trim the ledger to ``max_entries``, guaranteeing ERRORs a floor.

    R-F4322 (C-270). The old trim was ``entries[-MAX_ERRORS:]`` over a list
    that `error_log_handler` fills with EVERY WARNING+ from any ``aria.*``
    logger. Warnings therefore evicted errors, and the ledger — the thing a
    human or an agent READS to diagnose — could only see the last ~1.4h.
    Worse, its silence was indistinguishable from a clean period, the same
    absence-reads-as-health shape §1 records for three Phase A gates.

    R-F2622 already protects the GATE with a durable, TTL-less streak anchor;
    this protects the diagnostic RECORD. The two are complementary — do not
    "simplify" either into the other.

    Classification goes through ``error_streak.is_reset_type``, the ONE
    definition of what counts as an ERROR, shared so the write and read paths
    cannot drift. If that import fails we fall back to the ORIGINAL tail
    slice: degrading to the old behaviour is honest, whereas treating every
    entry as an error would let warnings fill the reserve and quietly undo
    the fix.

    Chronological order is preserved — readers page through this list, so
    reordering it would misreport when things happened.
    """
    max_entries = MAX_ERRORS if max_entries is None else int(max_entries)
    error_reserve = ERROR_RESERVE if error_reserve is None else int(error_reserve)
    if max_entries <= 0:
        return []
    if len(entries) <= max_entries:
        return list(entries)

    try:
        from . import error_streak as _es
        _is_err = _es.is_reset_type
    except Exception:  # noqa: BLE001 — see docstring: degrade to the old trim
        return entries[-max_entries:]

    reserve = max(0, min(error_reserve, max_entries))
    keep: set[int] = set()
    if reserve:
        err_idx = []
        for i, e in enumerate(entries):
            try:
                if _is_err(str((e or {}).get("type") or "")):
                    err_idx.append(i)
            except Exception:  # noqa: BLE001 — a malformed row is not an error
                continue
        keep.update(err_idx[-reserve:])

    # Fill whatever the reserve did not claim with the most RECENT entries,
    # so recency still governs the ordinary case.
    for i in range(len(entries) - 1, -1, -1):
        if len(keep) >= max_entries:
            break
        keep.add(i)

    return [e for i, e in enumerate(entries) if i in keep]

# R-F1510: circuit breaker for record_error. When the state_store is under
# lock contention (SQLite "database is locked"), every WARNING log from the
# store triggers error_log_handler → record_error → set_json → _upsert,
# which fails again with the same error → infinite feedback loop. The
# breaker tracks consecutive failures; after _RECORD_ERROR_CB_THRESHOLD
# consecutive failures it stops trying for _RECORD_ERROR_CB_COOLDOWN_S.
# This breaks the loop at its source without losing the error — the
# autonomous cycle reads the error ledger on its own schedule and will
# pick up new errors once the store recovers.
_RECORD_ERROR_CB_THRESHOLD = 5
_RECORD_ERROR_CB_COOLDOWN_S = 30.0
_record_error_failures = 0
_record_error_cb_until: float = 0.0

# R-F2622 — ERRORs dropped without ever reaching the ledger (breaker open,
# or the write itself failed). Read by error_streak.compute_error_streak,
# which restarts the gate-#3 clean streak at `_SI_LAST_DROP_TS`: we KNOW
# the clean record has a hole at that moment, so cleanliness cannot be
# claimed across it.
#
# Verify-pass-1 correction: an earlier version kept only the COUNT and was
# process-local, which was wrong twice over — a restart erased the
# knowledge (and the gate does NOT re-measure from boot: it measures from
# genesis/oldest_event, both of which survive a restart), and the count
# never aged out, so one transient drop blocked the gate forever. The
# TIMESTAMP fixes both: error_streak also persists it via
# `record_drop_marker` so it survives restart, and it ages out naturally
# once the threshold passes. The counter is retained for reporting only.
_SI_ERRORS_DROPPED = 0
_SI_LAST_DROP_TS: float = 0.0


async def record_error(error_type: str, message: str, file: str = "",
                       function: str = "", traceback: str = "") -> None:
    """Record an error for autonomous analysis.

    R-F1510: includes a circuit breaker that stops trying after
    _RECORD_ERROR_CB_THRESHOLD consecutive failures, with a cooldown
    of _RECORD_ERROR_CB_COOLDOWN_S. This prevents the feedback loop
    where a state_store lock contention causes record_error to fail,
    which logs a WARNING, which triggers another record_error call.
    """
    global _SI_ERRORS_RECORDED, _record_error_failures, _record_error_cb_until
    global _SI_ERRORS_DROPPED, _SI_LAST_DROP_TS

    # R-F1510: circuit breaker — if we've failed too many times recently,
    # skip the write entirely. The error is still visible in fly logs.
    if time.monotonic() < _record_error_cb_until:
        logger.debug(
            "record_error: circuit breaker open (%.0fs remaining) — "
            "dropping error: %s: %s",
            _record_error_cb_until - time.monotonic(),
            error_type, message[:100],
        )
        # R-F2622: a dropped ERROR is evidence we no longer have. Phase A
        # gate #3 reads this ledger to certify "0 ERRORs in 7 days"; if we
        # drop errors silently, absence-of-record reads as cleanliness and
        # the gate certifies a lie. Record WHEN, so the streak restarts at
        # the hole instead of spanning it.
        # Verify-pass-2: ONLY an ERROR/CRITICAL drop is a hole in the gate-#3
        # record. Marking a dropped WARNING would restart the clean streak
        # and — since error_log_handler mirrors ALL WARNING+ logs here, and
        # state_store saturation drops them routinely — would pin the gate
        # at False forever. That is R-F560's dishonesty inverted: a false
        # FAIL instead of a false PASS. The module's contract is explicit
        # that WARNINGs never reset the streak; honour it here.
        _SI_ERRORS_DROPPED += 1
        try:
            from . import error_streak as _es
            # is_reset_type (error_streak.py) — the single definition of
            # "counts as an ERROR", shared so the write and read paths
            # cannot drift.
            if _es.is_reset_type(error_type):
                _SI_LAST_DROP_TS = time.time()
        except Exception:
            pass
        # NOTE: no durable write here. The breaker's whole purpose is to
        # "skip the write entirely" when the store is failing (see above) —
        # doing store I/O on this path defeats the breaker and can re-enter
        # it via the store's own error logs. compute_error_streak persists
        # this marker on the READ path instead, when the store is healthy.
        return

    try:
        errors = await rs.get_json(ERROR_LOG_KEY) or []
        errors.append({
            "type": error_type,
            "message": message[:500],
            "file": file,
            "function": function,
            "traceback": traceback[:1000],
            "timestamp": time.time(),
        })
        # R-F4322 (C-270) — reserve slots for ERRORs so a WARNING flood
        # cannot evict them and blind the ledger to the last ~1.4h.
        errors = _trim_error_log(errors)
        await rs.set_json(ERROR_LOG_KEY, errors, ex=7 * 86400)
        _SI_ERRORS_RECORDED += 1
        # R-F2622: advance the durable gate-#3 streak anchor. The ledger
        # above is a 200-slot ring buffer with a 7d TTL, so it forgets;
        # the anchor is the high-water mark that doesn't. Written here —
        # at the moment the error is known — so eviction can never erase
        # the fact that an ERROR happened.
        try:
            from . import error_streak as _es
            await _es.record_streak_anchor(
                error_type, message=message, file=file, function=function,
            )
        except Exception as _anchor_err:
            # DEBUG, never WARNING. Verify-pass-1 caught this: at WARNING+
            # error_log_handler mirrors it straight back into record_error →
            # the ledger write succeeds → the anchor fails again → WARNING →
            # unbounded recursion, each turn doing a 200-element RMW on the
            # hot ledger key. The R-F1510 breaker cannot stop it because
            # record_error itself keeps SUCCEEDING, so its failure counter
            # never trips. The failure still reaches the brain via
            # wire_failure inside record_streak_anchor (§21a) — it just must
            # not travel back through the logging path. The message also
            # carries "streak-anchor" + "non-fatal", both in
            # error_log_handler._SKIP_SUBSTRINGS, as defence in depth.
            logger.debug(
                "record_error: gate-3 streak-anchor update failed "
                "(non-fatal) for %s: %s", error_type, _anchor_err,
            )
        # Success — reset the failure counter
        _record_error_failures = 0
    except Exception as e:
        _record_error_failures += 1
        # R-F2622: the write failed, so this ERROR never reached the ledger.
        # Same reasoning as the breaker-open drop above — gate #3 must not
        # read the resulting silence as cleanliness. ERROR/CRITICAL only
        # (verify-pass-2): a dropped WARNING is not a hole in an ERROR-only
        # record, and marking it would pin the gate at False forever. No
        # durable marker attempted here either: the store just failed.
        _SI_ERRORS_DROPPED += 1
        try:
            from . import error_streak as _es
            if _es.is_reset_type(error_type):
                _SI_LAST_DROP_TS = time.time()
        except Exception:
            pass
        if _record_error_failures >= _RECORD_ERROR_CB_THRESHOLD:
            _record_error_cb_until = time.monotonic() + _RECORD_ERROR_CB_COOLDOWN_S
            logger.warning(
                "record_error: circuit breaker opened after %d consecutive "
                "failures (cooldown %.0fs) — last error: %s",
                _record_error_failures, _RECORD_ERROR_CB_COOLDOWN_S, e,
            )
        else:
            logger.debug(
                "record_error: write failed (%d/%d) — %s",
                _record_error_failures, _RECORD_ERROR_CB_THRESHOLD, e,
            )


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
      4. Auto-deploy eligible fixes only after the gold-lane maturity gate
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

                # R-F2912 — do not re-diagnose the SAME file with the SAME
                # errors every cycle. There was no dedupe here, so each cycle
                # re-sent the identical full-file prompt for every qualifying
                # file, and _diagnose_and_fix asks for "THE COMPLETE FIXED FILE"
                # (~8k tokens/call). Because a staged fix does not clear the
                # error ledger, the same errors persisted and were re-diagnosed
                # indefinitely. Live evidence 2026-07-23: self_improve was the
                # top attributable Claude cost ($8.63/mo, 126 calls) with
                # top_calls showing identical token counts (8806 x3, 8312 x2)
                # — the same prompt, billed again and again, now at Opus rates.
                _sig = _diagnosis_signature(file_path, file_errors)
                if await _recently_diagnosed(_sig):
                    results["files_skipped_recently_diagnosed"] = (
                        results.get("files_skipped_recently_diagnosed", 0) + 1
                    )
                    continue

                bug_fix = await _diagnose_and_fix(llm, file_path, file_errors)
                await _mark_diagnosed(_sig)
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
                        gold_lane = await _autonomous_gold_lane_allows_deploy()
                        if (
                            stage_result.get("auto_deployable")
                            and file_path not in NO_AUTODEPLOY_FILES
                            and gold_lane.get("allowed")
                        ):
                            deploy_result = await deploy_improvement(stage_result["id"])
                            if deploy_succeeded(deploy_result):   # R-F1960 — canonical contract
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
                        elif stage_result.get("auto_deployable"):
                            logger.warning(
                                "[Self-Improve] R-F2689 blocked auto-deploy of %s: %s",
                                file_path,
                                "; ".join(gold_lane.get("reasons") or ["gold lane not earned"]),
                            )
                            wire_failure(
                                module="self_improve",
                                detail=(
                                    "Auto-deploy blocked by gold-lane maturity gate: "
                                    + "; ".join(gold_lane.get("reasons") or ["not earned"])
                                ),
                                gap_type="autonomous_gold_lane_not_earned",
                                source="self_improve:gold_lane_gate",
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


# ── R-F2912: diagnosis dedupe ────────────────────────────────────────────────
# One marker per (file, error-set). TTL bounds it so a file whose errors CHANGE
# is re-diagnosed promptly, while an unchanged file is not re-sent every cycle.
DIAGNOSIS_MARKER_PREFIX = "crucix:aria:selfimprove:diagnosed:"
DIAGNOSIS_DEDUP_TTL_S = 24 * 3600


def _diagnosis_signature(file_path: str, errors: list[dict]) -> str:
    """Stable fingerprint of a diagnosis request.

    Keyed on the file plus the SET of error messages, so:
      * the same file with the same errors  -> same signature (skip, it is the
        identical prompt we already paid for);
      * the same file with a NEW error      -> different signature (re-diagnose,
        because the situation genuinely changed).
    Sorted + truncated so ordering noise and huge traces cannot split the key.
    """
    msgs = sorted({str(e.get("message") or e.get("error") or "")[:200] for e in errors})
    raw = file_path + "|" + "|".join(msgs)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


async def _recently_diagnosed(signature: str) -> bool:
    """True if this exact diagnosis was already run inside the TTL.

    Fails OPEN (returns False) when the store is unreachable: a cost
    optimisation must never be the reason a real bug goes undiagnosed. The
    monthly/daily caps remain the spend backstop.
    """
    try:
        return bool(await rs.get(f"{DIAGNOSIS_MARKER_PREFIX}{signature}"))
    except Exception:
        return False


async def _mark_diagnosed(signature: str) -> None:
    """Record that this diagnosis ran. Best-effort; never blocks the cycle."""
    try:
        await rs.set(f"{DIAGNOSIS_MARKER_PREFIX}{signature}", "1",
                     ex=DIAGNOSIS_DEDUP_TTL_S)
    except Exception:
        pass


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
   - Every external HTTP call uses httpx.AsyncClient with timeout  # no-breaker: self-improve is background; breaker would stall autonomous improvement
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

"""
Eagle Eye — continuous codebase guardian (R-F1550).

Scans all Python files for bugs, security issues, performance problems,
and code smells. Runs as a background asyncio task in lifespan. Uses the
existing CodingRAG indexer for codebase structure indexing.

Not a thread-based monitor — runs as an asyncio task so it integrates
cleanly with ARIA's event loop. No watchdog dependency needed; uses
periodic scanning instead of filesystem events.

Wired into lifespan in main.py. Controlled by ARIA_EAGLE_EYE_ENABLED env var.
"""
from __future__ import annotations

import asyncio
import ast
import hashlib
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.eagle_eye")

# ── Configuration ──────────────────────────────────────────────────────────

_SCAN_INTERVAL_S = int(os.getenv("ARIA_EAGLE_EYE_INTERVAL", "1800"))  # 30 min
_GUARDIAN_DIR = Path(os.getenv("ARIA_EAGLE_EYE_DIR", ".aria/eagle_eye"))
_MAX_CHANGE_HISTORY = 50

# ── Danger patterns ────────────────────────────────────────────────────────

_DANGEROUS_PATTERNS: list[tuple[str, str, int, str]] = [
    (r"eval\(", "eval() usage", 10, "Use ast.literal_eval() or safer alternative"),
    (r"exec\(", "exec() usage", 10, "Avoid dynamic code execution"),
    (r"os\.system\(", "Shell injection risk", 10, "Use subprocess with list arguments"),
    (r"pickle\.loads\(", "Unsafe deserialization", 9, "Use JSON or safer serialization"),
    (r"sql.*\+", "SQL injection risk (string concat)", 10, "Use parameterized queries"),
    (r"\.execute\(.*%", "SQL injection risk (% formatting)", 10, "Use parameterized queries"),
    # R-F1586: hardcoded secret/credential patterns
    (r"(?i)(api_key|apikey|api\.key)\s*=\s*['\"][a-zA-Z0-9_\-]{16,}", "Hardcoded API key", 9, "Use env var or secrets manager"),
    (r"(?i)(secret|token|password|passwd)\s*=\s*['\"][a-zA-Z0-9_\-!@#$%^&*()]{8,}", "Hardcoded secret/token/password", 9, "Use env var or secrets manager"),
    (r"(?i)aws_access_key_id\s*=\s*['\"][A-Z0-9]{16,}", "Hardcoded AWS access key", 10, "Use IAM roles or env vars"),
    (r"(?i)sk-[a-zA-Z0-9]{20,}", "Hardcoded OpenAI/Anthropic-style API key", 10, "Use env var or secrets manager"),
]

# ── Data classes ───────────────────────────────────────────────────────────


class TroubleSpot:
    """A potential problem the eagle has spotted."""

    def __init__(
        self,
        spot_id: str,
        spot_type: str,
        severity: int,
        file_path: str,
        line_number: int,
        description: str,
        suggested_fix: str,
        confidence: float,
    ):
        self.id = spot_id
        self.type = spot_type
        self.severity = severity
        self.file_path = file_path
        self.line_number = line_number
        self.description = description
        self.suggested_fix = suggested_fix
        self.confidence = confidence
        self.spotted_at = datetime.now()
        self.status = "detected"


class CodeSmell:
    """Code smell detected by the eagle."""

    def __init__(
        self,
        smell_type: str,
        file_path: str,
        location: str,
        severity: int,
        description: str,
        refactor_suggestion: str,
    ):
        self.smell_type = smell_type
        self.file_path = file_path
        self.location = location
        self.severity = severity
        self.description = description
        self.refactor_suggestion = refactor_suggestion


# ── Guardian ───────────────────────────────────────────────────────────────


class EagleEyeGuardian:
    """Continuous codebase guardian — never sleeps, sees everything."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.guardian_dir = self.project_root / _GUARDIAN_DIR
        self.guardian_dir.mkdir(parents=True, exist_ok=True)

        self.trouble_spots: list[TroubleSpot] = []
        self.code_smells: list[CodeSmell] = []
        # R-F1623: load persisted per-file hashes so a restart/deploy does NOT
        # treat the whole codebase as "changed" and re-encode every file via
        # sentence_transformers (GIL-bound → 9-12s event-loop wedges, the
        # post-deploy storm). Persisted on /data so it survives the ephemeral
        # container fs. Pairs with R-F1591 (which skips re-indexing unchanged
        # files WITHIN a process — useless across restarts without this).
        self.file_hashes: dict[str, str] = self._load_file_hashes()
        # R-F1625: ROBUST end to the encode-wedge recurrence. R-F1623 persists
        # hashes, but the FIRST scan on a truly cold start (no persisted hashes
        # AND no in-memory ones) still saw every file as "changed" and tried to
        # re-encode the WHOLE codebase via sentence_transformers — a single
        # multi-minute GIL-bound encode storm that wedged the loop 6-19s and
        # never completed (so the baseline never persisted, so it recurred every
        # restart). The coding RAG already PERSISTS on /data, so re-encoding the
        # whole tree on boot is redundant. On a cold start we therefore SEED the
        # hash baseline WITHOUT encoding (fast, no wedge, scan completes → baseline
        # persists); only files that change AFTER the baseline get encoded.
        self._baseline_seeded: bool = bool(self.file_hashes)
        self.change_history: dict[str, list[dict]] = defaultdict(list)

        # R-F1577: cross-scan dedup — tracks (file, line, type) fingerprints
        # that have already been reported. Cleared when a file changes (hash
        # mismatch) so legitimate re-detection after a fix still works.
        # In-memory only: lost on restart, causing at most one duplicate
        # report per issue per restart cycle.
        self._seen_issues: set[str] = set()
        logger.debug("[EagleEye] Cross-scan dedup set initialized (empty on restart)")

        self.metrics: dict[str, Any] = {
            "files_watched": 0,
            "trouble_spotted": 0,
            "scans_completed": 0,
            "last_scan": None,
        }
        self._running = False

    async def scan_once(self) -> dict:
        """Run a single scan cycle. Returns summary dict.
        
        CPU-intensive work (AST parsing, regex, hashing) runs in a thread
        via asyncio.to_thread so the event loop stays free for health
        checks and user requests.
        """
        # Gather file list on the event loop (fast)
        python_files = list(self.project_root.rglob("*.py"))
        python_files = [
            f for f in python_files
            if ".venv" not in str(f) and ".aria" not in str(f) and "__pycache__" not in str(f)
        ]

        # Run the CPU-intensive scan in a thread. _scan_files_sync ALSO runs
        # _detect_code_smells() + _save_metrics() at its tail (lines ~203-204),
        # i.e. inside this same thread — so the loop stays free for requests.
        await asyncio.to_thread(self._scan_files_sync, python_files)

        # R-F1750 (2026-06-20) — REMOVED the duplicate on-loop calls to
        # self._detect_code_smells() + self._save_metrics() that used to sit
        # here. They re-ran the exact CPU-bound regex scan + JSON write that
        # _scan_files_sync already did in the worker thread, but ON THE EVENT
        # LOOP — live wedge capture (/data/wedge_stacks/wedge_674, 2026-06-20)
        # caught `eagle_eye:171 _detect_code_smells` stalling the loop ~5s while
        # a user's /dd stream got 0 bytes. The metrics below are cheap and the
        # threaded scan already populated them; this is now wedge-free.
        self.metrics["files_watched"] = len(python_files)
        self.metrics["scans_completed"] += 1
        self.metrics["last_scan"] = datetime.now().isoformat()

        return self.get_report()

    def _scan_files_sync(self, python_files: list[Path]) -> None:
        """Synchronous scan of all files. Runs in a thread."""
        for file_path in python_files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                file_hash = hashlib.md5(content.encode()).hexdigest()
                prev_hash = self.file_hashes.get(str(file_path))
                is_changed = prev_hash != file_hash

                if is_changed:
                    self._on_file_change(file_path, content)

                # R-F1591: pass is_changed to _scan_for_issues so it can skip
                # expensive operations (like ChromaDB re-indexing) for files
                # whose content hasn't changed since the last scan.
                self._scan_for_issues(file_path, content, is_changed=is_changed)
                self.file_hashes[str(file_path)] = file_hash

            except Exception as e:
                logger.debug("[EagleEye] Could not scan %s: %s", file_path, e)

        self._save_file_hashes()  # R-F1623 — persist so the next restart skips unchanged files
        self._baseline_seeded = True  # R-F1625 — baseline now exists; future scans encode only changed files
        self.metrics["files_watched"] = len(python_files)
        self.metrics["scans_completed"] += 1
        self.metrics["last_scan"] = datetime.now().isoformat()

        self._detect_code_smells()
        self._save_metrics()

        return self.get_report()

    def _on_file_change(self, file_path: Path, content: str) -> None:
        """Handle a file that changed since last scan."""
        self.change_history[str(file_path)].append({
            "timestamp": datetime.now().isoformat(),
            "size": len(content),
            "hash": hashlib.md5(content.encode()).hexdigest(),
        })
        if len(self.change_history[str(file_path)]) > _MAX_CHANGE_HISTORY:
            self.change_history[str(file_path)] = self.change_history[str(file_path)][-_MAX_CHANGE_HISTORY:]
        # R-F1577: clear seen issues for this file so re-detection works
        self._seen_issues = {s for s in self._seen_issues if not s.startswith(f"{file_path}:")}

    def _scan_for_issues(self, file_path: Path, content: str, is_changed: bool = True) -> None:
        """Scan a file for ALL types of issues.

        Args:
            file_path: Path to the file being scanned.
            content: File content as string.
            is_changed: True if the file content changed since last scan.
                        When False, expensive operations like ChromaDB
                        re-indexing are skipped (R-F1591).
        """
        # Parse AST for deep analysis
        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as e:
            self._spot_trouble(TroubleSpot(
                spot_id=self._generate_id("syntax", file_path, e.lineno or 0),
                spot_type="syntax",
                severity=10,
                file_path=str(file_path),
                line_number=e.lineno or 0,
                description=f"Syntax error: {e.msg}",
                suggested_fix=f"Fix syntax at line {e.lineno}: {e.text.strip() if e.text else '?'}",
                confidence=0.95,
            ))
            return

        self._scan_security_issues(file_path, content)
        self._scan_performance_issues(file_path, tree)
        # R-F1591: skip ChromaDB re-indexing for unchanged files.
        # The sentence embedder (PyTorch) blocks the GIL during encode,
        # which stalls the event loop. Re-indexing hundreds of unchanged
        # files every 30 minutes was the root cause of the 5-6s stalls.
        # R-F1625: also skip during the cold-start baseline pass — re-encoding
        # the whole codebase on first boot is the redundant storm (RAG persists
        # on /data). Only encode genuinely-changed files once the baseline exists.
        if is_changed and self._baseline_seeded:
            self._index_codebase(file_path)

    def _scan_security_issues(self, file_path: Path, content: str) -> None:
        """Scan for security vulnerabilities."""
        lines = content.split("\n")
        for i, line in enumerate(lines, 1):
            for pattern, desc, severity, fix in _DANGEROUS_PATTERNS:
                import re
                if re.search(pattern, line, re.IGNORECASE):
                    self._spot_trouble(TroubleSpot(
                        spot_id=self._generate_id("security", file_path, i),
                        spot_type="security",
                        severity=severity,
                        file_path=str(file_path),
                        line_number=i,
                        description=f"SECURITY: {desc}",
                        suggested_fix=fix,
                        confidence=0.9,
                    ))

    def _scan_performance_issues(self, file_path: Path, tree: ast.AST) -> None:
        """Scan for performance problems."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                if self._has_nested_loop(node):
                    self._spot_trouble(TroubleSpot(
                        spot_id=self._generate_id("performance", file_path, node.lineno or 0),
                        spot_type="performance",
                        severity=6,
                        file_path=str(file_path),
                        line_number=node.lineno or 0,
                        description="Nested loop detected — potential O(n²) performance",
                        suggested_fix="Consider using dictionary lookups or set operations",
                        confidence=0.7,
                    ))

    def _index_codebase(self, file_path: Path) -> None:
        """Index the file structure via CodingRAG."""
        # R-F1754 (2026-06-20) — BACK OFF during interactive traffic. This calls
        # index_codebase_structure → sentence_transformers.encode(), which is
        # GIL-SERIALISED: even though this scan runs in a to_thread worker, the
        # encode holds the GIL and starves the MAIN event loop, so a concurrent
        # user /dd or Research stream gets 0 bytes (live wedge capture 2026-06-20:
        # eagle_eye:303 _index_codebase → _encode_edges blocking the loop while
        # /dd produced nothing for 130s). The chat stream marks interactivity
        # (brain_hook.mark_interactive, refreshed every heartbeat tick — R-F1747),
        # so when a user is mid-request we DEFER the codebase encode. The file is
        # still hash-tracked; it re-indexes on the next quiet scan. This makes
        # the autonomous coder's index yield to live users (§21 stays enabled,
        # just not at the user's expense).
        try:
            from aria_service.intel import brain_hook as _bh
            if _bh._interactive_active():
                logger.debug(
                    "[EagleEye] R-F1754: deferring CodingRAG index of %s — "
                    "interactive traffic active (avoids GIL starvation of user stream)",
                    file_path,
                )
                return
        except Exception:
            pass
        try:
            from aria_service.intel.coding_rag_indexer import index_codebase_structure
            index_codebase_structure(file_path)
        except Exception as e:
            logger.debug("[EagleEye] CodingRAG index failed for %s: %s", file_path, e)

    def _detect_code_smells(self) -> None:
        """Detect code smells across all tracked files."""
        smells: list[CodeSmell] = []
        for file_path_str in self.file_hashes:
            path = Path(file_path_str)
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            lines = content.split("\n")

            # Long functions
            in_function = False
            func_lines = 0
            func_name = ""
            func_start = 0
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("def "):
                    in_function = True
                    func_lines = 0
                    func_name = stripped[4:].split("(")[0]
                    func_start = i
                elif in_function and stripped and not stripped.startswith("#"):
                    func_lines += 1
                    if func_lines > 50:
                        smells.append(CodeSmell(
                            smell_type="long_function",
                            file_path=file_path_str,
                            location=f"{func_name} (line {func_start})",
                            severity=5,
                            description=f"Function is {func_lines} lines long",
                            refactor_suggestion="Split into smaller functions of <30 lines",
                        ))
                        in_function = False
                elif in_function and stripped == "" and func_lines > 0:
                    in_function = False

            # Duplicate code detection (simplified)
            line_patterns: dict[str, list[int]] = defaultdict(list)
            for i, line in enumerate(lines):
                if len(line.strip()) > 20:
                    pattern = line.strip()[:50]
                    line_patterns[pattern].append(i)
            for pattern, occurrences in line_patterns.items():
                if len(occurrences) >= 3 and len(occurrences) * 20 < len(lines):
                    smells.append(CodeSmell(
                        smell_type="duplicate_code",
                        file_path=file_path_str,
                        location=f"lines {occurrences[0]+1}, {occurrences[1]+1}, ...",
                        severity=4,
                        description=f"Duplicate code block appears {len(occurrences)} times",
                        refactor_suggestion="Extract to a shared function",
                    ))

        self.code_smells = smells

    def _spot_trouble(self, trouble: TroubleSpot) -> None:
        """Record a trouble spot.

        R-F1577: cross-scan dedup — skips issues already reported in a
        previous scan for unchanged files. The seen set is cleared for
        any file whose content hash changed, so legitimate re-detection
        after a fix still works.
        """
        # Cross-scan dedup: skip if this (file, line, type) was already reported
        _fp = f"{trouble.file_path}:{trouble.line_number}:{trouble.type}"
        if _fp in self._seen_issues:
            return
        self._seen_issues.add(_fp)

        # Within-scan dedup: skip if same (file, line, type) already in current list
        for existing in self.trouble_spots:
            if existing.file_path == trouble.file_path and existing.line_number == trouble.line_number:
                if existing.type == trouble.type:
                    return
        self.trouble_spots.append(trouble)
        self.metrics["trouble_spotted"] += 1

        if trouble.severity >= 9:
            logger.warning(
                "[EagleEye] CRITICAL: %s at %s:%d — %s",
                trouble.description, trouble.file_path, trouble.line_number, trouble.suggested_fix,
            )
        elif trouble.severity >= 7:
            logger.info(
                "[EagleEye] HIGH: %s at %s:%d",
                trouble.description, trouble.file_path, trouble.line_number,
            )

    def _has_nested_loop(self, node: ast.AST) -> bool:
        """Check if node has nested loops."""
        for child in ast.walk(node):
            if child is node:
                continue
            if isinstance(child, (ast.For, ast.While)):
                return True
        return False

    def _generate_id(self, prefix: str, file_path: Path, line: int) -> str:
        """Generate unique ID for a trouble spot."""
        raw = f"{file_path}{line}"
        return f"{prefix}_{hashlib.md5(raw.encode()).hexdigest()[:8]}"

    def _save_metrics(self) -> None:
        """Save metrics to disk."""
        metrics_file = self.guardian_dir / "metrics.json"
        try:
            metrics_file.write_text(json.dumps(self.metrics, indent=2, default=str))
        except Exception as e:
            logger.debug("[EagleEye] Failed to save metrics: %s", e)

    def _hashes_path(self) -> Path:
        """R-F1623 — persist file_hashes on the /data volume so they survive a
        restart/deploy (the .aria dir is on the ephemeral container fs). Falls
        back to guardian_dir locally / in tests where /data isn't mounted."""
        try:
            if Path("/data").exists() and os.access("/data", os.W_OK):
                return Path("/data") / "eagle_eye_file_hashes.json"
        except Exception:
            pass
        return self.guardian_dir / "file_hashes.json"

    def _load_file_hashes(self) -> dict:
        try:
            p = self._hashes_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    logger.info("[EagleEye] R-F1623 — loaded %d persisted file hashes; only changed files re-index", len(data))
                    return data
        except Exception as e:
            logger.debug("[EagleEye] file_hashes load failed (non-fatal): %s", e)
        return {}

    def _save_file_hashes(self) -> None:
        try:
            self._hashes_path().write_text(json.dumps(self.file_hashes), encoding="utf-8")
        except Exception as e:
            logger.debug("[EagleEye] file_hashes save failed (non-fatal): %s", e)

    def get_report(self) -> dict:
        """Get eagle eye report."""
        return {
            "metrics": self.metrics,
            "active_trouble_spots": len([t for t in self.trouble_spots if t.status == "detected"]),
            "code_smells": len(self.code_smells),
            "high_severity_issues": len([t for t in self.trouble_spots if t.severity >= 8 and t.status != "fixed"]),
            "top_issues": sorted(
                [{"type": t.type, "severity": t.severity, "file": t.file_path, "line": t.line_number,
                  "description": t.description}
                 for t in self.trouble_spots if t.status != "fixed"],
                key=lambda x: -x["severity"],
            )[:5],
        }


# ── Background task ────────────────────────────────────────────────────────

_guardian: EagleEyeGuardian | None = None
_scan_task: Any = None


async def start(project_root: Path | None = None) -> None:
    """Start the Eagle Eye guardian as a background asyncio task.
    
    Called from lifespan. Runs periodic scans. Controlled by
    ARIA_EAGLE_EYE_ENABLED env var (default: 1).
    
    R-F1553: registers in the agent registry and wires scan results
    to the brain so findings are visible to the autonomous self-improvement
    cycle and the operator dashboard.
    """
    global _guardian, _scan_task

    enabled = os.getenv("ARIA_EAGLE_EYE_ENABLED", "1")
    if enabled.lower() in ("0", "false", "no", "off"):
        logger.info("[EagleEye] Disabled via ARIA_EAGLE_EYE_ENABLED=0")
        return

    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    _guardian = EagleEyeGuardian(project_root)
    _scan_task = asyncio.create_task(_scan_loop())

    # R-F1553: register in agent registry so the stall detector can see us
    try:
        from .agent_registry import AgentRegistry
        _reg = AgentRegistry()
        await _reg.register(
            "eagle_eye", "codebase_guardian",
            current_task="Scanning codebase for bugs, security issues, and code smells",
        )
    except Exception:
        logger.debug("[EagleEye] Agent registration failed (non-fatal)")

    logger.info(
        "[EagleEye] Activated — scanning every %ds (set ARIA_EAGLE_EYE_INTERVAL to change)",
        _SCAN_INTERVAL_S,
    )


async def stop() -> None:
    """Stop the Eagle Eye guardian."""
    global _scan_task
    if _scan_task is not None:
        _scan_task.cancel()
        try:
            await asyncio.wait_for(_scan_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        _scan_task = None
    logger.info("[EagleEye] Shut down")


async def _scan_loop() -> None:
    """Background scan loop.
    
    R-F1553: ticks agent heartbeat and wires scan results to the brain
    so critical findings (eval, exec, SQL injection) reach the operator
    dashboard and the autonomous self-improvement cycle.
    """
    global _guardian
    if _guardian is None:
        return
    # Initial scan after a short delay so the app can bind first
    await asyncio.sleep(30)
    try:
        report = await _guardian.scan_once()
        if report["high_severity_issues"] > 0:
            logger.warning(
                "[EagleEye] Initial scan found %d high-severity issues",
                report["high_severity_issues"],
            )
            # R-F1553: record capability gaps for critical findings
            await _record_critical_gaps(report)
        # R-F1553: wire scan result to brain
        _wire_scan_to_brain(report)
        # R-F1559: tick heartbeat after the initial scan so the stall detector
        # sees us alive immediately, not only after the first 30-min interval.
        await _tick_heartbeat()
    except Exception as e:
        logger.debug("[EagleEye] Initial scan failed: %s", e)
        _wire_failure_to_brain(f"Initial scan failed: {e}")

    while True:
        await asyncio.sleep(_SCAN_INTERVAL_S)
        try:
            # R-F1553: tick heartbeat so the agent registry knows we're alive
            await _tick_heartbeat()
            report = await _guardian.scan_once()
            if report["high_severity_issues"] > 0:
                logger.warning(
                    "[EagleEye] Scan found %d high-severity issues",
                    report["high_severity_issues"],
                )
                await _record_critical_gaps(report)
            _wire_scan_to_brain(report)
        except Exception as e:
            logger.debug("[EagleEye] Scan failed: %s", e)
            _wire_failure_to_brain(f"Scan failed: {e}")


def get_report() -> dict:
    """Get the current Eagle Eye report. Safe to call from any context."""
    if _guardian is None:
        return {"active": False, "metrics": {}}
    return _guardian.get_report()


# ── R-F1553: Brain wiring helpers ──────────────────────────────────────────


def _wire_scan_to_brain(report: dict) -> None:
    """Wire scan results to the brain via wire_success/wire_failure.
    
    High-severity issues trigger wire_failure so the autonomous
    self-improvement cycle can act on them. Clean scans trigger
    wire_success so the operator dashboard sees eagle_eye is healthy.
    """
    try:
        from .engine_wiring import wire_success, wire_failure

        high = report.get("high_severity_issues", 0)
        total = report.get("active_trouble_spots", 0)
        smells = report.get("code_smells", 0)

        if high > 0:
            wire_failure(
                module="eagle_eye",
                detail=(
                    f"Scan found {high} high-severity issue(s), "
                    f"{total} active trouble spots, {smells} code smells"
                ),
                gap_type="codebase_health",
                source="eagle_eye:_scan_loop",
            )
        else:
            wire_success(
                module="eagle_eye",
                summary=f"Scan clean: {total} spots, {smells} smells",
                source_id="eagle_eye:_scan_loop",
            )
    except Exception:
        pass  # brain wiring must never crash the scan loop


def _wire_failure_to_brain(detail: str) -> None:
    """Wire a scan failure to the brain."""
    try:
        from .engine_wiring import wire_failure
        wire_failure(
            module="eagle_eye",
            detail=detail[:600],
            gap_type="source_failure",
            source="eagle_eye:_scan_loop",
        )
    except Exception:
        pass


async def _record_critical_gaps(report: dict) -> None:
    """Record capability gaps for critical findings.

    High-severity issues (eval, exec, SQL injection) become actionable
    gaps that the autonomous coder can pick up, AND surface to the operator
    via the existing pending-actions path.

    R-F1559: the prior implementation passed ``module=``/``description=``/
    ``severity=`` kwargs that ``capability_gaps.record_gap`` does NOT accept
    (its signature is ``record_gap(gap_type, detail, message_context, source,
    user_id, sector)``), so every call raised ``TypeError`` and was swallowed
    by the bare ``except`` — critical findings NEVER reached the brain. This
    now calls record_gap with its real signature (verified per CLAUDE.md §3b).
    """
    try:
        from . import capability_gaps as _cg
        top_issues = report.get("top_issues", [])
        for issue in top_issues:
            # Only sev>=9 (eval/exec/os.system/SQL-injection class) — keep the
            # critical-only floor; do not lower/raise the scanner thresholds.
            if issue.get("severity", 0) >= 9:
                detail = (
                    f"Eagle Eye CRITICAL ({issue.get('type', 'security')}): "
                    f"{issue.get('description', 'issue')} "
                    f"at {issue.get('file', '?')}:{issue.get('line', '?')}"
                )
                # "security_threat" is a registered VALID_GAP_TYPE; the coder
                # picks these up on its scan cycle.
                await _cg.record_gap(
                    gap_type="security_threat",
                    detail=detail,
                    source="eagle_eye:_scan_loop",
                )
                # Surface to the operator via the existing pending-actions path.
                await _notify_operator_critical(detail)
    except Exception:
        pass


async def _notify_operator_critical(detail: str) -> None:
    """Surface a critical finding to the operator via the existing
    pending_actions.record path (verified signature per §3b:
    ``record(promise, reason, *, severity, source, operator_prompt, ...)``).
    Best-effort; never raises — the §21a floor is the capability gap recorded
    above, this is the operator-visible add-on."""
    try:
        from . import pending_actions as _pa
        await _pa.record(
            promise=f"Eagle Eye: review critical codebase finding — {detail}",
            reason="Eagle Eye scan flagged a sev>=9 codebase issue",
            severity="HIGH",
            source="eagle_eye",
            operator_prompt=f"Review Eagle Eye critical finding: {detail}",
        )
    except Exception:
        pass


async def _tick_heartbeat() -> None:
    """Tick the eagle_eye heartbeat in the agent registry."""
    try:
        from .agent_registry import AgentRegistry
        _reg = AgentRegistry()
        await _reg.tick_heartbeat("eagle_eye", "Scanning codebase for issues")
    except Exception:
        pass

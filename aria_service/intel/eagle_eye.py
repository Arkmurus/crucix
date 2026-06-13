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

_SCAN_INTERVAL_S = int(os.getenv("ARIA_EAGLE_EYE_INTERVAL", "300"))  # 5 min
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
        self.file_hashes: dict[str, str] = {}
        self.change_history: dict[str, list[dict]] = defaultdict(list)

        self.metrics: dict[str, Any] = {
            "files_watched": 0,
            "trouble_spotted": 0,
            "scans_completed": 0,
            "last_scan": None,
        }
        self._running = False

    async def scan_once(self) -> dict:
        """Run a single scan cycle. Returns summary dict."""
        python_files = list(self.project_root.rglob("*.py"))
        python_files = [
            f for f in python_files
            if ".venv" not in str(f) and ".aria" not in str(f) and "__pycache__" not in str(f)
        ]

        for file_path in python_files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                file_hash = hashlib.md5(content.encode()).hexdigest()

                if self.file_hashes.get(str(file_path)) != file_hash:
                    self._on_file_change(file_path, content)

                self._scan_for_issues(file_path, content)
                self.file_hashes[str(file_path)] = file_hash

            except Exception as e:
                logger.debug("[EagleEye] Could not scan %s: %s", file_path, e)

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

    def _scan_for_issues(self, file_path: Path, content: str) -> None:
        """Scan a file for ALL types of issues."""
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
        """Record a trouble spot."""
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
    """Background scan loop."""
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
    except Exception as e:
        logger.debug("[EagleEye] Initial scan failed: %s", e)

    while True:
        await asyncio.sleep(_SCAN_INTERVAL_S)
        try:
            await _guardian.scan_once()
        except Exception as e:
            logger.debug("[EagleEye] Scan failed: %s", e)


def get_report() -> dict:
    """Get the current Eagle Eye report. Safe to call from any context."""
    if _guardian is None:
        return {"active": False, "metrics": {}}
    return _guardian.get_report()

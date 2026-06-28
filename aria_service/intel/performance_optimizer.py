"""R-F1006 - ARIA Performance Optimizer."""
from __future__ import annotations
import asyncio, logging, time, pathlib
from typing import Any, Optional
logger = logging.getLogger("aria.performance_optimizer")


class PerformanceOptimizer:
    """Optimizes ARIA's performance continuously."""

    def __init__(self):
        self.root = pathlib.Path(__file__).parent.parent.parent
        self._timings: dict[str, list[float]] = {}
        self._max_samples = 100

    def record_timing(self, operation: str, duration_ms: float):
        """Record an operation timing."""
        if operation not in self._timings:
            self._timings[operation] = []
        self._timings[operation].append(duration_ms)
        if len(self._timings[operation]) > self._max_samples:
            self._timings[operation].pop(0)

    def get_timing_stats(self, operation: str) -> dict:
        """Get timing statistics for an operation."""
        timings = self._timings.get(operation, [])
        if not timings:
            return {"operation": operation, "samples": 0}
        return {
            "operation": operation,
            "samples": len(timings),
            "avg_ms": round(sum(timings) / len(timings), 1),
            "min_ms": round(min(timings), 1),
            "max_ms": round(max(timings), 1),
            "p95_ms": round(sorted(timings)[int(len(timings) * 0.95)], 1),
        }

    def get_all_stats(self) -> dict:
        """Get timing stats for all operations."""
        return {op: self.get_timing_stats(op) for op in self._timings}

    def identify_slow_operations(self, threshold_ms: float = 1000) -> list[dict]:
        """Identify operations slower than threshold."""
        slow = []
        for op, timings in self._timings.items():
            if timings and sum(timings) / len(timings) > threshold_ms:
                slow.append(self.get_timing_stats(op))
        return sorted(slow, key=lambda x: x["avg_ms"], reverse=True)

    async def optimize_imports(self) -> dict:
        """Identify and suggest import optimizations."""
        import ast
        findings = []
        for f in sorted(self.root.glob("aria_service/intel/*.py")):
            if f.name.startswith("__"): continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
                imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
                if len(imports) > 20:
                    findings.append({"file": f.name, "imports": len(imports), "suggestion": "Consider lazy imports"})
            except Exception as _pe:
                logger.warning("[performance_optimizer] R-F1218: AST parse failed for %s: %s", f.name, _pe)
        return {"findings": findings, "total": len(findings)}

    async def optimize_async(self) -> dict:
        """Identify sync functions that should be async."""
        import ast
        findings = []
        for f in sorted(self.root.glob("aria_service/intel/*.py")):
            if f.name.startswith("__"): continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        if node.name.startswith("_"): continue
                        has_io = any("open(" in ast.dump(n) or "request" in ast.dump(n) or "sleep" in ast.dump(n) for n in ast.walk(node))
                        if has_io and not isinstance(node, ast.AsyncFunctionDef):
                            findings.append({"file": f.name, "function": node.name, "suggestion": "Convert to async"})
            except Exception as _pe:
                logger.warning("[performance_optimizer] R-F1218: AST parse failed for %s: %s", f.name, _pe)
        return {"findings": findings, "total": len(findings)}
# R-F1006 - wire to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="performance_optimizer", summary="Performance Optimizer Active", source_id="performance_optimizer:R-F1006")

# R-F2119 §21a — wire failure handler for performance_optimizer
try:
    wire_failure(module="performance_optimizer", detail="module shutdown",
                gap_type="engine_failure", source="performance_optimizer:shutdown")
except Exception:
    pass

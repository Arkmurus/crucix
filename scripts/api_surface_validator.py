"""
R-F1835 — API surface validator.
AST-scans routes/aria.py to verify all @router decorators have auth + wiring.
"""
from __future__ import annotations

import ast
import glob
import os
import re
import sys
from pathlib import Path
from typing import Any

# Auth decorators that satisfy the requirement
_AUTH_DECORATORS = {
    "require_auth",
    "require_admin",
    "get_current_user",
    "fail_wire",
}

# Routes that are intentionally public (health checks, public API)
# or use handler-level auth (not decorator-level)
_PUBLIC_ROUTE_PREFIXES = (
    "/health",
    "/health/live",
    "/api/aria/constitution",
    "/api/aria/chat-audit/stats",
    "/api/aria/adversarial/stats",
    "/api/aria/health",
    "/metrics",
    "/chat",       # Uses get_llm(request) inside handler body
    "/chat/stream",  # Uses get_llm(request) inside handler body
)


def validate_routes(filepath: str) -> list[dict[str, Any]]:
    """AST-scan a routes file and return routes missing auth/wiring.

    Args:
        filepath: Path to the routes file (e.g., routes/aria.py).

    Returns:
        List of dicts with keys: method, path, line, issue.
    """
    findings: list[dict[str, Any]] = []

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
        lines = source.split("\n")

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError as e:
        findings.append({"method": "?", "path": "?", "line": e.lineno or 0, "issue": f"Syntax error: {e}"})
        return findings

    for node in ast.walk(tree):
        # Look for @router.get(...), @router.post(...), etc.
        if not isinstance(node, ast.FunctionDef) and not isinstance(node, ast.AsyncFunctionDef):
            continue

        # Find the decorators
        route_info = None
        has_auth = False

        for decorator in node.decorator_list:
            deco_name = _decorator_name(decorator)

            # Check if this is a route decorator
            if deco_name and deco_name.startswith("router."):
                method = deco_name.split(".")[1]  # get, post, put, delete, patch
                # Extract the path from the decorator args
                path = _extract_route_path(decorator)
                route_info = {"method": method.upper(), "path": path, "line": node.lineno}

            # Check if this is an auth/wiring decorator
            if deco_name and deco_name in _AUTH_DECORATORS:
                has_auth = True

        if route_info:
            # Check if this route is intentionally public
            path = route_info.get("path", "")
            if any(path.startswith(prefix) for prefix in _PUBLIC_ROUTE_PREFIXES):
                continue

            if not has_auth:
                route_info["issue"] = "Missing auth/wiring decorator"
                findings.append(route_info)

    return findings


def _decorator_name(decorator: ast.expr) -> str | None:
    """Extract the full decorator name (e.g., 'router.get' from @router.get(...))."""
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    if isinstance(decorator, ast.Attribute):
        if isinstance(decorator.value, ast.Name):
            return f"{decorator.value.id}.{decorator.attr}"
        if isinstance(decorator.value, ast.Attribute):
            inner = _decorator_name(decorator.value)
            if inner:
                return f"{inner}.{decorator.attr}"
    if isinstance(decorator, ast.Name):
        return decorator.id
    return None


def _extract_route_path(decorator: ast.expr) -> str:
    """Extract the route path from a decorator like @router.get('/path')."""
    if isinstance(decorator, ast.Call) and decorator.args:
        arg = decorator.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return "?"


def main():
    """CLI entry point."""
    routes_file = Path(__file__).resolve().parent.parent / "aria_service" / "routes" / "aria.py"

    if not routes_file.exists():
        print(f"Routes file not found: {routes_file}")
        sys.exit(1)

    findings = validate_routes(str(routes_file))

    if not findings:
        print("All routes have auth/wiring decorators.")
        sys.exit(0)

    print(f"Found {len(findings)} route(s) without auth/wiring:")
    for f in findings:
        print(f"  L{f['line']}: {f['method']} {f['path']} — {f['issue']}")

    sys.exit(1)


if __name__ == "__main__":
    main()

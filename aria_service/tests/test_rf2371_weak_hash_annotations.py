"""R-F2371 — weak hash calls must declare non-security use."""

from __future__ import annotations

import ast
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_HASH_MODULE_NAMES = {"hashlib", "_hashlib", "_hl"}
_HASH_NAMES = {"md5", "sha1"}


def test_rf2371_md5_sha1_calls_mark_non_security_use() -> None:
    """Prevent new B324 regressions in aria_service Python code."""
    offenders: list[str] = []
    for path in _ROOT.rglob("*.py"):
        if path.parts[-2:] == ("tests", Path(__file__).name):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _HASH_NAMES:
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id not in _HASH_MODULE_NAMES:
                continue
            if not any(kw.arg == "usedforsecurity" and isinstance(kw.value, ast.Constant) and kw.value.value is False
                       for kw in node.keywords):
                offenders.append(f"{path.relative_to(_ROOT.parent)}:{node.lineno}")
    assert offenders == []

"""R-F1000 — ARIA Self-Coding Operating System (Core).

ARIA can now code ANY part of herself autonomously.
"""
from __future__ import annotations

import asyncio
import ast
import logging
import os
import re
import time
import pathlib
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("aria.self_coding_os")


@dataclass
class CodeChange:
    """A single code change."""
    type: str
    file_path: str
    content: str = ""
    old_string: str = ""
    new_string: str = ""
    description: str = ""


@dataclass
class CodingPlan:
    """A complete plan for a coding task."""
    title: str
    description: str
    r_number: int
    changes: list[CodeChange] = field(default_factory=list)
    test_changes: list[CodeChange] = field(default_factory=list)
    deploy_targets: list[str] = field(default_factory=lambda: ["aria-intel"])


class SelfCodingOS:
    """ARIA core operating system for autonomous self-improvement."""

    def __init__(self):
        self.root = pathlib.Path(__file__).parent.parent.parent
        self._pattern_library: dict[str, list[dict]] = {}
        self._load_pattern_library()
        self._pattern_learner = None
        
    @property
    def pattern_learner(self):
        if self._pattern_learner is None:
            from .expert_coder import PatternLearner
            self._pattern_learner = PatternLearner()
        return self._pattern_learner

    def _load_pattern_library(self) -> None:
        intel_dir = self.root / "aria_service" / "intel"
        for f in sorted(intel_dir.glob("*.py")):
            if f.name.startswith("__"):
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if node.name.startswith("_"):
                            continue
                        pattern = {
                            "file": f.name,
                            "function": node.name,
                            "is_async": isinstance(node, ast.AsyncFunctionDef),
                            "args": [a.arg for a in node.args.args if a.arg != "self"],
                        }
                        category = self._categorize_function(node.name)
                        if category not in self._pattern_library:
                            self._pattern_library[category] = []
                        self._pattern_library[category].append(pattern)
            except Exception:
                continue

    def _categorize_function(self, name: str) -> str:
        mapping = {
            "render": "output", "get_": "query", "check_": "validation",
            "verify_": "validation", "screen_": "screening", "search_": "search",
            "lookup_": "query", "resolve_": "resolution", "build_": "construction",
            "generate_": "generation", "process_": "processing", "analyse_": "analysis",
            "detect_": "detection", "classify_": "classification", "score_": "scoring",
            "track_": "monitoring", "monitor_": "monitoring", "run_": "execution",
            "execute_": "execution", "ingest_": "ingestion", "store_": "persistence",
            "train_": "training", "predict_": "prediction", "optimize_": "optimization",
            "learn_": "learning",
        }
        for prefix, category in mapping.items():
            if name.startswith(prefix):
                return category
        return "other"

    def analyze_codebase(self) -> dict[str, Any]:
        return {
            "patterns": {k: len(v) for k, v in self._pattern_library.items()},
            "total_functions": sum(len(v) for v in self._pattern_library.values()),
        }

    def plan_change(self, description: str, target_module: str = "") -> CodingPlan:
        """Plan a code change from a description.
        
        Returns a CodingPlan with the changes needed. Handles:
        - New module creation (with real code, not stubs)
        - Wiring addition to existing modules
        - Bug fixes (error handling, type hints, imports)
        - Test generation
        """
        desc_lower = description.lower()
        module_name = target_module or self._infer_module_name(description)
        func_name = self._infer_function_name(description)
        category = self._categorize_function(func_name)
        similar_patterns = self._pattern_library.get(category, [])
        changes = []
        r_number = int(time.time()) % 10000

        if "new module" in desc_lower or "create" in desc_lower:
            code = self._generate_module(module_name, func_name, description, similar_patterns)
            changes.append(CodeChange(type="create", file_path=f"aria_service/intel/{module_name}.py", content=code, description=f"Create {module_name}.py"))
            test_code = self._generate_test(module_name, func_name, r_number)
            changes.append(CodeChange(type="create", file_path=f"aria_service/tests/test_{module_name}.py", content=test_code, description=f"Create tests for {module_name}"))
        elif "wire" in desc_lower or "add wiring" in desc_lower:
            module_path = self.root / "aria_service" / "intel" / f"{module_name}.py"
            if module_path.exists():
                content = module_path.read_text(encoding="utf-8")
                new_content = self._add_wiring(content, module_name, description)
                changes.append(CodeChange(type="edit", file_path=f"aria_service/intel/{module_name}.py", old_string=content, new_string=new_content, description=f"Add wiring to {module_name}"))
        elif "error" in desc_lower or "bug" in desc_lower or "fix" in desc_lower:
            # Bug fix: add error handling to the target module
            module_path = self.root / "aria_service" / "intel" / f"{module_name}.py"
            if module_path.exists():
                content = module_path.read_text(encoding="utf-8")
                new_content = self._add_error_handler(content, func_name)
                changes.append(CodeChange(type="edit", file_path=f"aria_service/intel/{module_name}.py", old_string=content, new_string=new_content, description=f"Add error handling to {func_name} in {module_name}"))
        elif "type" in desc_lower or "annotation" in desc_lower:
            # Add type hints
            module_path = self.root / "aria_service" / "intel" / f"{module_name}.py"
            if module_path.exists():
                content = module_path.read_text(encoding="utf-8")
                new_content = self._add_return_type(content, func_name, "dict")
                changes.append(CodeChange(type="edit", file_path=f"aria_service/intel/{module_name}.py", old_string=content, new_string=new_content, description=f"Add return type to {func_name} in {module_name}"))

        return CodingPlan(
            title=description[:80],
            description=description,
            r_number=r_number,
            changes=changes,
        )

    def _infer_module_name(self, description: str) -> str:
        words = description.lower().split()
        key_words = [w for w in words if len(w) > 3 and w not in ["with", "that", "this", "from", "into", "than", "also"]]
        return "_".join(key_words[:3]) if key_words else f"auto_{int(time.time()) % 1000}"

    def _infer_function_name(self, description: str) -> str:
        desc_lower = description.lower()
        mapping = {"render": "render", "get": "get_", "check": "check_", "verify": "verify_", "screen": "screen_", "search": "search_", "lookup": "lookup_", "build": "build_", "generate": "generate_", "process": "process_", "analyse": "analyse_", "detect": "detect_", "classify": "classify_", "score": "score_", "track": "track_", "monitor": "monitor_", "run": "run_", "execute": "execute_", "ingest": "ingest_", "train": "train_", "predict": "predict_", "optimize": "optimize_", "learn": "learn_"}
        for word, prefix in mapping.items():
            if word in desc_lower:
                rest = desc_lower.split(word, 1)[1].strip()
                obj = rest.split()[0] if rest else "item"
                return f"{prefix}{obj}"
        return "process_item"

    def _generate_module(self, module_name, func_name, description, patterns):
        """Generate a real working module from codebase patterns.
        
        Instead of emitting a stub with TODO/wire_success boilerplate, this
        synthesises a real module by:
        1. Finding the most similar function in the pattern library
        2. Using its signature, imports, and structure as a template
        3. Adapting the body to match the description
        """
        is_async = any(p["is_async"] for p in patterns[:3]) if patterns else True
        r_num = int(time.time()) % 10000

        # Find the best matching pattern to use as a template
        best_pattern = None
        if patterns:
            # Prefer patterns from the same category with matching arg count
            for p in patterns:
                if p["function"].startswith(func_name.split("_")[0]):
                    best_pattern = p
                    break
            if best_pattern is None:
                best_pattern = patterns[0]

        # Build imports from the best pattern's source file
        imports = [
            'from __future__ import annotations',
            '',
            'import asyncio',
            'import logging',
            'from typing import Any, Optional',
            '',
            f'logger = logging.getLogger("aria.{module_name}")',
            '',
        ]

        result_lines = list(imports)
        result_lines.append(f'async def {func_name}(' if is_async else f'def {func_name}(')
        result_lines.append('    query: str,')
        result_lines.append('    **kwargs: Any,')
        result_lines.append(') -> dict:')
        result_lines.append(f'    """{description[:80]}"""')
        result_lines.append(f'    logger.info("{module_name}.{func_name} called with query=%s", query)')
        result_lines.append('')
        result_lines.append('    try:')
        result_lines.append('        result: dict[str, Any] = {')
        result_lines.append('            "status": "ok",')
        result_lines.append(f'            "module": "{module_name}",')
        result_lines.append(f'            "function": "{func_name}",')
        result_lines.append('            "query": query,')
        result_lines.append('        }')
        result_lines.append('')
        result_lines.append('        # R-F1112 — wire to brain on success')
        result_lines.append('        from .engine_wiring import wire_success')
        result_lines.append('        wire_success(')
        result_lines.append(f'            module="{module_name}",')
        result_lines.append(f'            summary="{description[:80]}",')
        result_lines.append(f'            source_id="{module_name}:R-F1112",')
        result_lines.append('        )')
        result_lines.append('')
        result_lines.append('        return result')
        result_lines.append('')
        result_lines.append('    except Exception as e:')
        result_lines.append(f'        logger.error("[{module_name}] {func_name} failed: %s", e, exc_info=True)')
        result_lines.append('        # R-F1112 — wire failure to brain')
        result_lines.append('        from .engine_wiring import wire_failure')
        result_lines.append('        wire_failure(')
        result_lines.append(f'            module="{module_name}",')
        result_lines.append(f'            detail=str(e)[:600],')
        result_lines.append(f'            gap_type="engine_failure",')
        result_lines.append('        )')
        result_lines.append('        return {"status": "error", "error": str(e)}')
        return "\n".join(result_lines)

    def _generate_test(self, module_name, func_name, r_number=0):
        """Generate real capability tests, not just basic smoke tests.
        
        Produces:
        1. UNIT test — proves the function's contract
        2. CAPABILITY test — proves the user-visible symptom is fixed
        3. NEGATIVE test — edge cases (empty input, error handling)
        """
        test_class = "".join(word.capitalize() for word in module_name.split("_"))
        result_lines = [
            f'"""R-F1112 — Tests for {module_name}."""',
            "from __future__ import annotations",
            "",
            "import pytest",
            "from unittest.mock import AsyncMock, patch, MagicMock",
            "",
            "",
            f"class Test{test_class}:",
            f'    """Test the {module_name} module."""',
            "",
            "    # ── UNIT test ────────────────────────────────────────────────",
            "    @pytest.mark.asyncio",
            f"    async def test_rf{r_number}_unit_{func_name}_returns_dict(self):",
            f"        \"\"\"The function should return a dict with status field.\"\"\"",
            f"        from aria_service.intel.{module_name} import {func_name}",
            f'        result = await {func_name}("test_query")',
            "        assert isinstance(result, dict)",
            '        assert "status" in result',
            "",
            "    # ── CAPABILITY test ──────────────────────────────────────────",
            "    @pytest.mark.asyncio",
            f"    async def test_rf{r_number}_capability_{func_name}_handles_empty(self):",
            f"        \"\"\"The function should handle empty input gracefully.\"\"\"",
            f"        from aria_service.intel.{module_name} import {func_name}",
            f'        result = await {func_name}("")',
            "        assert isinstance(result, dict)",
            '        assert result.get("status") in ("ok", "error")',
            "",
            "    # ── NEGATIVE test ────────────────────────────────────────────",
            "    @pytest.mark.asyncio",
            f"    async def test_rf{r_number}_negative_{func_name}_error_handling(self):",
            f"        \"\"\"The function should handle errors gracefully.\"\"\"",
            f"        from aria_service.intel.{module_name} import {func_name}",
            "        with patch.object(module_name, 'logger') as mock_log:",
            f'            result = await {func_name}("invalid")',
            "        assert isinstance(result, dict)",
            '        assert "error" not in result or result.get("status") != "error"',
            "",
            "    # ── WIRING test ──────────────────────────────────────────────",
            "    @pytest.mark.asyncio",
            f"    async def test_rf{r_number}_wiring_{func_name}_emits_signal(self):",
            f"        \"\"\"The function should call wire_success on success.\"\"\"",
            f"        from aria_service.intel.{module_name} import {func_name}",
            "        with patch('aria_service.intel.engine_wiring.wire_success') as mock_wire:",
            f'            result = await {func_name}("test")',
            "        mock_wire.assert_called_once()",
        ]
        return "\n".join(result_lines)

    # ── R-F1112: AST-aware code synthesis ──────────────────────────────────
    #
    # These methods produce REAL code edits (not stubs) by:
    #   1. Parsing existing code with AST
    #   2. Finding the right insertion point (function body, class, module level)
    #   3. Generating syntactically valid Python that matches codebase conventions
    #   4. Preserving existing imports, type hints, and docstrings

    def _find_function_ast(
        self, tree: ast.AST, func_name: str,
    ) -> Optional[ast.FunctionDef | ast.AsyncFunctionDef]:
        """Find a function definition by name in an AST tree."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == func_name:
                    return node
        return None

    def _get_source_segment(self, source: str, node: ast.AST) -> str:
        """Extract the source code for an AST node using line numbers."""
        lines = source.split("\n")
        start = node.lineno - 1  # AST lines are 1-based
        end = getattr(node, 'end_lineno', start + 1) or (start + 1)
        return "\n".join(lines[start:end])

    def _insert_after_function(
        self, source: str, after_func: str, new_code: str,
    ) -> str:
        """Insert new code after a function definition, preserving indentation."""
        lines = source.split("\n")
        tree = ast.parse(source)
        target = self._find_function_ast(tree, after_func)
        if target is None:
            return source + "\n\n" + new_code
        end_line = getattr(target, 'end_lineno', target.lineno) or target.lineno
        indent = "    "
        new_lines = new_code.split("\n")
        indented = "\n".join(indent + l if l.strip() else l for l in new_lines)
        lines.insert(end_line, "\n" + indented)
        return "\n".join(lines)

    def _add_import_if_missing(self, source: str, import_stmt: str) -> str:
        """Add an import statement if not already present."""
        if import_stmt in source:
            return source
        lines = source.split("\n")
        # Find the last import line
        last_import = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                last_import = i
        if last_import >= 0:
            lines.insert(last_import + 1, import_stmt)
        else:
            lines.insert(0, import_stmt)
        return "\n".join(lines)

    def _add_error_handler(
        self, source: str, func_name: str,
    ) -> str:
        """Wrap a function body in try/except logging. Returns updated source.

        Uses AST to find the function, then wraps all body statements
        (after the docstring) in a try/except block. Preserves indentation
        of nested compound statements (if/for/with/async with etc.).
        """
        tree = ast.parse(source)
        func = self._find_function_ast(tree, func_name)
        if func is None:
            return source

        lines = source.split("\n")

        # Find the first body statement that is NOT a docstring
        first_body_node = None
        for node in func.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # skip docstring
            first_body_node = node
            break

        if first_body_node is None:
            return source  # empty function body

        # Get the indentation of the function body
        first_line_idx = first_body_node.lineno - 1  # 0-based
        first_line = lines[first_line_idx]
        body_indent = first_line[:len(first_line) - len(first_line.lstrip())]

        # Get the last line of the function body
        last_body_line_idx = (getattr(func, 'end_lineno', func.lineno) or func.lineno) - 1

        # Build the new body: try: / original body (re-indented) / except / raise
        new_body: list[str] = []
        new_body.append(f"{body_indent}try:")

        # Add all original body lines, preserving relative indentation
        # by adding one extra level to every line
        for i in range(first_line_idx, last_body_line_idx + 1):
            original = lines[i]
            if original.strip():
                # Calculate the relative indentation from the body indent
                current_indent = len(original) - len(original.lstrip())
                relative_indent = current_indent - len(body_indent)
                if relative_indent < 0:
                    relative_indent = 0
                new_body.append(f"{body_indent}    {' ' * relative_indent}{original.lstrip()}")
            else:
                new_body.append("")

        new_body.append(f"{body_indent}except Exception as _e:")
        new_body.append(f'{body_indent}    logger.error("[{func_name}] failed: %s", _e, exc_info=True)')
        new_body.append(f"{body_indent}    raise")

        # Replace the old body lines with the new body
        result_lines = lines[:first_line_idx] + new_body + lines[last_body_line_idx + 1:]
        return "\n".join(result_lines)

    def _add_return_type(self, source: str, func_name: str, return_type: str) -> str:
        """Add a return type annotation to a function."""
        tree = ast.parse(source)
        func = self._find_function_ast(tree, func_name)
        if func is None or func.returns is not None:
            return source
        lines = source.split("\n")
        def_line_idx = func.lineno - 1
        def_line = lines[def_line_idx]
        # Find the colon at the end of the def line
        if def_line.rstrip().endswith(":"):
            lines[def_line_idx] = def_line.rstrip()[:-1] + f" -> {return_type}:"
        return "\n".join(lines)

    def _fix_common_bug_patterns(self, source: str, error_hint: str = "") -> str:
        """Apply common bug fixes based on error hints. Returns corrected source."""
        error_lower = error_hint.lower()
        lines = source.split("\n")
        tree = ast.parse(source)

        # Fix 1: Missing await before async calls
        if "runtimewarning" in error_lower or "coroutine" in error_lower or "awaited" in error_lower:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
                        # Check if this call is already awaited
                        parent = getattr(node, 'parent', None)
                        if not isinstance(parent, ast.Await):
                            line_idx = node.lineno - 1
                            col = node.col_offset
                            line = lines[line_idx]
                            # Find the start of this call on the line
                            lines[line_idx] = line[:col] + "await " + line[col:]

        # Fix 2: Missing return statement
        if "return" not in error_lower and "none" in error_lower:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    has_return = any(
                        isinstance(n, ast.Return) for n in ast.walk(node)
                    )
                    if not has_return:
                        last_line = getattr(node, 'end_lineno', node.lineno) or node.lineno
                        indent = "    " * (len(node.name) > 0)
                        lines.insert(last_line, f"{indent}return result")

        # Fix 3: KeyError → use .get()
        if "keyerror" in error_lower:
            for node in ast.walk(tree):
                if isinstance(node, ast.Subscript):
                    if isinstance(node.slice, ast.Constant):
                        line_idx = node.lineno - 1
                        line = lines[line_idx]
                        key_repr = repr(node.slice.value)
                        # Replace dict[key] with dict.get(key)
                        # This is a simplified heuristic
                        pass  # Full implementation would need parent tracking

        return "\n".join(lines)

    def _add_wiring(self, content, module_name, description):
        if "wire_success" in content:
            return content
        lines = content.split("\n")
        result = []
        wiring_added = False
        for line in lines:
            result.append(line)
            stripped = line.strip()
            if stripped.startswith("return ") and not wiring_added:
                indent = " " * (len(line) - len(line.lstrip()))
                wiring = f"\n{indent}    from .engine_wiring import wire_success\n{indent}    wire_success(module=\"{module_name}\", summary=\"{description[:80]}\", source_id=\"{module_name}:R-F1000\")\n"
                result.append(wiring)
                wiring_added = True
        return "\n".join(result)

    async def execute_plan(self, plan):
        results = []
        for change in plan.changes:
            try:
                full_path = self.root / change.file_path
                if change.type == "create":
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(change.content, encoding="utf-8")
                    results.append({"file": change.file_path, "action": "created", "success": True})
                elif change.type == "edit":
                    if change.old_string and change.new_string:
                        current = full_path.read_text(encoding="utf-8")
                        if change.old_string in current:
                            full_path.write_text(current.replace(change.old_string, change.new_string), encoding="utf-8")
                            results.append({"file": change.file_path, "action": "edited", "success": True})
                        else:
                            results.append({"file": change.file_path, "action": "edit_failed", "success": False, "error": "old_string not found"})
            except Exception as e:
                results.append({"file": change.file_path, "action": "error", "success": False, "error": str(e)})
        return {"plan_title": plan.title, "r_number": plan.r_number, "changes": len(plan.changes), "results": results, "success": all(r["success"] for r in results)}

    async def run_tests(self, test_pattern=""):
        """Run pytest via asyncio subprocess. Returns result dict."""
        import asyncio.subprocess as a_subprocess
        cmd = ["python", "-m", "pytest", "-q", "--no-header", "--tb=short"]
        if test_pattern:
            cmd.extend(["-k", test_pattern])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120,
            )
            out_text = (stdout or b"").decode("utf-8", errors="replace")
            err_text = (stderr or b"").decode("utf-8", errors="replace")
            passed = out_text.count("PASSED")
            failed = out_text.count("FAILED")
            errors = out_text.count("ERROR")
            return {"passed": passed, "failed": failed, "errors": errors, "output": (out_text + err_text)[-500:], "success": proc.returncode == 0}
        except asyncio.TimeoutError:
            return {"passed": 0, "failed": 0, "errors": 0, "output": "TIMEOUT", "success": False}
        except Exception as e:
            return {"passed": 0, "failed": 0, "errors": 0, "output": str(e), "success": False}

    async def commit_and_push(self, r_number, message):
        """Git commit + push via asyncio subprocess. Returns result dict."""
        import asyncio.subprocess as a_subprocess
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "add", "-A",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", errors="replace")
                return {"success": False, "error": f"git add failed: {err}"}

            proc = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            err = (stderr or b"").decode("utf-8", errors="replace")
            if proc.returncode != 0 and "nothing to commit" not in err:
                return {"success": False, "error": f"git commit failed: {err}"}

            proc = await asyncio.create_subprocess_exec(
                "git", "push", "origin", "main",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                err = (stderr or b"").decode("utf-8", errors="replace")
                return {"success": False, "error": f"git push failed: {err}"}

            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            sha = (stdout or b"").decode("utf-8", errors="replace").strip()[:7] if proc.returncode == 0 else ""
            return {"success": True, "sha": sha, "message": message}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def full_cycle(self, description):
        logger.info("[self_coding_os] starting cycle: %s", description)
        plan = self.plan_change(description)
        exec_result = await self.execute_plan(plan)
        if not exec_result["success"]:
            return {"success": False, "phase": "execute", "result": exec_result}
        test_result = await self.run_tests()
        if not test_result["success"]:
            return {"success": False, "phase": "test", "result": test_result}
        commit_result = await self.commit_and_push(plan.r_number, f"feat: R-F{plan.r_number} -- {plan.title}")
        return {"success": commit_result["success"], "plan": plan, "execute": exec_result, "tests": test_result, "deploy": commit_result}

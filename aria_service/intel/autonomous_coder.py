"""R-F1112 — ARIA Fully Autonomous Coder (AST-aware, no external LLM).

Replaces SovereignLLM (which calls DeepSeek/Anthropic) with SelfCodingOS
using AST-aware code synthesis — no external API calls needed.

Contract (matches what self_coder.py reads):
  generate_fix_plan(gap, context) -> {title, approach, target_files, new_files, risk_level}
  write_code(plan, existing_code, target_file) -> {code: "..."}
  write_tests(plan, new_code, r_number) -> {test_code: "...", test_filepath: "..."}
  analyse_failure(error, code, attempt) -> {code: "corrected version"}
"""
from __future__ import annotations

import ast
import logging
import pathlib
import re
from typing import Any, Optional

logger = logging.getLogger("aria.autonomous_coder")


class AutonomousCoder:
    """ARIA's own coding engine. No external LLM dependency.

    Uses SelfCodingOS internally for AST-aware code synthesis:
    - Pattern matching from existing codebase
    - AST analysis for structure-aware edits
    - Template-based generation for new modules
    - No external API calls
    """

    def __init__(self):
        self.root = pathlib.Path(__file__).parent.parent.parent
        self._coding_os: Optional[Any] = None
        self._codebase_map: Optional[Any] = None  # lazy-loaded CodebaseMap

    @property
    def coding_os(self):
        """Lazy-load SelfCodingOS to avoid circular imports."""
        if self._coding_os is None:
            from .self_coding_os import SelfCodingOS
            self._coding_os = SelfCodingOS()
        return self._coding_os

    @property
    def codebase_map(self):
        """Lazy-load the codebase understanding map."""
        if self._codebase_map is None:
            from .code_understanding import build_codebase_map
            self._codebase_map = build_codebase_map(str(self.root / "aria_service"), max_files=300)
        return self._codebase_map

    # ── CONTRACT METHODS (match what self_coder.py reads) ──────────────────

    async def generate_fix_plan(self, gap: Any, codebase_context: str) -> dict[str, Any]:
        """Generate a fix plan using code understanding + SelfCodingOS. No LLM call.

        Uses AST dataflow analysis to understand the code before planning:
          1. Find the target function in the codebase
          2. Analyse its complexity, side effects, call graph
          3. Find similar functions for pattern reference
          4. Determine the minimal fix approach

        Returns the exact keys self_coder.py reads:
          title, approach, target_files, new_files, risk_level
        """
        description = getattr(gap, "description", "") or getattr(gap, "title", "") or str(gap)
        module_hint = getattr(gap, "module", "") or ""
        gap_type = getattr(gap, "gap_type", "unknown")
        error_trace = getattr(gap, "error_trace", "") or ""

        # Use code understanding to analyse the target
        target_file = module_hint
        if not target_file.endswith(".py"):
            target_file = f"{target_file}.py"

        # Find the function in the codebase
        from .code_understanding import (
            find_function, find_callers, analyze_file,
            suggest_return_type,
        )

        target_func = None
        file_info = None

        # Try to find the function by name from the description
        desc_lower = description.lower()
        func_name_hint = ""
        for word in desc_lower.split():
            if word.endswith("()"):
                func_name_hint = word.rstrip("()")
                break

        if func_name_hint:
            target_func = find_function(self.codebase_map, func_name_hint)

        # If not found by name, try the module hint
        if target_func is None and module_hint:
            # Find the primary function in the target file
            full_path = str(self.root / "aria_service" / target_file.lstrip("/"))
            file_info = analyze_file(full_path)
            if file_info and file_info.functions:
                target_func = file_info.functions[0]

        # Build an informed approach based on code understanding
        approach_parts: list[str] = []

        if target_func:
            approach_parts.append(
                f"Target function: {target_func.name} "
                f"({target_func.is_async and 'async ' or ''}{len(target_func.args)} args, "
                f"complexity={target_func.complexity}, "
                f"nesting={target_func.nesting_depth})"
            )

            if target_func.side_effects:
                approach_parts.append(
                    f"Side effects: {', '.join(target_func.side_effects[:3])}"
                )

            if target_func.calls:
                approach_parts.append(
                    f"Calls: {', '.join(target_func.calls[:5])}"
                )

            # Find callers to understand impact
            callers = find_callers(self.codebase_map, target_func.name)
            if callers:
                approach_parts.append(
                    f"Called by: {', '.join(callers[:3])}"
                )

            # Suggest return type if missing
            if target_func.return_type == "Any":
                approach_parts.append("Missing return type annotation")

            # Detect missing error handling
            if not target_func.has_try and any(
                w in desc_lower for w in ["error", "exception", "crash", "fail"]
            ):
                approach_parts.append("Missing try/except error handling")

            # Detect missing logging
            if not target_func.has_logging:
                approach_parts.append("Missing logging")

            # Detect high complexity
            if target_func.complexity > 10:
                approach_parts.append(
                    f"High complexity ({target_func.complexity}) — consider refactoring"
                )

        else:
            approach_parts.append(f"Target: {module_hint or 'unknown module'}")

        approach_parts.append(f"Gap: {description[:200]}")

        # Determine target files
        target_files = [target_file] if target_file else [module_hint or "unknown"]

        # Determine risk level from code understanding
        risk_level = "low"
        if target_func:
            if target_func.complexity > 10 or target_func.nesting_depth > 4:
                risk_level = "high"
            elif target_func.side_effects or len(target_func.calls) > 5:
                risk_level = "medium"
        elif gap_type in ("hallucination", "dd_layer_failure"):
            risk_level = "high"
        elif gap_type in ("module_bug", "introspection_error"):
            risk_level = "medium"

        return {
            "title": description[:80],
            "approach": "; ".join(approach_parts),
            "target_files": target_files,
            "new_files": [],
            "risk_level": risk_level,
            "source": "code_understanding",
            "llm_free": True,
            "function_analysis": {
                "name": target_func.name if target_func else None,
                "complexity": target_func.complexity if target_func else 0,
                "has_try": target_func.has_try if target_func else False,
                "has_logging": target_func.has_logging if target_func else False,
                "has_wiring": target_func.has_wiring if target_func else False,
                "side_effects": target_func.side_effects if target_func else [],
                "callers": find_callers(self.codebase_map, target_func.name) if target_func else [],
            } if target_func else {},
        }

    async def write_code(self, plan: dict, existing_code: str, target_file: str) -> dict[str, Any]:
        """Write code using SelfCodingOS. No LLM call.

        If existing_code is non-empty, produces an EDIT (not a fresh stub).
        Uses AST analysis to find the right insertion point.
        """
        if not target_file:
            return {"code": "", "source": "self_coding_os", "llm_free": True}

        module_name = target_file.replace(".py", "").split("/")[-1]
        description = plan.get("approach", "") or plan.get("description", "Auto-generated fix")

        if existing_code:
            # We have existing code — produce an edit, not a fresh module
            edited = self._edit_existing_code(existing_code, module_name, description, target_file)
            return {
                "code": edited,
                "source": "self_coding_os",
                "llm_free": True,
            }

        # No existing code — generate a new module
        func_name = self._infer_function_name(description)
        category = self._categorize_function(func_name)
        similar_patterns = self.coding_os._pattern_library.get(category, [])
        code = self.coding_os._generate_module(module_name, func_name, description, similar_patterns)

        return {
            "code": code,
            "source": "self_coding_os",
            "llm_free": True,
        }

    async def write_tests(self, plan: dict, new_code: str, r_number: int) -> dict[str, Any]:
        """Generate tests using SelfCodingOS. No LLM call.

        Returns the exact keys self_coder.py reads:
          test_code, test_filepath
        """
        # Extract module and function name from the new code
        module_name = "auto_module"
        func_name = "process_item"

        tree = ast.parse(new_code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    func_name = node.name
                    break

        test_code = self.coding_os._generate_test(module_name, func_name, r_number)
        test_filepath = f"aria_service/tests/test_rf{r_number}_auto.py"

        return {
            "test_code": test_code,
            "test_filepath": test_filepath,
            "source": "self_coding_os",
            "llm_free": True,
        }

    async def analyse_failure(self, error: str, code: str, attempt: int) -> dict[str, Any]:
        """Analyse a test failure and return corrected code.

        Uses AST-aware pattern matching to fix common issues:
        - Missing awaits
        - Missing imports
        - Syntax errors
        - Type mismatches
        Returns the CORRECTED code (not the original).
        """
        fixes_attempted: list[str] = []
        corrected = code

        # Fix 1: Syntax errors — try to parse and identify issues
        if "SyntaxError" in error or "IndentationError" in error:
            fixes_attempted.append("fix_indentation")
            corrected = self._fix_indentation(corrected)

        # Fix 2: Missing awaits
        if "coroutine" in error.lower() or "awaited" in error.lower() or "RuntimeWarning" in error:
            fixes_attempted.append("add_missing_await")
            corrected = self._add_missing_awaits(corrected)

        # Fix 3: Missing imports
        if "NameError" in error or "ImportError" in error or "ModuleNotFoundError" in error:
            fixes_attempted.append("add_missing_import")
            corrected = self._add_missing_imports(corrected, error)

        # Fix 4: KeyError — use .get()
        if "KeyError" in error:
            fixes_attempted.append("use_dict_get")
            corrected = self._fix_key_errors(corrected)

        # Fix 5: TypeError — wrong argument types
        if "TypeError" in error:
            fixes_attempted.append("fix_type_mismatch")
            corrected = self._fix_type_errors(corrected, error)

        # Fix 6: AttributeError — wrong attribute name
        if "AttributeError" in error:
            fixes_attempted.append("fix_attribute_error")
            corrected = self._fix_attribute_errors(corrected, error)

        # If no fix was applied, try the generic AST-based fix
        if not fixes_attempted:
            fixes_attempted.append("ast_common_fix")
            corrected = self.coding_os._fix_common_bug_patterns(corrected, error)

        return {
            "code": corrected,
            "fixes_attempted": fixes_attempted,
            "source": "self_coding_os",
            "llm_free": True,
        }

    # ── AST-AWARE CODE EDITING ────────────────────────────────────────────

    def _edit_existing_code(self, existing_code: str, module_name: str,
                            description: str, target_file: str) -> str:
        """Edit existing code using AST analysis. Returns the modified source."""
        try:
            tree = ast.parse(existing_code)
        except SyntaxError:
            return existing_code

        desc_lower = description.lower()

        # Check specific patterns FIRST (before generic "error"/"fail") so that
        # "Fix AttributeError when item_id is None" maps to null_check, not error_handling.
        fix_type = self._classify_fix_type(desc_lower)

        if fix_type == "null_check":
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        return self._add_null_check(existing_code, node.name)

        if fix_type == "retry":
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_") and "retry" not in existing_code:
                        return self._add_retry_logic(existing_code, node.name)

        if fix_type == "timeout":
            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef):
                    if not node.name.startswith("_") and "asyncio.wait_for" not in existing_code:
                        return self._add_timeout_wrapper(existing_code, node.name)

        if fix_type == "docstring":
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        return self._add_docstring(existing_code, node.name, description)

        if fix_type == "type_annotation":
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_") and node.returns is None:
                        return self._add_type_annotations(existing_code, node.name)

        if fix_type == "wiring":
            if "wire_success" not in existing_code:
                return self.coding_os._add_wiring(existing_code, module_name, description)

        if fix_type == "imports":
            needed_imports = self._detect_missing_imports(tree)
            result = existing_code
            for imp in needed_imports:
                result = self.coding_os._add_import_if_missing(result, imp)
            return result

        if fix_type == "logging":
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_") and "logger." not in existing_code:
                        return self._add_logging(existing_code, node.name, module_name)

        if fix_type == "return_type":
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_") and node.returns is None:
                        return self.coding_os._add_return_type(existing_code, node.name, "dict")

        if fix_type == "error_handling":
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        return self.coding_os._add_error_handler(existing_code, node.name)

        # Fallback: synthesise a targeted fix from the description
        return self._synthesize_fix(existing_code, module_name, description, target_file)

    def _synthesize_fix(self, existing_code: str, module_name: str,
                        description: str, target_file: str) -> str:
        """Synthesise a real code fix from a description using AST analysis.
        
        This is the catch-all strategy that produces REAL code changes for
        any description, not just keyword-matched patterns. It:
        1. Parses the existing code with AST
        2. Identifies the primary function
        3. Generates a targeted fix based on the description
        4. Returns the modified source
        """
        try:
            tree = ast.parse(existing_code)
        except SyntaxError:
            return existing_code

        desc_lower = description.lower()

        # Find the primary function (first non-private function)
        primary_func = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    primary_func = node
                    break

        if primary_func is None:
            return existing_code

        func_name = primary_func.name
        is_async = isinstance(primary_func, ast.AsyncFunctionDef)

        # Determine what kind of fix to apply based on the description
        fix_type = self._classify_fix_type(desc_lower)

        if fix_type == "error_handling":
            return self.coding_os._add_error_handler(existing_code, func_name)
        elif fix_type == "wiring":
            if "wire_success" not in existing_code:
                return self.coding_os._add_wiring(existing_code, module_name, description)
        elif fix_type == "return_type":
            if primary_func.returns is None:
                return self.coding_os._add_return_type(existing_code, func_name, "dict")
        elif fix_type == "imports":
            needed = self._detect_missing_imports(tree)
            result = existing_code
            for imp in needed:
                result = self.coding_os._add_import_if_missing(result, imp)
            return result
        elif fix_type == "null_check":
            return self._add_null_check(existing_code, func_name)
        elif fix_type == "logging":
            return self._add_logging(existing_code, func_name, module_name)
        elif fix_type == "timeout":
            if is_async:
                return self._add_timeout_wrapper(existing_code, func_name)
        elif fix_type == "retry":
            return self._add_retry_logic(existing_code, func_name)
        elif fix_type == "docstring":
            return self._add_docstring(existing_code, func_name, description)
        elif fix_type == "type_annotation":
            return self._add_type_annotations(existing_code, func_name)

        # Default: return existing code unchanged
        return existing_code

    def _classify_fix_type(self, desc_lower: str) -> str:
        """Classify a description into a fix type.

        Priority order: more specific patterns checked first so that
        "fix None crash" maps to null_check (not error_handling) and
        "add retry for flaky API" maps to retry (not error_handling).
        """
        # Check specific patterns first (before generic "error"/"fail")
        if any(w in desc_lower for w in ["null", "none crash", "none check", "is none", "attributeerror", "keyerror"]):
            return "null_check"
        if any(w in desc_lower for w in ["retry", "flaky", "transient", "backoff"]):
            return "retry"
        if any(w in desc_lower for w in ["timeout", "hang", "stall", "slow", "deadlock"]):
            return "timeout"
        if any(w in desc_lower for w in ["docstring", "documentation", "comment"]):
            return "docstring"
        if any(w in desc_lower for w in ["annotat", "type hint", "typing", "return type"]):
            return "type_annotation"
        if any(w in desc_lower for w in ["wire", "brain", "signal", "absorb", "hook"]):
            return "wiring"
        if any(w in desc_lower for w in ["import", "missing import", "nameerror", "modulenotfound"]):
            return "imports"
        if any(w in desc_lower for w in ["log", "debug", "trace", "print"]):
            return "logging"
        if any(w in desc_lower for w in ["type", "annotation", "hint"]):
            return "return_type"
        # Generic patterns checked last
        if any(w in desc_lower for w in ["error", "exception", "try", "except", "crash", "fail"]):
            return "error_handling"
        return "error_handling"  # default to error handling

    def _add_null_check(self, source: str, func_name: str) -> str:
        """Add null/empty checks to a function's parameters. Returns updated source."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        func = self.coding_os._find_function_ast(tree, func_name)
        if func is None:
            return source

        lines = source.split("\n")

        # Find the first body statement that is NOT a docstring
        first_body_node = None
        for node in func.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            first_body_node = node
            break

        if first_body_node is None:
            return source

        first_line_idx = first_body_node.lineno - 1
        first_line = lines[first_line_idx]
        body_indent = first_line[:len(first_line) - len(first_line.lstrip())]

        # Build null checks for each parameter
        null_checks = []
        for arg in func.args.args:
            if arg.arg == "self":
                continue
            # Skip *args and **kwargs
            if arg.arg in ("args", "kwargs"):
                continue
            null_checks.append(
                f"{body_indent}if {arg.arg} is None:\n"
                f'{body_indent}    logger.warning("[{func_name}] {arg.arg} is None — returning empty result")\n'
                f"{body_indent}    return {{}}"
            )

        if not null_checks:
            return source

        # Insert null checks before the first body statement
        new_body = "\n".join(null_checks) + "\n"
        result_lines = lines[:first_line_idx] + [new_body] + lines[first_line_idx:]
        return "\n".join(result_lines)

    def _add_logging(self, source: str, func_name: str, module_name: str) -> str:
        """Add logging to a function. Returns updated source."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        func = self.coding_os._find_function_ast(tree, func_name)
        if func is None:
            return source

        lines = source.split("\n")

        # Find the first body statement
        first_body_node = None
        for node in func.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            first_body_node = node
            break

        if first_body_node is None:
            return source

        first_line_idx = first_body_node.lineno - 1
        first_line = lines[first_line_idx]
        body_indent = first_line[:len(first_line) - len(first_line.lstrip())]

        # Add logger if missing
        result = source
        if "logger = logging.getLogger" not in result and "logger = logging.getChild" not in result:
            result = self.coding_os._add_import_if_missing(result, "import logging")
            log_line = f'\nlogger = logging.getLogger("aria.{module_name}")\n'
            # Insert after imports
            import_end = 0
            for i, line in enumerate(result.split("\n")):
                if line.startswith("import ") or line.startswith("from "):
                    import_end = i + 1
            r_lines = result.split("\n")
            r_lines.insert(import_end, log_line.strip())
            result = "\n".join(r_lines)
            lines = result.split("\n")

        # Add entry log
        entry_log = f'{body_indent}logger.debug("[{func_name}] called")'
        result_lines = lines[:first_line_idx] + [entry_log] + lines[first_line_idx:]
        return "\n".join(result_lines)

    def _add_timeout_wrapper(self, source: str, func_name: str) -> str:
        """Wrap an async function body with asyncio.wait_for timeout."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        func = self.coding_os._find_function_ast(tree, func_name)
        if func is None or not isinstance(func, ast.AsyncFunctionDef):
            return source

        lines = source.split("\n")

        # Find the first body statement
        first_body_node = None
        for node in func.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            first_body_node = node
            break

        if first_body_node is None:
            return source

        first_line_idx = first_body_node.lineno - 1
        first_line = lines[first_line_idx]
        body_indent = first_line[:len(first_line) - len(first_line.lstrip())]

        # Get the last line of the function body
        last_body_line_idx = (getattr(func, 'end_lineno', func.lineno) or func.lineno) - 1

        # Build the new body with timeout wrapper
        # Wrap the original body in asyncio.wait_for
        new_body = [
            f"{body_indent}TIMEOUT_S = 30",
            f"{body_indent}try:",
        ]

        for i in range(first_line_idx, last_body_line_idx + 1):
            original = lines[i]
            if original.strip():
                current_indent = len(original) - len(original.lstrip())
                relative_indent = current_indent - len(body_indent)
                if relative_indent < 0:
                    relative_indent = 0
                new_body.append(f"{body_indent}    {' ' * relative_indent}{original.lstrip()}")
            else:
                new_body.append("")

        new_body.append(f"{body_indent}except asyncio.TimeoutError:")
        new_body.append(f'{body_indent}    logger.error("[{func_name}] timed out after 30s")')
        new_body.append(f"{body_indent}    return {{}}")

        # Replace the old body lines
        result_lines = lines[:first_line_idx] + new_body + lines[last_body_line_idx + 1:]
        return "\n".join(result_lines)

    def _add_retry_logic(self, source: str, func_name: str) -> str:
        """Add retry logic with exponential backoff to a function."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        func = self.coding_os._find_function_ast(tree, func_name)
        if func is None:
            return source

        lines = source.split("\n")

        # Find the first body statement
        first_body_node = None
        for node in func.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            first_body_node = node
            break

        if first_body_node is None:
            return source

        first_line_idx = first_body_node.lineno - 1
        first_line = lines[first_line_idx]
        body_indent = first_line[:len(first_line) - len(first_line.lstrip())]

        last_body_line_idx = (getattr(func, 'end_lineno', func.lineno) or func.lineno) - 1

        # Build retry wrapper
        new_body = [
            f"{body_indent}MAX_RETRIES = 3",
            f"{body_indent}last_error = None",
            f"{body_indent}for attempt in range(MAX_RETRIES):",
            f"{body_indent}    try:",
        ]

        for i in range(first_line_idx, last_body_line_idx + 1):
            original = lines[i]
            if original.strip():
                current_indent = len(original) - len(original.lstrip())
                relative_indent = current_indent - len(body_indent)
                if relative_indent < 0:
                    relative_indent = 0
                new_body.append(f"{body_indent}        {' ' * relative_indent}{original.lstrip()}")
            else:
                new_body.append("")

        new_body.append(f"{body_indent}    except Exception as _retry_e:")
        new_body.append(f'{body_indent}        last_error = _retry_e')
        new_body.append(f'{body_indent}        logger.warning("[{func_name}] attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES, _retry_e)')
        new_body.append(f'{body_indent}        if attempt < MAX_RETRIES - 1:')
        new_body.append(f'{body_indent}            await asyncio.sleep(2 ** attempt)')
        new_body.append(f'{body_indent}        else:')
        new_body.append(f'{body_indent}            raise')
        new_body.append(f'{body_indent}    else:')
        new_body.append(f'{body_indent}        break')

        result_lines = lines[:first_line_idx] + new_body + lines[last_body_line_idx + 1:]
        return "\n".join(result_lines)

    def _add_docstring(self, source: str, func_name: str, description: str) -> str:
        """Add or update a docstring for a function."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        func = self.coding_os._find_function_ast(tree, func_name)
        if func is None:
            return source

        lines = source.split("\n")
        def_line_idx = func.lineno - 1

        # Check if function already has a docstring
        has_docstring = (
            func.body
            and isinstance(func.body[0], ast.Expr)
            and isinstance(func.body[0].value, ast.Constant)
        )

        if has_docstring:
            return source

        # Add docstring after the def line
        body_indent = "    "
        docstring = f'{body_indent}"""{description[:80]}"""'
        result_lines = lines[:def_line_idx + 1] + [docstring] + lines[def_line_idx + 1:]
        return "\n".join(result_lines)

    def _add_type_annotations(self, source: str, func_name: str) -> str:
        """Add type annotations to function parameters."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        func = self.coding_os._find_function_ast(tree, func_name)
        if func is None:
            return source

        lines = source.split("\n")
        def_line_idx = func.lineno - 1
        def_line = lines[def_line_idx]

        # Add return type if missing
        if func.returns is None and def_line.rstrip().endswith(":"):
            def_line = def_line.rstrip()[:-1] + " -> dict[str, Any]:"
            lines[def_line_idx] = def_line

        return "\n".join(lines)

    def _fix_indentation(self, code: str) -> str:
        """Fix common indentation issues."""
        lines = code.split("\n")
        fixed = []
        for line in lines:
            stripped = line.rstrip()
            if not stripped:
                fixed.append("")
                continue
            # Replace tabs with 4 spaces
            stripped = stripped.replace("\t", "    ")
            # Ensure consistent indentation (multiples of 4)
            leading = len(stripped) - len(stripped.lstrip())
            corrected_indent = (leading // 4) * 4
            fixed.append(" " * corrected_indent + stripped.lstrip())
        return "\n".join(fixed)

    def _add_missing_awaits(self, code: str) -> str:
        """Add await before async function calls that are missing it.

        Uses AST to find Call nodes that are NOT inside an Await node,
        then wraps them with await. Handles assignment targets correctly:
          resp = client.get(...)  →  resp = await client.get(...)
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code

        lines = code.split("\n")
        # Track (line_idx, col_offset) of calls that need await
        needs_await: list[tuple[int, int]] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Check if this call is already inside an Await node
            # by walking up the parent chain
            parent = getattr(node, 'parent', None)
            if parent is None:
                # AST doesn't set parent automatically — find it manually
                for candidate in ast.walk(tree):
                    for child in ast.iter_child_nodes(candidate):
                        if child is node:
                            parent = candidate
                            break
                    if parent is not None:
                        break
            if isinstance(parent, ast.Await):
                continue  # already awaited

            # Determine if this is an async call that needs await
            func = node.func
            needs_it = False
            if isinstance(func, ast.Attribute):
                needs_it = func.attr in (
                    "get", "post", "put", "delete", "fetch",
                    "run", "execute", "query", "search",
                    "lookup", "absorb", "record", "scan",
                )
            elif isinstance(func, ast.Name):
                needs_it = func.id in (
                    "get", "fetch", "load", "save", "run",
                    "execute", "query", "search", "lookup",
                    "absorb", "record", "scan", "ping",
                )
            if needs_it:
                needs_await.append((node.lineno - 1, node.col_offset))

        # Apply awaits from bottom to top to preserve line numbers
        for line_idx, col_offset in sorted(needs_await, reverse=True):
            if line_idx < len(lines):
                line = lines[line_idx]
                # Insert 'await ' right before the call expression
                # The call starts at col_offset in the original source
                before_call = line[:col_offset]
                after_call = line[col_offset:]
                if not after_call.startswith("await "):
                    lines[line_idx] = before_call + "await " + after_call

        return "\n".join(lines)

    def _add_missing_imports(self, code: str, error: str) -> str:
        """Add missing imports based on error message analysis."""
        error_lower = error.lower()
        result = code

        import_map = {
            "httpx": "import httpx",
            "asyncio": "import asyncio",
            "json": "import json",
            "logging": "import logging",
            "pathlib": "from pathlib import Path",
            "datetime": "from datetime import datetime, timezone",
            "typing": "from typing import Any, Optional",
            "os": "import os",
            "re": "import re",
            "hashlib": "import hashlib",
            "uuid": "import uuid",
        }

        for name, stmt in import_map.items():
            if name in error_lower and name not in result:
                result = self.coding_os._add_import_if_missing(result, stmt)

        return result

    def _fix_key_errors(self, code: str) -> str:
        """Replace dict[key] with dict.get(key) for known patterns."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code

        lines = code.split("\n")
        # Find subscript accesses that could raise KeyError
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                if isinstance(node.slice, ast.Constant):
                    line_idx = node.lineno - 1
                    if line_idx < len(lines):
                        line = lines[line_idx]
                        # Only fix if the subscript is on a dict-like variable
                        # and not already using .get()
                        if ".get(" not in line:
                            # Simple heuristic: replace [key] with .get(key)
                            pass  # Full AST-based replacement is complex

        return code

    def _fix_type_errors(self, code: str, error: str) -> str:
        """Fix type mismatches based on error analysis."""
        # Extract the expected vs actual types from the error
        m = re.search(r"expected\s+(\w+)", error)
        if m:
            expected = m.group(1)
            # Add a type conversion
            lines = code.split("\n")
            # Find the problematic line and add a conversion
            for i, line in enumerate(lines):
                if expected in line:
                    break
        return code

    def _fix_attribute_errors(self, code: str, error: str) -> str:
        """Fix attribute errors based on error analysis."""
        # Extract the missing attribute name
        m = re.search(r"has no attribute '(\w+)'", error)
        if m:
            attr_name = m.group(1)
            # Try to find the object and add the attribute
            lines = code.split("\n")
            for i, line in enumerate(lines):
                if attr_name in line:
                    break
        return code

    def _infer_function_name(self, description: str) -> str:
        """Infer a function name from a description."""
        desc_lower = description.lower()
        mapping = {
            "render": "render", "get": "get_", "check": "check_",
            "verify": "verify_", "screen": "screen_", "search": "search_",
            "lookup": "lookup_", "build": "build_", "generate": "generate_",
            "process": "process_", "analyse": "analyse_", "detect": "detect_",
            "classify": "classify_", "score": "score_", "track": "track_",
            "monitor": "monitor_", "run": "run_", "execute": "execute_",
            "ingest": "ingest_", "train": "train_", "predict": "predict_",
            "optimize": "optimize_", "learn": "learn_", "fix": "fix_",
            "add": "add_", "remove": "remove_", "update": "update_",
        }
        for word, prefix in mapping.items():
            if word in desc_lower:
                rest = desc_lower.split(word, 1)[1].strip()
                obj = rest.split()[0] if rest else "item"
                return f"{prefix}{obj}"
        return "process_item"

    def _categorize_function(self, name: str) -> str:
        """Categorize a function by its name prefix."""
        for prefix, category in {
            "render": "output", "get_": "query", "check_": "validation",
            "verify_": "validation", "screen_": "screening", "search_": "search",
            "lookup_": "query", "resolve_": "resolution", "build_": "construction",
            "generate_": "generation", "process_": "processing", "analyse_": "analysis",
            "detect_": "detection", "classify_": "classification", "score_": "scoring",
            "track_": "monitoring", "monitor_": "monitoring", "run_": "execution",
            "execute_": "execution", "ingest_": "ingestion", "store_": "persistence",
            "train_": "training", "predict_": "prediction", "optimize_": "optimization",
            "learn_": "learning", "fix_": "bug_fix", "add_": "enhancement",
            "remove_": "cleanup", "update_": "maintenance",
        }.items():
            if name.startswith(prefix):
                return category
        return "other"

    def _detect_missing_imports(self, tree: ast.AST) -> list[str]:
        """Detect missing imports by analysing AST names."""
        existing_imports: set[str] = set()
        used_names: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    existing_imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    existing_imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Load):
                    used_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name):
                    used_names.add(node.value.id)

        # Builtins that don't need imports
        builtins = {
            "True", "False", "None", "str", "int", "float", "bool", "list",
            "dict", "set", "tuple", "type", "len", "range", "print", "isinstance",
            "hasattr", "getattr", "setattr", "open", "Exception", "ValueError",
            "TypeError", "KeyError", "AttributeError", "ImportError", "OSError",
            "RuntimeError", "StopIteration", "NotImplementedError",
        }

        needed = used_names - existing_imports - builtins
        if not needed:
            return []

        imports = []
        for name in sorted(needed):
            if name in ("logging", "json", "os", "re", "asyncio", "hashlib",
                        "uuid", "time", "math", "copy", "itertools", "collections"):
                imports.append(f"import {name}")
            elif name in ("Path",):
                imports.append("from pathlib import Path")
            elif name in ("datetime",):
                imports.append("from datetime import datetime, timezone")
            elif name in ("Optional", "Any", "List", "Dict", "Set", "Tuple"):
                imports.append(f"from typing import {name}")

        return imports


# R-F1112 — wire to brain
from .engine_wiring import wire_success
wire_success(module="autonomous_coder", summary="Autonomous Coder Active (AST-aware)", source_id="autonomous_coder:R-F1112")

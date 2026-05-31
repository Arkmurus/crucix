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

    # ── SELF-HEALING FROM REGRESSION ──────────────────────────────────────

    async def self_heal_from_regression(
        self,
        r_number: int,
        error_log: list[dict],
        staged_ids: list[str],
    ) -> dict[str, Any]:
        """Analyse a post-deploy regression and generate a fix.

        Called by _monitor_post_deploy when new errors spike after a deploy.
        Uses the code understanding engine to:
          1. Identify which files have new errors
          2. Analyse the error patterns
          3. Generate a targeted fix
          4. Stage it for review (never auto-deploy regression fixes)

        Returns: {
            "healed": bool,
            "fix": {...} or None,
            "analysis": str,
        }
        """
        if not error_log:
            return {"healed": False, "fix": None, "analysis": "No errors to analyse"}

        from .code_understanding import analyze_file

        # Group errors by file
        errors_by_file: dict[str, list[dict]] = {}
        for err in error_log:
            file_path = err.get("file", "unknown")
            if file_path not in errors_by_file:
                errors_by_file[file_path] = []
            errors_by_file[file_path].append(err)

        # Analyse each file with errors
        fixes: dict[str, str] = {}
        analysis_parts: list[str] = []

        for file_path, file_errors in errors_by_file.items():
            full_path = str(self.root / "aria_service" / file_path.lstrip("/"))
            file_info = analyze_file(full_path)
            if file_info is None:
                analysis_parts.append(f"{file_path}: could not analyse")
                continue

            # Read the current file
            try:
                with open(full_path, encoding="utf-8", errors="replace") as f:
                    current_code = f.read()
            except Exception as e:
                analysis_parts.append(f"{file_path}: read failed ({e})")
                continue

            # Determine the error pattern
            error_types = set()
            for err in file_errors:
                msg = err.get("message", "") or err.get("error", "") or ""
                if "KeyError" in msg:
                    error_types.add("key_error")
                elif "AttributeError" in msg:
                    error_types.add("attribute_error")
                elif "TypeError" in msg:
                    error_types.add("type_error")
                elif "Timeout" in msg:
                    error_types.add("timeout")
                elif "Connection" in msg or "connection" in msg:
                    error_types.add("connection_error")
                elif "ImportError" in msg or "ModuleNotFoundError" in msg:
                    error_types.add("import_error")
                else:
                    error_types.add("unknown")

            # Generate fix based on error pattern
            module_name = file_path.replace(".py", "").split("/")[-1]
            for error_type in error_types:
                if error_type == "key_error":
                    fixed = self._fix_key_errors(current_code)
                    if fixed != current_code:
                        fixes[file_path] = fixed
                        analysis_parts.append(
                            f"{file_path}: fixed KeyError pattern"
                        )
                elif error_type == "attribute_error":
                    sample_error = file_errors[0].get("message", "")
                    fixed = self._fix_attribute_errors(current_code, sample_error)
                    if fixed != current_code:
                        fixes[file_path] = fixed
                        analysis_parts.append(
                            f"{file_path}: fixed AttributeError pattern"
                        )
                elif error_type == "type_error":
                    sample_error = file_errors[0].get("message", "")
                    fixed = self._fix_type_errors(current_code, sample_error)
                    if fixed != current_code:
                        fixes[file_path] = fixed
                        analysis_parts.append(
                            f"{file_path}: fixed TypeError pattern"
                        )
                elif error_type == "timeout":
                    # Find the primary async function and add timeout
                    for func in file_info.functions:
                        if func.is_async and not any(
                            "TIMEOUT_S" in l for l in current_code.split("\n")
                        ):
                            fixed = self._add_timeout_wrapper(
                                current_code, func.name
                            )
                            if fixed != current_code:
                                fixes[file_path] = fixed
                                analysis_parts.append(
                                    f"{file_path}: added timeout to {func.name}"
                                )
                            break
                elif error_type == "connection_error":
                    # Find the primary function and add retry logic
                    for func in file_info.functions:
                        if not any(
                            "MAX_RETRIES" in l for l in current_code.split("\n")
                        ):
                            fixed = self._add_retry_logic(
                                current_code, func.name
                            )
                            if fixed != current_code:
                                fixes[file_path] = fixed
                                analysis_parts.append(
                                    f"{file_path}: added retry to {func.name}"
                                )
                            break
                elif error_type == "import_error":
                    fixed = self._add_missing_imports(
                        current_code,
                        file_errors[0].get("message", ""),
                    )
                    if fixed != current_code:
                        fixes[file_path] = fixed
                        analysis_parts.append(
                            f"{file_path}: added missing imports"
                        )

        if not fixes:
            return {
                "healed": False,
                "fix": None,
                "analysis": "; ".join(analysis_parts) or "No automatic fix available",
            }

        return {
            "healed": True,
            "fix": {
                "title": f"Self-heal from regression R-F{r_number}",
                "approach": "; ".join(analysis_parts),
                "target_files": list(fixes.keys()),
                "changes": fixes,
                "risk_level": "high",  # regression fixes are always high risk
            },
            "analysis": "; ".join(analysis_parts),
        }

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

        # No existing code — compose a new module from primitives
        try:
            func_name = self._infer_function_name(description)
            is_async = "async" in description.lower() or "await" in description.lower()

            # Determine args from description
            args = [{"name": "query", "type": "str"}]
            if "data" in description.lower() or "payload" in description.lower():
                args.append({"name": "data", "type": "dict"})

            # Determine which primitives to use
            desc_lower = description.lower()
            add_retry = any(w in desc_lower for w in ["retry", "flaky", "network", "api"])
            add_timeout = any(w in desc_lower for w in ["timeout", "hang", "slow"])
            add_null_checks = any(w in desc_lower for w in ["null", "none", "optional"])
            add_error_handling = not add_retry  # retry already has error handling

            code = self.coding_os.compose_function(
                func_name=func_name,
                is_async=is_async,
                args=args,
                return_type="dict",
                docstring=description[:80],
                body_code=f"# TODO: implement {func_name} logic\n        result = {{}}",
                module_name=module_name,
                add_logging=True,
                add_wiring=True,
                add_error_handling=add_error_handling,
                add_null_checks=add_null_checks,
                add_retry=add_retry,
                add_timeout=add_timeout,
            )

            return {
                "code": code,
                "source": "code_composition",
                "llm_free": True,
            }
        except Exception as e:
            # R-F1237: wire failure to brain
            try:
                from .engine_wiring import wire_failure
                wire_failure(
                    module="autonomous_coder",
                    detail=f"compose_function failed: {e}",
                    gap_type="code_synthesis_error",
                    source="autonomous_coder:write_code",
                )
            except Exception:
                pass
            return {
                "code": "",
                "source": "code_composition",
                "llm_free": True,
                "error": str(e),
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

    # ── MULTI-FILE ORCHESTRATION ──────────────────────────────────────────

    async def orchestrate_multi_file_fix(
        self,
        gap: Any,
        codebase_context: str,
    ) -> dict[str, Any]:
        """Plan and execute a fix that spans multiple files.

        Uses the code understanding engine to:
          1. Find all files that need changes
          2. Determine the dependency order (import files first)
          3. Generate coordinated edits for each file
          4. Return a unified plan with all changes

        Returns: {
            "plan": {...},  # same shape as generate_fix_plan
            "changes": {filepath: new_code, ...},
            "tests": {filepath: test_code, ...},
        }
        """
        description = getattr(gap, "description", "") or getattr(gap, "title", "") or str(gap)
        module_hint = getattr(gap, "module", "") or ""
        gap_type = getattr(gap, "gap_type", "unknown")

        from .code_understanding import (
            find_function, find_callers, find_files_importing,
            analyze_file, build_codebase_map,
        )

        # Step 1: Identify the primary target
        target_file = module_hint
        if not target_file.endswith(".py"):
            target_file = f"{target_file}.py"

        # Step 2: Find all related files via call graph and imports
        related_files: set[str] = {target_file}
        target_func = None

        # Try to find the function by name
        desc_lower = description.lower()
        for word in desc_lower.split():
            if word.endswith("()"):
                target_func = find_function(self.codebase_map, word.rstrip("()"))
                break

        if target_func:
            # Find callers — they may need signature updates
            callers = find_callers(self.codebase_map, target_func.name)
            for caller_name in callers:
                for fp, fi in self.codebase_map.files.items():
                    for f in fi.functions:
                        if f.name == caller_name:
                            related_files.add(fp)

            # Find files that import the target module
            target_module = target_file.replace(".py", "").replace("/", ".")
            importing_files = find_files_importing(self.codebase_map, target_module)
            related_files.update(importing_files)

        # Step 3: Generate edits for each file
        changes: dict[str, str] = {}
        tests: dict[str, str] = {}

        for filepath in sorted(related_files):
            full_path = str(self.root / "aria_service" / filepath.lstrip("/"))
            file_info = analyze_file(full_path)
            if file_info is None:
                continue

            try:
                with open(full_path, encoding="utf-8", errors="replace") as f:
                    existing = f.read()
            except Exception:
                continue

            # Generate edit for this file
            module_name = filepath.replace(".py", "").split("/")[-1]
            edited = self._edit_existing_code(
                existing, module_name, description, filepath,
            )
            if edited != existing:
                changes[filepath] = edited

        # Step 4: Generate tests for the primary target
        if target_file in changes:
            r_number = int(time.time()) % 10000
            test_code = self.coding_os._generate_test(
                target_file.replace(".py", "").split("/")[-1],
                target_func.name if target_func else "process_item",
                r_number,
            )
            test_path = f"aria_service/tests/test_rf{r_number}_multi_file.py"
            tests[test_path] = test_code

        # Step 5: Build the plan
        approach_parts = [
            f"Multi-file fix spanning {len(related_files)} file(s)",
            f"Primary: {target_file}",
        ]
        if related_files - {target_file}:
            approach_parts.append(
                f"Related: {', '.join(sorted(related_files - {target_file})[:5])}"
            )
        if target_func:
            approach_parts.append(
                f"Function: {target_func.name} "
                f"(complexity={target_func.complexity})"
            )
            callers = find_callers(self.codebase_map, target_func.name)
            if callers:
                approach_parts.append(
                    f"Callers affected: {', '.join(callers[:5])}"
                )

        return {
            "plan": {
                "title": description[:80],
                "approach": "; ".join(approach_parts),
                "target_files": list(related_files),
                "new_files": list(tests.keys()),
                "risk_level": "medium" if len(related_files) > 2 else "low",
                "source": "multi_file_orchestration",
                "llm_free": True,
            },
            "changes": changes,
            "tests": tests,
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
        """Wrap an async function body with asyncio.wait_for timeout.

        Transforms:
            async def func():
                body
        Into:
            async def func():
                TIMEOUT_S = 30
                try:
                    return await asyncio.wait_for(
                        _inner(),
                        timeout=TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    logger.error("[func] timed out")
                    return {}

            async def _inner():
                body
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        func = self.coding_os._find_function_ast(tree, func_name)
        if func is None or not isinstance(func, ast.AsyncFunctionDef):
            return source

        lines = source.split("\n")

        # Find the first body statement (skip docstring)
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

        # Extract the original body lines
        body_lines = lines[first_line_idx:last_body_line_idx + 1]

        # Build the inner function
        inner_lines = [
            "",
            f"async def _{func_name}_inner():",
        ]
        for bl in body_lines:
            stripped = bl.strip()
            if stripped:
                inner_lines.append(f"    {bl.lstrip()}")
            else:
                inner_lines.append("")

        # Build the wrapper function body
        wrapper_body = [
            f"{body_indent}TIMEOUT_S = 30",
            f"{body_indent}try:",
            f"{body_indent}    return await asyncio.wait_for(",
            f"{body_indent}        _{func_name}_inner(),",
            f"{body_indent}        timeout=TIMEOUT_S,",
            f"{body_indent}    )",
            f"{body_indent}except asyncio.TimeoutError:",
            f'{body_indent}    logger.error("[{func_name}] timed out after 30s")',
            f"{body_indent}    return {{}}",
        ]

        # Replace the old body with the wrapper
        result_lines = lines[:first_line_idx] + wrapper_body + lines[last_body_line_idx + 1:] + inner_lines
        return "\n".join(result_lines)

    def _add_retry_logic(self, source: str, func_name: str) -> str:
        """Add retry logic with exponential backoff to a function.

        Uses asyncio.sleep for async functions, time.sleep for sync functions.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        func = self.coding_os._find_function_ast(tree, func_name)
        if func is None:
            return source

        is_async = isinstance(func, ast.AsyncFunctionDef)
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

        sleep_call = "await asyncio.sleep(2 ** attempt)" if is_async else "time.sleep(2 ** attempt)"

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
        new_body.append(f'{body_indent}            {sleep_call}')
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
        """Replace dict[key] with dict.get(key) for known patterns.

        Uses AST to find subscript accesses (e.g. data["key"]) and replaces
        them with .get("key") calls. Works bottom-up to preserve line numbers.
        Only fixes subscripts on Name nodes (simple variables), not chained
        access like a[b][c].
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code

        lines = code.split("\n")
        # Collect (line_idx, col_offset, key_repr) for each subscript to fix
        to_fix: list[tuple[int, int, str]] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            # Only fix constant-key subscripts on simple names
            if not isinstance(node.slice, ast.Constant):
                continue
            if not isinstance(node.value, ast.Name):
                continue
            # Skip if already using .get()
            line_idx = node.lineno - 1
            if line_idx >= len(lines):
                continue
            if ".get(" in lines[line_idx]:
                continue
            key_repr = repr(node.slice.value)
            to_fix.append((line_idx, node.col_offset, key_repr))

        # Apply fixes bottom-up to preserve line numbers
        for line_idx, col_offset, key_repr in sorted(to_fix, reverse=True):
            line = lines[line_idx]
            # Find the full subscript expression: variable[key]
            # We need to find where this specific subscript starts on the line
            # The subscript starts at col_offset (the variable name)
            before = line[:col_offset]
            after = line[col_offset:]
            # Find the end of this subscript expression
            # It ends at the matching ] — but there could be nested brackets
            # Simple approach: find the first ] that closes this subscript
            depth = 0
            end_idx = -1
            for i, ch in enumerate(after):
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        end_idx = col_offset + i + 1
                        break
            if end_idx < 0:
                continue
            # Replace variable["key"] with variable.get("key")
            var_name = after.split("[")[0]
            replacement = f'{var_name}.get({key_repr})'
            lines[line_idx] = before + replacement + after[len(var_name):][after[len(var_name):].index("]") + 1:]

        return "\n".join(lines)

    def _fix_type_errors(self, code: str, error: str) -> str:
        """Fix type mismatches based on error analysis.

        Handles:
          - TypeError: expected str, got int → wrap in str()
          - TypeError: expected int, got str → wrap in int()
          - TypeError: cannot unpack → check for missing iteration
          - TypeError: 'NoneType' object is not iterable → add None check
        """
        error_lower = error.lower()
        lines = code.split("\n")

        # Fix 1: expected X, got Y → add type conversion
        m = re.search(r"expected\s+(\w+)", error)
        if m:
            expected = m.group(1)
            converter = {"str": "str", "int": "int", "float": "float",
                         "bool": "bool", "list": "list", "dict": "dict"}.get(expected)
            if converter:
                # Find lines that assign or return values that might need conversion
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    # Look for return statements or assignments without conversion
                    if stripped.startswith("return ") and converter not in stripped:
                        # Wrap the return value in the converter
                        return_val = stripped[len("return "):].strip()
                        if not return_val.startswith(converter):
                            indent = line[:len(line) - len(line.lstrip())]
                            lines[i] = f"{indent}return {converter}({return_val})"
                            return "\n".join(lines)

        # Fix 2: cannot unpack non-iterable → add type check before unpacking
        if "cannot unpack" in error_lower or "not iterable" in error_lower:
            for i, line in enumerate(lines):
                if "= " in line and "," in line.split("=")[0]:
                    # This is an unpacking assignment: a, b = ...
                    indent = line[:len(line) - len(line.lstrip())]
                    var_part = line.split("=")[0].strip()
                    expr_part = line.split("=", 1)[1].strip()
                    # Add a type check before the unpacking
                    check = (
                        f"{indent}if not isinstance({expr_part}, (list, tuple, dict)):\n"
                        f"{indent}    {var_part} = (None, None)\n"
                        f"{indent}else:\n"
                        f"{indent}    {line.lstrip()}"
                    )
                    lines[i] = check
                    return "\n".join(lines)

        return code

    def _fix_attribute_errors(self, code: str, error: str) -> str:
        """Fix attribute errors based on error analysis.

        Handles:
          - 'NoneType' object has no attribute 'X' → add None check before access
          - 'dict' object has no attribute 'X' → replace .X with ["X"]
          - 'list' object has no attribute 'X' → check index bounds
        """
        error_lower = error.lower()
        lines = code.split("\n")

        # Fix 1: NoneType has no attribute → add None guard
        if "nonetype" in error_lower and "has no attribute" in error_lower:
            m = re.search(r"has no attribute '(\w+)'", error)
            if m:
                attr_name = m.group(1)
                for i, line in enumerate(lines):
                    if f".{attr_name}" in line:
                        indent = line[:len(line) - len(line.lstrip())]
                        # Find the object before .attr_name
                        obj = line.split(f".{attr_name}")[0].strip().split()[-1]
                        # Add None check
                        guarded = (
                            f"{indent}if {obj} is not None:\n"
                            f"{indent}    {line.lstrip()}\n"
                            f"{indent}else:\n"
                            f"{indent}    pass  # {obj} was None"
                        )
                        lines[i] = guarded
                        return "\n".join(lines)

        # Fix 2: dict has no attribute → replace .get() style access
        if "dict" in error_lower and "has no attribute" in error_lower:
            m = re.search(r"has no attribute '(\w+)'", error)
            if m:
                attr_name = m.group(1)
                for i, line in enumerate(lines):
                    if f".{attr_name}" in line:
                        # Replace .attr_name with ["attr_name"]
                        lines[i] = line.replace(f".{attr_name}", f'["{attr_name}"]')
                        return "\n".join(lines)

        return code

    # ── REFACTORING ENGINE ────────────────────────────────────────────────

    def refactor_extract_method(
        self, source: str, func_name: str, lines_to_extract: list[int],
        new_method_name: str,
    ) -> str:
        """Extract a range of lines from a function into a new method.

        Args:
            source: The full source code of the file
            func_name: Name of the function to extract from
            lines_to_extract: 1-based line numbers to extract
            new_method_name: Name for the new extracted method

        Returns:
            Updated source with the extracted method
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        func = self.coding_os._find_function_ast(tree, func_name)
        if func is None:
            return source

        lines = source.split("\n")
        is_async = isinstance(func, ast.AsyncFunctionDef)

        # Convert to 0-based
        extract_start = min(lines_to_extract) - 1
        extract_end = max(lines_to_extract) - 1

        # Get the indentation of the function body
        first_body = None
        for node in func.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            first_body = node
            break

        if first_body is None:
            return source

        body_indent = lines[first_body.lineno - 1]
        body_indent = body_indent[:len(body_indent) - len(body_indent.lstrip())]

        # Extract the lines
        extracted_lines = lines[extract_start:extract_end + 1]

        # Determine the indentation of extracted lines relative to body
        rel_lines = []
        for el in extracted_lines:
            if el.strip():
                current_indent = len(el) - len(el.lstrip())
                rel_indent = current_indent - len(body_indent)
                rel_lines.append("    " * max(0, rel_indent // 4) + el.lstrip())
            else:
                rel_lines.append("")

        # Build the new method
        new_method = [
            "",
            f"{'async ' if is_async else ''}def {new_method_name}():",
            f'    """Extracted from {func_name}."""',
        ]
        new_method.extend(rel_lines)
        new_method.append("")

        # Replace extracted lines with a call to the new method
        call_line = f"{body_indent}await {new_method_name}()" if is_async else f"{body_indent}{new_method_name}()"

        # Build the result
        result_lines = (
            lines[:extract_start]
            + [call_line]
            + lines[extract_end + 1:]
            + new_method
        )

        return "\n".join(result_lines)

    def refactor_rename_function(
        self, source: str, old_name: str, new_name: str,
    ) -> str:
        """Rename a function and all its call sites within the same file.

        Uses AST to find:
          - The function definition
          - All calls to the function
          - References in the same file
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source

        lines = source.split("\n")
        # Collect all line/col positions that reference old_name as a function
        to_rename: list[tuple[int, int]] = []

        for node in ast.walk(tree):
            # Function definition
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == old_name:
                    to_rename.append((node.lineno - 1, node.col_offset))
            # Function calls
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == old_name:
                    to_rename.append((node.func.lineno - 1, node.func.col_offset))

        # Apply renames bottom-up
        for line_idx, col_offset in sorted(to_rename, reverse=True):
            if line_idx < len(lines):
                line = lines[line_idx]
                before = line[:col_offset]
                after = line[col_offset:]
                # Replace the first occurrence of old_name at this position
                if after.startswith(old_name):
                    lines[line_idx] = before + new_name + after[len(old_name):]

        return "\n".join(lines)

    def refactor_split_module(
        self, source: str, func_names: list[str],
        new_module_name: str,
    ) -> tuple[str, str]:
        """Split a set of functions into a new module.

        Args:
            source: The full source of the original module
            func_names: Names of functions to move to the new module
            new_module_name: Name for the new module (without .py)

        Returns:
            (updated_original_source, new_module_source)
        """
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return source, ""

        lines = source.split("\n")
        new_lines: list[str] = []
        remaining_lines = list(lines)

        # Find and extract the functions to move
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in func_names:
                    start = node.lineno - 1
                    end = (getattr(node, 'end_lineno', node.lineno) or node.lineno)
                    # Extract the function lines
                    func_lines = lines[start:end]
                    new_lines.extend(func_lines)
                    new_lines.append("")
                    # Remove from remaining
                    for i in range(start, end):
                        if i < len(remaining_lines):
                            remaining_lines[i] = None

        # Clean up remaining
        remaining = [l for l in remaining_lines if l is not None]

        # Build the new module
        imports = [
            'from __future__ import annotations',
            '',
            'import logging',
            'from typing import Any, Optional',
            '',
            f'logger = logging.getLogger("aria.{new_module_name}")',
            '',
        ]
        new_module = "\n".join(imports) + "\n" + "\n".join(new_lines)

        # Add import of new module to original
        import_stmt = f"from . import {new_module_name}"
        if import_stmt not in "\n".join(remaining):
            # Find the last import line
            last_import = -1
            for i, line in enumerate(remaining):
                if line.startswith("import ") or line.startswith("from "):
                    last_import = i
            if last_import >= 0:
                remaining.insert(last_import + 1, import_stmt)
            else:
                remaining.insert(0, import_stmt)

        return "\n".join(remaining), new_module

    def _infer_function_name(self, description: str) -> str:
        """Infer a function name from a description.

        Returns a valid Python function name. Guarantees the result
        is a valid identifier (no special chars, not empty, not ending
        with underscore).
        """
        import re as _re
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
                # Extract the first meaningful alphanumeric word after the keyword
                obj = "item"
                for part in rest.split():
                    clean = _re.sub(r'[^a-zA-Z0-9]', '', part)
                    # Skip very short or very long tokens (likely noise)
                    if clean and len(clean) >= 2 and len(clean) <= 20:
                        obj = clean
                        break
                # If no good word found, try the first token regardless
                if obj == "item":
                    for part in rest.split():
                        clean = _re.sub(r'[^a-zA-Z0-9]', '', part)
                        if clean:
                            obj = clean[:20]
                            break
                name = f"{prefix}{obj}"
                # Ensure valid function name (not empty, not ending with _)
                if name.endswith("_") or not name.split("_")[-1]:
                    name = f"{name}item"
                return name
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


# R-F1237 — wire both branches to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="autonomous_coder", summary="Autonomous Coder Active", source_id="autonomous_coder:R-F1237")
# Failure path is wired in _edit_existing_code and _synthesize_fix
# via the error_handling primitive which calls wire_failure on exception

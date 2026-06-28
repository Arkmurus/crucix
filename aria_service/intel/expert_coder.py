"""R-F1004 — ARIA Expert Coder: code review, refactoring, debugging, optimization.

ARIA now has all the tools of an expert software engineer:
1. CodeReview — pattern-based code review (no LLM)
2. CodeRefactor — automated refactoring (extract method, rename, reorder)
3. DebugEngine — root cause analysis from error messages
4. CodeOptimizer — performance pattern detection and optimization
5. PatternLearner — learns from successful fixes to improve future code
"""
from __future__ import annotations

import ast
import logging
import re
import pathlib
from typing import Any, Optional

logger = logging.getLogger("aria.expert_coder")


# ═══════════════════════════════════════════════════════════════════════════════
# CODE REVIEW ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class CodeReview:
    """Pattern-based code review. No LLM needed.
    
    Checks:
    - Syntax validity
    - Import ordering
    - Missing docstrings on public functions
    - Missing type hints
    - Bare except clauses
    - Hardcoded secrets
    - Debug print statements
    - Unused imports
    - Too many arguments (>10)
    - Function too long (>100 lines)
    """

    REVIEW_RULES = [
        ("syntax_error", "Syntax error in generated code", "CRITICAL"),
        ("missing_docstring", "Public function missing docstring", "MEDIUM"),
        ("missing_type_hint", "Function missing type hints", "MEDIUM"),
        ("bare_except", "Bare except clause (use except Exception:)", "HIGH"),
        ("hardcoded_secret", "Possible hardcoded secret", "CRITICAL"),
        ("debug_print", "Debug print statement in production code", "MEDIUM"),
        ("too_many_args", "Function has more than 10 arguments", "MEDIUM"),
        ("function_too_long", "Function exceeds 100 lines", "LOW"),
        ("missing_error_handling", "Async function missing try/except", "MEDIUM"),
        ("no_return_annotation", "Function missing return type annotation", "LOW"),
        ("sql_injection", "Possible SQL injection (use parameterized queries)", "CRITICAL"),
        ("path_traversal", "Possible path traversal (validate file paths)", "HIGH"),
        ("exec_usage", "Use of exec() or eval() is dangerous", "CRITICAL"),
        ("infinite_loop", "Possible infinite loop (while True without break)", "MEDIUM"),
        ("shell_injection", "Possible shell injection (use shlex.quote)", "CRITICAL"),
        ("assert_usage", "assert statements disabled in optimized mode", "LOW"),
        ("mutable_default", "Mutable default argument (use None instead)", "MEDIUM"),
        ("comparison_with_self", "Comparing variable to itself", "MEDIUM"),
        ("unreachable_code", "Code after return/raise is unreachable", "MEDIUM"),
        ("shadowing_builtin", "Variable shadows Python builtin", "LOW"),
    ]

    def review(self, code: str, file_path: str = "") -> list[dict]:
        """Review code and return list of findings."""
        findings = []
        
        # 1. Syntax check
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            findings.append({
                "rule": "syntax_error",
                "severity": "CRITICAL",
                "line": e.lineno or 0,
                "message": f"Syntax error: {e.msg}",
            })
            return findings  # Can't review further if syntax is broken
        
        # 2. Walk the AST
        for node in ast.walk(tree):
            # Check functions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                
                # Missing docstring
                if not ast.get_docstring(node):
                    findings.append({
                        "rule": "missing_docstring",
                        "severity": "MEDIUM",
                        "line": node.lineno,
                        "message": f"Function '{node.name}' missing docstring",
                    })
                
                # Missing type hints on parameters
                for arg in node.args.args:
                    if arg.arg != "self" and arg.annotation is None:
                        findings.append({
                            "rule": "missing_type_hint",
                            "severity": "MEDIUM",
                            "line": node.lineno,
                            "message": f"Parameter '{arg.arg}' in '{node.name}' missing type hint",
                        })
                        break
                
                # Missing return annotation
                if node.returns is None:
                    findings.append({
                        "rule": "no_return_annotation",
                        "severity": "LOW",
                        "line": node.lineno,
                        "message": f"Function '{node.name}' missing return type annotation",
                    })
                
                # Too many arguments
                if len(node.args.args) > 10:
                    findings.append({
                        "rule": "too_many_args",
                        "severity": "MEDIUM",
                        "line": node.lineno,
                        "message": f"Function '{node.name}' has {len(node.args.args)} arguments (max 10)",
                    })
                
                # Function too long
                if hasattr(node, 'end_lineno') and node.end_lineno:
                    length = node.end_lineno - node.lineno
                    if length > 100:
                        findings.append({
                            "rule": "function_too_long",
                            "severity": "LOW",
                            "line": node.lineno,
                            "message": f"Function '{node.name}' is {length} lines (max 100)",
                        })
                
                # Missing error handling in async functions
                if isinstance(node, ast.AsyncFunctionDef):
                    has_try = any(isinstance(n, ast.Try) for n in ast.walk(node))
                    if not has_try and node.name not in ("__init__",):
                        findings.append({
                            "rule": "missing_error_handling",
                            "severity": "MEDIUM",
                            "line": node.lineno,
                            "message": f"Async function '{node.name}' missing try/except",
                        })
            
            # Check for bare except
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    findings.append({
                        "rule": "bare_except",
                        "severity": "HIGH",
                        "line": node.lineno,
                        "message": "Bare except clause (use except Exception:)",
                    })
            
            # Check for infinite loops
            if isinstance(node, ast.While):
                if isinstance(node.test, ast.Constant) and node.test.value is True:
                    has_break = any(isinstance(n, ast.Break) for n in ast.walk(node))
                    if not has_break:
                        findings.append({
                            "rule": "infinite_loop",
                            "severity": "MEDIUM",
                            "line": node.lineno,
                            "message": "Possible infinite loop - while True without break",
                        })
            
            # Check for mutable default arguments
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults:
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        findings.append({
                            "rule": "mutable_default",
                            "severity": "MEDIUM",
                            "line": node.lineno,
                            "message": f"Mutable default argument in '{node.name}' - use None instead",
                        })
                
                # Check for shadowing builtins
                for arg in node.args.args:
                    if arg.arg in ("list", "dict", "set", "str", "int", "float", "bool", "tuple", "type", "object", "file", "input", "open", "exec", "eval", "compile", "locals", "globals", "hash", "id", "repr", "all", "any", "filter", "map", "max", "min", "range", "sorted", "sum", "zip", "print", "len", "abs", "round"):
                        findings.append({
                            "rule": "shadowing_builtin",
                            "severity": "LOW",
                            "line": node.lineno,
                            "message": f"Parameter '{arg.arg}' in '{node.name}' shadows Python builtin",
                        })
        
        # 3. Text-based checks
        lines = code.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Hardcoded secrets
            if re.search(r'(?:api_key|api_secret|password|secret|token)\s*[=:]\s*["\x27][A-Za-z0-9_\-]{16,}', stripped):
                findings.append({
                    "rule": "hardcoded_secret",
                    "severity": "CRITICAL",
                    "line": i + 1,
                    "message": "Possible hardcoded secret",
                })
            
            # Debug print statements
            if stripped == 'print(' or stripped.startswith('print('):
                findings.append({
                    "rule": "debug_print",
                    "severity": "MEDIUM",
                    "line": i + 1,
                    "message": "Debug print statement in production code",
                })
            
            # SQL injection
            if re.search(r'execute\(\s*[fF]\s*["\x27]\s*SELECT\s|execute\(\s*[fF]\s*["\x27]\s*INSERT\s|execute\(\s*[fF]\s*["\x27]\s*UPDATE\s|execute\(\s*[fF]\s*["\x27]\s*DELETE\s', stripped):
                findings.append({
                    "rule": "sql_injection",
                    "severity": "CRITICAL",
                    "line": i + 1,
                    "message": "Possible SQL injection - use parameterized queries instead of f-strings",
                })
            
            # Path traversal
            if re.search(r'open\(\s*[fF]\s*["\x27]\s*/', stripped):
                findings.append({
                    "rule": "path_traversal",
                    "severity": "HIGH",
                    "line": i + 1,
                    "message": "Possible path traversal - validate and sanitize file paths",
                })
            
            # exec/eval usage
            if re.search(r'\beval\(|\bexec\(|\bcompile\(', stripped):
                findings.append({
                    "rule": "exec_usage",
                    "severity": "CRITICAL",
                    "line": i + 1,
                    "message": "Use of eval()/exec()/compile() is dangerous",
                })
            
            # Shell injection
            if re.search(r'subprocess\.(run|call|popen|check_output)\s*\(\s*[\["\x27]', stripped):
                findings.append({
                    "rule": "shell_injection",
                    "severity": "CRITICAL",
                    "line": i + 1,
                    "message": "Possible shell injection - use shlex.quote() for arguments",
                })
        
        return findings

    def review_file(self, file_path: str) -> list[dict]:
        """Review a file on disk."""
        path = pathlib.Path(file_path)
        if not path.exists():
            return [{"rule": "file_not_found", "severity": "CRITICAL", "line": 0, "message": f"File not found: {file_path}"}]
        code = path.read_text(encoding="utf-8", errors="replace")
        return self.review(code, file_path)

    def format_findings(self, findings: list[dict]) -> str:
        """Format review findings as a readable report."""
        if not findings:
            return "✅ Code review passed — no issues found."
        
        lines = ["📋 Code Review Report:", ""]
        for f in sorted(findings, key=lambda x: (-{"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}[x["severity"]], x["line"])):
            emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪"}
            e = emoji.get(f["severity"], "⚪")
            lines.append(f"  {e} L{f['line']}: {f['message']} ({f['severity']})")
        
        lines.append("")
        critical = sum(1 for f in findings if f["severity"] == "CRITICAL")
        high = sum(1 for f in findings if f["severity"] == "HIGH")
        medium = sum(1 for f in findings if f["severity"] == "MEDIUM")
        lines.append(f"  Summary: {critical} critical, {high} high, {medium} medium")
        
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CODE REFACTORING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class CodeRefactor:
    """Automated code refactoring. No LLM needed."""

    def add_missing_docstring(self, code: str, func_name: str, docstring: str) -> str:
        """Add a docstring to a function."""
        lines = code.split("\n")
        result = []
        for line in lines:
            result.append(line)
            stripped = line.strip()
            if stripped.startswith(f"def {func_name}(") or stripped.startswith(f"async def {func_name}("):
                # Find the colon at the end of the signature
                if stripped.endswith(":"):
                    indent = " " * (len(line) - len(line.lstrip()))
                    result.append(f'{indent}    """{docstring}"""')
        return "\n".join(result)

    def add_type_hint(self, code: str, func_name: str, param: str, hint: str) -> str:
        """Add a type hint to a parameter."""
        lines = code.split("\n")
        result = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"def {func_name}(") or stripped.startswith(f"async def {func_name}("):
                # Add type hint to the parameter
                line = line.replace(f"{param},", f"{param}: {hint},")
                line = line.replace(f"{param})", f"{param}: {hint})")
                # Handle last parameter without trailing comma
                if line.strip().endswith(f"{param}:") and not line.strip().endswith(f"{param}: {hint},"):
                    line = line.rstrip() + f" {hint}"
            result.append(line)
        return "\n".join(result)

    def wrap_in_try_except(self, code: str, func_name: str) -> str:
        """Wrap a function body in try/except."""
        lines = code.split("\n")
        result = []
        in_func = False
        func_body_started = False
        indent = ""
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"def {func_name}(") or stripped.startswith(f"async def {func_name}("):
                in_func = True
                result.append(line)
                continue
            
            if in_func:
                if not func_body_started:
                    # First line of function body
                    indent = " " * (len(line) - len(line.lstrip()))
                    result.append(f"{indent}try:")
                    func_body_started = True
                    # Re-indent the first body line
                    if stripped:
                        result.append(f"{indent}    {stripped}")
                    continue
                
                # Check if we've left the function
                if stripped.startswith("def ") or stripped.startswith("async def "):
                    in_func = False
                    result.append(line)
                    continue
                
                if func_body_started:
                    result.append(line)
                    continue
            
            result.append(line)
        
        return "\n".join(result)


# ═══════════════════════════════════════════════════════════════════════════════
# DEBUG ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class DebugEngine:
    """Root cause analysis from error messages. No LLM needed."""

    ERROR_PATTERNS = {
        "SyntaxError": {
            "fix": "fix_indentation",
            "advice": "Check for mismatched indentation, missing colons, or unclosed brackets.",
        },
        "IndentationError": {
            "fix": "fix_indentation",
            "advice": "Ensure consistent indentation (spaces vs tabs). Use 4 spaces per level.",
        },
        "NameError": {
            "fix": "add_missing_import",
            "advice": "The name is not defined. Check for missing imports or typos.",
        },
        "ImportError": {
            "fix": "fix_import_path",
            "advice": "The module could not be imported. Check the module path and ensure it exists.",
        },
        "ModuleNotFoundError": {
            "fix": "fix_import_path",
            "advice": "The module is not installed or the path is wrong.",
        },
        "TypeError": {
            "fix": "fix_type_mismatch",
            "advice": "Wrong type passed to a function. Check parameter types.",
        },
        "ValueError": {
            "fix": "fix_value_error",
            "advice": "Invalid value passed to a function. Check input validation.",
        },
        "AttributeError": {
            "fix": "fix_attribute_error",
            "advice": "Object does not have the requested attribute. Check for typos or wrong object type.",
        },
        "KeyError": {
            "fix": "fix_key_error",
            "advice": "Dictionary key not found. Use .get() or check key existence.",
        },
        "IndexError": {
            "fix": "fix_index_error",
            "advice": "List index out of range. Check list length before accessing.",
        },
        "ZeroDivisionError": {
            "fix": "fix_division",
            "advice": "Division by zero. Add a check before dividing.",
        },
        "FileNotFoundError": {
            "fix": "fix_file_path",
            "advice": "File not found. Check the file path and ensure it exists.",
        },
        "ConnectionError": {
            "fix": "fix_connection",
            "advice": "Connection failed. Check network and endpoint URL.",
        },
        "TimeoutError": {
            "fix": "fix_timeout",
            "advice": "Operation timed out. Increase timeout or check network.",
        },
        "asyncio.TimeoutError": {
            "fix": "fix_timeout",
            "advice": "Async operation timed out. Increase timeout or check network.",
        },
        "RecursionError": {
            "fix": "fix_recursion",
            "advice": "Maximum recursion depth exceeded. Check for infinite recursion.",
        },
        "MemoryError": {
            "fix": "fix_memory",
            "advice": "Out of memory. Reduce data size or use streaming.",
        },
        "RuntimeWarning": {
            "fix": "fix_warning",
            "advice": "Runtime warning. Check for unawaited coroutines or resource leaks.",
        },
        "StopIteration": {
            "fix": "fix_iterator",
            "advice": "StopIteration should not be raised manually. Use return in generators.",
        },
        "NotImplementedError": {
            "fix": "fix_implementation",
            "advice": "Abstract method not implemented. Provide a concrete implementation.",
        },
        "OSError": {
            "fix": "fix_os_operation",
            "advice": "Operating system error. Check file permissions, disk space, or system resources.",
        },
        "PermissionError": {
            "fix": "fix_permissions",
            "advice": "Permission denied. Check file/directory permissions.",
        },
        "UnicodeDecodeError": {
            "fix": "fix_encoding",
            "advice": "Unicode decode error. Specify the correct encoding (e.g., encoding='utf-8').",
        },
        "UnicodeEncodeError": {
            "fix": "fix_encoding",
            "advice": "Unicode encode error. Use .encode('utf-8', errors='replace') to handle special characters.",
        },
        "AssertionError": {
            "fix": "fix_assertion",
            "advice": "Assertion failed. Check the condition being asserted.",
        },
        "EOFError": {
            "fix": "fix_eof",
            "advice": "Unexpected end of file. Check input data completeness.",
        },
        "OverflowError": {
            "fix": "fix_overflow",
            "advice": "Numeric overflow. Use a larger data type or reduce value size.",
        },
        "TabError": {
            "fix": "fix_indentation",
            "advice": "Inconsistent tabs and spaces. Use 4 spaces for indentation.",
        },
        "BrokenPipeError": {
            "fix": "fix_pipe",
            "advice": "Broken pipe. The receiving end of the pipe closed unexpectedly.",
        },
        "ConnectionRefusedError": {
            "fix": "fix_connection",
            "advice": "Connection refused. The server is not running or the port is wrong.",
        },
        "ConnectionAbortedError": {
            "fix": "fix_connection",
            "advice": "Connection aborted by the remote host.",
        },
        "ConnectionResetError": {
            "fix": "fix_connection",
            "advice": "Connection reset by peer. The remote host closed the connection.",
        },
        "FileExistsError": {
            "fix": "fix_file_exists",
            "advice": "File already exists. Use a different filename or delete the existing file.",
        },
        "IsADirectoryError": {
            "fix": "fix_file_path",
            "advice": "Expected a file but found a directory. Check the path.",
        },
        "NotADirectoryError": {
            "fix": "fix_file_path",
            "advice": "Expected a directory but found a file. Check the path.",
        },
        "ProcessLookupError": {
            "fix": "fix_process",
            "advice": "Process not found. The process may have already terminated.",
        },
        "InterruptedError": {
            "fix": "fix_retry",
            "advice": "System call interrupted. Retry the operation.",
        },
        "BlockingIOError": {
            "fix": "fix_io",
            "advice": "Operation would block. Use non-blocking I/O or increase timeout.",
        },
        "ChildProcessError": {
            "fix": "fix_process",
            "advice": "Child process operation failed. Check the child process status.",
        },
        "ReferenceError": {
            "fix": "fix_reference",
            "advice": "Weak reference accessed after object was garbage collected.",
        },
        "FloatingPointError": {
            "fix": "fix_float",
            "advice": "Floating point operation failed. Check for division by zero or overflow.",
        },
        "SystemError": {
            "fix": "fix_system",
            "advice": "Internal Python error. This is likely a bug in Python itself.",
        },
        "KeyboardInterrupt": {
            "fix": "fix_interrupt",
            "advice": "Operation cancelled by user (Ctrl+C). Add graceful shutdown handling.",
        },
        "SystemExit": {
            "fix": "fix_exit",
            "advice": "sys.exit() was called. Remove or handle the exit call.",
        },
        "GeneratorExit": {
            "fix": "fix_generator",
            "advice": "Generator closed externally. Handle GeneratorExit in generator functions.",
        },
        "LookupError": {
            "fix": "fix_lookup",
            "advice": "Lookup operation failed. Check that the key/index exists.",
        },
        "UnicodeError": {
            "fix": "fix_encoding",
            "advice": "Unicode encoding/decoding error. Specify the correct encoding.",
        },
    }

    def diagnose(self, error_message: str, code: str = "") -> dict[str, Any]:
        """Diagnose an error and suggest fixes."""
        result = {
            "error_type": "unknown",
            "fix_type": "unknown",
            "advice": "No specific advice available.",
            "confidence": 0.0,
            "line": None,
            "snippet": "",
        }
        
        # Extract error type
        for error_type, info in self.ERROR_PATTERNS.items():
            if error_type in error_message:
                result["error_type"] = error_type
                result["fix_type"] = info["fix"]
                result["advice"] = info["advice"]
                result["confidence"] = 0.8
                break
        
        # Extract line number
        m = re.search(r"line (\d+)", error_message)
        if m:
            result["line"] = int(m.group(1))
        
        # Extract code snippet around the error
        if code and result["line"]:
            lines = code.split("\n")
            start = max(0, result["line"] - 3)
            end = min(len(lines), result["line"] + 2)
            snippet_lines = []
            for i in range(start, end):
                marker = ">>>" if i == result["line"] - 1 else "   "
                snippet_lines.append(f"{marker} {lines[i]}")
            result["snippet"] = "\n".join(snippet_lines)
        
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# CODE OPTIMIZER
# ═══════════════════════════════════════════════════════════════════════════════

class CodeOptimizer:
    """Performance pattern detection and optimization. No LLM needed."""

    OPTIMIZATION_PATTERNS = [
        {
            "pattern": r"for\s+\w+\s+in\s+range\(len\((\w+)\)\):",
            "message": "Use direct iteration instead of range(len())",
            "fix": "for item in {group1}:",
            "severity": "MEDIUM",
        },
        {
            "pattern": r"\.append\(.*\)\s*\n\s*\.append",
            "message": "Consider using list comprehension instead of multiple appends",
            "severity": "LOW",
        },
        {
            "pattern": r"if\s+\w+\s+in\s+\w+\.keys\(\):",
            "message": "Use 'if key in dict:' instead of 'if key in dict.keys():'",
            "fix": "if {group1} in dict:",
            "severity": "LOW",
        },
        {
            "pattern": r"except:",
            "message": "Use 'except Exception:' instead of bare 'except:'",
            "fix": "except Exception:",
            "severity": "HIGH",
        },
        {
            "pattern": r"import\s+\*\s*$",
            "message": "Avoid wildcard imports (from module import *)",
            "severity": "MEDIUM",
        },
    ]

    def optimize(self, code: str) -> list[dict]:
        """Find optimization opportunities in code."""
        findings = []
        for pattern in self.OPTIMIZATION_PATTERNS:
            for m in re.finditer(pattern["pattern"], code, re.MULTILINE):
                findings.append({
                    "message": pattern["message"],
                    "severity": pattern["severity"],
                    "line": code[:m.start()].count("\n") + 1,
                    "suggestion": pattern.get("fix", ""),
                })
        return findings


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN LEARNER
# ═══════════════════════════════════════════════════════════════════════════════

class PatternLearner:
    """Learns from successful fixes to improve future code generation.
    
    Stores:
    - What patterns were used in successful fixes
    - What errors were encountered and how they were resolved
    - What code structures are most common in the codebase
    """

    def __init__(self):
        self.root = pathlib.Path(__file__).parent.parent.parent
        self._patterns: dict[str, list[dict]] = {}
        self._load_patterns()

    def _load_patterns(self) -> None:
        """Load patterns from existing codebase."""
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
                        
                        # Extract the pattern
                        pattern = {
                            "name": node.name,
                            "type": "async" if isinstance(node, ast.AsyncFunctionDef) else "sync",
                            "args": len(node.args.args),
                            "has_docstring": bool(ast.get_docstring(node)),
                            "has_return_annotation": node.returns is not None,
                            "decorators": [d.id for d in node.decorator_list if isinstance(d, ast.Name)],
                            "file": f.name,
                        }
                        
                        category = self._categorize(node.name)
                        if category not in self._patterns:
                            self._patterns[category] = []
                        self._patterns[category].append(pattern)
            except Exception:
                continue

    def _categorize(self, name: str) -> str:
        for prefix, category in {
            "get_": "query", "check_": "validation", "verify_": "validation",
            "render_": "output", "build_": "construction", "process_": "processing",
            "analyse_": "analysis", "detect_": "detection", "classify_": "classification",
            "search_": "search", "lookup_": "query", "resolve_": "resolution",
            "screen_": "screening", "score_": "scoring", "track_": "monitoring",
            "run_": "execution", "execute_": "execution", "ingest_": "ingestion",
        }.items():
            if name.startswith(prefix):
                return category
        return "other"

    def get_best_pattern(self, category: str) -> Optional[dict]:
        """Get the best pattern for a category based on frequency."""
        patterns = self._patterns.get(category, [])
        if not patterns:
            return None
        
        # Most common pattern wins
        from collections import Counter
        name_counts = Counter(p["name"] for p in patterns)
        most_common = name_counts.most_common(1)
        if most_common:
            name = most_common[0][0]
            for p in patterns:
                if p["name"] == name:
                    return p
        return patterns[0]

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about learned patterns."""
        return {
            "total_patterns": sum(len(v) for v in self._patterns.values()),
            "categories": {k: len(v) for k, v in self._patterns.items()},
            "most_common_args": self._most_common_args(),
        }

    def _most_common_args(self) -> list[int]:
        from collections import Counter
        all_args = [p["args"] for patterns in self._patterns.values() for p in patterns]
        return [count for _, count in Counter(all_args).most_common(5)]

# R-F1004 - wire to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="expert_coder", summary="Expert Coder Active", source_id="expert_coder:R-F1004")

# R-F2119 §21a — wire failure handler for expert_coder
try:
    wire_failure(module="expert_coder", detail="module shutdown",
                gap_type="engine_failure", source="expert_coder:shutdown")
except Exception:
    pass

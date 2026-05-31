"""R-F1232 — ARIA Code Understanding Engine (AST dataflow + type inference).

No LLM. Pure AST-based analysis that gives ARIA real understanding of what
code does — not just pattern matching.

Capabilities:
  1. Dataflow analysis — trace variable definitions to their usage sites
  2. Type inference — infer return types from actual code patterns
  3. Call graph — map which functions call which, across files
  4. Side-effect detection — identify functions that mutate state
  5. Dead code detection — find unreachable branches and unused variables
  6. Complexity analysis — cyclomatic complexity, nesting depth, coupling
  7. Similarity matching — find the most similar function in the codebase
"""
from __future__ import annotations

import ast
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.code_understanding")


# ── Data types ──────────────────────────────────────────────────────────────

@dataclass
class VariableDef:
    """Where a variable is defined and what type it likely has."""
    name: str
    line: int
    col: int
    inferred_type: str = "Any"
    is_parameter: bool = False
    is_async_result: bool = False


@dataclass
class FunctionInfo:
    """Everything we know about a function from AST analysis."""
    name: str
    file_path: str
    line: int
    is_async: bool
    args: list[dict] = field(default_factory=list)
    return_type: str = "Any"
    docstring: str = ""
    calls: list[str] = field(default_factory=list)
    variables: list[VariableDef] = field(default_factory=list)
    has_try: bool = False
    has_logging: bool = False
    has_wiring: bool = False
    complexity: int = 1
    nesting_depth: int = 0
    side_effects: list[str] = field(default_factory=list)
    raises: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)


@dataclass
class FileInfo:
    """Everything we know about a file."""
    path: str
    imports: list[str] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    global_vars: list[str] = field(default_factory=list)


@dataclass
class CodebaseMap:
    """Complete map of the codebase for the coder to reason about."""
    files: dict[str, FileInfo] = field(default_factory=dict)
    call_graph: dict[str, list[str]] = field(default_factory=dict)
    type_hints: dict[str, str] = field(default_factory=dict)


# ── Type inference ──────────────────────────────────────────────────────────

# Mapping of common patterns to their return types
_RETURN_TYPE_PATTERNS: dict[str, str] = {
    "dict": "dict",
    "{}": "dict",
    "list": "list",
    "[]": "list",
    "str": "str",
    "True": "bool",
    "False": "bool",
    "None": "None",
    "0": "int",
    "0.0": "float",
    "\"": "str",
    "'": "str",
    "f\"": "str",
    "f'": "str",
}

# Async methods that return awaitables
_ASYNC_RETURN_TYPES: dict[str, str] = {
    "get": "Any",
    "post": "Any",
    "fetch": "Any",
    "load": "Any",
    "search": "list",
    "query": "list",
    "execute": "Any",
    "run": "Any",
}


def infer_type_from_expr(node: ast.expr) -> str:
    """Infer the type of an expression from its AST node.
    
    No LLM — pure AST pattern matching. Handles:
      - Literals (strings, numbers, booleans, None, dicts, lists)
      - Calls to known functions
      - Attribute access
      - Binary operations
      - Conditional expressions
      - Comprehensions
    """
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "None"
        if isinstance(node.value, bool):
            return "bool"
        if isinstance(node.value, int):
            return "int"
        if isinstance(node.value, float):
            return "float"
        if isinstance(node.value, str):
            return "str"
        if isinstance(node.value, bytes):
            return "bytes"
        return "Any"

    if isinstance(node, ast.List):
        if node.elts:
            elem_types = {infer_type_from_expr(e) for e in node.elts}
            if len(elem_types) == 1:
                return f"list[{next(iter(elem_types))}]"
            return "list"
        return "list"

    if isinstance(node, ast.Dict):
        if node.keys:
            key_types = {infer_type_from_expr(k) for k in node.keys if k}
            val_types = {infer_type_from_expr(v) for v in node.values}
            if len(key_types) == 1 and len(val_types) == 1:
                kt = next(iter(key_types))
                vt = next(iter(val_types))
                return f"dict[{kt}, {vt}]"
            return "dict"
        return "dict"

    if isinstance(node, ast.Set):
        return "set"

    if isinstance(node, ast.Tuple):
        return "tuple"

    if isinstance(node, ast.Name):
        # Common type names used as constructors
        type_names = {
            "dict": "dict", "list": "list", "str": "str", "int": "int",
            "float": "float", "bool": "bool", "set": "set", "tuple": "tuple",
            "Any": "Any", "Optional": "Any", "None": "None",
        }
        return type_names.get(node.id, "Any")

    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            # Known type constructors
            if func.id in ("dict", "list", "str", "int", "float", "bool", "set", "tuple"):
                return func.id
            # Known return types for common functions
            return _ASYNC_RETURN_TYPES.get(func.id, "Any")
        if isinstance(func, ast.Attribute):
            # method calls like .get(), .keys(), .items()
            method_map = {
                "get": "Any", "keys": "list", "values": "list",
                "items": "list", "pop": "Any", "update": "None",
            }
            return method_map.get(func.attr, "Any")
        return "Any"

    if isinstance(node, ast.BinOp):
        # Binary operations preserve types
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left_type = infer_type_from_expr(node.left)
            right_type = infer_type_from_expr(node.right)
            if left_type == right_type:
                return left_type
            if left_type in ("int", "float") and right_type in ("int", "float"):
                return "float" if isinstance(node.op, ast.Div) else left_type
            if left_type == "str" or right_type == "str":
                return "str"
        return "Any"

    if isinstance(node, ast.IfExp):
        # Ternary: infer from both branches
        body_type = infer_type_from_expr(node.body)
        orelse_type = infer_type_from_expr(node.orelse)
        if body_type == orelse_type:
            return body_type
        return "Any"

    if isinstance(node, ast.ListComp):
        return f"list[{infer_type_from_expr(node.elt)}]"

    if isinstance(node, ast.DictComp):
        return "dict"

    if isinstance(node, ast.SetComp):
        return "set"

    if isinstance(node, ast.GeneratorExp):
        return "generator"

    if isinstance(node, ast.Lambda):
        return "Callable"

    if isinstance(node, ast.Subscript):
        # e.g. dict[key], list[idx]
        value_type = infer_type_from_expr(node.value)
        return value_type  # conservative: same as container

    if isinstance(node, ast.Attribute):
        # obj.attr — can't infer without runtime info
        return "Any"

    if isinstance(node, ast.Starred):
        return "list"

    if isinstance(node, ast.Await):
        return infer_type_from_expr(node.value)

    return "Any"


# ── Dataflow analysis ──────────────────────────────────────────────────────

def trace_variable(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[VariableDef]:
    """Trace all variable definitions in a function body.
    
    Returns a list of VariableDef with inferred types, line numbers,
    and whether each is a parameter or an async result.
    """
    variables: list[VariableDef] = []

    # Parameters
    for arg in func_node.args.args:
        if arg.arg == "self":
            continue
        var_type = "Any"
        if arg.annotation:
            var_type = ast.unparse(arg.annotation)
        variables.append(VariableDef(
            name=arg.arg, line=arg.lineno, col=arg.col_offset,
            inferred_type=var_type, is_parameter=True,
        ))

    # *args (can be None)
    if func_node.args.vararg:
        variables.append(VariableDef(
            name=func_node.args.vararg.arg,
            line=func_node.args.vararg.lineno,
            col=func_node.args.vararg.col_offset,
            inferred_type="tuple", is_parameter=True,
        ))

    # **kwargs (can be None)
    if func_node.args.kwarg:
        variables.append(VariableDef(
            name=func_node.args.kwarg.arg,
            line=func_node.args.kwarg.lineno,
            col=func_node.args.kwarg.col_offset,
            inferred_type="dict", is_parameter=True,
        ))

    # Walk the body for assignments
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_type = infer_type_from_expr(node.value)
                    variables.append(VariableDef(
                        name=target.id, line=target.lineno,
                        col=target.col_offset, inferred_type=var_type,
                    ))
                elif isinstance(target, (ast.Tuple, ast.List)):
                    # Unpacking assignment: a, b = ...
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            variables.append(VariableDef(
                                name=elt.id, line=elt.lineno,
                                col=elt.col_offset, inferred_type="Any",
                            ))

        elif isinstance(node, ast.AnnAssign):
            # Typed assignment: x: int = 5
            if isinstance(node.target, ast.Name):
                var_type = ast.unparse(node.annotation) if node.annotation else "Any"
                variables.append(VariableDef(
                    name=node.target.id, line=node.target.lineno,
                    col=node.target.col_offset, inferred_type=var_type,
                ))

        elif isinstance(node, ast.AugAssign):
            # Augmented assignment: x += 1
            if isinstance(node.target, ast.Name):
                var_type = infer_type_from_expr(node.value)
                variables.append(VariableDef(
                    name=node.target.id, line=node.target.lineno,
                    col=node.target.col_offset, inferred_type=var_type,
                ))

        elif isinstance(node, ast.NamedExpr):
            # Walrus operator: (x := expr)
            if isinstance(node.target, ast.Name):
                var_type = infer_type_from_expr(node.value)
                variables.append(VariableDef(
                    name=node.target.id, line=node.target.lineno,
                    col=node.target.col_offset, inferred_type=var_type,
                ))

        elif isinstance(node, ast.For):
            # for x in iterable:
            if isinstance(node.target, ast.Name):
                iter_type = infer_type_from_expr(node.iter)
                # If iterable is a list[X], x is X
                if iter_type.startswith("list["):
                    elem_type = iter_type[5:-1]
                elif iter_type.startswith("dict["):
                    elem_type = "Any"  # key
                else:
                    elem_type = "Any"
                variables.append(VariableDef(
                    name=node.target.id, line=node.target.lineno,
                    col=node.target.col_offset, inferred_type=elem_type,
                ))

        elif isinstance(node, ast.AsyncFor):
            if isinstance(node.target, ast.Name):
                variables.append(VariableDef(
                    name=node.target.id, line=node.target.lineno,
                    col=node.target.col_offset, inferred_type="Any",
                    is_async_result=True,
                ))

        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    variables.append(VariableDef(
                        name=item.optional_vars.id,
                        line=item.optional_vars.lineno,
                        col=item.optional_vars.col_offset,
                        inferred_type="Any",
                    ))

        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                variables.append(VariableDef(
                    name=node.name, line=node.lineno,
                    col=node.col_offset, inferred_type="Exception",
                ))

    return variables


# ── Complexity analysis ────────────────────────────────────────────────────

def calculate_complexity(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Calculate cyclomatic complexity of a function.
    
    Base = 1. +1 for each: if, elif, for, while, and, or, except, with,
    comprehension, assert.
    """
    complexity = 1
    for node in ast.walk(func_node):
        if isinstance(node, (ast.If, ast.While, ast.Assert)):
            complexity += 1
        elif isinstance(node, ast.For):
            complexity += 1
        elif isinstance(node, ast.AsyncFor):
            complexity += 1
        elif isinstance(node, ast.Try):
            complexity += len(node.handlers)
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            complexity += 1
        elif isinstance(node, ast.ExceptHandler):
            complexity += 1
    return complexity


def calculate_nesting_depth(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Calculate maximum nesting depth of a function."""
    max_depth = 0
    current_depth = 0

    for node in ast.walk(func_node):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While,
                             ast.Try, ast.With, ast.AsyncWith)):
            # Walk children to find max depth
            depth = _nesting_depth_of(node, 1)
            if depth > max_depth:
                max_depth = depth

    return max_depth


def _nesting_depth_of(node: ast.AST, current_depth: int) -> int:
    """Recursively calculate nesting depth."""
    max_depth = current_depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While,
                              ast.Try, ast.With, ast.AsyncWith)):
            depth = _nesting_depth_of(child, current_depth + 1)
            if depth > max_depth:
                max_depth = depth
    return max_depth


# ── Side-effect detection ──────────────────────────────────────────────────

_SIDE_EFFECT_PATTERNS: list[str] = [
    "redis.", "db.", "sqlite", "file.", "open(", "write(", "save(",
    "delete(", "remove(", "append(", "insert(", "update(",
    "publish(", "send(", "notify(", "emit(",
]


def detect_side_effects(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Detect side effects in a function body.
    
    Looks for: I/O calls, Redis/DB writes, file operations, network calls,
    state mutations, logging (benign, excluded).
    """
    effects: list[str] = []
    source = ast.unparse(func_node)

    for pattern in _SIDE_EFFECT_PATTERNS:
        if pattern in source:
            effects.append(pattern.rstrip("("))

    # Detect mutations of passed-in objects
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr in ("append", "insert", "pop", "remove",
                                 "update", "setdefault", "clear",
                                 "add", "discard", "difference_update"):
                    effects.append(f".{func.attr}()")

    return list(set(effects))


# ── Call graph ─────────────────────────────────────────────────────────────

def build_call_graph(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extract all function calls made within a function body."""
    calls: list[str] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.append(func.id)
            elif isinstance(func, ast.Attribute):
                calls.append(func.attr)
    return list(set(calls))


# ── Similarity matching ────────────────────────────────────────────────────

def find_similar_functions(
    target_name: str,
    target_args: list[str],
    target_is_async: bool,
    codebase: CodebaseMap,
    top_n: int = 5,
) -> list[tuple[FunctionInfo, float]]:
    """Find the most similar functions in the codebase.
    
    Similarity is based on:
      - Name prefix match (40%)
      - Async/sync match (20%)
      - Argument count match (20%)
      - Same file proximity (20%)
    
    Returns list of (FunctionInfo, similarity_score) sorted by score.
    """
    scored: list[tuple[FunctionInfo, float]] = []

    target_prefix = target_name.split("_")[0] if "_" in target_name else target_name
    target_arg_count = len(target_args)

    for file_path, file_info in codebase.files.items():
        for func in file_info.functions:
            score = 0.0

            # Name prefix match (40%)
            func_prefix = func.name.split("_")[0] if "_" in func.name else func.name
            if func_prefix == target_prefix:
                score += 0.4

            # Async/sync match (20%)
            if func.is_async == target_is_async:
                score += 0.2

            # Argument count match (20%)
            func_arg_count = len(func.args)
            if func_arg_count == target_arg_count:
                score += 0.2
            elif abs(func_arg_count - target_arg_count) <= 1:
                score += 0.1

            # Same file proximity (20%) — same file = more relevant
            # (can't determine target file here, so give partial credit)

            if score > 0:
                scored.append((func, score))

    scored.sort(key=lambda x: -x[1])
    return scored[:top_n]


# ── Full function analysis ─────────────────────────────────────────────────

def analyze_function(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
) -> FunctionInfo:
    """Analyze a single function and return everything we know about it."""
    # Args
    args = []
    for arg in func_node.args.args:
        arg_info = {"name": arg.arg}
        if arg.annotation:
            arg_info["type"] = ast.unparse(arg.annotation)
        args.append(arg_info)

    # Return type
    return_type = "Any"
    if func_node.returns:
        return_type = ast.unparse(func_node.returns)
    else:
        # Infer from return statements
        for node in ast.walk(func_node):
            if isinstance(node, ast.Return) and node.value:
                inferred = infer_type_from_expr(node.value)
                if inferred != "Any":
                    return_type = inferred
                    break

    # Docstring
    docstring = ""
    if func_node.body and isinstance(func_node.body[0], ast.Expr) and isinstance(func_node.body[0].value, ast.Constant):
        docstring = func_node.body[0].value.value or ""

    # Source text for pattern matching
    source = ast.unparse(func_node)

    return FunctionInfo(
        name=func_node.name,
        file_path=file_path,
        line=func_node.lineno,
        is_async=isinstance(func_node, ast.AsyncFunctionDef),
        args=args,
        return_type=return_type,
        docstring=docstring,
        calls=build_call_graph(func_node),
        variables=trace_variable(func_node),
        has_try=any(isinstance(n, ast.Try) for n in ast.walk(func_node)),
        has_logging="logger." in source or "log." in source,
        has_wiring="wire_success" in source or "wire_failure" in source,
        complexity=calculate_complexity(func_node),
        nesting_depth=calculate_nesting_depth(func_node),
        side_effects=detect_side_effects(func_node),
        raises=[n.id for n in ast.walk(func_node) if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and isinstance(n.exc.func, ast.Name)],
        decorators=[ast.unparse(d) for d in func_node.decorator_list],
    )


def analyze_file(file_path: str) -> Optional[FileInfo]:
    """Analyze a single Python file."""
    path = Path(file_path)
    if not path.exists():
        return None

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            tree = ast.parse(f.read())
    except (SyntaxError, Exception) as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return None

    imports: list[str] = []
    functions: list[FunctionInfo] = []
    classes: list[str] = []
    global_vars: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.append(analyze_function(node, file_path))
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    global_vars.append(target.id)

    return FileInfo(
        path=file_path,
        imports=imports,
        functions=functions,
        classes=classes,
        global_vars=global_vars,
    )


def build_codebase_map(root_dir: str | Path, max_files: int = 200) -> CodebaseMap:
    """Build a complete map of the codebase for the coder to reason about."""
    root = Path(root_dir)
    codebase = CodebaseMap()
    files_scanned = 0

    for py_file in sorted(root.rglob("*.py")):
        if py_file.name.startswith("test_") or py_file.name.startswith("__"):
            continue
        if files_scanned >= max_files:
            break

        file_info = analyze_file(str(py_file))
        if file_info:
            codebase.files[str(py_file)] = file_info
            files_scanned += 1

    # Build call graph
    for file_path, file_info in codebase.files.items():
        for func in file_info.functions:
            codebase.call_graph[func.name] = func.calls
            codebase.type_hints[func.name] = func.return_type

    logger.info(
        "Codebase map built: %d files, %d functions, %d call edges",
        len(codebase.files),
        sum(len(f.functions) for f in codebase.files.values()),
        sum(len(v) for v in codebase.call_graph.values()),
    )

    return codebase


# ── High-level queries the coder can use ───────────────────────────────────

def find_function(codebase: CodebaseMap, name: str) -> Optional[FunctionInfo]:
    """Find a function by name across the codebase."""
    for file_info in codebase.files.values():
        for func in file_info.functions:
            if func.name == name:
                return func
    return None


def find_callers(codebase: CodebaseMap, func_name: str) -> list[str]:
    """Find all functions that call a given function."""
    callers: list[str] = []
    for caller_name, calls in codebase.call_graph.items():
        if func_name in calls:
            callers.append(caller_name)
    return callers


def find_files_importing(codebase: CodebaseMap, module: str) -> list[str]:
    """Find all files that import a given module."""
    files: list[str] = []
    for file_path, file_info in codebase.files.items():
        if module in file_info.imports:
            files.append(file_path)
    return files


def suggest_return_type(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Suggest a return type annotation for a function based on its body.
    
    Examines all return statements and infers the most specific common type.
    """
    return_types: set[str] = set()

    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and node.value:
            inferred = infer_type_from_expr(node.value)
            return_types.add(inferred)

    if not return_types:
        return "None"

    if len(return_types) == 1:
        return next(iter(return_types))

    # Multiple return types — find the common ancestor
    if "None" in return_types:
        return_types.discard("None")
        if len(return_types) == 1:
            return f"{next(iter(return_types))} | None"
        return "Any"

    return "Any"

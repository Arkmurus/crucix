"""R-F1232 — Tests for the Code Understanding Engine.

Tests that ARIA can understand code through AST analysis alone
(no LLM, no external API calls).
"""
from __future__ import annotations

import ast
import pytest

from aria_service.intel.code_understanding import (
    infer_type_from_expr,
    trace_variable,
    calculate_complexity,
    calculate_nesting_depth,
    detect_side_effects,
    build_call_graph,
    analyze_function,
    suggest_return_type,
    CodebaseMap,
    FileInfo,
    FunctionInfo,
    find_similar_functions,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

SIMPLE_FUNC = ast.parse("def add(a, b):\n    return a + b\n").body[0]
ASYNC_FUNC = ast.parse("async def fetch(url):\n    result = await get(url)\n    return result\n").body[0]
COMPLEX_FUNC = ast.parse("""
def process(items):
    result = []
    for item in items:
        if item.get("active"):
            try:
                value = item["value"] * 2
                result.append(value)
            except KeyError:
                continue
        elif item.get("pending"):
            result.append(0)
    return result
""").body[0]
SIDE_EFFECT_FUNC = ast.parse("""
def save_user(user):
    db.users.insert(user)
    redis.set(f"user:{user.id}", json.dumps(user))
    logger.info("Saved user %s", user.id)
    return {"status": "ok"}
""").body[0]
MULTI_RETURN_FUNC = ast.parse("""
def lookup(key):
    if key in cache:
        return cache[key]
    result = fetch(key)
    if result is None:
        return None
    return result
""").body[0]


# ── Type inference tests ───────────────────────────────────────────────────

def test_infer_type_from_string_literal():
    node = ast.parse('"hello"').body[0].value
    assert infer_type_from_expr(node) == "str"


def test_infer_type_from_int_literal():
    node = ast.parse("42").body[0].value
    assert infer_type_from_expr(node) == "int"


def test_infer_type_from_float_literal():
    node = ast.parse("3.14").body[0].value
    assert infer_type_from_expr(node) == "float"


def test_infer_type_from_bool():
    node = ast.parse("True").body[0].value
    assert infer_type_from_expr(node) == "bool"


def test_infer_type_from_none():
    node = ast.parse("None").body[0].value
    assert infer_type_from_expr(node) == "None"


def test_infer_type_from_dict():
    node = ast.parse('{"key": "value"}').body[0].value
    result = infer_type_from_expr(node)
    assert result.startswith("dict")


def test_infer_type_from_list():
    node = ast.parse("[1, 2, 3]").body[0].value
    result = infer_type_from_expr(node)
    assert result.startswith("list")


def test_infer_type_from_list_comp():
    node = ast.parse("[x * 2 for x in items]").body[0].value
    result = infer_type_from_expr(node)
    assert result.startswith("list")


def test_infer_type_from_call():
    node = ast.parse('dict()').body[0].value
    assert infer_type_from_expr(node) == "dict"


def test_infer_type_from_await():
    node = ast.parse('await get_data()').body[0].value
    assert infer_type_from_expr(node) == "Any"


# ── Variable tracing tests ─────────────────────────────────────────────────

def test_trace_parameters():
    vars = trace_variable(SIMPLE_FUNC)
    param_names = [v.name for v in vars if v.is_parameter]
    assert "a" in param_names
    assert "b" in param_names


def test_trace_assignments():
    vars = trace_variable(ASYNC_FUNC)
    var_names = [v.name for v in vars]
    assert "result" in var_names


def test_trace_for_loop_var():
    code = ast.parse("def f(items):\n    for x in items:\n        print(x)\n").body[0]
    vars = trace_variable(code)
    assert any(v.name == "x" for v in vars)


def test_trace_exception_var():
    code = ast.parse("def f():\n    try:\n        pass\n    except Exception as e:\n        pass\n").body[0]
    vars = trace_variable(code)
    assert any(v.name == "e" for v in vars)


# ── Complexity tests ───────────────────────────────────────────────────────

def test_simple_function_complexity():
    assert calculate_complexity(SIMPLE_FUNC) == 1


def test_complex_function_complexity():
    # if, elif, try, except, for = 5 branches + 1 base
    assert calculate_complexity(COMPLEX_FUNC) >= 5


def test_nesting_depth():
    depth = calculate_nesting_depth(COMPLEX_FUNC)
    assert depth >= 2  # for > if > try


# ── Side-effect tests ──────────────────────────────────────────────────────

def test_detects_db_write():
    effects = detect_side_effects(SIDE_EFFECT_FUNC)
    assert any("insert" in e for e in effects)


def test_detects_redis_write():
    effects = detect_side_effects(SIDE_EFFECT_FUNC)
    assert any("redis" in e or "set" in e for e in effects)


def test_no_side_effects_for_pure_function():
    effects = detect_side_effects(SIMPLE_FUNC)
    assert len(effects) == 0


# ── Call graph tests ───────────────────────────────────────────────────────

def test_build_call_graph():
    calls = build_call_graph(ASYNC_FUNC)
    assert "get" in calls


def test_call_graph_empty_for_no_calls():
    code = ast.parse("def f():\n    return 42\n").body[0]
    calls = build_call_graph(code)
    assert len(calls) == 0


# ── Return type suggestion tests ───────────────────────────────────────────

def test_suggest_return_type_simple():
    # a + b where both are parameters (type Any) -> Any
    rt = suggest_return_type(SIMPLE_FUNC)
    assert rt in ("Any", "int")  # depends on inference depth


def test_suggest_return_type_async():
    # async def fetch(url): result = await get(url); return result
    rt = suggest_return_type(ASYNC_FUNC)
    assert rt in ("Any", "str", "dict")  # depends on inference


def test_suggest_return_type_multi():
    rt = suggest_return_type(MULTI_RETURN_FUNC)
    assert rt in ("Any", "dict | None", "str | None", "Any | None")


# ── Similarity matching tests ──────────────────────────────────────────────

def test_find_similar_functions():
    codebase = CodebaseMap()
    codebase.files["test.py"] = FileInfo(
        path="test.py",
        functions=[
            FunctionInfo(name="get_user", file_path="test.py", line=1, is_async=True, args=[{"name": "id"}]),
            FunctionInfo(name="get_order", file_path="test.py", line=10, is_async=True, args=[{"name": "id"}]),
            FunctionInfo(name="save_user", file_path="test.py", line=20, is_async=False, args=[{"name": "user"}]),
        ],
    )
    similar = find_similar_functions("get_item", ["id"], True, codebase, top_n=2)
    assert len(similar) >= 1
    # get_user and get_order should match (same prefix, async, arg count)
    names = [s[0].name for s in similar]
    assert "get_user" in names or "get_order" in names


# ── Full function analysis tests ───────────────────────────────────────────

def test_analyze_function_returns_info():
    info = analyze_function(SIMPLE_FUNC, "test.py")
    assert info.name == "add"
    assert info.is_async is False
    assert len(info.args) == 2
    assert info.args[0]["name"] == "a"


def test_analyze_function_detects_try():
    code = ast.parse("def f():\n    try:\n        pass\n    except:\n        pass\n").body[0]
    info = analyze_function(code, "test.py")
    assert info.has_try is True


def test_analyze_function_detects_logging():
    code = ast.parse("def f():\n    logger.info('hello')\n").body[0]
    info = analyze_function(code, "test.py")
    assert info.has_logging is True


def test_analyze_function_detects_wiring():
    code = ast.parse("def f():\n    wire_success(module='test')\n").body[0]
    info = analyze_function(code, "test.py")
    assert info.has_wiring is True


def test_analyze_function_complexity():
    info = analyze_function(COMPLEX_FUNC, "test.py")
    assert info.complexity > 1
    assert info.nesting_depth >= 1


def test_analyze_function_side_effects():
    info = analyze_function(SIDE_EFFECT_FUNC, "test.py")
    assert len(info.side_effects) > 0


def test_analyze_function_call_graph():
    info = analyze_function(ASYNC_FUNC, "test.py")
    assert len(info.calls) > 0

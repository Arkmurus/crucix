"""R-F1809 — Phase 1 batch 6: autonomous/ + llm/ + search_engine/ wired.

Proves @fail_wire fires (correct gap_type) on a real failure in each newly-wired
module. Force-fails a wired callable (module-level fn or class method) via a
no-args / arity-overflow call -> TypeError before the body (no side effects).

Control-flow exemptions verified separately: llm providers exempt ProviderError
(handled by fallback), cost_monitor exempts its cost-breaker RuntimeError,
codebase_reader exempts validation ValueError. fallback.complete is intentionally
NOT exempt (its final ProviderError = real all-providers-exhausted signal).
"""
import asyncio
import ast
import importlib
import inspect

import pytest

import aria_service.intel.wire as wire
from aria_service.intel import wiring_harness as wh

BATCH6 = [
    # autonomous/
    "autonomous_deploy", "claude_reviewer", "codebase_reader", "coder_entrypoint",
    "constitutional_validator", "cost_monitor", "delivery", "deploy_verifier",
    "dryrun_history", "engine", "fly_deployer", "gap_detector", "machines_deployer",
    "r_counter", "review_ticket", "safety", "self_coder", "sovereign_llm", "tasks",
    "test_runner", "wa_notifier",
    # llm/
    "anthropic", "aria_llm_provider", "factory", "fallback", "gemini", "hybrid",
    "local_llm", "metered", "openai_compat", "prompt_budget", "provider",
    "rate_limiter", "resilience", "tier_router",
    # search_engine/
    "internal_search",
]
_PKG = {**{m: "autonomous" for m in BATCH6[:21]},
        **{m: "llm" for m in BATCH6[21:35]},
        "internal_search": "search_engine"}
_SENTINEL = object()


def _forcefail_args(fn):
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (ValueError, TypeError):
        return None
    if any(p.default is inspect.Parameter.empty and p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.KEYWORD_ONLY) for p in params):
        return ()
    if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params):
        return None
    slots = sum(1 for p in params if p.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY))
    return tuple(_SENTINEL for _ in range(slots + 1))


def _decname(d):
    t = d.func if isinstance(d, ast.Call) else d
    return getattr(t, "id", getattr(t, "attr", None))


def _pick_forcefail(mod):
    """Prefer a method (proves method-wiring), else a module-level fn."""
    with open(mod.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    module_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            cls = getattr(mod, node.name, None)
            if cls is None:
                continue
            for c in node.body:
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) and not c.name.startswith("_"):
                    if any(_decname(d) == "fail_wire" for d in c.decorator_list):
                        fn = getattr(cls, c.name, None)
                        if fn is not None and _forcefail_args(fn) is not None:
                            return f"{node.name}.{c.name}", fn, _forcefail_args(fn)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            if any(_decname(d) == "fail_wire" for d in node.decorator_list):
                fn = getattr(mod, node.name, None)
                if fn is not None and _forcefail_args(fn) is not None and module_fn is None:
                    module_fn = (node.name, fn, _forcefail_args(fn))
    return module_fn if module_fn else (None, None, None)


@pytest.mark.parametrize("module_name", BATCH6)
def test_batch6_module_fail_wire_records_gap(module_name):
    mod = importlib.import_module(f"aria_service.{_PKG[module_name]}.{module_name}")
    expected = wh.get_gap_type(module_name)
    label, fn, args = _pick_forcefail(mod)
    assert fn is not None, f"{module_name}: no force-failable wired callable"

    async def _run():
        recorded = []

        async def _mock(gap_type, detail, source):
            recorded.append(gap_type)

        original = wire._record_gap
        wire._record_gap = _mock
        try:
            with pytest.raises(Exception):
                if inspect.iscoroutinefunction(fn):
                    await fn(*args)
                else:
                    fn(*args)
            import time
            deadline = time.time() + 5
            while time.time() < deadline:
                if recorded:
                    break
                await asyncio.sleep(0.01)
        finally:
            wire._record_gap = original

        assert recorded, f"{module_name}.{label}(): no gap recorded"
        assert expected in recorded, f"{module_name}.{label}(): expected {expected}, got {recorded}"

    asyncio.run(_run())


def _decorators_with_cfe(path):
    """Count fail_wire decorators carrying control_flow_exempt, via AST (handles
    same-named methods that fail_wire_decorators collapses by name)."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if _decname(d) == "fail_wire" and isinstance(d, ast.Call):
                    if any(kw.arg == "control_flow_exempt" for kw in d.keywords):
                        n += 1
    return n


def test_llm_provider_error_exempted_but_fallback_not():
    res = importlib.import_module("aria_service.llm.resilience")
    # resilience has a ProviderError-exempt complete (the one that raises it).
    assert _decorators_with_cfe(res.__file__) >= 1, "resilience ProviderError not exempt"
    # fallback intentionally has NO control_flow_exempt (its final ProviderError /
    # last_error = real all-providers-exhausted signal that MUST gap).
    fb = importlib.import_module("aria_service.llm.fallback")
    assert _decorators_with_cfe(fb.__file__) == 0, "fallback should NOT exempt its exhaustion raise"

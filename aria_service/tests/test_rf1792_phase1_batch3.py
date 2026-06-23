"""R-F1792 — Phase 1 batch 3: 8 MIXED intel modules (module-level fns + methods).

FAIL->PASS (§3c): proves @fail_wire fires on CLASS METHODS (the novel surface
for this batch), not just module-level functions.

For each module it resolves a wired method off its class and calls it with no
args: the bound wrapper forwards to the underlying function, which raises
TypeError ("missing required positional argument: 'self'") BEFORE the body runs
— no instance, no side effects — and asserts the module's registered gap_type
lands. @property accessors are HARD_EXEMPT (not wired) and so not exercised.
"""
import ast
import asyncio
import inspect

import pytest

import aria_service.intel.wire as wire
from aria_service.intel import wiring_harness as wh

BATCH3 = [
    "grounded_reasoner", "dd_schema", "document_reader", "content_scanner",
    "web_search", "rag_store", "semantic_search", "dd_vault",
]


_SENTINEL = object()


def _decname(d):
    t = d.func if isinstance(d, ast.Call) else d
    return getattr(t, "id", getattr(t, "attr", None))


def _forcefail_args(fn):
    """Positional args that make fn() raise TypeError BEFORE its body (no side
    effects): () if it has a required arg (incl. `self` for unbound methods),
    else arity-overflow for fixed-arity fns, else None."""
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


def _first_wired_method(mod):
    """Return (label, callable) for a wired public method on a class in mod,
    preferring a method (the batch-3 surface) but falling back to a wired
    module-level function so every module is behaviorally proven."""
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
                        if fn is not None:
                            return f"{node.name}.{c.name}", fn  # method preferred
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            if any(_decname(d) == "fail_wire" for d in node.decorator_list):
                fn = getattr(mod, node.name, None)
                if fn is not None and module_fn is None:
                    module_fn = (node.name, fn)
    return module_fn if module_fn else (None, None)


@pytest.mark.parametrize("module_name", BATCH3)
def test_batch3_method_fail_wire_records_gap(module_name):
    import importlib
    mod = importlib.import_module(f"aria_service.intel.{module_name}")
    expected = wh.get_gap_type(module_name)
    label, fn = _first_wired_method(mod)
    if fn is None:
        # e.g. document_reader: its only methods are exempt @property accessors;
        # coverage is module-level functions (proven by the batch-2 pattern).
        pytest.skip(f"{module_name}: no wired method (all methods exempt properties)")

    async def _run():
        recorded = []

        async def _mock(gap_type, detail, source):
            recorded.append(gap_type)

        original = wire._record_gap
        wire._record_gap = _mock
        args = _forcefail_args(fn)
        assert args is not None, f"{label}: cannot force-fail (accepts *args/**kwargs)"
        try:
            with pytest.raises(Exception):
                if inspect.iscoroutinefunction(fn):
                    await fn(*args)   # missing `self`/bad arity -> TypeError
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

        assert recorded, f"{label}(): no gap recorded — method @fail_wire did not fire"
        assert expected in recorded, f"{label}(): expected '{expected}', got {recorded}"

    asyncio.run(_run())


def test_property_methods_are_exempt_not_wired():
    """@property accessors must be HARD_EXEMPT (wrapping changes semantics)."""
    for fname, prop in [("document_reader.py", "is_usable"),
                        ("document_reader.py", "summary"),
                        ("semantic_search.py", "size")]:
        ok, reason = wh.is_exempt(fname, prop)
        assert ok and "property" in reason.lower(), f"{fname}:{prop} not property-exempt"

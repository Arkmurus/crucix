"""R-F1789 — Phase 1 batch 2: 35 clean pure-fn intel modules wired to the brain.

FAIL->PASS (§3c): BEFORE, a failure in any of these 35 modules reached nothing
(dark path). AFTER, @fail_wire emits the module's registered gap_type to the
brain on any unhandled exception.

Generic proof: for each module, pick a module-level public function that has a
REQUIRED argument, call it with no args (TypeError raised inside the wrapper's
try, before the body executes — no side effects), and assert a gap of the
module's registered gap_type lands. A module with no force-failable function is
a test failure because its wiring contract has become untestable.
"""
import asyncio
import importlib
import inspect

import pytest

import aria_service.intel.wire as wire
from aria_service.intel import wiring_harness as wh

BATCH2 = [
    "brain_hook", "brain_hook_bg", "brave_answers", "capability_gaps",
    "companies_house", "compliance_watch", "compliance_workflow",
    "continuous_learner", "cost_free_learning", "country_sanctions",
    "dd_case_library", "dd_layer_extensions", "dd_trigger_pipeline",
    "dd_versioning", "document_intelligence", "file_type_detector",
    "github_search", "knowledge", "learning_progress", "memory_wal",
    "neural_memory", "news_monitor", "ocr", "pdf_deep_ingest",
    "portal_coverage_audit", "portal_registry", "reasoning_library",
    "registration_check", "research_tasks", "researcher", "sanctions",
    "search_searxng", "sipri_ingest", "sipri_knowledge", "student",
]


_SENTINEL = object()


def _forcefail_args(fn):
    """Return a positional-args tuple that makes fn() raise TypeError BEFORE its
    body runs (so no side effects), or None if it can't be done at the call
    boundary (function accepts *args/**kwargs and has no required arg).

    - has a required arg            -> () (missing-required-argument)
    - fixed arity, all optional     -> too many positionals (arity overflow)
    - *args/**kwargs, no required    -> None (accepts anything; can't force-fail)
    """
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return None
    params = list(sig.parameters.values())
    has_required = any(
        p.default is inspect.Parameter.empty and p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.KEYWORD_ONLY,
        )
        for p in params
    )
    if has_required:
        return ()
    has_var = any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params)
    if has_var:
        return None
    positional_slots = sum(
        1 for p in params
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.POSITIONAL_ONLY)
    )
    return tuple(_SENTINEL for _ in range(positional_slots + 1))


def _pick_forcefail(mod):
    """Pick a wired fn we can force-fail, preferring required-arg fns."""
    wired = list(wh.fail_wire_decorators(mod.__file__))
    chosen = None
    for name in wired:
        fn = getattr(mod, name, None)
        if fn is None:
            continue
        args = _forcefail_args(fn)
        if args == ():
            return name, fn, ()          # cleanest: missing required arg
        if args is not None and chosen is None:
            chosen = (name, fn, args)     # fallback: arity overflow
    return chosen if chosen else (None, None, None)


@pytest.mark.parametrize("module_name", BATCH2)
def test_batch2_module_fail_wire_records_gap(module_name):
    mod = importlib.import_module(f"aria_service.intel.{module_name}")
    expected = wh.get_gap_type(module_name)
    name, fn, args = _pick_forcefail(mod)
    assert fn is not None, (
        f"{module_name}: no force-failable wired function; add a deterministic "
        "failure-path capability fixture"
    )

    async def _run():
        recorded = []

        async def _mock(gap_type, detail, source):
            recorded.append(gap_type)

        original = wire._record_gap
        wire._record_gap = _mock
        try:
            with pytest.raises(Exception):
                if inspect.iscoroutinefunction(fn):
                    await fn(*args)   # bad arity -> TypeError, caught by wrapper
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

        assert recorded, f"{module_name}.{name}(): no gap recorded — @fail_wire did not fire"
        assert expected in recorded, (
            f"{module_name}.{name}(): expected gap_type='{expected}', got {recorded}"
        )

    asyncio.run(_run())

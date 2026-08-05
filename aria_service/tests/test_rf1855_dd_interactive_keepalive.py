"""R-F1855 — keep the interactive-yield window alive for the whole DD.

Heavy background CPU work (R-F1754 encode-absorb backoff, semantic-index queue,
eagle_eye, brain_hook_bg) defers while a user request is in flight — but only for
the ~8s `_interactive_active` window after the triggering chat request. A DD runs
for minutes, so that window expired mid-DD and the background work resumed,
GIL-starving the loop (live wedge 2026-06-23/24 → "DD produces nothing").

orchestrate_dd now runs a keepalive that refreshes mark_interactive() for the
DD's lifetime and cancels it when the DD ends. These tests drive the real
orchestrate_dd (impl mocked) and assert: interactive is refreshed during the DD,
the keepalive is cancelled after (no leak), and it survives the timeout path.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport

# R-F3754/§16 — NOT inspect.getsource: it slices at the line numbers captured
# AT IMPORT, so an edit mid-run returns a DIFFERENT function's body, silently.
from ._source_probe import function_source


def test_keepalive_refreshes_interactive_during_dd():
    calls = []

    async def fake_impl(*a, **k):
        # yield control a few times so the keepalive task gets scheduled
        for _ in range(6):
            await asyncio.sleep(0)
        return ARKDDReport()

    with patch.object(ddo, "_orchestrate_dd_impl", fake_impl), \
         patch("aria_service.intel.brain_hook.mark_interactive", lambda: calls.append(1)):
        rep = asyncio.run(ddo.orchestrate_dd({"name": "X", "type": "company"}, mode="quick"))

    assert isinstance(rep, ARKDDReport)
    assert len(calls) >= 1, "the keepalive must refresh mark_interactive during the DD"


def test_keepalive_cancelled_after_dd_completes():
    """The keepalive task must not leak past the DD."""
    captured = {}

    real_create_task = asyncio.create_task

    def spy_create_task(coro, *a, **k):
        t = real_create_task(coro, *a, **k)
        # the keepalive is the only task orchestrate_dd creates
        if getattr(coro, "__name__", "") == "_dd_interactive_keepalive" or "keepalive" in repr(coro):
            captured["task"] = t
        return t

    async def fake_impl(*a, **k):
        await asyncio.sleep(0)
        return ARKDDReport()

    async def run():
        with patch.object(ddo, "_orchestrate_dd_impl", fake_impl), \
             patch("aria_service.intel.brain_hook.mark_interactive", lambda: None), \
             patch("aria_service.intel.dd_orchestrator.asyncio.create_task", spy_create_task):
            await ddo.orchestrate_dd({"name": "X", "type": "company"}, mode="quick")
        await asyncio.sleep(0)  # let the cancellation settle
        return captured.get("task")

    task = asyncio.run(run())
    assert task is not None, "orchestrate_dd must start the keepalive task"
    assert task.cancelled() or task.done(), "keepalive must be cancelled when the DD ends"


def test_keepalive_cancel_is_in_a_finally():
    """The cancel must be in a `finally` so it runs on EVERY exit path —
    normal return, the hard-deadline TimeoutError, and any other exception.
    (Structural guard: driving the real 150s+ hard-deadline path in a unit test
    is impractical; the finally is what makes cancellation total.)"""
    import ast
    import inspect
    src = function_source(ddo, "orchestrate_dd")
    tree = ast.parse(src)
    func = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == "orchestrate_dd")
    # find a Try with a finalbody that cancels _ka_task
    found = False
    for node in ast.walk(func):
        if isinstance(node, ast.Try) and node.finalbody:
            fin = "\n".join(ast.unparse(statement) for statement in node.finalbody)
            if "_ka_task.cancel()" in fin:
                found = True
                break
    assert found, "the keepalive cancel must live in a try/finally (covers normal + timeout paths)"

"""R-F518 — _execute_direct_tool uses passed `llm` parameter (not app rebind).

Audit finding on R-F470 (2026-05-14): the new `run_eval` branch in
`_execute_direct_tool(tool_kind, task, llm)` ignored its `llm` parameter
and reached back into `from ..main import app` to fetch the LLM provider
from `app.state.llm_provider`. The existing R-F470 capability test
(`test_rf470_runtime_dispatch_calls_eval_runner`) re-called
`eval_runner.run_eval` directly — it never exercised the dispatch path,
so the `from ..main import app` rebind was untested.

Risk: if `run_eval` fired during lifespan startup, the circular import
could blow up. Daily 06:00 UTC task fires for the first time post-deploy
on 2026-05-15 — the rebind needed to go before then.

R-F518 dropped the rebind. This test pins the parameter usage so a
future refactor can't silently re-introduce the import.

Also covered: R-F511's `_JURISDICTION_QUALIFIER` regex was moved from
function scope to module scope. The R-F511 capability tests still
prove the behavior, but this test makes the location explicit so a
future cleanup can't move it back without flagging.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


# ── R-F511 / R-F518 — module-scope regex ────────────────────────────────────


def test_rf517_jurisdiction_qualifier_is_module_scope():
    """Regex must be defined at module scope on local_brain — moved
    from inside try_local_response by R-F518 so it's compiled once at
    import time, not per call.
    """
    from aria_service.intel import local_brain

    assert hasattr(local_brain, "_JURISDICTION_QUALIFIER"), (
        "R-F518: _JURISDICTION_QUALIFIER must be module-level on "
        "aria_service.intel.local_brain"
    )
    assert isinstance(local_brain._JURISDICTION_QUALIFIER, re.Pattern), (
        "R-F518: must be a pre-compiled re.Pattern (not a string regex)"
    )

    # Spot-check the contract: matches a UK qualifier, doesn't fire on a
    # neutral sentence. The full behavioural coverage lives in
    # test_rf511_local_brain_jurisdiction_yield.py — this is a tripwire,
    # not a duplicate.
    assert local_brain._JURISDICTION_QUALIFIER.search(
        "Is Hikvision sanctioned in the UK?"
    ), "R-F518: regex must still match 'UK' after the module-scope move"
    assert not local_brain._JURISDICTION_QUALIFIER.search(
        "Is Hikvision sanctioned?"
    ), "R-F518: unqualified sanctions phrasing must not match"


# ── R-F470 / R-F518 — parameter usage in dispatcher ─────────────────────────


def test_rf517_run_eval_dispatch_uses_passed_llm_parameter(monkeypatch):
    """_execute_direct_tool must hand the `llm` it was given to
    eval_runner.run_eval — NOT a value pulled from app.state.

    Pin: stub eval_runner.run_eval with a counter; call the dispatcher
    with a uniquely-tagged llm; assert run_eval saw exactly that
    instance. If a future refactor reintroduces `from ..main import app`,
    this assertion fails.

    Repo convention: tests are synchronous functions that drive async
    code via asyncio.run() — pytest-asyncio is not installed.
    """
    from aria_service.autonomous import tasks as _tasks
    from aria_service.intel import eval_runner as _real_er

    # Tagged stub so identity check is unambiguous.
    tagged_llm = SimpleNamespace(
        is_configured=True,
        _rf517_tag="this-must-reach-eval-runner",
    )

    # Minimal task shape — the dispatcher only reads tool_chain[0].
    task = SimpleNamespace(tool_chain=[{"label": "rf517_test"}])

    # Patch the run_eval attribute on the real module. The dispatcher
    # does `from ..intel import eval_runner as _er` and then
    # `await _er.run_eval(llm, ...)` — replacing the function attribute
    # is robust to test-order effects (sys.modules monkeypatching
    # doesn't intercept `from package import submodule` once another
    # test has already imported the real submodule).
    run_eval_mock = AsyncMock(return_value={"ok": True, "pass_rate": 1.0})
    monkeypatch.setattr(_real_er, "run_eval", run_eval_mock)

    result = asyncio.run(
        _tasks._execute_direct_tool("run_eval", task, tagged_llm)
    )

    assert result == {"run_eval": {"ok": True, "pass_rate": 1.0}}, (
        "R-F518: dispatcher must return the eval_runner output verbatim"
    )
    run_eval_mock.assert_awaited_once()
    called_llm = run_eval_mock.await_args.args[0]
    assert getattr(called_llm, "_rf517_tag", None) == "this-must-reach-eval-runner", (
        "R-F518: eval_runner.run_eval received a different llm than the one "
        "passed into _execute_direct_tool — the `from ..main import app` "
        "rebind has been silently re-introduced"
    )


def test_rf517_run_eval_dispatch_skips_when_llm_not_configured():
    """Honesty preservation: when the passed llm is missing or
    is_configured is False, the dispatcher must NOT call run_eval and
    must surface a readable skip message. R-F470 already had this
    guard; R-F518 keeps it after dropping the rebind."""
    from aria_service.autonomous import tasks as _tasks

    task = SimpleNamespace(tool_chain=[{"label": "rf517_skip_test"}])

    # No llm at all.
    result = asyncio.run(
        _tasks._execute_direct_tool("run_eval", task, None)
    )
    assert result["run_eval"]["ok"] is False
    assert result["run_eval"]["reason"] == "no_llm_configured"
    assert "SKIPPED" in result["run_eval"]["readable"]

    # llm present but is_configured False.
    unconfigured = SimpleNamespace(is_configured=False)
    result = asyncio.run(
        _tasks._execute_direct_tool("run_eval", task, unconfigured)
    )
    assert result["run_eval"]["ok"] is False
    assert result["run_eval"]["reason"] == "no_llm_configured"

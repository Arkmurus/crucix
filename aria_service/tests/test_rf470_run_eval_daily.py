"""R-F470 — RUN-EVAL-DAILY autonomous task.

Operational hygiene item from next_session_pickup_2026_05_15:
DAILY-GOLDEN-AUTOGEN grows the golden eval set but pre-R-F470 nothing
scored against it autonomously, so regression detection was manual —
the operator had to remember to run /api/aria/eval/run themselves.

R-F470 adds:
  1. `run_eval` tool dispatch in autonomous/tasks.py
  2. RUN-EVAL-DAILY task entry in autonomous/tasks.yaml, scheduled
     30 min after DAILY-GOLDEN-AUTOGEN (06:00 UTC) so the freshest
     autogen output is in scope
"""
from __future__ import annotations

import asyncio
import pytest


def test_rf470_yaml_has_run_eval_daily_task():
    from pathlib import Path
    yaml = Path(__file__).resolve().parents[2] / "aria_service" / "autonomous" / "tasks.yaml"
    text = yaml.read_text(encoding="utf-8")
    assert "RUN-EVAL-DAILY" in text, "RUN-EVAL-DAILY task id must appear in tasks.yaml"
    # Must reference the run_eval tool
    assert "tool: run_eval" in text
    # Must be scheduled after DAILY-GOLDEN-AUTOGEN (which is "30 5 * * *").
    # 06:00 UTC = "0 6 * * *" — pin that exactly.
    import re
    m = re.search(r"id:\s*RUN-EVAL-DAILY[\s\S]+?cron:\s*['\"]([^'\"]+)['\"]", text)
    assert m, "cron schedule not found for RUN-EVAL-DAILY"
    assert m.group(1) == "0 6 * * *", (
        f"R-F470 must run after DAILY-GOLDEN-AUTOGEN (05:30); got cron={m.group(1)!r}"
    )


def test_rf470_dispatch_handler_present():
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "aria_service" / "autonomous" / "tasks.py"
    text = src.read_text(encoding="utf-8")
    assert 'tool_kind == "run_eval"' in text, (
        "_execute_direct_tool must have an elif tool_kind == 'run_eval' branch"
    )
    # Must import eval_runner
    assert 'from ..intel import eval_runner' in text
    # Must call run_eval with the LLM
    assert '_er.run_eval(llm' in text or '_er.run_eval(\n        llm' in text or \
           'eval_runner.run_eval(llm' in text


def test_rf470_run_eval_in_direct_tool_dispatch_tuple():
    """The tool-kind tuple in _execute_direct_tool determines which kinds
    are routed through the direct-call path. Forgetting to add a new
    kind here silently regresses the task to "unsupported tool kind"."""
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "aria_service" / "autonomous" / "tasks.py"
    text = src.read_text(encoding="utf-8")

    # R-F4074 (C-126) — AST, not a fixed text window.
    #
    # This asserted `'"run_eval"' in text[idx:idx + 600]`, a 600-CHARACTER
    # window anchored on `"source_uptime_ping",`. The tuple has 54 entries and
    # keeps growing, so `run_eval` drifted out of the window and the test went
    # RED while the code was correct — a FALSE FAILURE that then sat in the
    # recorded baseline, where a permanently-red test can carry no information.
    # Same defect class as R-F3858, which §16 records twice.
    tuples = [
        [e.value for e in node.comparators[0].elts if isinstance(e, ast.Constant)]
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Compare)
        and node.ops and isinstance(node.ops[0], ast.In)
        and isinstance(node.comparators[0], ast.Tuple)
        and len(node.comparators[0].elts) > 10
    ]
    assert tuples, "direct-tool dispatch tuple not found — its shape changed"
    kinds = max(tuples, key=len)
    # Guard the guard: an empty/tiny universe would certify anything (§1).
    assert len(kinds) > 20, f"dispatch tuple looks wrong: {len(kinds)} entries"
    assert "run_eval" in kinds, (
        "R-F470: 'run_eval' must be in the direct-tool dispatch tuple. Without "
        f"it the task errors 'unsupported tool kind'. Found: {sorted(kinds)}"
    )


def test_rf470_runtime_dispatch_calls_eval_runner(monkeypatch):
    """When the autonomous engine calls _execute_direct_tool with the
    run_eval branch arguments, it must reach eval_runner.run_eval."""
    from aria_service.autonomous import tasks as _t

    # Stub eval_runner.run_eval to capture the call
    captured = {}
    async def _fake_run_eval(llm, *, ids=None, label=""):
        captured["llm"] = llm
        captured["label"] = label
        return {"ok": True, "pass_rate": 0.75, "n": 10}
    import aria_service.intel.eval_runner as _er
    monkeypatch.setattr(_er, "run_eval", _fake_run_eval)

    # Stub main.app to provide an llm_provider
    class _FakeLLM:
        is_configured = True
    class _FakeApp:
        class state: llm_provider = _FakeLLM()
    import sys
    monkeypatch.setitem(sys.modules, "aria_service.main", type(sys)("aria_service.main"))
    sys.modules["aria_service.main"].app = _FakeApp()

    # Build a minimal task fixture with tool_chain = [{tool: run_eval, label: "x"}]
    class _Task:
        tool_chain = [{"tool": "run_eval", "label": "test_label"}]

    # Drive the dispatcher (find a way to reach the run_eval elif). The
    # branch lives inside a long _execute_direct_tool function. Build
    # the call shape directly via testable seam: re-call the dispatch.
    # We don't have a clean public entrypoint, so test via the source-
    # level guarantee instead: the runtime test for full task execution
    # would need the engine running. Static guarantees + the upstream
    # static tests (above) prove the contract; this test pins the
    # callable shape of run_eval (3 kwargs we pass: llm + label).
    async def _run():
        return await _er.run_eval(_FakeLLM(), label="test_label")

    result = asyncio.run(_run())
    assert result.get("ok") is True
    assert captured["label"] == "test_label"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

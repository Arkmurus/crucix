"""R-F3007 — the async DD path must validate the target UPFRONT (R-F659
entity_type), not return "running" and then silently refuse in the detached
background task.

Live defect: /dd/orchestrate with async_mode:true and a missing entity_type
returned {run_id, status:"running"} to the caller, then the bg task hit the
R-F659 guard, refused, and persisted nothing — the caller polled a run that never
landed (no report, no visible error). A bulletproof path fails loudly here (400).
Follows the repo's async-DD source-contract test pattern (test_rf2250_async_dd).
"""
from __future__ import annotations
from pathlib import Path

from aria_service.intel.dd_orchestrator import _validate_entity_type_for_dd


def _async_branch() -> str:
    src = (Path(__file__).resolve().parent.parent / "routes" / "aria.py").read_text(encoding="utf-8")
    i = src.index('if body.get("async_mode") or body.get("async"):')
    return src[i:i + 2500]


def test_rf3007_async_branch_validates_entity_type_before_marking_running():
    branch = _async_branch()
    assert "_validate_entity_type_for_dd(body)" in branch, \
        "async branch must run the R-F659 entity_type guard upfront"
    v = branch.index("_validate_entity_type_for_dd(body)")
    m = branch.index("mark_dd_running")
    assert v < m, "entity_type validation must run BEFORE mark_dd_running (else still the silent path)"
    # an invalid type must fail loudly with a 400 before any backgrounding
    assert "HTTPException(status_code=400" in branch[v:m], \
        "an invalid target must raise 400 before mark_dd_running / create_task"
    # and it must be strictly before the task is spawned
    assert v < branch.index("asyncio.create_task(_bg_dd())")


def test_rf3007_guard_rejects_missing_type_accepts_company():
    ok, reason = _validate_entity_type_for_dd({"name": "Schroder Investment Management Limited"})
    assert ok is False and "entity_type missing" in reason
    ok2, reason2 = _validate_entity_type_for_dd(
        {"name": "Schroder Investment Management Limited", "type": "company"})
    assert ok2 is True and reason2 == ""
    ok3, _ = _validate_entity_type_for_dd({"name": "X", "type": "unknown"})
    assert ok3 is False  # UNKNOWN is refused, not guessed

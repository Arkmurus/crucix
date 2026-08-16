"""R-F2395 — the capability-test gate must rest on a GENUINELY-EXECUTED green
test, never on a vacuous no-op green.

The hole this closes
────────────────────
`TestRunner.run_isolated` returns a NO-OP `TestResult(all_green=True, passed=0)`
whenever ARIA_CODER_TESTS_ENABLED != "1" (Gate 1), whenever it is rate-limited
(Gate 3), or whenever a test file collects zero tests. STEP 6.5 of `fix_gap`
re-runs the reproduce test on the FIXED code through `run_isolated` and used to
set `_reproduce_fail_to_pass` from a bare `all_green`. So a vacuous green made
`apply_capability_test_gate` trivially satisfied and the autonomous fix
AUTO-DEPLOYED without a single test ever running — exactly "AUTO_DEPLOY guarded
only by the truncation check".

R-F2395 requires GENUINE execution (>=1 test actually ran, none failed) before a
reproduce test can unlock auto-deploy. When tests cannot genuinely run the fix is
NOT rejected — it STAGES for operator review (the capability-test gate forces
stage-only). THE TEST is the gate, never the model's confidence.

Two behavioural capability tests drive the REAL `fix_gap` end-to-end (real
GapDetector reproduce + real TestRunner + real CodebaseReader against a tiny temp
repo with a genuine bug), with the R-F462 auto-deploy gate forced OPEN so the
ONLY thing that can block deploy is the capability-test gate:

  * tests DISABLED  → no genuine capability test → STAGE-ONLY, deploy_improvement
    NEVER called, but the fix is not rejected (it stages).
  * tests ENABLED   → reproduce test genuinely FAIL->PASS → AUTO-DEPLOY allowed,
    deploy_improvement IS called.

Plus a pure truth-table for `capability_test_genuinely_passed`.
"""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.autonomous.self_coder import capability_test_genuinely_passed


# ── PURE: what counts as a genuinely-passed capability test ─────────────────

def test_noop_green_is_not_a_passed_capability_test():
    """The exact shape run_isolated returns when ARIA_CODER_TESTS_ENABLED!=1 /
    rate-limited: green but zero tests ran. MUST NOT count as passed."""
    noop = SimpleNamespace(all_green=True, passed=0, failed=0, errors=0)
    assert capability_test_genuinely_passed(noop) is False


def test_genuine_green_is_a_passed_capability_test():
    genuine = SimpleNamespace(all_green=True, passed=1, failed=0, errors=0)
    assert capability_test_genuinely_passed(genuine) is True


def test_genuine_failure_is_not_passed():
    failed = SimpleNamespace(all_green=False, passed=0, failed=1, errors=0)
    assert capability_test_genuinely_passed(failed) is False


def test_green_with_errors_is_not_passed():
    erred = SimpleNamespace(all_green=True, passed=1, failed=0, errors=1)
    assert capability_test_genuinely_passed(erred) is False


def test_none_result_is_not_passed():
    assert capability_test_genuinely_passed(None) is False


# ── BEHAVIOURAL: drive the real fix_gap with AUTO_DEPLOY forced open ─────────

class _StubRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def _set(self, key, value, *a, **k):
        self.kv[key] = value
    set = _set  # redis-interface alias (avoids the def set( builtin-shadow lint)

    async def setex(self, key, ttl, value):
        self.kv[key] = value

    async def incr(self, key, amount=1):
        v = int(self.kv.get(key, "0")) + amount
        self.kv[key] = str(v)
        return v

    async def expire(self, key, seconds):
        return key in self.kv

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def lrange(self, key, start, end):
        return self.lists.get(key, [])[start:end + 1]

    async def ltrim(self, key, start, end):
        if key in self.lists:
            self.lists[key] = self.lists[key][start:end + 1]

    async def delete(self, key):
        existed = key in self.kv
        self.kv.pop(key, None)
        return existed


_BUGGY = "def add(a, b):\n    return a - b\n"
_FIXED = "def add(a, b):\n    return a + b\n"

_REPRO_TEST = (
    "import importlib.util, pathlib\n"
    "def _load():\n"
    "    p = pathlib.Path('aria_service/intel/calc.py')\n"
    "    spec = importlib.util.spec_from_file_location('calc_under_test', p)\n"
    "    m = importlib.util.module_from_spec(spec)\n"
    "    spec.loader.exec_module(m)\n"
    "    return m\n"
    "def test_add_is_sum():\n"
    "    assert _load().add(2, 3) == 5\n"
)


def _make_temp_repo(root: Path) -> None:
    (root / "aria_service" / "intel").mkdir(parents=True, exist_ok=True)
    (root / "aria_service" / "tests").mkdir(parents=True, exist_ok=True)
    (root / "aria_service" / "intel" / "calc.py").write_text(_BUGGY, encoding="utf-8")
    (root / "aria_service" / "tests" / "test_calc_repro.py").write_text(
        _REPRO_TEST, encoding="utf-8",
    )


def _build_coder(redis, temp_repo: Path, fixed_code: str):
    from aria_service.autonomous.gap_detector import GapDetector
    from aria_service.autonomous.test_runner import TestRunner
    from aria_service.autonomous.codebase_reader import CodebaseReader
    from aria_service.autonomous.self_coder import ARIACoder

    llm = AsyncMock()
    llm.generate_fix_plan = AsyncMock(return_value={
        "title": "Fix calc.add to add instead of subtract",
        "approach": "Replace the subtraction with addition",
        "target_files": ["aria_service/intel/calc.py"],
        "new_files": [],
        "risk_level": "low",
    })
    llm.write_tests = AsyncMock(return_value={"test_code": "", "test_filepath": ""})

    gap_detector = GapDetector(redis, llm=llm)
    test_runner = TestRunner(redis, repo_root=temp_repo)
    codebase = CodebaseReader("http://test", repo_path=temp_repo)

    r_counter = MagicMock()
    r_counter.next = AsyncMock(return_value=23950)

    coder = ARIACoder(
        redis_client=redis,
        aria_service_url="http://test",
        gap_detector=gap_detector,
        llm=llm,
        validator=None,
        codebase=codebase,
        test_runner=test_runner,
        deployer=MagicMock(),
        r_counter=r_counter,
        workspace_base=temp_repo / "_ws",
        brain_hook=None,
    )
    coder._generate_target_code = AsyncMock(return_value=fixed_code)
    # No real 5-minute post-deploy sleep in tests.
    coder._monitor_post_deploy = AsyncMock(return_value=False)
    return coder


def _make_gap():
    from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
    return Gap(
        gap_id="rf2395gate",
        gap_type=GapType.MODULE_BUG,
        severity=GapSeverity.HIGH,
        title="calc.add returns wrong value",
        description="calc.add subtracts instead of adding (add(2,3) should be 5)",
        module="aria_service/intel/calc.py",
    )


async def _run_fix(coder, gap, fake_stage, deploy_mock):
    """Drive fix_gap with EVERY unrelated gate deliberately open.

    R-F4048 (C-107) — these tests were RED for two reasons that had nothing to
    do with the capability-test gate they exist to prove:

      * `ARIA_CODER_ENABLED` is unset in a test environment, so `fix_gap`
        returned `coder_disabled` before reaching any gate. The "never
        autodeploy" case then passed for the WRONG REASON — the coder refused
        outright, so the capability-test gate was never exercised at all.
      * R-F2689 later added an evidence gate (20 fixed + 10 gold) that a test
        scoreboard can never satisfy, so the "allows autodeploy" case could
        never go green.

    Both preconditions are established HERE so each test isolates the one gate
    it names. The evidence is fed through the real decision function's inputs,
    never by disabling it.
    """
    import os as _os
    from aria_service.intel import self_improve as si

    _MATURE_SCOREBOARD = {
        "counts": {"fixed": 25, "gold": 12, "blocked": 0, "claimed": 25},
        "recent": [{"outcome": "fixed"} for _ in range(20)],
    }
    with patch.dict(_os.environ, {"ARIA_CODER_ENABLED": "1"}), \
         patch.object(coder, "get_scoreboard",
                      AsyncMock(return_value=_MATURE_SCOREBOARD)), \
         patch("aria_service.autonomous.safety.can_task_run",
               AsyncMock(return_value=(True, "ok"))), \
         patch("aria_service.intel.self_improve.stage_improvement", fake_stage), \
         patch("aria_service.intel.self_improve.deploy_improvement", deploy_mock), \
         patch.dict(si.CHANGE_TYPES, {"bug_fix": {"auto_deploy": True,
                                                  "description": "Fix a detected bug"}}), \
         patch.object(coder, "_claude_review", AsyncMock(return_value=MagicMock(
             is_flagged=False, is_blocked=False, review_disabled=True,
             verdict=MagicMock(value="APPROVED"), reasons=[],
         ))), \
         patch.object(coder, "_open_review_ticket", AsyncMock()):
        return await coder.fix_gap(gap, operator_initiated=False)


@pytest.mark.asyncio
async def test_tests_disabled_forces_stage_only_never_autodeploy(tmp_path, monkeypatch):
    """GUARDRAIL: with the R-F462 auto-deploy gate OPEN and a CORRECT fix, but
    tests DISABLED (no genuine capability test), the coder must STAGE — it may
    NOT auto-deploy. The fix is not rejected; it is held for operator review."""
    temp_repo = tmp_path / "repo"
    _make_temp_repo(temp_repo)

    monkeypatch.delenv("ARIA_CODER_TESTS_ENABLED", raising=False)  # tests OFF -> no-op green
    monkeypatch.delenv("ARIA_CODER_TESTS_FULL", raising=False)
    monkeypatch.setenv("ARIA_CODER_GOLD_PATH", str(tmp_path / "gold.jsonl"))
    monkeypatch.chdir(temp_repo)

    staged: list[dict] = []

    async def fake_stage(file_path, new_content, change_type, description, reasoning):
        staged.append({"file_path": file_path, "change_type": change_type})
        return {"staged": True, "id": f"staged_{len(staged)}", "error": None}

    deploy_mock = AsyncMock(return_value={"ok": True, "deployed": True})

    redis = _StubRedis()
    coder = _build_coder(redis, temp_repo, _FIXED)
    result = await _run_fix(coder, _make_gap(), fake_stage, deploy_mock)

    assert result.success, f"fix should stage successfully, not fail: {result.failure_reason}"
    assert len(staged) == 1, "the fix must be staged for review"
    assert deploy_mock.call_count == 0, (
        "AUTO-DEPLOY BREACH: tests were disabled (no genuine capability test) yet "
        "deploy_improvement was called — the capability-test gate did not hold."
    )


@pytest.mark.asyncio
async def test_genuine_capability_test_pass_allows_autodeploy(tmp_path, monkeypatch):
    """PROVES THE GATE ISN'T OVER-BLOCKING: with tests ENABLED and the reproduce
    test genuinely going FAIL->PASS on the fixed code, the same OPEN auto-deploy
    gate now DOES auto-deploy — deploy_improvement is called."""
    temp_repo = tmp_path / "repo"
    _make_temp_repo(temp_repo)

    monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", "1")  # tests genuinely run
    monkeypatch.delenv("ARIA_CODER_TESTS_FULL", raising=False)
    monkeypatch.setenv("ARIA_CODER_GOLD_PATH", str(tmp_path / "gold.jsonl"))
    monkeypatch.chdir(temp_repo)

    async def fake_stage(file_path, new_content, change_type, description, reasoning):
        return {"staged": True, "id": "staged_1", "error": None}

    deploy_mock = AsyncMock(return_value={"ok": True, "deployed": True})

    redis = _StubRedis()
    coder = _build_coder(redis, temp_repo, _FIXED)
    result = await _run_fix(coder, _make_gap(), fake_stage, deploy_mock)

    assert result.success, f"fix_gap failed: {result.failure_reason}"
    assert deploy_mock.call_count >= 1, (
        "a genuinely capability-tested fix (reproduce FAIL->PASS) must be allowed "
        "to auto-deploy when the R-F462 gate is open — the gate is over-blocking."
    )

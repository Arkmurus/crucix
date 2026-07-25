"""R-F1685 — BEHAVIORAL capability test: the coder gold flywheel actually
produces a `gold=True` verifiable-reward row when a fix takes its reproduce
test from FAIL (unfixed) -> PASS (fixed).

Why this test exists (the R-F1682 lesson, again)
─────────────────────────────────────────────────
`build_coder_reward_record` was unit-tested green (test_rf1674), yet the
INTEGRATION could never set `reproduce_fail_to_pass=True`, so the flywheel
had never produced a single gold row. Three compounding root causes, all in
the live `fix_gap` path that the isolated unit tests never exercised:

  1. The existing-test reproduce branch never captured `_reproduce_test_path`
     (only the auto-written branch did) — so a known failing test could never
     drive gold.
  2. STEP 5.5 stored the reproduce test's PATH (not its code) in
     `plan.new_tests`; `run_isolated` writes new_tests VALUES as file CONTENTS,
     so the reproduce test became a one-line path string -> collection error
     -> all_green=False -> the fix was rejected before the gold gate.
  3. STEP 6.5 re-verified via `gap_detector.verify_reproduce_test_passes`,
     which ran `pytest <repo_path>` with cwd=repo — i.e. against the UNFIXED
     repo (the fix lives only in the workspace). It always failed, so
     `reproduce_fail_to_pass` stayed False forever.

This test drives the REAL `fix_gap` with a REAL GapDetector (reproduce),
REAL TestRunner (isolated fixed-code run) and REAL CodebaseReader against a
tiny temp repo with a genuine bug. Only the LLM codegen is stubbed (to return
the actual fix deterministically). It asserts:

  * a CORRECT fix lands a `gold=True` row, and
  * an INCORRECT fix (reproduce still fails) lands NO gold row and the fix is
    rejected — the gate stays ungameable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _rf3064_enable_coder_lane():
    """R-F3064: fix_gap now refuses unless ARIA_CODER_ENABLED is truthy (the
    flag previously gated only the loop starter, so the coder ran while the
    operator believed it was off). These tests exercise behaviour DOWNSTREAM of
    that gate, so they enable the lane explicitly. The gate itself is covered by
    test_rf3064_3065_coder_gate_and_profiler_idempotence.py."""
    import os as _os
    _prev = _os.environ.get("ARIA_CODER_ENABLED")
    _os.environ["ARIA_CODER_ENABLED"] = "1"
    try:
        yield
    finally:
        if _prev is None:
            _os.environ.pop("ARIA_CODER_ENABLED", None)
        else:
            _os.environ["ARIA_CODER_ENABLED"] = _prev




# ── minimal in-memory redis stub (async surface used by the coder) ──────────
class _StubRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, *a, **k):
        self.kv[key] = value

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


_BUGGY = "def add(a, b):\n    return a - b\n"     # subtracts — the bug
_FIXED = "def add(a, b):\n    return a + b\n"     # adds — the fix

# A reproduce test that loads the module-under-test BY RELATIVE PATH, so it
# binds to whatever copy is on disk under the current working dir: the buggy
# repo during reproduce, the fixed app_copy during isolated verification.
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
    """Real GapDetector/TestRunner/CodebaseReader; stub LLM returns `fixed_code`."""
    from aria_service.autonomous.gap_detector import GapDetector
    from aria_service.autonomous.test_runner import TestRunner
    from aria_service.autonomous.codebase_reader import CodebaseReader
    from aria_service.autonomous.self_coder import ARIACoder

    llm = MagicMock()
    llm.generate_fix_plan = AsyncMock(return_value={
        "title": "Fix calc.add to add instead of subtract",
        "approach": "Replace the subtraction with addition",
        "target_files": ["aria_service/intel/calc.py"],
        "new_files": [],
        "risk_level": "low",
    })
    # write_tests returns no extra generated test — keep the run deterministic
    # (only the reproduce test executes).
    llm.write_tests = AsyncMock(return_value={"test_code": "", "test_filepath": ""})

    gap_detector = GapDetector(redis, llm=llm)
    test_runner = TestRunner(redis, repo_root=temp_repo)
    codebase = CodebaseReader("http://test", repo_path=temp_repo)

    r_counter = MagicMock()
    r_counter.next = AsyncMock(return_value=16850)

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
    # Patch codegen to return the (correct or incorrect) fix deterministically.
    coder._generate_target_code = AsyncMock(return_value=fixed_code)
    return coder


def _make_gap():
    from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
    return Gap(
        gap_id="rf1685goldtest",
        gap_type=GapType.MODULE_BUG,
        severity=GapSeverity.HIGH,
        title="calc.add returns wrong value",
        description="calc.add subtracts instead of adding (add(2,3) should be 5)",
        module="aria_service/intel/calc.py",
    )


async def _run_fix(coder, gap, gold_path: Path, fake_stage):
    async def fake_can_task_run(*_args, **_kwargs):
        return True, "ok"

    async def fake_deploy_improvement(*_args, **_kwargs):
        return {"success": True}

    async def fake_claude_review(_plan):
        return MagicMock(
            is_flagged=False,
            is_blocked=False,
            review_disabled=True,
            verdict=MagicMock(value="APPROVED"),
            reasons=[],
        )

    async def fake_open_review_ticket(*_args, **_kwargs):
        return None

    with patch("aria_service.autonomous.safety.can_task_run",
               fake_can_task_run), \
         patch("aria_service.intel.self_improve.stage_improvement", fake_stage), \
         patch("aria_service.intel.self_improve.deploy_improvement",
               fake_deploy_improvement), \
         patch.object(coder, "_claude_review", fake_claude_review), \
         patch.object(coder, "_open_review_ticket", fake_open_review_ticket):
        return await coder.fix_gap(gap, operator_initiated=False)


def _gold_rows(gold_path: Path) -> list[dict]:
    if not gold_path.exists():
        return []
    return [json.loads(ln) for ln in gold_path.read_text(encoding="utf-8").splitlines() if ln.strip()]


@pytest.mark.asyncio
async def test_correct_fix_produces_gold_row(tmp_path, monkeypatch):
    """CAPABILITY: a real fix whose reproduce test goes FAIL->PASS lands a
    gold=True verifiable-reward row. This is the first-gold milestone proof."""
    temp_repo = tmp_path / "repo"
    _make_temp_repo(temp_repo)
    gold_path = tmp_path / "gold.jsonl"

    monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", "1")   # tests genuinely run
    monkeypatch.delenv("ARIA_CODER_TESTS_FULL", raising=False)  # safe mode
    monkeypatch.setenv("ARIA_CODER_GOLD_PATH", str(gold_path))
    monkeypatch.delenv("ARIA_SELF_IMPROVE_AUTO_DEPLOY", raising=False)  # stage only
    # reproduce_symptom + _find_test_for_module use RELATIVE paths -> chdir to
    # the buggy repo so the unfixed run sees the bug.
    monkeypatch.chdir(temp_repo)

    staged: list[dict] = []

    async def fake_stage(file_path, new_content, change_type, description, reasoning):
        staged.append({"file_path": file_path, "change_type": change_type})
        return {"staged": True, "id": f"staged_{len(staged)}", "error": None}

    redis = _StubRedis()
    coder = _build_coder(redis, temp_repo, _FIXED)
    result = await _run_fix(coder, _make_gap(), gold_path, fake_stage)

    assert result.success, f"fix_gap failed: {result.failure_reason}"
    assert len(staged) == 1 and staged[0]["change_type"] == "bug_fix"

    rows = _gold_rows(gold_path)
    assert len(rows) == 1, f"expected exactly one reward row, got {rows}"
    rec = rows[0]
    assert rec["gold"] is True, f"row is not gold: {rec}"
    rw = rec["reward"]
    assert rw["tests_ran"] is True
    assert rw["reproduce_fail_to_pass"] is True
    assert rw["function_preserved"] is True


@pytest.mark.asyncio
async def test_incorrect_fix_produces_no_gold(tmp_path, monkeypatch):
    """UNGAMEABLE: a fix that does NOT resolve the bug leaves the reproduce
    test RED on fixed code -> the fix is rejected and NO gold row is written."""
    temp_repo = tmp_path / "repo"
    _make_temp_repo(temp_repo)
    gold_path = tmp_path / "gold.jsonl"

    monkeypatch.setenv("ARIA_CODER_TESTS_ENABLED", "1")
    monkeypatch.delenv("ARIA_CODER_TESTS_FULL", raising=False)
    monkeypatch.setenv("ARIA_CODER_GOLD_PATH", str(gold_path))
    monkeypatch.delenv("ARIA_SELF_IMPROVE_AUTO_DEPLOY", raising=False)
    monkeypatch.chdir(temp_repo)

    async def fake_stage(file_path, new_content, change_type, description, reasoning):
        return {"staged": True, "id": "x", "error": None}

    redis = _StubRedis()
    # "Fix" that still multiplies (wrong) — reproduce test still fails on fixed code
    coder = _build_coder(redis, temp_repo, "def add(a, b):\n    return a * b\n")
    result = await _run_fix(coder, _make_gap(), gold_path, fake_stage)

    assert not result.success, "a non-resolving fix must be rejected"
    assert _gold_rows(gold_path) == [], "no gold row may be written for a bad fix"

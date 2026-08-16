"""R-F2432 — the truncation/symbol-preservation guard is the BACKSTOP that stops
a truncating fixer from auto-deploying, proven END-TO-END through the real
`fix_gap` pipeline (not just the `stage_improvement` unit).

Why this test exists
────────────────────
§21c is binding: "Do NOT flip ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 until the fixer
reliably emits COMPLETE, non-truncating fixes." The live 2026-05-26 incident:
staged "fixes" were syntactically-valid full-file STUBS (researcher.py 4087→164,
routes/aria.py 19443→208, neural_memory.py 1447→3) that would have WIPED core
modules had they auto-deployed. R-F904 (50%-line guard) + R-F1285/R-F1567 (AST
symbol-preservation) block them at stage AND deploy.

Existing coverage (test_rf903_904_stage_guards.py, test_rf1285_*) exercises the
guard at the `stage_improvement`/`deploy_improvement` UNIT level only. Nothing
proved that when the FIXER LLM emits a truncated stub, the END-TO-END coder
pipeline (`fix_gap` → `_stage_or_deploy` → `stage_improvement`) refuses to stage
or deploy it. This test closes that gap by driving the REAL `fix_gap` with every
OTHER gate forced OPEN — R-F462 auto-deploy ON, the reproduce/capability gate
GENUINELY satisfied, Claude review APPROVED — so the ONLY thing that can stop a
truncated stub from reaching disk is the truncation guard. If the guard ever
regresses, case 1 auto-deploys a module-wiping stub and this test goes red.

Case 2 is the anti-over-block control: an identical pipeline with a COMPLETE fix
(all symbols preserved, no shrinkage) DOES auto-deploy — proving the guard blocks
only destructive shrinkage, never legitimate whole-file fixes.
"""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# A 100-line module with several top-level public functions, so BOTH the
# 50%-line guard (R-F904) and the symbol-preservation guard (R-F1285/R-F1567)
# have teeth against a stub.
def _make_module_src() -> str:
    lines = ['"""A real module the coder might touch."""', ""]
    for name in ("alpha", "beta", "gamma", "delta", "epsilon"):
        lines.append(f"def {name}(x):")
        lines.append(f"    # {name} does work")
        lines.append(f"    y = x + 1")
        lines.append(f"    return y")
        lines.append("")
    # pad out to ~100 lines of real content so the file is "substantial"
    for i in range(100 - len(lines)):
        lines.append(f"CONST_{i} = {i}")
    return "\n".join(lines) + "\n"


_MODULE = "aria_service/intel/knowledge.py"

# Truncated stub: syntactically valid, 1 line, drops EVERY symbol — the exact
# shape of the live destructive stubs. Must be blocked.
_TRUNCATED_STUB = "x = 1\n"

_REPRO_TEST = (
    "def test_probe():\n"
    "    assert True\n"
)


class _StubRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def _set(self, key, value, *a, **k):
        self.kv[key] = value
    set = _set

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


class _FakeSIRedis:
    """Minimal get_json/set_json for self_improve's staged-queue store."""
    def __init__(self) -> None:
        self.store: dict = {}

    async def get_json(self, key, *a, **kw):
        return self.store.get(key)

    async def set_json(self, key, value, *a, **kw):
        self.store[key] = value
        return True


def _make_temp_repo(root: Path) -> str:
    (root / "aria_service" / "intel").mkdir(parents=True, exist_ok=True)
    (root / "aria_service" / "tests").mkdir(parents=True, exist_ok=True)
    (root / _MODULE).write_text(_make_module_src(), encoding="utf-8")
    repro_rel = "aria_service/tests/test_repro_probe.py"
    (root / repro_rel).write_text(_REPRO_TEST, encoding="utf-8")
    return repro_rel


def _build_coder(redis, temp_repo: Path, fix_output: str):
    from aria_service.autonomous.gap_detector import GapDetector
    from aria_service.autonomous.test_runner import TestRunner
    from aria_service.autonomous.codebase_reader import CodebaseReader
    from aria_service.autonomous.self_coder import ARIACoder

    llm = AsyncMock()
    llm.generate_fix_plan = AsyncMock(return_value={
        "title": "Fix knowledge module bug",
        "approach": "Correct the logic",
        "target_files": [_MODULE],
        "new_files": [],
        "risk_level": "low",
    })
    llm.write_tests = AsyncMock(return_value={"test_code": "", "test_filepath": ""})

    gap_detector = GapDetector(redis, llm=llm)
    test_runner = TestRunner(redis, repo_root=temp_repo)
    codebase = CodebaseReader("http://test", repo_path=temp_repo)

    r_counter = MagicMock()
    r_counter.next = AsyncMock(return_value=24320)

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
    # The fixer's output (what the LLM "wrote"): stub or complete.
    coder._generate_target_code = AsyncMock(return_value=fix_output)
    coder._monitor_post_deploy = AsyncMock(return_value=False)
    # Force the reproduce/capability gate GENUINELY satisfied so it cannot be
    # the reason a fix is blocked — isolating the truncation guard.
    coder.gap_detector.reproduce_symptom = AsyncMock(return_value=(
        True, "symptom reproduced: test aria_service/tests/test_repro_probe.py failed (exit=1)",
    ))
    # Both the healing run (STEP 6) and the reproduce re-run (STEP 6.5) return a
    # GENUINE green (passed=1) so capability_test_genuinely_passed() is True and
    # reproduce_fail_to_pass is set — the auto-deploy path is fully unlocked
    # except for the truncation guard.
    genuine = SimpleNamespace(
        all_green=True, passed=1, failed=0, errors=0, attempts=1,
        failure_summary="", duration_s=0.01, output_tail="", safe_mode=True,
    )
    coder.test_runner.run_isolated = AsyncMock(return_value=genuine)
    return coder


def _make_gap():
    from aria_service.autonomous.gap_detector import Gap, GapType, GapSeverity
    return Gap(
        gap_id="rf2432trunc",
        gap_type=GapType.MODULE_BUG,   # → bug_fix → auto-deployable when gate open
        severity=GapSeverity.HIGH,
        title="knowledge module returns wrong value",
        description="knowledge.alpha computes the wrong result",
        module=_MODULE,
    )


async def _run_fix(coder, gap, si_module, deploy_mock):
    """Drive the REAL fix_gap with R-F462 auto-deploy FORCED OPEN + Claude
    APPROVED. stage_improvement stays REAL (its truncation guard is under test);
    deploy_improvement is mocked so we can assert whether it was reached."""
    return await _run_with_patches(coder, gap, si_module, deploy_mock)


async def _run_with_patches(coder, gap, si_module, deploy_mock):
    # R-F4048 (C-107) — open the two gates that are NOT under test here, so the
    # truncation guard is what the assertions actually measure:
    #   * ARIA_CODER_ENABLED is unset in a test env, so fix_gap returned
    #     `coder_disabled` before reaching the guard at all;
    #   * R-F2689's evidence gate (20 fixed + 10 gold) can never be satisfied by
    #     a test scoreboard, so the "complete fix autodeploys" case could never
    #     go green.
    # The evidence is supplied through the real gate's INPUTS, never by
    # disabling it — a truncating fixer must still be blocked below.
    import os as _os

    _MATURE_SCOREBOARD = {
        "counts": {"fixed": 25, "gold": 12, "blocked": 0, "claimed": 25},
        "recent": [{"outcome": "fixed"} for _ in range(20)],
    }
    with patch.dict(_os.environ, {"ARIA_CODER_ENABLED": "1"}), \
         patch.object(coder, "get_scoreboard",
                      AsyncMock(return_value=_MATURE_SCOREBOARD)), \
         patch("aria_service.autonomous.safety.can_task_run",
               AsyncMock(return_value=(True, "ok"))), \
         patch("aria_service.intel.self_improve.deploy_improvement", deploy_mock), \
         patch.dict(si_module.CHANGE_TYPES,
                    {"bug_fix": {"auto_deploy": True, "description": "Fix a detected bug"}}), \
         patch.object(coder, "_claude_review", AsyncMock(return_value=MagicMock(
             is_flagged=False, is_blocked=False, review_disabled=True,
             verdict=MagicMock(value="APPROVED"), reasons=[],
         ))), \
         patch.object(coder, "_open_review_ticket", AsyncMock()):
        return await coder.fix_gap(gap, operator_initiated=False)


def _wire_self_improve(monkeypatch, temp_repo: Path):
    import aria_service.intel.self_improve as si

    async def _noop_log(*a, **kw):
        return None

    monkeypatch.setattr(si, "rs", _FakeSIRedis())
    monkeypatch.setattr(si, "_root", temp_repo)
    monkeypatch.setattr(si, "_log_improvement", _noop_log)
    return si


@pytest.mark.asyncio
async def test_truncating_fixer_cannot_autodeploy_truncation_guard_backstops(
    tmp_path, monkeypatch,
):
    """§21c PROOF: with EVERY other gate forced open (auto-deploy ON, reproduce
    FAIL->PASS genuinely satisfied, Claude APPROVED), a truncated full-file stub
    from the fixer MUST NOT stage or deploy — the truncation guard backstops it.
    The real module on disk stays intact."""
    temp_repo = tmp_path / "repo"
    _make_temp_repo(temp_repo)
    monkeypatch.setenv("ARIA_CODER_REPRO_DIR", str(tmp_path / "repro"))
    monkeypatch.setenv("ARIA_CODER_GOLD_PATH", str(tmp_path / "gold.jsonl"))
    monkeypatch.chdir(temp_repo)

    si = _wire_self_improve(monkeypatch, temp_repo)
    deploy_mock = AsyncMock(return_value={"ok": True, "deployed": True})

    redis = _StubRedis()
    coder = _build_coder(redis, temp_repo, _TRUNCATED_STUB)
    result = await _run_fix(coder, _make_gap(), si, deploy_mock)

    # The fix FAILS — it was never staged or deployed.
    assert result.success is False, (
        "TRUNCATION BREACH: a module-wiping stub was accepted end-to-end. "
        f"failure_reason={result.failure_reason!r}"
    )
    # And it failed for the RIGHT reason (the truncation guard), not an unrelated
    # syntax/schema rejection.
    assert "truncat" in (result.failure_reason or "").lower(), (
        f"expected the truncation guard to block, got: {result.failure_reason!r}"
    )
    # deploy_improvement must NEVER have been reached.
    assert deploy_mock.call_count == 0, (
        "AUTO-DEPLOY BREACH: deploy_improvement was called for a truncated stub — "
        "the guard did not backstop the auto-deploy path."
    )
    # Nothing entered the staged queue.
    assert not si.rs.store.get(si.STAGED_KEY), "a truncated stub reached the staged queue"
    # The real module on disk is byte-for-byte intact.
    assert (temp_repo / _MODULE).read_text(encoding="utf-8") == _make_module_src()


@pytest.mark.asyncio
async def test_complete_fix_autodeploys_when_all_gates_pass(tmp_path, monkeypatch):
    """ANTI-OVER-BLOCK CONTROL: the SAME pipeline with a COMPLETE fix (all symbols
    preserved, no shrinkage) DOES auto-deploy — deploy_improvement is called.
    Proves the truncation guard blocks only destructive shrinkage, never a real
    whole-file fix, so it can't silently starve the loop."""
    temp_repo = tmp_path / "repo"
    _make_temp_repo(temp_repo)
    monkeypatch.setenv("ARIA_CODER_REPRO_DIR", str(tmp_path / "repro"))
    monkeypatch.setenv("ARIA_CODER_GOLD_PATH", str(tmp_path / "gold.jsonl"))
    monkeypatch.chdir(temp_repo)

    # A complete fix: identical structure (all 5 functions kept) with one body
    # line changed. Same line count, no dropped symbol → guard passes.
    complete_fix = _make_module_src().replace("y = x + 1", "y = x + 2", 1)
    assert complete_fix != _make_module_src()

    si = _wire_self_improve(monkeypatch, temp_repo)
    deploy_mock = AsyncMock(return_value={"ok": True, "deployed": True})

    redis = _StubRedis()
    coder = _build_coder(redis, temp_repo, complete_fix)
    result = await _run_fix(coder, _make_gap(), si, deploy_mock)

    assert result.success is True, f"complete fix should succeed: {result.failure_reason!r}"
    assert deploy_mock.call_count >= 1, (
        "OVER-BLOCK: a complete, symbol-preserving fix with all gates open failed "
        "to auto-deploy — the truncation guard is rejecting legitimate fixes."
    )

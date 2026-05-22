"""R-F802 — Tests for the autonomous coder foundation modules.

Covers:
  - ConstitutionalValidator: protected-file rejection, dangerous imports,
    weakening patterns, eval/exec detection, clean-code pass-through.
  - DiffValidator: critical-line-removal detection.
  - GapDetector: dedup, mark_attempted suppression, severity sort,
    extractor isolation (failures don't crash the scan).
  - CodebaseReader: path-escape refusal, missing-file safe read.
  - RNumberCounter: atomic increment via mocked Redis.

These are unit tests — no live Redis, no real LLM calls. The
ConstitutionalValidator tests are the critical-safety regression guard
per CLAUDE.md §5: capability test = "if these tests pass, the safety
membrane is intact."

This file uses `asyncio.run()` directly (project convention — see
`test_chain_correlator.py:7`) so the suite runs without pytest-asyncio.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _run(coro):
    """Run an async coroutine — pytest-asyncio is not available."""
    return asyncio.run(coro)

from aria_service.autonomous.codebase_reader import CodebaseReader
from aria_service.autonomous.constitutional_validator import (
    DANGEROUS_IMPORTS,
    PROTECTED_FILES,
    ConstitutionalValidator,
    DiffValidator,
)
from aria_service.autonomous.gap_detector import (
    Gap,
    GapDetector,
    GapSeverity,
    GapType,
    _gap_id_for,
)
from aria_service.autonomous.r_counter import RNumberCounter


# ════════════════════════════════════════════════════════════════════════════
# ConstitutionalValidator
# ════════════════════════════════════════════════════════════════════════════

class TestConstitutionalValidator:
    def setup_method(self) -> None:
        self.v = ConstitutionalValidator()

    def test_protected_file_rejected_fatal(self) -> None:
        # Pick any protected file from the constant set
        protected = next(iter(PROTECTED_FILES))
        result = self.v.validate("print('hello')", protected)
        assert not result.passed
        assert result.risk_score >= 0.99
        assert any("FATAL" in v for v in result.violations)

    def test_r_f462_protected_files_present(self) -> None:
        """R-F462 (2026-05-14) listed these as protected. Regression guard."""
        for f in [
            "server.mjs", ".env", "Dockerfile", "package.json",
            "lib/auth/users.mjs", "lib/auth/email.mjs",
        ]:
            assert f in PROTECTED_FILES, f"{f} should be in PROTECTED_FILES"

    def test_self_improve_protected(self) -> None:
        """ARIA must not autonomously modify the existing staging engine."""
        assert "aria_service/intel/self_improve.py" in PROTECTED_FILES

    def test_clean_code_passes(self) -> None:
        code = (
            "async def add(a: int, b: int) -> int:\n"
            "    '''Trivial pure function.'''\n"
            "    return a + b\n"
        )
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert result.passed, result.violations

    def test_eval_blocked(self) -> None:
        code = "result = eval(user_input)\n"
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed
        assert any("eval" in v.lower() for v in result.violations)

    def test_exec_blocked(self) -> None:
        code = "exec(payload, globals())\n"
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed

    def test_subprocess_run_blocked(self) -> None:
        code = (
            "import subprocess\n"
            "subprocess.run(['echo', 'hi'])\n"
        )
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed
        # Should flag both the dangerous import AND the shell-execution call
        joined = "\n".join(result.violations).lower()
        assert "subprocess" in joined

    def test_os_system_blocked(self) -> None:
        code = (
            "import os\n"
            "os.system('rm -rf /')\n"
        )
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed
        assert any("os.system" in v.lower() or "system" in v.lower()
                   for v in result.violations)

    def test_dangerous_pickle_import_blocked(self) -> None:
        code = "import pickle\nx = pickle.loads(b'...')\n"
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed

    def test_weakening_pattern_skip_verification(self) -> None:
        code = (
            "def process(payload):\n"
            "    skip_verification = True\n"
            "    return payload\n"
        )
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed
        assert any("verification" in v.lower() for v in result.violations)

    def test_weakening_pattern_bypass_guards(self) -> None:
        code = "bypass_guards = True\n"
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed

    def test_weakening_pattern_jwt_verify_false(self) -> None:
        code = "decoded = jwt.decode(token, key, verify=False)\n"
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed

    def test_weakening_pattern_protected_files_cleared(self) -> None:
        # Attempt to wipe PROTECTED_FILES
        code = "PROTECTED_FILES = frozenset()\n"
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed

    def test_guard_attribute_deletion_blocked(self) -> None:
        code = (
            "class X:\n"
            "    pass\n"
            "x = X()\n"
            "del x.constitution_guard\n"
        )
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed
        assert any("guard" in v.lower() for v in result.violations)

    def test_syntax_error_blocks(self) -> None:
        code = "def broken(:\n  return\n"  # invalid syntax
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        assert not result.passed
        assert any("syntax" in v.lower() for v in result.violations)

    def test_setattr_warns_does_not_block(self) -> None:
        code = (
            "def f(obj):\n"
            "    setattr(obj, 'x', 1)\n"
        )
        result = self.v.validate(code, "aria_service/intel/knowledge.py")
        # setattr is a warning, not a violation
        assert result.passed
        assert any("setattr" in w.lower() for w in result.warnings)

    def test_dangerous_imports_constant_contains_subprocess(self) -> None:
        # Regression guard so a future refactor doesn't accidentally
        # drop subprocess from the dangerous set
        assert "subprocess" in DANGEROUS_IMPORTS


# ════════════════════════════════════════════════════════════════════════════
# DiffValidator
# ════════════════════════════════════════════════════════════════════════════

class TestDiffValidator:
    def setup_method(self) -> None:
        self.v = DiffValidator()

    def test_constitution_enforce_removal_blocked(self) -> None:
        diff = (
            "--- a/aria_engine.py\n"
            "+++ b/aria_engine.py\n"
            "@@ -1,3 +1,2 @@\n"
            " def x():\n"
            "-    constitution.enforce(response)\n"
            "     return response\n"
        )
        r = self.v.validate_diff(diff)
        assert not r.passed
        assert any("constitution.enforce" in v for v in r.violations)

    def test_brain_hook_absorb_removal_blocked(self) -> None:
        diff = (
            "-    await brain_hook.absorb(text)\n"
            "+    pass  # disabled\n"
        )
        r = self.v.validate_diff(diff)
        assert not r.passed
        assert any("brain_hook.absorb" in v for v in r.violations)

    def test_clean_diff_passes(self) -> None:
        diff = (
            "-    return None\n"
            "+    return value\n"
        )
        r = self.v.validate_diff(diff)
        assert r.passed


# ════════════════════════════════════════════════════════════════════════════
# GapDetector
# ════════════════════════════════════════════════════════════════════════════

class _StubRedis:
    """In-memory async stub matching the slice of the redis.asyncio surface
    that GapDetector + R-counter use."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str) -> None:
        self.kv[key] = value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        # TTL ignored — tests don't care about expiry
        self.kv[key] = value

    async def incr(self, key: str) -> int:
        v = int(self.kv.get(key, "0")) + 1
        self.kv[key] = str(v)
        return v

    async def lrange(self, key: str, start: int, end: int) -> list:
        return self.lists.get(key, [])[start:end + 1]

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        if key in self.lists:
            self.lists[key] = self.lists[key][start:end + 1]


class TestGapDedup:
    def test_gap_id_stable_for_same_inputs(self) -> None:
        a = _gap_id_for("module_bug", "intel/foo.py", "ValueError: bad input")
        b = _gap_id_for("module_bug", "intel/foo.py", "ValueError: bad input")
        assert a == b

    def test_gap_id_differs_for_different_modules(self) -> None:
        a = _gap_id_for("module_bug", "intel/foo.py", "X")
        b = _gap_id_for("module_bug", "intel/bar.py", "X")
        assert a != b


class TestGapAutonomyLevel:
    def test_module_bug_is_auto_fixable(self) -> None:
        g = Gap(
            gap_id="x", gap_type=GapType.MODULE_BUG,
            severity=GapSeverity.HIGH,
            title="t", description="d", module="m",
        )
        assert g.auto_fixable
        assert not g.requires_wa_approval
        assert not g.requires_hard_gate

    def test_hallucination_requires_wa_notify(self) -> None:
        g = Gap(
            gap_id="x", gap_type=GapType.HALLUCINATION,
            severity=GapSeverity.CRITICAL,
            title="t", description="d", module="m",
        )
        assert g.auto_fixable
        assert g.requires_wa_approval

    def test_missing_capability_not_auto_fixable(self) -> None:
        g = Gap(
            gap_id="x", gap_type=GapType.MISSING_CAPABILITY,
            severity=GapSeverity.HIGH,
            title="t", description="d", module="m",
        )
        assert not g.auto_fixable  # operator decides via WA
        assert g.requires_wa_approval


class TestGapDetectorScan:
    def test_mark_attempted_suppresses_next_scan(self) -> None:
        async def body() -> None:
            redis = _StubRedis()
            d = GapDetector(redis)
            await d.mark_attempted("gap_X")
            assert await d._is_recently_attempted("gap_X")
            assert not await d._is_recently_attempted("gap_Y")
        _run(body())

    def test_mark_fixed_records_r_number(self) -> None:
        async def body() -> None:
            redis = _StubRedis()
            d = GapDetector(redis)
            await d.mark_fixed("gap_X", 802)
            stored = await redis.get(f"{d.FIXED_KEY_PREFIX}gap_X")
            assert stored == "802"
        _run(body())

    def test_scan_returns_empty_on_no_data(self) -> None:
        async def body() -> None:
            redis = _StubRedis()
            d = GapDetector(redis)
            gaps = await d.scan()
            assert gaps == []
        _run(body())

    def test_scan_handles_extractor_failure(self) -> None:
        """A broken extractor must not crash the scan loop."""
        async def body() -> None:
            redis = _StubRedis()
            d = GapDetector(redis)
            broken = MagicMock()
            broken.extract = AsyncMock(side_effect=RuntimeError("boom"))
            broken.__class__.__name__ = "BrokenExtractor"
            d.extractors = [broken]
            gaps = await d.scan()
            assert gaps == []
        _run(body())


# ════════════════════════════════════════════════════════════════════════════
# CodebaseReader
# ════════════════════════════════════════════════════════════════════════════

class TestCodebaseReader:
    def test_read_missing_file_returns_empty(self, tmp_path: Path) -> None:
        r = CodebaseReader("http://x", repo_path=tmp_path)
        assert r.read("nonexistent.py") == ""

    def test_read_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("CONTENT", encoding="utf-8")
        r = CodebaseReader("http://x", repo_path=tmp_path)
        assert r.read("x.py") == "CONTENT"

    def test_write_to_workspace_refuses_absolute(self, tmp_path: Path) -> None:
        r = CodebaseReader("http://x", repo_path=tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(ValueError, match="absolute"):
            r.write_to_workspace(ws, "/etc/passwd", "x")

    def test_write_to_workspace_refuses_path_escape(
        self, tmp_path: Path,
    ) -> None:
        r = CodebaseReader("http://x", repo_path=tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(ValueError, match="outside workspace"):
            r.write_to_workspace(ws, "../../etc/passwd", "x")

    def test_write_to_workspace_creates_nested_dirs(
        self, tmp_path: Path,
    ) -> None:
        r = CodebaseReader("http://x", repo_path=tmp_path)
        ws = tmp_path / "ws"
        ws.mkdir()
        target = r.write_to_workspace(ws, "a/b/c.py", "X")
        assert target.read_text(encoding="utf-8") == "X"
        assert target == (ws / "a" / "b" / "c.py").resolve()


# ════════════════════════════════════════════════════════════════════════════
# RNumberCounter
# ════════════════════════════════════════════════════════════════════════════

class TestRNumberCounter:
    def test_next_increments(self) -> None:
        async def body() -> None:
            redis = _StubRedis()
            c = RNumberCounter(redis)
            assert await c.next() == 1
            assert await c.next() == 2
            assert await c.next() == 3
        _run(body())

    def test_current_reads_without_increment(self) -> None:
        async def body() -> None:
            redis = _StubRedis()
            c = RNumberCounter(redis)
            await c.next()
            await c.next()
            assert await c.current() == 2
            assert await c.current() == 2
        _run(body())

    def test_seed_sets_value(self) -> None:
        async def body() -> None:
            redis = _StubRedis()
            c = RNumberCounter(redis)
            await c.seed(800)
            assert await c.current() == 800
            assert await c.next() == 801
        _run(body())


# ════════════════════════════════════════════════════════════════════════════
# Cross-module smoke test — capability test per CLAUDE.md §5
# ════════════════════════════════════════════════════════════════════════════

def test_capability_validator_blocks_self_improve_modification() -> None:
    """Capability test: the constitutional validator MUST block any attempt
    to modify the existing R-F462 staging engine. This is the symptom that
    R-F802 protects against — a future ARIA-Coder writing to self_improve.py
    would silently disarm the operator's audit gate."""
    v = ConstitutionalValidator()
    payload = "MODIFIABLE_FILES = set()\nPROTECTED_FILES = set()\n"
    result = v.validate(payload, "aria_service/intel/self_improve.py")
    assert not result.passed
    assert result.risk_score >= 0.99
    # FATAL not just BLOCK
    assert any("FATAL" in v for v in result.violations)


def test_capability_validator_blocks_protected_function_gutting() -> None:
    """Capability test: gutting a protected function (replacing body with
    pass/return) in a guard file must be blocked."""
    v = ConstitutionalValidator()
    code = (
        "def verify_sources(claims):\n"
        "    pass\n"
        "def check_constitution(response):\n"
        "    pass\n"
    )
    # Target a guard-named file so the protected-function check runs
    result = v.validate(code, "aria_service/intel/some_guard.py")
    assert not result.passed

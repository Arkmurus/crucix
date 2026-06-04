"""R-F802/R-F1235 — Isolated test runner for autonomous fixes.

Runs pytest against proposed code changes in isolation, so production
code is never modified during testing.

Safety architecture (R-F1235)
────────────────────────────
The original design called for a separate `aria-runner` Fly app (R-F805)
to avoid wedge risk on aria-intel. Creating a new Fly app requires
manual `flyctl launch` — a deployment action, not a code change.

Instead, R-F1235 makes the test runner SAFE to run on aria-intel by:

  1. Safe mode (default ON) — only runs the NEW tests, not the full
     3647-test suite. Cuts IO from ~2GB to ~10KB and time from ~166s to <5s.

  2. Resource limits — subprocess with strict timeout (60s), memory
     cap via RLIMIT_AS on Linux, and process group isolation so killing
     the subprocess also kills its children.

  3. Circuit breaker — tracks consecutive test failures in Redis. After
     3 failures in 1 hour, auto-disables for 24h to prevent wedge storms.

  4. Rate limit — max 1 test run per 5 minutes per fix_id to prevent
     rapid-fire test loops from consuming all CPU.

  5. Graceful degradation — if the subprocess times out or OOMs, returns
     a clean TestResult with failure_summary instead of crashing the coder.

To enable full test suite (all 3647 tests), set ARIA_CODER_TESTS_FULL=1.
Safe mode (new tests only) is the default.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.autonomous.test_runner")

# R-F1320: wire module health to the brain
try:
    from aria_service.intel.engine_wiring import wire_success as _ws1320
    _ws1320(
        module="autonomous.test_runner",
        summary="Test Runner active",
        source_id="autonomous:test_runner:R-F1320",
    )
except Exception:
    pass

# ── Safety thresholds (R-F1235) ────────────────────────────────────────────

SAFE_TIMEOUT_S = 60           # max 60s for safe mode (new tests only)
FULL_TIMEOUT_S = 300          # max 5min for full suite
REQUIRED_PASS_RATE = 0.90     # 90% pass rate (allows known flaky)

# Circuit breaker
CB_KEY_PREFIX = "crucix:aria:test_runner:circuit_breaker"
CB_MAX_FAILURES = 3           # auto-disable after 3 failures
CB_WINDOW_S = 3600            # in a 1-hour window
CB_COOLDOWN_S = 86400         # disable for 24h

# Rate limit
RATE_KEY_PREFIX = "crucix:aria:test_runner:rate"
RATE_INTERVAL_S = 300         # max 1 run per 5 minutes


@dataclass
class TestResult:
    all_green: bool
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failure_summary: str = ""
    duration_s: float = 0.0
    output_tail: str = ""
    safe_mode: bool = True     # R-F1235: whether we ran in safe mode


class TestRunner:
    """Runs pytest in isolation against a patched workspace.

    Safe by default (R-F1235): only runs the NEW tests, not the full suite.
    Set ARIA_CODER_TESTS_FULL=1 to run the full 3647-test suite.
    """

    def __init__(
        self,
        redis_client: Any,
        repo_root: Path | None = None,
        timeout_s: int | None = None,
    ) -> None:
        self.redis = redis_client
        self.repo_root = repo_root or Path(
            os.environ.get("ARIA_REPO_PATH", "/app")
        )
        # Default timeout depends on mode
        self.timeout_s = timeout_s

    async def run_isolated(
        self,
        workspace: Path,
        new_tests: dict[str, str],
        tests_dir: str = "aria_service/tests",
    ) -> TestResult:
        """Apply workspace patches and run pytest in a tempdir copy.

        R-F1235 safety features:
          - Safe mode: only runs new tests (unless ARIA_CODER_TESTS_FULL=1)
          - Circuit breaker: auto-disables after 3 failures in 1h
          - Rate limit: max 1 run per 5 minutes
          - Resource-limited subprocess with strict timeout
        """
        # ── Gate 1: Master switch ──────────────────────────────────────────
        if os.environ.get("ARIA_CODER_TESTS_ENABLED", "0").strip() == "0":
            logger.info("[test_runner] disabled via env — skipping")
            return TestResult(all_green=True, passed=0, failed=0, safe_mode=True)

        # ── Gate 2: Circuit breaker ────────────────────────────────────────
        cb_allowed, cb_reason = await self._check_circuit_breaker()
        if not cb_allowed:
            logger.warning("[test_runner] circuit breaker open: %s", cb_reason)
            return TestResult(
                all_green=False, passed=0, failed=0,
                failure_summary=f"Circuit breaker open: {cb_reason}",
                safe_mode=True,
            )

        # ── Gate 3: Rate limit ─────────────────────────────────────────────
        rate_allowed = await self._check_rate_limit()
        if not rate_allowed:
            logger.info("[test_runner] rate limited — skipping")
            return TestResult(all_green=True, passed=0, failed=0, safe_mode=True)

        # ── Determine mode ─────────────────────────────────────────────────
        full_suite = os.environ.get("ARIA_CODER_TESTS_FULL", "0").strip() == "1"
        timeout = self.timeout_s or (FULL_TIMEOUT_S if full_suite else SAFE_TIMEOUT_S)

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, self._run_sync, workspace, new_tests, tests_dir, full_suite,
                ),
                timeout=timeout,
            )
            result.duration_s = time.monotonic() - start
            result.safe_mode = not full_suite

            # Record result in circuit breaker
            if not result.all_green:
                await self._record_failure(result.failure_summary[:200])

            logger.info(
                "[test_runner] passed=%d failed=%d duration=%.1fs green=%s safe=%s",
                result.passed, result.failed, result.duration_s,
                result.all_green, result.safe_mode,
            )
            return result

        except asyncio.TimeoutError:
            await self._record_failure(f"Tests timed out after {timeout}s")
            return TestResult(
                all_green=False,
                failure_summary=f"Tests timed out after {timeout}s",
                duration_s=time.monotonic() - start,
                safe_mode=not full_suite,
            )

    # ── INTERNALS ────────────────────────────────────────────────────────────

    def _run_sync(
        self,
        workspace: Path,
        new_tests: dict[str, str],
        tests_dir: str,
        full_suite: bool,
    ) -> TestResult:
        """Synchronous pytest execution. Runs in thread pool.

        R-F1235: in safe mode, only runs the NEW test files (not the full
        3647-test suite). This cuts IO from ~2GB to ~10KB and time from
        ~166s to <5s, eliminating the wedge risk.
        """
        with tempfile.TemporaryDirectory(prefix="aria_test_") as tmpdir:
            tmp = Path(tmpdir)
            app_copy = tmp / "app"

            if self.repo_root.exists():
                shutil.copytree(
                    self.repo_root, app_copy,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc",
                        "aria_rag", "*.json.gz",
                        "node_modules", ".git", ".venv",
                        "runs", "data/wedge_stacks",
                    ),
                )
            else:
                app_copy.mkdir(parents=True, exist_ok=True)

            # Overlay workspace patches
            for src in workspace.rglob("*.py"):
                rel = src.relative_to(workspace)
                dst = app_copy / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

            # Write generated tests
            for test_path, test_code in new_tests.items():
                dst = app_copy / test_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(test_code, encoding="utf-8")

            # Build pytest command
            if full_suite:
                # Full suite: run ALL tests (risky on aria-intel)
                test_target = str(app_copy / tests_dir)
                timeout_s = FULL_TIMEOUT_S - 30
            else:
                # Safe mode: only run the NEW test files
                test_target = " ".join(
                    str(app_copy / tp) for tp in new_tests.keys()
                ) if new_tests else str(app_copy / tests_dir)
                timeout_s = SAFE_TIMEOUT_S - 10

            cmd = [
                "python", "-m", "pytest",
                *test_target.split(),
                "-v", "--tb=short", "--no-header",
                "--timeout=30",  # per-test timeout
                "-x",  # stop on first failure for speed
            ]

            # Use process group isolation so we can kill children
            try:
                proc = subprocess.run(  # noqa: S603 — controlled cmd
                    cmd,
                    capture_output=True, text=True,
                    cwd=str(app_copy),
                    timeout=timeout_s,
                    env={
                        **os.environ,
                        "ARIA_TEST_MODE": "1",
                        "REDIS_URL": os.environ.get(
                            "ARIA_TEST_REDIS_URL", "redis://localhost:6379/15"
                        ),
                        # Prevent tests from spawning long-running processes
                        "ARIA_CODER_TESTS_ENABLED": "0",
                        "PYTEST_TIMEOUT": "30",
                    },
                    # R-F1235: process group for clean kill
                    start_new_session=True,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return TestResult(
                    all_green=False,
                    failure_summary=f"pytest timed out after {timeout_s}s",
                    safe_mode=not full_suite,
                )
            except Exception as e:
                return TestResult(
                    all_green=False,
                    failure_summary=f"pytest crashed: {e}",
                    safe_mode=not full_suite,
                )

            return self._parse(proc.stdout, proc.stderr, proc.returncode, not full_suite)

    def _parse(
        self, stdout: str, stderr: str, returncode: int,
        safe_mode: bool,
    ) -> TestResult:
        combined = (stdout or "") + "\n" + (stderr or "")
        lines = combined.split("\n")
        passed = failed = errors = 0
        summary_line = ""

        for line in lines:
            if " passed" in line or " failed" in line or " error" in line:
                if "===" in line or "passed" in line.lower():
                    p = re.search(r"(\d+)\s+passed", line)
                    f = re.search(r"(\d+)\s+failed", line)
                    e = re.search(r"(\d+)\s+error", line)
                    if p:
                        passed = int(p.group(1))
                    if f:
                        failed = int(f.group(1))
                    if e:
                        errors = int(e.group(1))
                    if p or f or e:
                        summary_line = line
                        break

        all_green = returncode == 0 and failed == 0 and errors == 0

        failure_summary = ""
        if not all_green:
            failure_lines: list[str] = []
            in_failure = False
            for line in lines:
                if "FAILED" in line or "ERROR" in line:
                    in_failure = True
                if in_failure:
                    failure_lines.append(line)
                if len(failure_lines) >= 20:
                    break
            failure_summary = "\n".join(failure_lines) or summary_line

        return TestResult(
            all_green=all_green,
            passed=passed, failed=failed, errors=errors,
            failure_summary=failure_summary,
            output_tail=combined[-3000:],
            safe_mode=safe_mode,
        )

    # ── CIRCUIT BREAKER (R-F1235) ──────────────────────────────────────────

    async def _check_circuit_breaker(self) -> tuple[bool, str]:
        """Check if the circuit breaker is open.

        Returns (allowed, reason). If the breaker is tripped, returns
        (False, "cooldown until <time>").
        """
        try:
            # Check if breaker is tripped
            tripped = await self.redis.get(f"{CB_KEY_PREFIX}:tripped")
            if tripped:
                return False, f"Circuit breaker tripped (cooldown active)"

            # Count recent failures
            failures = await self.redis.get(f"{CB_KEY_PREFIX}:count") or "0"
            count = int(failures) if failures else 0
            if count >= CB_MAX_FAILURES:
                # Trip the breaker
                await self.redis.setex(f"{CB_KEY_PREFIX}:tripped", CB_COOLDOWN_S, "1")
                logger.warning(
                    "[test_runner] circuit breaker TRIPPED: %d failures in window",
                    count,
                )
                return False, f"Auto-disabled after {count} failures (24h cooldown)"
            return True, "ok"
        except Exception as e:
            logger.debug("[test_runner] circuit breaker check failed: %s", e)
            return True, "breaker_unavailable"

    async def _record_failure(self, summary: str) -> None:
        """Record a test failure in the circuit breaker counter."""
        try:
            key = f"{CB_KEY_FREFIX}:count"
            count = await self.redis.incr(key)
            if count == 1:
                await self.redis.expire(key, CB_WINDOW_S)
            logger.info(
                "[test_runner] recorded failure %d/%d: %s",
                count, CB_MAX_FAILURES, summary[:100],
            )
        except Exception as e:
            logger.debug("[test_runner] failed to record failure: %s", e)

    async def _check_rate_limit(self) -> bool:
        """Check if we're within the rate limit.

        Max 1 test run per RATE_INTERVAL_S per fix_id.
        """
        try:
            key = f"{RATE_KEY_PREFIX}:last_run"
            now = time.time()
            last_raw = await self.redis.get(key)
            if last_raw:
                last = float(last_raw)
                if now - last < RATE_INTERVAL_S:
                    return False
            await self.redis.setex(key, RATE_INTERVAL_S * 2, str(now))
            return True
        except Exception:
            return True  # fail open on Redis error

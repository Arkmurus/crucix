"""R-F802 — Isolated test runner for autonomous fixes.

Runs ARIA's test suite against proposed code changes in isolation, so
production code is never modified during testing.

Performance note
────────────────
Today's implementation copies the repo to a tempdir and applies the
patch. For a 3,647-test suite that's ~2GB of IO + ~166s pytest. This
is too heavy to run on the aria-intel machine without risking PR04
wedge cascades (the same class of wedge R-F795 fixed). The proper
deployment target is the `aria-runner` Fly app (R-F805) — ephemeral
machines spun up per test run, destroyed after.

Until aria-runner ships, set `ARIA_CODER_TESTS_ENABLED=0` to skip
isolated testing (the fix still passes through the constitutional
validator, but tests run only at PR-CI time). For prod use, the
ARIACoder orchestrator chooses one of:
  - skip (env var off, rely on PR CI)
  - subprocess on aria-coder (separate Fly app, R-F805)
  - subprocess on aria-runner (per-fix Fly machine, R-F805)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.autonomous.test_runner")

PYTEST_TIMEOUT_S = 300       # 5 minutes per run
REQUIRED_PASS_RATE = 0.90    # 90% pass rate (allows known flaky)


@dataclass
class TestResult:
    all_green: bool
    passed: int = 0
    failed: int = 0
    errors: int = 0
    failure_summary: str = ""
    duration_s: float = 0.0
    output_tail: str = ""


class TestRunner:
    """Runs pytest in isolation against a patched workspace."""

    def __init__(
        self,
        redis_client: Any,
        repo_root: Path | None = None,
        timeout_s: int = PYTEST_TIMEOUT_S,
    ) -> None:
        self.redis = redis_client
        self.repo_root = repo_root or Path(
            os.environ.get("ARIA_REPO_PATH", "/app")
        )
        self.timeout_s = timeout_s

    async def run_isolated(
        self,
        workspace: Path,
        new_tests: dict[str, str],
        tests_dir: str = "aria_service/tests",
    ) -> TestResult:
        """Apply workspace patches and run pytest in a tempdir copy."""
        if os.environ.get("ARIA_CODER_TESTS_ENABLED", "1").strip() == "0":
            logger.info("[test_runner] disabled via env — skipping")
            return TestResult(all_green=True, passed=0, failed=0)

        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    None, self._run_sync, workspace, new_tests, tests_dir,
                ),
                timeout=self.timeout_s,
            )
            result.duration_s = time.monotonic() - start
            logger.info(
                "[test_runner] passed=%d failed=%d duration=%.1fs green=%s",
                result.passed, result.failed, result.duration_s,
                result.all_green,
            )
            return result
        except asyncio.TimeoutError:
            return TestResult(
                all_green=False,
                failure_summary=f"Tests timed out after {self.timeout_s}s",
                duration_s=time.monotonic() - start,
            )

    # ── INTERNALS ────────────────────────────────────────────────────────────

    def _run_sync(
        self,
        workspace: Path,
        new_tests: dict[str, str],
        tests_dir: str,
    ) -> TestResult:
        """Synchronous pytest execution. Runs in thread pool."""
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

            cmd = [
                "python", "-m", "pytest",
                str(app_copy / tests_dir),
                "-v", "--tb=short", "--no-header",
                "--timeout=60",
                "-x",  # stop on first failure for speed
            ]
            proc = subprocess.run(  # noqa: S603 — controlled cmd
                cmd,
                capture_output=True, text=True,
                cwd=str(app_copy),
                timeout=max(60, self.timeout_s - 30),
                env={
                    **os.environ,
                    "ARIA_TEST_MODE": "1",
                    # Per CLAUDE.md the prod REDIS_URL is upstash-cancelled.
                    # Tests must run against a local Redis or with mocks.
                    "REDIS_URL": os.environ.get(
                        "ARIA_TEST_REDIS_URL", "redis://localhost:6379/15"
                    ),
                },
                check=False,
            )
            return self._parse(proc.stdout, proc.stderr, proc.returncode)

    def _parse(
        self, stdout: str, stderr: str, returncode: int,
    ) -> TestResult:
        combined = (stdout or "") + "\n" + (stderr or "")
        lines = combined.split("\n")
        passed = failed = errors = 0
        summary_line = ""

        for line in lines:
            # pytest summary line, e.g. "=== 5 failed, 100 passed in 12s ==="
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
        )

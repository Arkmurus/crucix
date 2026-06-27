"""R-F2060 — SelfCodingOS.run_tests must run pytest under THIS interpreter.

Same root cause/fix as the coder_tools path (and R-F1928): a bare `python -m pytest`
can hit a system interpreter missing numpy/chromadb → spurious collection errors.
Capability test: drive the REAL run_tests() with create_subprocess_exec patched to
capture argv, and assert argv[0] is sys.executable (not 'python').
"""
import asyncio
import sys

from aria_service.intel.self_coding_os import SelfCodingOS


class _FakeProc:
    returncode = 0
    async def communicate(self):
        return (b"collected 7860 items\n1 passed", b"")


def test_rf2060_run_tests_uses_running_interpreter(monkeypatch):
    captured = {}

    async def _fake_exec(*cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    os_ = SelfCodingOS()
    asyncio.run(os_.run_tests(test_pattern="rf2060"))

    cmd = captured["cmd"]
    assert cmd[0] == sys.executable, f"run_tests must use the running interpreter, got {cmd[0]!r}"
    assert cmd[0] != "python", "must not invoke bare PATH python"
    assert cmd[1:3] == ["-m", "pytest"]

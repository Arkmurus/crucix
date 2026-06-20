"""R-F1750 — EagleEye scan_once must not re-run the CPU scan on the event loop.

Live wedge capture 2026-06-20 (/data/wedge_stacks/wedge_674) caught
`eagle_eye:171 _detect_code_smells` blocking the MAIN event loop ~5s while a
user's /dd stream produced 0 bytes. Root cause: scan_once awaited the threaded
_scan_files_sync (which ALREADY calls _detect_code_smells + _save_metrics at its
tail) and THEN called both again — on the loop. The duplicate is removed.

Capability: across one scan_once(), _detect_code_smells and _save_metrics each
run EXACTLY ONCE (inside the worker thread via _scan_files_sync), never a second
time on the event loop.
"""
import asyncio

import pytest


@pytest.mark.asyncio
async def test_scan_once_does_not_double_run_cpu_work(monkeypatch, tmp_path):
    from aria_service.intel import eagle_eye

    # Build a guardian rooted at a tiny temp tree (one .py file).
    (tmp_path / "sample.py").write_text("x = 1\n", encoding="utf-8")
    guardian = eagle_eye.EagleEyeGuardian(tmp_path)

    calls = {"smells": 0, "save": 0}
    monkeypatch.setattr(guardian, "_detect_code_smells",
                        lambda: calls.__setitem__("smells", calls["smells"] + 1))
    monkeypatch.setattr(guardian, "_save_metrics",
                        lambda: calls.__setitem__("save", calls["save"] + 1))

    report = await guardian.scan_once()

    assert isinstance(report, dict)
    # Each runs exactly once — inside the threaded _scan_files_sync, NOT again
    # on the loop. Pre-fix this was 2.
    assert calls["smells"] == 1, f"_detect_code_smells ran {calls['smells']}x (expected 1)"
    assert calls["save"] == 1, f"_save_metrics ran {calls['save']}x (expected 1)"

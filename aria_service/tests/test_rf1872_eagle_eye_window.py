"""R-F1872 — EagleEye scan must be gated to an evening window + skip unchanged
files, so its GIL-bound scan can't wedge the event loop during the day.

Live 2026-06-24: EagleEye's full-codebase scan (AST + regex + a flood of WARNING
logs) ran every 30 min and — though in asyncio.to_thread — is GIL-bound, so it
starved aria-intel's loop and aria-wa brain-fetches timed out (DD never
returned). Fix: only scan inside ARIA_EAGLE_EYE_WINDOW, and after the baseline
pass re-scan only changed files.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime
from pathlib import Path

import pytest


def _dt(h, m=0):
    return datetime(2026, 6, 24, h, m, 0)


class TestScanWindow:
    def test_inside_window_allows(self, monkeypatch):
        import aria_service.intel.eagle_eye as ee
        monkeypatch.setattr(ee, "_SCAN_WINDOW", "20:00-22:00")
        assert ee._within_scan_window(_dt(20, 30)) is True
        assert ee._within_scan_window(_dt(21, 59)) is True

    def test_outside_window_blocks(self, monkeypatch):
        import aria_service.intel.eagle_eye as ee
        monkeypatch.setattr(ee, "_SCAN_WINDOW", "20:00-22:00")
        assert ee._within_scan_window(_dt(9, 0)) is False    # working day
        assert ee._within_scan_window(_dt(14, 30)) is False   # live testing time
        assert ee._within_scan_window(_dt(22, 1)) is False

    def test_empty_window_means_always(self, monkeypatch):
        import aria_service.intel.eagle_eye as ee
        monkeypatch.setattr(ee, "_SCAN_WINDOW", "")
        assert ee._within_scan_window(_dt(3, 0)) is True

    def test_window_crossing_midnight(self, monkeypatch):
        import aria_service.intel.eagle_eye as ee
        monkeypatch.setattr(ee, "_SCAN_WINDOW", "22:00-02:00")
        assert ee._within_scan_window(_dt(23, 0)) is True
        assert ee._within_scan_window(_dt(1, 0)) is True
        assert ee._within_scan_window(_dt(12, 0)) is False

    def test_bad_config_defaults_to_allow(self, monkeypatch):
        import aria_service.intel.eagle_eye as ee
        monkeypatch.setattr(ee, "_SCAN_WINDOW", "not-a-window")
        assert ee._within_scan_window(_dt(12, 0)) is True


class TestLoopGating:
    def test_scan_loop_checks_the_window(self):
        # The loop must consult _within_scan_window before scanning.
        src = Path("aria_service/intel/eagle_eye.py").read_text(encoding="utf-8")
        assert "if not _within_scan_window():" in src, "loop does not gate scanning on the window"
        assert "if _within_scan_window():" in src, "initial scan not gated on the window"


class TestChangedFilesOnly:
    def test_unchanged_files_skipped_after_baseline(self, tmp_path, monkeypatch):
        """After the baseline pass, a second scan must NOT re-parse unchanged files."""
        import aria_service.intel.eagle_eye as ee
        # isolate persistence to a temp dir
        monkeypatch.setattr(ee, "_GUARDIAN_DIR", tmp_path / "eagle")
        (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")

        guardian = ee.EagleEyeGuardian(tmp_path)
        asyncio.run(guardian.scan_once())          # baseline pass
        assert guardian._baseline_seeded is True

        calls = []
        real = guardian._scan_for_issues
        monkeypatch.setattr(guardian, "_scan_for_issues", lambda *a, **k: calls.append(a))
        asyncio.run(guardian.scan_once())          # nothing changed
        assert calls == [], "unchanged files were re-scanned after baseline (wasted GIL)"

        # change the file → it must be scanned again
        (tmp_path / "mod.py").write_text("x = 2\ny = eval('1')\n", encoding="utf-8")
        asyncio.run(guardian.scan_once())
        assert len(calls) >= 1, "a changed file was NOT re-scanned"

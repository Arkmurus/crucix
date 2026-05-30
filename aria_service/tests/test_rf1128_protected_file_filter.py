"""R-F1128 — Capability tests for protected-file gap filtering in self_coder.

Tests that _one_cycle correctly:
1. Filters out gaps whose module maps to a PROTECTED_FILES entry
2. Logs a warning for skipped protected-file gaps
3. Still processes gaps targeting non-protected files
4. Surfaces protected-file gaps for human review
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.autonomous.gap_detector import Gap, GapDetector, GapSeverity, GapType


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_gap_detector():
    detector = MagicMock(spec=GapDetector)
    detector.scan = AsyncMock()
    detector.mark_attempted = AsyncMock()
    detector.mark_fixed = AsyncMock()
    return detector


def make_gap(gap_id: str, module: str, title: str = "Test gap",
             severity: GapSeverity = GapSeverity.HIGH,
             gap_type: str = "module_bug") -> Gap:
    return Gap(
        gap_id=gap_id,
        gap_type=gap_type,
        severity=severity,
        title=title,
        description=f"Description for {title}",
        module=module,
    )


# ── Tests ───────────────────────────────────────────────────────────────────

class TestProtectedFileFilter:
    """Proves the self_coder filters out protected-file gaps."""

    async def test_filters_protected_file_gap(self, mock_gap_detector):
        """A gap targeting self_improve.py is filtered out."""
        from aria_service.autonomous.self_coder import _PROTECTED_FILES

        # Verify self_improve.py is in the protected set
        assert "aria_service/intel/self_improve.py" in _PROTECTED_FILES

        # Create a gap targeting self_improve
        gap = make_gap("gap_self_improve", "self_improve", "Bug in staging")
        mock_gap_detector.scan.return_value = [gap]

        # Run _one_cycle — the gap should be filtered out
        with patch("aria_service.autonomous.self_coder.logger") as mock_logger:
            from aria_service.autonomous.self_coder import ARIACoder
            coder = ARIACoder(
                redis_client=MagicMock(),
                aria_service_url="http://localhost:8000",
                gap_detector=mock_gap_detector,
            )
            coder.fix_gap = AsyncMock()

            await coder._one_cycle()

        # fix_gap should NOT have been called (gap was filtered)
        coder.fix_gap.assert_not_called()

        # A warning should have been logged
        mock_logger.warning.assert_called_once()
        warning_args = mock_logger.warning.call_args[0]
        assert "protected" in str(warning_args).lower()

    async def test_processes_non_protected_gap(self, mock_gap_detector):
        """A gap targeting a non-protected file is processed normally."""
        gap = make_gap("gap_normal", "dd_orchestrator", "Bug in DD")
        mock_gap_detector.scan.return_value = [gap]

        from aria_service.autonomous.self_coder import ARIACoder
        coder = ARIACoder(
            redis_client=MagicMock(),
            aria_service_url="http://localhost:8000",
            gap_detector=mock_gap_detector,
        )
        coder.fix_gap = AsyncMock(return_value=MagicMock(success=True, r_number=123))

        await coder._one_cycle()

        # fix_gap SHOULD have been called
        coder.fix_gap.assert_called_once_with(gap)

    async def test_skips_low_severity_gaps(self, mock_gap_detector):
        """Low-severity gaps are still skipped regardless of protected status."""
        gap = make_gap("gap_low", "dd_orchestrator", "Low severity",
                       severity=GapSeverity.LOW)
        mock_gap_detector.scan.return_value = [gap]

        from aria_service.autonomous.self_coder import ARIACoder
        coder = ARIACoder(
            redis_client=MagicMock(),
            aria_service_url="http://localhost:8000",
            gap_detector=mock_gap_detector,
        )
        coder.fix_gap = AsyncMock()

        await coder._one_cycle()

        coder.fix_gap.assert_not_called()

    async def test_protected_set_contains_key_files(self):
        """The _PROTECTED_FILES set contains the expected critical files."""
        from aria_service.autonomous.self_coder import _PROTECTED_FILES

        critical_files = [
            "aria_service/autonomous/constitutional_validator.py",
            "aria_service/autonomous/self_coder.py",
            "aria_service/autonomous/safety.py",
            "aria_service/intel/self_improve.py",
            "aria_service/intel/adversarial_challenge.py",
        ]
        for f in critical_files:
            assert f in _PROTECTED_FILES, f"{f} should be in _PROTECTED_FILES"

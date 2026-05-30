"""R-F1136 — Capability tests for behavioural anomaly detection.

Tests that:
1. Hard allowlist blocks disallowed actions (e.g., document_reader writing to /etc)
2. Learned baseline detects novel targets
3. Normal behaviour passes both checks
4. Anomaly logging + brain wiring works
5. Baseline building works over time
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel.behavioural_anomaly import (
    HARD_ALLOWLIST,
    AnomalyResult,
    _check_hard_allowlist,
    _matches_pattern,
    check_anomaly,
    get_anomaly_log,
    get_baseline,
    log_anomaly,
    record_observation,
)


# ── Tests for pattern matching ──────────────────────────────────────────────

class TestPatternMatching:
    """Proves path pattern matching works."""

    def test_directory_wildcard(self):
        """Directory wildcard matches files in that directory."""
        assert _matches_pattern("/data/doc.pdf", "/data/*") is True
        assert _matches_pattern("/etc/passwd", "/data/*") is False

    def test_extension_wildcard(self):
        """Extension wildcard matches files with that extension."""
        assert _matches_pattern("report.pdf", "*.pdf") is True
        assert _matches_pattern("report.docx", "*.pdf") is False

    def test_recursive_py_wildcard(self):
        """Recursive wildcard matches .py files anywhere."""
        assert _matches_pattern("aria_service/intel/security.py",
                                "aria_service/**/*.py") is True
        assert _matches_pattern("scripts/deploy.sh",
                                "aria_service/**/*.py") is False
        assert _matches_pattern("aria_service/intel/security.txt",
                                "aria_service/**/*.py") is False


# ── Tests for hard allowlist ────────────────────────────────────────────────

class TestHardAllowlist:
    """Proves hard allowlist blocks disallowed actions."""

    def test_allows_normal_file_read(self):
        """document_reader reading /data/doc.pdf is allowed."""
        result = _check_hard_allowlist("document_reader", "file_read", "/data/doc.pdf")
        assert result is None

    def test_blocks_etc_write(self):
        """document_reader writing to /etc/passwd is blocked."""
        result = _check_hard_allowlist("document_reader", "file_write", "/etc/passwd")
        assert result is not None
        assert "not in hard allowlist" in result

    def test_allows_self_coder_py_write(self):
        """self_coder writing to a .py file is allowed."""
        result = _check_hard_allowlist(
            "self_coder", "file_write", "aria_service/intel/security.py"
        )
        assert result is None

    def test_blocks_unknown_module(self):
        """Unknown module with no rules passes through (no false positive)."""
        result = _check_hard_allowlist("unknown_module", "file_write", "/etc/passwd")
        assert result is None  # No rules = no block


# ── Tests for anomaly detection ─────────────────────────────────────────────

class TestAnomalyDetection:
    """Proves anomaly detection works with persistent mock store."""

    @pytest.fixture(autouse=True)
    def mock_redis(self):
        """Persistent in-memory Redis mock."""
        store: dict[str, object] = {}

        async def mock_get(key: str) -> object:
            return store.get(key)

        async def mock_set(key: str, value: object, **kwargs) -> None:
            store[key] = value

        with patch("aria_service.intel.redis_store.get_json", side_effect=mock_get):
            with patch("aria_service.intel.redis_store.set_json", side_effect=mock_set):
                yield store

    async def test_detects_novel_target(self, mock_redis):
        """A target never seen before is detected as anomalous."""
        # Build baseline with normal observations
        for i in range(10):
            await record_observation("document_reader", "file_read", "/data/normal.pdf")

        # Now check a novel target
        result = await check_anomaly("document_reader", "file_read", "/data/never_seen.pdf")
        assert result.anomaly is True
        assert "Novel target" in result.reason

    async def test_normal_behaviour_passes(self, mock_redis):
        """Normal behaviour passes both checks."""
        for i in range(10):
            await record_observation("document_reader", "file_read", "/data/normal.pdf")

        result = await check_anomaly("document_reader", "file_read", "/data/normal.pdf")
        assert result.anomaly is False

    async def test_hard_allowlist_overrides_baseline(self, mock_redis):
        """Hard allowlist blocks even if baseline has seen the target."""
        # Build baseline with a disallowed action
        for i in range(10):
            await record_observation("document_reader", "file_write", "/etc/passwd")

        # Hard allowlist should still block it
        result = await check_anomaly("document_reader", "file_write", "/etc/passwd")
        assert result.anomaly is True
        assert "hard allowlist" in result.reason

    async def test_no_baseline_not_anomalous(self, mock_redis):
        """No baseline yet is not anomalous."""
        result = await check_anomaly("new_module", "file_read", "/data/file.pdf")
        assert result.anomaly is False

    async def test_anomaly_logging(self, mock_redis):
        """Anomalies are logged."""
        result = AnomalyResult(
            anomaly=True,
            reason="Test anomaly",
            confidence="HIGH",
            details={"module": "test", "action": "test", "target": "/test"},
        )
        await log_anomaly(result)

        log = await get_anomaly_log()
        assert len(log) >= 1
        assert log[0]["reason"] == "Test anomaly"

    async def test_anomaly_wires_to_brain(self, mock_redis):
        """Anomalies wire to brain."""
        result = AnomalyResult(
            anomaly=True,
            reason="Brain test anomaly",
            confidence="HIGH",
            details={"module": "test", "action": "test", "target": "/test"},
        )
        with patch("aria_service.intel.engine_wiring.wire_failure") as mock_wf:
            await log_anomaly(result)

        mock_wf.assert_called_once()
        args, kwargs = mock_wf.call_args
        assert kwargs.get("gap_type") == "security_threat"

    async def test_baseline_persistence(self, mock_redis):
        """Baseline persists across calls."""
        await record_observation("test_mod", "file_read", "/data/file1.pdf")
        await record_observation("test_mod", "file_read", "/data/file2.pdf")

        baseline = await get_baseline("test_mod")
        assert len(baseline) >= 1
        key = "test_mod:file_read"
        assert key in baseline
        assert baseline[key]["total_observations"] == 2

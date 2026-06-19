"""R-F1684: Capability test — TestFailureExtractor reads pytest cache
and produces MODULE_BUG gaps for the coder's gold pipeline.

This test drives the REAL TestFailureExtractor.extract() method with
a synthetic pytest cache to prove the user-visible behaviour: failing
tests are surfaced as MODULE_BUG gaps with the correct module mapping.
"""
import os
import sys
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aria_service.autonomous.gap_detector import (
    TestFailureExtractor, GapType, GapSeverity,
)


class TestTestFailureExtractor:
    """Capability test: TestFailureExtractor produces MODULE_BUG gaps."""

    def setup_method(self):
        """Create a temp pytest cache for testing."""
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.cache_dir = self.tmp_dir / ".pytest_cache" / "v" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Write a synthetic lastfailed cache
        self.lastfailed = {
            "aria_service/tests/test_rf672_lifespan_silent_except_promoted.py::test_rf672_no_silent_except_pass_in_lifespan": True,
            "aria_service/tests/test_rf434_brandified_hostname_cap.py::test_rf434_something": True,
            "aria_service/tests/test_session_2026_05_11.py::TestStateStoreSQLite::test_delete_returns_bool": True,
            "aria_service/tests/test_session_2026_05_11.py::TestStateStoreSQLite::test_scan_keys_glob": True,
        }
        (self.cache_dir / "lastfailed").write_text(json.dumps(self.lastfailed))

        # Write nodeids
        self.nodeids = list(self.lastfailed.keys()) + [
            "aria_service/tests/test_rf672_lifespan_silent_except_promoted.py::test_rf672_other",
            "aria_service/tests/test_session_2026_05_11.py::TestStateStoreSQLite::test_something_else",
        ]
        (self.cache_dir / "nodeids").write_text(json.dumps(self.nodeids))

    def teardown_method(self):
        """Clean up temp cache."""
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_extractor_produces_module_bug_gaps(self):
        """R-F1684: TestFailureExtractor produces MODULE_BUG gaps from cache."""
        # Create extractor with patched repo root
        extractor = TestFailureExtractor(redis_client=None)
        extractor._repo_root = self.tmp_dir

        # Run extract with a recent lookback
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        gaps = await extractor.extract(since)

        # Should produce gaps
        assert len(gaps) > 0, f"Expected at least 1 gap, got {len(gaps)}"

        # All gaps should be MODULE_BUG type
        for gap in gaps:
            assert gap.gap_type == GapType.MODULE_BUG, \
                f"Expected MODULE_BUG, got {gap.gap_type} for {gap.title}"

        # Check specific mappings
        gap_titles = {g.title for g in gaps}
        gap_modules = {g.module for g in gaps}

        # test_rf672 → main.py
        assert any("test_rf672" in t for t in gap_titles), \
            f"Expected test_rf672 in titles: {gap_titles}"
        assert "main.py" in gap_modules, \
            f"Expected main.py in modules: {gap_modules}"

        # test_rf434 → routes/aria.py
        assert any("test_rf434" in t for t in gap_titles), \
            f"Expected test_rf434 in titles: {gap_titles}"
        assert "routes/aria.py" in gap_modules, \
            f"Expected routes/aria.py in modules: {gap_modules}"

        # test_session_2026_05_11 → heuristic fallback
        assert any("test_session" in t for t in gap_titles), \
            f"Expected test_session in titles: {gap_titles}"

    @pytest.mark.asyncio
    async def test_extractor_returns_empty_without_cache(self):
        """R-F1684: No cache file → empty result."""
        empty_dir = Path(tempfile.mkdtemp())
        try:
            extractor = TestFailureExtractor(redis_client=None)
            extractor._repo_root = empty_dir

            since = datetime.now(timezone.utc) - timedelta(hours=1)
            gaps = await extractor.extract(since)
            assert len(gaps) == 0, f"Expected 0 gaps without cache, got {len(gaps)}"
        finally:
            import shutil
            shutil.rmtree(empty_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_extractor_respects_lookback_window(self):
        """R-F1684: Old lookback → empty result (no scan)."""
        extractor = TestFailureExtractor(redis_client=None)
        extractor._repo_root = self.tmp_dir

        # Use a lookback older than 2h
        since = datetime.now(timezone.utc) - timedelta(hours=3)
        gaps = await extractor.extract(since)
        assert len(gaps) == 0, f"Expected 0 gaps for old lookback, got {len(gaps)}"

    def test_module_mapping_known_clusters(self):
        """R-F1684: Known R-clusters map to correct modules."""
        extractor = TestFailureExtractor(redis_client=None)

        # Test the explicit mapping
        assert extractor._map_test_to_module("aria_service/tests/test_rf672_lifespan_silent_except_promoted.py") == "main.py"
        assert extractor._map_test_to_module("aria_service/tests/test_rf434_brandified_hostname_cap.py") == "routes/aria.py"
        assert extractor._map_test_to_module("aria_service/tests/test_rf528_read_document_clientdisconnect.py") == "routes/aria.py"
        assert extractor._map_test_to_module("aria_service/tests/test_rf513_build_rev_autoderive.py") == "main.py"

    def test_module_mapping_heuristic_fallback(self):
        """R-F1684: Unknown test files use heuristic fallback."""
        extractor = TestFailureExtractor(redis_client=None)

        # test_session_2026_05_11 → heuristic strips test_ → session_2026_05_11
        # → no _rfNNNN_ pattern → intel/session_2026_05_11.py
        module = extractor._map_test_to_module("aria_service/tests/test_session_2026_05_11.py")
        assert "session" in module, f"Expected session in module, got {module}"

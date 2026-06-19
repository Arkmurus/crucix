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
    async def test_extractor_disabled_by_default(self, monkeypatch):
        """R-F1686: OPT-IN — with ARIA_CODER_TEST_FUEL_ENABLED unset/0 the
        extractor produces NO gaps even with a cache full of failures. The
        firehose never fires unreviewed."""
        monkeypatch.delenv("ARIA_CODER_TEST_FUEL_ENABLED", raising=False)
        extractor = TestFailureExtractor(redis_client=None)
        extractor._repo_root = self.tmp_dir
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        gaps = await extractor.extract(since)
        assert gaps == [], f"opt-in gate OFF must yield no gaps, got {len(gaps)}"

    @pytest.mark.asyncio
    async def test_extractor_produces_curated_module_bug_gaps(self, monkeypatch):
        """R-F1684/R-F1686: when ARMED, produces MODULE_BUG gaps ONLY for
        curated _TEST_TO_MODULE_MAP clusters; uncurated failing tests (e.g.
        test_session_*) are DROPPED, never guessed; module paths are
        aria_service/-prefixed so the coder can find them."""
        monkeypatch.setenv("ARIA_CODER_TEST_FUEL_ENABLED", "1")
        extractor = TestFailureExtractor(redis_client=None)
        extractor._repo_root = self.tmp_dir

        since = datetime.now(timezone.utc) - timedelta(hours=1)
        gaps = await extractor.extract(since)

        assert len(gaps) > 0, f"Expected at least 1 gap, got {len(gaps)}"
        for gap in gaps:
            assert gap.gap_type == GapType.MODULE_BUG, \
                f"Expected MODULE_BUG, got {gap.gap_type} for {gap.title}"

        gap_titles = {g.title for g in gaps}
        gap_modules = {g.module for g in gaps}

        # test_rf672 → aria_service/main.py (curated, prefixed)
        assert any("test_rf672" in t for t in gap_titles), \
            f"Expected test_rf672 in titles: {gap_titles}"
        assert "aria_service/main.py" in gap_modules, \
            f"Expected aria_service/main.py in modules: {gap_modules}"

        # test_rf434 → aria_service/routes/aria.py (curated, prefixed)
        assert any("test_rf434" in t for t in gap_titles), \
            f"Expected test_rf434 in titles: {gap_titles}"
        assert "aria_service/routes/aria.py" in gap_modules, \
            f"Expected aria_service/routes/aria.py in modules: {gap_modules}"

        # test_session_2026_05_11 → NOT curated → DROPPED (no guessing)
        assert not any("test_session" in t for t in gap_titles), \
            f"uncurated test_session must be dropped, got: {gap_titles}"

    def test_curated_module_returns_none_for_unmapped(self):
        """R-F1686: _curated_module returns None (skip) for unmapped tests and
        aria_service/-prefixed paths for curated clusters."""
        extractor = TestFailureExtractor(redis_client=None)
        assert extractor._curated_module(
            "aria_service/tests/test_rf672_lifespan_silent_except_promoted.py",
        ) == "aria_service/main.py"
        assert extractor._curated_module(
            "aria_service/tests/test_rf434_brandified_hostname_cap.py",
        ) == "aria_service/routes/aria.py"
        assert extractor._curated_module(
            "aria_service/tests/test_session_2026_05_11.py",
        ) is None

    @pytest.mark.asyncio
    async def test_extractor_returns_empty_without_cache(self, monkeypatch):
        """R-F1684: No cache file → empty result (even when armed)."""
        monkeypatch.setenv("ARIA_CODER_TEST_FUEL_ENABLED", "1")
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
    async def test_extractor_respects_lookback_window(self, monkeypatch):
        """R-F1684: Old lookback → empty result (no scan)."""
        monkeypatch.setenv("ARIA_CODER_TEST_FUEL_ENABLED", "1")
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

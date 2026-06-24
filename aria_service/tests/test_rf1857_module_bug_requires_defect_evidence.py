"""R-F1857 — ErrorLedgerExtractor must not mint MODULE_BUG from tracebackless
operational WARNINGs.

Live 2026-06-24: /api/aria/coder/gaps held 25 module_bug gaps, ALL of them
`type=log:warning` with empty traceback — e.g. airtable_buffer
"[airtable_buffer] enqueued action=… reason=rate_limited attempts=1 (R-F529 —
will retry on next drain)". These are BY-DESIGN operational shed, not code
defects. Since R-F1681/R-F1685 the gold gate requires a reproduce test to go
FAIL→PASS, so a fault-less warning can NEVER become gold; feeding it to the
coder only churns LLM budget. The fix requires positive evidence of a code
defect (traceback OR error/critical level) before classifying MODULE_BUG.

Capability test: drive the actual `ErrorLedgerExtractor._entry_to_gap` with the
real log entry shapes and assert the user-visible outcome (the gap the coder
would pick up), not a helper.
"""
from __future__ import annotations

from aria_service.autonomous.gap_detector import ErrorLedgerExtractor, GapType


def _extractor() -> ErrorLedgerExtractor:
    # redis client is unused by _entry_to_gap; pass None.
    return ErrorLedgerExtractor(redis_client=None)


def test_tracebackless_warning_is_not_a_module_bug():
    """The real airtable rate-limit WARNING must produce NO gap."""
    ext = _extractor()
    entry = {
        "type": "log:warning",
        "message": "[airtable_buffer] enqueued action=5684b814 reason=rate_limited "
                   "attempts=1 (R-F529 — will retry on next drain)",
        "file": "aria_service/integrations/airtable_buffer.py",
        "function": "enqueue",
        "traceback": "",
        "timestamp": 1782286965.0,
    }
    assert ext._entry_to_gap(entry) is None


def test_warning_with_traceback_is_a_module_bug():
    """A WARNING that carries a real traceback IS a reproducible code defect."""
    ext = _extractor()
    entry = {
        "type": "log:warning",
        "message": "Unexpected failure in widget pipeline",
        "file": "aria_service/intel/widget.py",
        "function": "run",
        "traceback": "Traceback (most recent call last):\n  ...\nKeyError: 'x'",
        "timestamp": 1782286965.0,
    }
    gap = ext._entry_to_gap(entry)
    assert gap is not None
    assert gap.gap_type is GapType.MODULE_BUG


def test_error_level_without_traceback_is_a_module_bug():
    """An error/critical-level record is a code defect even without a traceback."""
    ext = _extractor()
    entry = {
        "type": "log:error",
        "message": "build_report failed: returned None",
        "file": "aria_service/intel/report.py",
        "function": "build_report",
        "traceback": "",
        "timestamp": 1782286965.0,
    }
    gap = ext._entry_to_gap(entry)
    assert gap is not None
    assert gap.gap_type is GapType.MODULE_BUG


def test_typed_routes_are_unaffected_by_the_gate():
    """Source/parse routes are set before the gate, so a tracebackless source
    WARNING still classifies (it is not a MODULE_BUG default)."""
    ext = _extractor()
    src = {
        "type": "log:warning",
        "message": "rss fetch failed for feed", "file": "aria_service/intel/feeds.py",
        "function": "poll", "traceback": "", "timestamp": 1782286965.0,
    }
    gap = ext._entry_to_gap(src)
    assert gap is not None
    assert gap.gap_type is GapType.SOURCE_FAILURE


def test_existing_shed_markers_still_drop():
    """Defense-in-depth: the R-F889/R-F908 deny-list still fires first."""
    ext = _extractor()
    entry = {
        "type": "log:warning", "message": "neural: timeout after 5s",
        "file": "aria_service/intel/neural_memory.py", "function": "encode",
        "traceback": "", "timestamp": 1782286965.0,
    }
    assert ext._entry_to_gap(entry) is None

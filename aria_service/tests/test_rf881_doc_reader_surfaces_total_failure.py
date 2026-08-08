"""R-F881 — document_reader surfaces a TOTAL extraction failure (Hole 2 honesty).

The 4-strategy fallback (pdfplumber → table → OCR → vision → online) logs each
individual strategy miss at debug BY DESIGN. But when ALL strategies are
exhausted the document is genuinely unreadable, and pre-R-F881 that still
surfaced only at debug — below the brain's WARNING+ error_log_handler line and
invisible to the operator (the silent-failure class behind the contract work).
R-F881 raises the total failure to WARNING + emits a capability_gap.
"""
from __future__ import annotations

import inspect

from aria_service.intel import document_reader as dr

# R-F3786/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


def test_read_document_is_async():
    # the WARNING + capability_gap path awaits record_gap.
    assert inspect.iscoroutinefunction(dr.read_document)


def test_total_failure_logged_at_warning_not_debug():
    src = function_source(dr, "read_document")
    # the ALL_STRATEGIES_FAILED return is preceded by a WARNING + capability_gap
    assert "R-F881 ALL %d strategies FAILED" in src
    assert "logger.warning(" in src
    assert "await _cg881.record_gap(" in src
    assert 'gap_type="file_parse"' in src
    # and the failure result is still returned (behaviour preserved)
    assert 'method="ALL_STRATEGIES_FAILED"' in src


def test_per_strategy_misses_stay_debug():
    """Guard: we did NOT noisily raise every strategy attempt to warning — only
    the total exhaustion. The individual strategy handlers keep logger.debug."""
    src = module_source(dr)
    assert 'logger.debug("pdfplumber extraction failed' in src
    assert 'logger.debug("Tesseract OCR failed' in src

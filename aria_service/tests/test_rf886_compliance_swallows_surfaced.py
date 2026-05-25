"""R-F886 — silent compliance-layer swallows in dd_orchestrator are surfaced.

Ecosystem-360 P1: the export-control / banned-weapons compliance sub-layers
(R-F638-644) and the PSC ownership screen degraded at logger.debug — a banned-
weapon (INF/CWC/BWC/Ottawa/CCM) screen failure was a SILENT compliance miss.
R-F886 raises them to WARNING (operator/fly-log visibility) and records the
eliminated-weapons engine failure to error_log once/run (so the reconnected
coder, R-F884, sees it — dd_orchestrator logs under "ARIA.*", outside
error_log_handler's "aria.*" gate, so WARNING alone wouldn't reach the coder).
"""
from __future__ import annotations

import inspect
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")


def test_no_compliance_layer_left_at_debug():
    # the R-F644 compliance sub-layer swallows were all raised
    assert 'logger.debug("R-F644' not in SRC
    assert SRC.count("R-F886") >= 7


def test_eliminated_weapons_failure_is_warning_and_recorded():
    assert "eliminated-weapons watchlist FAILED" in SRC
    assert "logger.warning(" in SRC
    # records to error_log once/run so the coder (R-F884) sees a banned-weapon miss
    assert "_ewl_err_logged" in SRC
    assert "record_error(" in SRC
    assert "compliance_engine_failure" in SRC


def test_psc_ownership_screen_surfaced():
    assert "R-F886 PSC (ownership) screen failed" in SRC


def test_import_ok():
    import aria_service.intel.dd_orchestrator as o
    assert hasattr(o, "orchestrate_dd")

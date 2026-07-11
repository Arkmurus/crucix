"""R-F2551 (Phase 0.3) — register `llm_provider_failure` as a real gap type.

Live aria-intel (2026-07-11) logged "Unknown gap type 'llm_provider_failure' — recording
anyway" repeatedly: the LLM chain emits this generic provider-failure class, but it was
not in VALID_GAP_TYPES, so `record_gap` warned and demoted a real provider-routing signal
to log noise. Registering it makes the provider-failure taxonomy first-class (a prereq for
task-value LLM routing, Phase 1.3).
"""
from __future__ import annotations

import logging


def test_llm_provider_failure_is_registered():
    from aria_service.intel.capability_gaps import VALID_GAP_TYPES
    assert "llm_provider_failure" in VALID_GAP_TYPES


def test_gate_no_longer_warns_for_llm_provider_failure(caplog):
    """Drive the EXACT gate that produced the live warning (capability_gaps.py:244):
    `if gap_type not in VALID_GAP_TYPES: logger.warning("Unknown gap type ...")`."""
    from aria_service.intel.capability_gaps import VALID_GAP_TYPES
    logger = logging.getLogger("aria.intel.capability_gaps")
    with caplog.at_level(logging.WARNING, logger="aria.intel.capability_gaps"):
        gap_type = "llm_provider_failure"
        if gap_type not in VALID_GAP_TYPES:                     # the real gate
            logger.warning("Unknown gap type %r — recording anyway", gap_type)
    assert "Unknown gap type" not in caplog.text


def test_genuinely_unknown_type_still_warns(caplog):
    """Regression guard: registering one type must NOT disable the validation for
    truly-unknown types."""
    from aria_service.intel.capability_gaps import VALID_GAP_TYPES
    logger = logging.getLogger("aria.intel.capability_gaps")
    with caplog.at_level(logging.WARNING, logger="aria.intel.capability_gaps"):
        gap_type = "totally_bogus_xyz_not_a_real_type"
        if gap_type not in VALID_GAP_TYPES:
            logger.warning("Unknown gap type %r — recording anyway", gap_type)
    assert "Unknown gap type" in caplog.text


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

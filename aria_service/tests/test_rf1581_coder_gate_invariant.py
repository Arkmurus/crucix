"""R-F1581 — Invariant test: coder gate matches documented behaviour.

This is a BEHAVIOUR-ASSERTING test. It reads the actual gate logic in
start_aria_coder() and asserts that the comment in main.py accurately
describes it. If the gate logic changes without updating the comment,
this test fails CI.

Per R-F996: the coder is ALWAYS enabled when ARIA_INTERNAL_TOKEN is set.
There is no ARIA_CODER_ENABLED env var gate. The auto-deploy brake is
ARIA_SELF_IMPROVE_AUTO_DEPLOY (must stay 0 until R-F1450 proven).
"""
from __future__ import annotations

import re


def test_coder_gate_comment_matches_implementation():
    """Assert that main.py's coder gate comment matches start_aria_coder()'s actual gates."""
    with open("aria_service/main.py", "r", encoding="utf-8") as f:
        main = f.read()

    with open("aria_service/autonomous/coder_entrypoint.py", "r", encoding="utf-8") as f:
        coder = f.read()

    # 1. The comment in main.py must NOT mention ARIA_CODER_ENABLED as a gate
    # (R-F996 removed that gate — the coder is always enabled)
    idx = main.find("R-F996: coder is ALWAYS enabled")
    assert idx >= 0, (
        "main.py must document that the coder is always enabled (R-F996). "
        "The comment about ARIA_CODER_ENABLED was removed in R-F1581."
    )

    # 2. The comment must mention ARIA_SELF_IMPROVE_AUTO_DEPLOY as the brake
    assert "ARIA_SELF_IMPROVE_AUTO_DEPLOY" in main[idx:idx+500], (
        "main.py must document ARIA_SELF_IMPROVE_AUTO_DEPLOY as the auto-deploy brake"
    )

    # 3. The actual gate in start_aria_coder() must be ARIA_INTERNAL_TOKEN only
    idx = coder.find("async def start_aria_coder")
    assert idx >= 0, "start_aria_coder() not found"

    # Check that ARIA_CODER_ENABLED is NOT checked in start_aria_coder
    gate_section = coder[idx:idx+3000]
    assert "ARIA_CODER_ENABLED" not in gate_section, (
        "start_aria_coder() must NOT check ARIA_CODER_ENABLED (R-F996 removed this gate). "
        "Found reference to ARIA_CODER_ENABLED in the gate logic."
    )

    # Check that ARIA_INTERNAL_TOKEN IS the gate
    assert "ARIA_INTERNAL_TOKEN" in gate_section, (
        "start_aria_coder() must check ARIA_INTERNAL_TOKEN as the gate"
    )

    # 4. Verify the R-F996 comment exists
    assert "R-F996" in gate_section, (
        "start_aria_coder() must reference R-F996 documenting the always-enabled policy"
    )

"""Regression guard for F7 — security audit advisory bucket split.

Live observation 2026-04-27 18:24:50: every self-improve cycle logged
"Security audit complete: 6 issues (0 critical, 6 warning, 2 clean)".
Looking at the implementation, 5 of the 6 warnings are
TODO-placeholder advisory checks (CHECK 4-8) that ALWAYS fire because
they say "Requires X — manual review recommended". They aren't
real warnings; they're scaffold for unimplemented checks.

Fix: split them out into a separate `advisory` bucket so:
  1. The warning count reflects only real warnings
  2. Operator can tell when a NEW warning appears (currently masked
     by the constant 5-from-the-stub-checks floor)
"""
from __future__ import annotations

import asyncio

from aria_service.intel.security_protocol import run_security_audit


def test_audit_returns_advisory_bucket():
    """The result dict must have a separate advisory list."""
    result = asyncio.run(run_security_audit())
    assert "advisory" in result
    assert isinstance(result["advisory"], list)
    assert len(result["advisory"]) >= 4, (
        "expected at least the 5 placeholder checks (CHECK 4-8); "
        f"got {len(result['advisory'])}"
    )


def test_audit_warnings_does_NOT_include_advisories():
    """The warning bucket must be free of CHECK 4-8 placeholder text."""
    result = asyncio.run(run_security_audit())
    warnings_text = " ".join(result.get("warning") or [])
    for check_id in ("CHECK 4", "CHECK 5", "CHECK 6", "CHECK 7", "CHECK 8"):
        assert check_id not in warnings_text, (
            f"{check_id} placeholder leaked into the warning bucket — "
            "should be in `advisory` instead"
        )


def test_audit_warning_count_is_only_real_warnings():
    """In a clean dev setup, warning count should be 0 (or 1-2 if the
    CHECK 1/2/3 SKIP path fires due to missing data). Definitely <5
    once we've split out the advisories."""
    result = asyncio.run(run_security_audit())
    assert len(result["warning"]) < 5, (
        f"expected <5 real warnings, got {len(result['warning'])}: "
        f"{result['warning']}"
    )


def test_audit_log_message_includes_advisory_count(caplog):
    """The 'Security audit complete' log line must surface the advisory
    count separately so the operator sees both."""
    import logging
    with caplog.at_level(logging.INFO, logger="ARIA.SecurityProtocol"):
        asyncio.run(run_security_audit())
    complete_lines = [r.message for r in caplog.records if "audit complete" in r.message]
    assert complete_lines, "expected an 'audit complete' log line"
    assert any("advisory" in m.lower() for m in complete_lines), (
        f"audit-complete log should include advisory count; got: {complete_lines!r}"
    )

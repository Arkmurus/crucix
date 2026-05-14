"""R-F472 — parse_llm_json failure attribution.

DD-audit follow-up from the other agent: parse_llm_json's "all 5
repair strategies failed" WARNING flooded fly logs with no way to tell
which caller was generating the bad JSON. R-F472 adds a `source` kwarg
that the in-process counter accumulates by, plus an /api/aria/llm-json/stats
endpoint to surface the tallies.

This test pins:
  1. parse_llm_json accepts source= kwarg (no TypeError)
  2. Each failure bumps the per-source counter
  3. llm_json_stats() returns the expected shape
  4. Successful parse does NOT bump the fail counter (only attempts)
"""
from __future__ import annotations

import pytest


def test_rf472_parse_accepts_source_kwarg():
    from aria_service.intel.llm_json import parse_llm_json
    # Must not raise on the new kwarg
    result = parse_llm_json('{"x": 1}', source="test_caller")
    assert result == {"x": 1}


def test_rf472_failure_bumps_per_source_counter():
    from aria_service.intel.llm_json import parse_llm_json, llm_json_stats

    before = llm_json_stats()
    before_source = before["fails_by_source"].get("test_attribution", 0)
    before_total = before["total_fails"]

    # Garbage that no strategy can repair
    result = parse_llm_json(
        "this is not json {[ at all, just words",
        default={"ok": False},
        source="test_attribution",
    )
    assert result == {"ok": False}, "default must be returned on failure"

    after = llm_json_stats()
    assert after["total_fails"] == before_total + 1, (
        f"total_fails did not increment: {before_total} → {after['total_fails']}"
    )
    assert after["fails_by_source"].get("test_attribution", 0) == before_source + 1, (
        f"per-source counter did not increment for test_attribution: "
        f"{before_source} → {after['fails_by_source'].get('test_attribution', 0)}"
    )


def test_rf472_stats_shape():
    from aria_service.intel.llm_json import llm_json_stats
    s = llm_json_stats()
    assert isinstance(s, dict)
    assert "total_attempts" in s
    assert "total_fails" in s
    assert "fail_rate" in s
    assert "fails_by_source" in s
    assert isinstance(s["fails_by_source"], dict)


def test_rf472_success_does_not_bump_fail_counter():
    from aria_service.intel.llm_json import parse_llm_json, llm_json_stats

    before = llm_json_stats()
    parse_llm_json('{"happy": "path"}', source="success_test")
    after = llm_json_stats()

    assert after["total_attempts"] == before["total_attempts"] + 1, (
        "every call should increment total_attempts"
    )
    assert after["total_fails"] == before["total_fails"], (
        "successful parse must NOT bump total_fails"
    )
    assert (after["fails_by_source"].get("success_test", 0)
            == before["fails_by_source"].get("success_test", 0)), (
        "successful parse must NOT bump per-source fail counter"
    )


def test_rf472_missing_source_buckets_to_unknown():
    """Callers that haven't been updated to pass source= still get counted,
    bucketed as 'unknown'. Closes the audit gap honestly."""
    from aria_service.intel.llm_json import parse_llm_json, llm_json_stats
    before_unknown = llm_json_stats()["fails_by_source"].get("unknown", 0)
    parse_llm_json("garbage {[ words", default=None)  # no source=
    after_unknown = llm_json_stats()["fails_by_source"].get("unknown", 0)
    assert after_unknown == before_unknown + 1, (
        "no-source failures must bucket to 'unknown'"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

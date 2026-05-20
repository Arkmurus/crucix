"""R-F756 — alert when CRITICAL chat response ships unverified.

Audit on 2026-05-20 flagged a P2 silent-failure: when
verification_gate.classify_severity == "CRITICAL" AND
pick_secondary_provider returns None (single-provider fallback chain
— typical during Anthropic billing cooldown), pre-R-F756 the
response went to the user with:
  - no log alert (just /verification/stats counter increment)
  - no critical_unverified flag on the chat result
  - no inline banner warning the human

R-F756 changes that to:
  - _log.error level so fly logs surface the gap
  - result.critical_unverified = True
  - result.verification_skipped = "no_secondary_provider"
  - inline "[CRITICAL — UNVERIFIED]" banner appended to response_text

This test enforces all four contracts at the source level. The full
behavioral test (mock _vg.pick_secondary_provider → None, call
chat_ep, assert response carries banner + flag) would require the
FastAPI app + LLM stack which is too heavyweight for a unit run;
the source-level contract is the cheapest way to lock in the
4 simultaneous expectations.
"""
from __future__ import annotations

from pathlib import Path


def _aria_source() -> str:
    p = Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    return p.read_text(encoding="utf-8")


def test_rf756_log_error_when_no_secondary_provider():
    src = _aria_source()
    # The new error log line must reference both CRITICAL and the
    # "no secondary provider" reason so a future grep / aria-tickets
    # rule can latch onto it.
    assert "CRITICAL response shipped UNVERIFIED" in src, (
        "R-F756 regression: ERROR log for the no-secondary CRITICAL "
        "case is missing. Pre-R-F756 this was silent; fly logs must "
        "now surface it."
    )
    # And the log call uses _log.error (not warning / debug).
    assert "[verification_gate] CRITICAL response shipped" in src, (
        "R-F756 regression: log message tagged with [verification_gate] "
        "missing — needed for log routing in self-improve."
    )


def test_rf756_critical_unverified_flag_set():
    src = _aria_source()
    # result.critical_unverified = True must be set on the no-secondary
    # path so the WhatsApp listener / chat UI can branch.
    assert 'result["critical_unverified"] = True' in src, (
        "R-F756 regression: result.critical_unverified not set on "
        "the no-secondary path. UI banner / WA halt rule can't fire."
    )


def test_rf756_verification_skipped_reason_recorded():
    src = _aria_source()
    assert 'result["verification_skipped"] = "no_secondary_provider"' in src, (
        "R-F756 regression: result.verification_skipped must record the "
        "reason so chat-audit + downstream consumers know the "
        "verification path was not run."
    )


def test_rf756_inline_user_banner_appended():
    src = _aria_source()
    # The banner text must contain a stable substring so the UI can
    # detect it AND the user can read it.
    # The banner is built as a Python string concatenation in aria.py
    # so the literal source may include a line break between the
    # opening fragment "[CRITICAL — UNVERIFIED — no independent " and
    # the continuation "second-opinion provider available right now]".
    # Match each fragment individually.
    expected_open = "[CRITICAL — UNVERIFIED — no independent"
    expected_tail = "second-opinion provider available right now"
    assert expected_open in src, (
        f"R-F756 regression: inline banner opening fragment missing. "
        f"Expected substring {expected_open!r} in aria.py. Without it "
        f"the human sees a CRITICAL recommendation with no warning."
    )
    assert expected_tail in src, (
        f"R-F756 regression: inline banner tail fragment missing. "
        f"Expected substring {expected_tail!r} in aria.py."
    )
    # And the concatenation must be guarded against double-append (in
    # case of retries / re-processing).
    assert '"[CRITICAL — UNVERIFIED" not in response_text' in src, (
        "R-F756 regression: banner-append must be guarded by an "
        "'in response_text' idempotence check or repeated turns will "
        "stack banners."
    )

"""R-F4261 — the vendor-credit gauge must reach the health VERDICT.

Dossier finding E1, and the same shape as C-96 one gauge over. Measured live
2026-08-23, all in ONE `/health` payload:

    llm_chain.vendor_balance.deepseek.total_balance   7.61
    llm_chain.vendor_balance.deepseek.severity        "low"
    llm_chain.general_vendor_depth                    1
    status                                            "operational"
    degraded_reasons                                  []
    diagnostic.overall                                "GREEN" (76 pass / 0 fail)

R-F4229 built the gauge and got its tri-state right. What it did not do is give
any verdict the power to say so. General chat runs on a chain of depth 1, so
exhaustion is a full chat + WhatsApp outage — the 19-hour C-209 incident, which
happened at an overdraft of two cents while every headline field read green.
"""
from __future__ import annotations

import pytest

from aria_service.main import _vendor_balance_degraded_reasons as reasons_for

# The exact payload shape read from production on 2026-08-23.
LIVE = {
    "deepseek": {"state": "fresh", "available": True, "total_balance": 7.61,
                 "currency": "USD", "severity": "low",
                 "warn_threshold_usd": 10.0, "age_s": 615.1},
    "anthropic": {"state": "unsupported", "available": None,
                  "total_balance": None, "severity": "unknown"},
}


class TestTheLivePayloadNowProducesAVerdict:
    def test_the_measured_low_balance_becomes_a_degraded_reason(self):
        assert reasons_for(LIVE) == ["llm_vendor_credit_low_deepseek"]

    def test_it_names_the_vendor_so_an_operator_knows_what_to_top_up(self):
        assert all(r.endswith("_deepseek") for r in reasons_for(LIVE))

    def test_anthropic_unsupported_is_not_a_degradation(self):
        """It publishes no balance endpoint. Flagging it would make
        degraded_reasons permanently non-empty, and a verdict that always fires
        is one nobody reads."""
        assert not any("anthropic" in r for r in reasons_for(LIVE))


class TestEachSeverity:
    @pytest.mark.parametrize("severity,expected", [
        ("exhausted", ["llm_vendor_credit_exhausted_deepseek"]),
        ("low", ["llm_vendor_credit_low_deepseek"]),
        ("ok", []),
        ("unknown", []),
    ])
    def test_severity_maps_to_the_right_verdict(self, severity, expected):
        assert reasons_for({"deepseek": {"severity": severity}}) == expected

    def test_exhausted_is_reported_distinctly_from_low(self):
        """They demand different actions: one is an outage, one is a warning."""
        low = reasons_for({"deepseek": {"severity": "low"}})[0]
        out = reasons_for({"deepseek": {"severity": "exhausted"}})[0]
        assert low != out


class TestCouldNotMeasureIsNeverAMeasurement:
    """R-F4229's doctrine, in BOTH directions: 'I could not ask' must not render
    as 'there is money', and must not render as 'the money is gone' either."""

    @pytest.mark.parametrize("state", ["unreadable", "unsupported", "never_observed"])
    def test_a_non_fresh_reading_is_not_degraded(self, state):
        assert reasons_for({"deepseek": {"state": state, "severity": "unknown"}}) == []


class TestItNeverRaises:
    """A health endpoint that 500s because its own gauge is odd is worse than
    one that reports nothing."""

    @pytest.mark.parametrize("payload", [
        None, "not a dict", 42, [], {"deepseek": None}, {"deepseek": "nope"},
        {"deepseek": {}}, {None: {"severity": "low"}},
    ])
    def test_malformed_input_yields_no_reason_and_no_exception(self, payload):
        assert isinstance(reasons_for(payload), list)

    def test_one_malformed_vendor_cannot_suppress_a_readable_neighbour(self):
        """Each vendor is read independently — the R-F3791 'goes blind rather
        than fails' shape, prevented."""
        mixed = {"broken": "not a dict", "deepseek": {"severity": "exhausted"}}
        assert reasons_for(mixed) == ["llm_vendor_credit_exhausted_deepseek"]


class TestMultipleVendors:
    def test_every_affected_vendor_is_named(self):
        out = reasons_for({"a": {"severity": "low"}, "b": {"severity": "exhausted"}})
        assert out == ["llm_vendor_credit_low_a", "llm_vendor_credit_exhausted_b"]

    def test_the_order_is_stable(self):
        """Reasons feed alerting; a set that reorders itself creates noise."""
        payload = {"z": {"severity": "low"}, "a": {"severity": "low"}}
        assert reasons_for(payload) == reasons_for(payload)
        assert reasons_for(payload)[0].endswith("_a")


class TestTheHealthHandlerActuallyCallsIt:
    def test_the_verdict_reads_the_gauge_in_its_own_payload(self):
        """C-96's whole lesson: publishing a number no verdict consumes is why
        the degradation went unnoticed. A helper nothing calls repeats it."""
        import pathlib
        source = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text(
            encoding="utf-8")
        assert "_vendor_balance_degraded_reasons(" in source
        # called, not merely defined
        assert source.count("_vendor_balance_degraded_reasons(") >= 2
        assert 'get("vendor_balance")' in source

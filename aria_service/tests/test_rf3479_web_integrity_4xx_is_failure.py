"""R-F3479 — the integrity agent reported "9 passed, 0 failed" on a 4xx.

Measured live 2026-07-30, every cycle for the whole 15-cycle review window:

    "POST /api/aria/report HTTP/1.1" 400
    "GET /api/aria/autonomous/status HTTP/1.1" 403
    ... [web_integrity] cycle complete: 9 endpoints (8 local + 1 public),
        9 passed, 0 failed (0 critical), 0 patterns actionable

Two of nine endpoints returned 4xx on every single cycle and the agent called
them passes. Three separate defects, and they compound:

  1. A 4xx appends a WARNING and never sets ``passed = False`` — only 5xx fails
     (web_integrity_agent.py:256-259). The cycle summary counts passes, so the
     warnings are invisible on the surface an operator reads.

  2. ``expected`` fields are only checked on a 2xx (line 272). A permanently-4xx
     endpoint therefore has its content contract NEVER evaluated — the probe
     looks configured but verifies nothing. This is the "guard that cannot fire"
     class from memory/producer-consumer-no-carrier-defect.md.

  3. Both failing probes are STRUCTURALLY incapable of passing:
       - /api/aria/report is POSTed ``json={}`` (line 238) against a route that
         requires report_type + subject (routes/aria.py:15597) → guaranteed 400
         forever. Its declared expected={"sections","sources"} has never once
         been evaluated.
       - /api/aria/autonomous/status 403s because R-F2139 operator-tier scoping
         matches ``/api/aria/autonomous/`` (routes/aria.py:297) and the agent
         holds only the internal token. R-F2561's comment still says the token is
         attached "so probes don't 401" — they no longer 401, they 403.

Defect 3 is the same failure R-F2567 fixed for coder/llm, where the autonomous
coder held only ARIA_INTERNAL_TOKEN and got 403 on EVERY LLM call, so the
self-coding loop reported fixed=0. Same shape, different endpoint.

A monitor that cannot fail is worse than no monitor: it produces a green number
that suppresses the investigation.
"""
from __future__ import annotations

import pytest

from aria_service.intel import web_integrity_agent as wia


class _Resp:
    def __init__(self, status: int, payload=None, text: str = "{}") -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


class TestFourXxIsAFailure:

    def test_4xx_marks_the_check_failed(self):
        check = wia.IntegrityCheck(endpoint="/api/aria/report", method="POST",
                                   passed=True)
        wia._apply_status_verdict(check, _Resp(400), expected_status=None)
        assert check.passed is False, (
            "a 400 was recorded as a pass — this is what produced '9 passed, "
            "0 failed' while two endpoints 4xx'd every cycle"
        )

    def test_403_marks_the_check_failed(self):
        check = wia.IntegrityCheck(endpoint="/api/aria/autonomous/status",
                                   method="GET", passed=True)
        wia._apply_status_verdict(check, _Resp(403), expected_status=None)
        assert check.passed is False

    def test_5xx_still_fails(self):
        check = wia.IntegrityCheck(endpoint="/x", method="GET", passed=True)
        wia._apply_status_verdict(check, _Resp(500), expected_status=None)
        assert check.passed is False

    def test_2xx_still_passes(self):
        check = wia.IntegrityCheck(endpoint="/x", method="GET", passed=True)
        wia._apply_status_verdict(check, _Resp(200), expected_status=None)
        assert check.passed is True

    def test_a_declared_expected_status_is_honoured(self):
        """An endpoint that SHOULD reject (e.g. an auth probe) declares it, so
        the pass is deliberate and visible rather than an accident of the rule."""
        check = wia.IntegrityCheck(endpoint="/x", method="GET", passed=True)
        wia._apply_status_verdict(check, _Resp(403), expected_status=403)
        assert check.passed is True

    def test_an_undeclared_status_still_fails_even_if_expected_is_set(self):
        check = wia.IntegrityCheck(endpoint="/x", method="GET", passed=True)
        wia._apply_status_verdict(check, _Resp(500), expected_status=403)
        assert check.passed is False


class TestProbesCanActuallySucceed:
    """Defect 3 — a probe that cannot pass verifies nothing."""

    def test_report_probe_declares_the_rejection_it_expects(self):
        """Corrected during R-F3479's own verify pass 2.

        My first fix gave this probe a valid body so it would 200. That was
        wrong and expensive: build_report() calls the LLM, so a 60s monitor
        would have generated ~1,440 reports/day through the §17 cost cap. A
        monitor must not buy anything. Probing that the route REJECTS an invalid
        payload is a real input-validation check and costs nothing.
        """
        spec = next(e for e in wia.WEB_ENDPOINTS if e["path"] == "/api/aria/report")
        assert spec.get("expected_status") == 400, (
            "the /report probe must declare the 400 it expects from an empty body"
        )
        assert not spec.get("body"), (
            "the integrity monitor must not send a payload that triggers LLM "
            "spend on every 60s cycle (§17)"
        )

    def test_no_probe_can_trigger_metered_work(self):
        """Stop the class: a monitor that buys something is a cost leak that
        scales with uptime."""
        for spec in wia.WEB_ENDPOINTS:
            if spec.get("method") == "POST" and spec.get("body"):
                assert spec.get("free") is True, (
                    f"{spec['path']} POSTs a payload on every cycle — if that "
                    f"path is metered this is a standing cost leak; mark it "
                    f"free=True only when it provably costs nothing"
                )

    def test_every_post_probe_declares_a_body_or_an_expected_status(self):
        """A POST with no body against a validating route is a permanent 400
        dressed up as a check."""
        for spec in wia.WEB_ENDPOINTS:
            if spec.get("method") == "POST":
                assert spec.get("body") or spec.get("expected_status"), (
                    f"{spec['path']} is POSTed with no body and no declared "
                    f"expected_status — it cannot verify anything"
                )

    def test_operator_scoped_probe_verifies_for_real_when_it_can(self):
        """/api/aria/autonomous/ is operator-tier (R-F2139) and the agent held
        only the internal token, so it 403d on every cycle while scoring a pass.

        Declaring the 403 would be honest but BLIND — the endpoint's
        {ok, engine} contract would never be checked again. The agent runs
        in-process against localhost, so it can hold the operator token and
        verify for real; the declared 403 is only the fallback for when
        ARIA_OPERATOR_TOKEN is unset. Honest either way, and covered when
        possible.
        """
        spec = next((e for e in wia.WEB_ENDPOINTS
                     if e["path"] == "/api/aria/autonomous/status"), None)
        if spec is None:
            pytest.skip("probe removed — acceptable resolution")
        assert spec.get("operator_scoped") is True, (
            "the probe must escalate to the operator token, or it can only 403"
        )
        assert spec.get("expected") == {"ok", "engine"}, (
            "with the operator token the real response contract must be checked"
        )
        assert spec.get("expected_status_without_operator_token") == 403, (
            "without the token the probe must declare the 403 it will get, so "
            "the rejection is visible rather than a silent pass"
        )

    def test_operator_token_is_used_only_for_scoped_probes(self, monkeypatch):
        """The operator token must not be sprayed at every endpoint."""
        monkeypatch.setenv("ARIA_OPERATOR_TOKEN", "op-secret")
        monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "internal-secret")
        plain = wia._probe_auth_headers({"path": "/health/live"})
        scoped = wia._probe_auth_headers({"path": "/x", "operator_scoped": True})
        assert "op-secret" not in plain.get("Authorization", "")
        assert "op-secret" in scoped.get("Authorization", "")

    def test_scoped_probe_falls_back_to_internal_token(self, monkeypatch):
        monkeypatch.delenv("ARIA_OPERATOR_TOKEN", raising=False)
        monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "internal-secret")
        hdrs = wia._probe_auth_headers({"path": "/x", "operator_scoped": True})
        assert "internal-secret" in hdrs.get("Authorization", "")


class TestExpectedFieldsAreCheckedWhenTheStatusIsExpected:

    def test_expected_fields_checked_on_2xx(self):
        check = wia.IntegrityCheck(endpoint="/x", method="GET", passed=True)
        wia._apply_field_verdict(check, _Resp(200, {"status": "ok"}), {"status"})
        assert check.passed is True

    def test_missing_expected_field_fails(self):
        check = wia.IntegrityCheck(endpoint="/x", method="GET", passed=True)
        wia._apply_field_verdict(check, _Resp(200, {"other": 1}), {"status"})
        assert check.passed is False
        assert any("status" in e for e in check.errors)

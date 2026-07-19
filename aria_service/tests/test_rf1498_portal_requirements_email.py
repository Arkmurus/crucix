"""R-F1498 capability test — ARIA emails the operator what each un-registerable portal needs.

Most pending portals fundamentally can't auto-register (CAPTCHA, email verification,
anti-bot, paywalls). Rather than leave them perpetually 'pending', ARIA emails the
operator EXACTLY what each needs — free API key / free signup / CAPTCHA-manual / paid —
with the signup URL + env var to set. Paid services are framed as the operator's
decision, never auto-pursued (§6/§18). send_email is mocked — no real email sent.
"""
import os
from unittest.mock import patch

import pytest

from aria_service.intel import portal_registry as pr  # noqa: E402


# R-F2801 — this module used to do `os.environ.setdefault("ARIA_OPERATOR_EMAIL",
# "op@imaria.io")` AT IMPORT TIME. That is a process-global mutation no
# monkeypatch ever undoes, so it leaked into every later test in the run. It
# silently broke test_memory_replication::test_stats_reports_live_when_smtp_configured,
# whose `email_destination` resolves ARIA_OPERATOR_EMAIL ahead of ARIA_SMTP_USER
# (memory_replication.py:632-637) — that test passed alone and failed in-suite,
# which is the signature of exactly this anti-pattern.
#
# Scoped to the tests that need it instead. autouse keeps them working unchanged
# while monkeypatch guarantees teardown.
@pytest.fixture(autouse=True)
def _operator_email(monkeypatch):
    monkeypatch.setenv("ARIA_OPERATOR_EMAIL", "op@imaria.io")


@pytest.mark.asyncio
async def test_requirements_email_classifies_and_composes():
    captured = {}

    def fake_send(**kw):
        captured.update(kw)
        return {"sent": True}

    with patch("aria_service.integrations.email_outbound.send_email", fake_send):
        res = await pr.email_portal_requirements_to_operator()

    assert res["to"], "must resolve an operator recipient"
    c = res["counts"]
    assert c["paid"] >= 1, "paid portals must be flagged"
    assert c["captcha"] >= 1, "CAPTCHA portals must be flagged"
    assert c["candidates"] >= 1, "un-integrated portals listed as candidates (not 'set a key')"
    # R-F1501: no data-fetcher reads a portal key, so nothing is activatable by a key alone.
    assert c["actionable"] == 0, "no portal is wired yet — actionable must be 0"
    body = captured["body"]
    assert "CANDIDATE SOURCES" in body and "NOT built" in body, "honest about un-integrated sources"
    assert "PAID" in body and "will not auto-pay" in body
    assert "RSS" in body, "honestly notes news already works via RSS"
    assert "set NEWSAPI" not in body and "set GNEWS" not in body, "never instruct setting a key no code reads"


@pytest.mark.asyncio
async def test_no_operator_email_returns_unsent_not_crash():
    with patch.dict(os.environ, {"ARIA_OPERATOR_EMAIL": "", "ARIA_EMAIL_OPERATOR_ALLOWLIST": "",
                                 "ARIA_SMTP_USER": ""}, clear=False):
        res = await pr.email_portal_requirements_to_operator()
    assert res["sent"] is False
    assert "no operator email" in res.get("error", "")

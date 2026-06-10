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

os.environ.setdefault("ARIA_OPERATOR_EMAIL", "op@arkmurus.com")
from aria_service.intel import portal_registry as pr  # noqa: E402


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
    assert c["paid"] >= 1, "paid portals (Crunchbase/PitchBook/etc.) must be flagged"
    assert c["captcha"] >= 1, "CAPTCHA portals must be flagged"
    assert (c["free_key"] + c["free_signup"]) >= 1, "free portals must be listed"
    body = captured["body"]
    assert "what each needs from you" in body
    assert "PAID" in body and "CAPTCHA" in body
    assert "will not auto-pay" in body, "paid framed as operator decision, never auto-pursued"


@pytest.mark.asyncio
async def test_no_operator_email_returns_unsent_not_crash():
    with patch.dict(os.environ, {"ARIA_OPERATOR_EMAIL": "", "ARIA_EMAIL_OPERATOR_ALLOWLIST": "",
                                 "ARIA_SMTP_USER": ""}, clear=False):
        res = await pr.email_portal_requirements_to_operator()
    assert res["sent"] is False
    assert "no operator email" in res.get("error", "")

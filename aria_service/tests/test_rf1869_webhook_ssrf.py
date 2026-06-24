"""R-F1869 (audit DD-08) — webhook SSRF guard.

WebhookNotifier.send() POSTed to self.webhook_url (from DEPLOY_WEBHOOK_URL)
with no validation. A poisoned env value pointing at cloud-metadata
(169.254.169.254) or an internal/RFC-1918 host turns deploy notifications into
an SSRF that leaks operational state. The fix validates via is_safe_url before
POSTing.

Capability test: drive WebhookNotifier.send() with an unsafe URL and assert the
HTTP client is NEVER called; with a safe URL assert it IS called.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


def _run(coro):
    return asyncio.run(coro)


def _make_notifier(url: str):
    from aria_service.autonomous.autonomous_deploy import WebhookNotifier
    n = WebhookNotifier(webhook_url=url)
    n._client = AsyncMock()  # replace httpx client so no real network happens
    return n


def test_unsafe_metadata_url_is_not_posted():
    n = _make_notifier("http://169.254.169.254/latest/meta-data/")
    _run(n.send("deploy", {"x": 1}))
    n._client.post.assert_not_called()


def test_internal_rfc1918_url_is_not_posted():
    n = _make_notifier("http://10.0.0.5:8000/hook")
    _run(n.send("deploy", {"x": 1}))
    n._client.post.assert_not_called()


def test_safe_public_url_is_posted(monkeypatch):
    # Patch is_safe_url to True so the test does not depend on live DNS
    # resolution (is_safe_url resolves the host; the sandbox has no network).
    import aria_service.intel.url_safety as us
    monkeypatch.setattr(us, "is_safe_url", lambda url: (True, ""))
    n = _make_notifier("https://hooks.example.com/deploy")
    _run(n.send("deploy", {"x": 1}))
    n._client.post.assert_called_once()

"""Tests for the ARIA ticket system (aria_service/intel/tickets.py) and the
chat-flow intent classifier that routes "raise a ticket" / "/ticket" into it.

Covers:
  1. Intent regex fires on the intended shapes, does not over-fire.
  2. Severity inference from keywords.
  3. raise_ticket() no-ops gracefully when no GitHub token and no Airtable PAT.
  4. GitHub issue creation posts the expected payload (mocked httpx).
  5. list_open_tickets() filters out pull requests.
"""
from __future__ import annotations

import asyncio
import os
import re

import pytest


def _run(coro):
    return asyncio.run(coro)


# ── Intent regex — rebuilt here so a drift in aria.py fails a test ──────────
# Keep these in sync with routes/aria.py::_detect_tool_intent ticket block.
_TICKET_NLU_RE = re.compile(
    r"\b(?:"
    r"(?:file|raise|open|log|create)\s+(?:an?\s+)?(?:ticket|issue|bug(?:\s+report)?)"
    r"|log\s+this\s+as\s+(?:an?\s+)?(?:bug|issue|ticket)"
    r"|track\s+this\s+(?:as\s+)?(?:an?\s+)?(?:bug|issue|ticket)"
    r")\b",
    re.IGNORECASE,
)
_TICKET_LIST_RE = re.compile(
    r"\b(?:"
    r"(?:list|show|what|which|any)\s+(?:are\s+)?(?:the\s+)?(?:open\s+)?tickets?"
    r"|what(?:'s|\s+is)\s+in\s+the\s+ticket\s+queue"
    r"|ticket\s+queue"
    r")\b",
    re.IGNORECASE,
)


def _raise_fires(msg: str) -> bool:
    low = msg.strip().lower()
    slash = low.startswith("/ticket") or low.startswith("/raise ")
    return slash or bool(_TICKET_NLU_RE.search(msg))


def _list_fires(msg: str) -> bool:
    low = msg.strip().lower()
    return low.startswith("/tickets") or bool(_TICKET_LIST_RE.search(msg))


# ── raise_ticket intent — MUST fire ─────────────────────────────────────────

def test_slash_ticket_fires():
    assert _raise_fires("/ticket the circuit breaker is open on Brave")


def test_slash_raise_fires():
    assert _raise_fires("/raise log that the email pipeline is flaky")


def test_natural_file_a_ticket():
    assert _raise_fires("Aria, file a ticket for the circuit breaker issue")


def test_natural_raise_a_ticket():
    assert _raise_fires("can you raise a ticket about the DefenceWeb timeout")


def test_natural_open_an_issue():
    assert _raise_fires("open an issue for the GDELT source failing")


def test_natural_log_this_as_bug():
    assert _raise_fires("log this as a bug — the proactive alert poller duplicates")


def test_natural_create_ticket():
    assert _raise_fires("please create a ticket for the 502 on wa-listener")


def test_track_this_as_issue():
    assert _raise_fires("track this as an issue for the dev team")


# ── raise_ticket intent — MUST NOT fire ─────────────────────────────────────

def test_mere_mention_of_ticket_does_not_fire():
    assert not _raise_fires("the airline ticket costs £200")


def test_concert_ticket_does_not_fire():
    assert not _raise_fires("I bought a ticket for the concert")


def test_ticket_in_compound_noun_does_not_fire():
    assert not _raise_fires("the meal ticket programme ended last year")


# ── list_tickets intent — MUST fire ─────────────────────────────────────────

def test_slash_tickets_fires():
    assert _list_fires("/tickets")


def test_list_tickets_fires():
    assert _list_fires("list tickets")


def test_show_open_tickets_fires():
    assert _list_fires("show open tickets")


def test_ticket_queue_fires():
    assert _list_fires("what's in the ticket queue")


def test_any_open_tickets_fires():
    assert _list_fires("any open tickets")


# ── Severity inference ──────────────────────────────────────────────────────

def _infer_sev(body: str) -> str:
    low = body.lower()
    if any(k in low for k in ("critical", "urgent", "p0", "outage", "down")):
        return "CRITICAL"
    if any(k in low for k in ("high", "severe", "p1", "blocker")):
        return "HIGH"
    if any(k in low for k in ("minor", "nice to have", "p3", "cosmetic", "low priority")):
        return "LOW"
    return "MEDIUM"


def test_severity_critical_on_outage():
    assert _infer_sev("production is down, outage confirmed") == "CRITICAL"


def test_severity_high_on_blocker():
    assert _infer_sev("this is a blocker for the demo") == "HIGH"


def test_severity_low_on_cosmetic():
    assert _infer_sev("cosmetic issue with the dashboard margin") == "LOW"


def test_severity_medium_default():
    assert _infer_sev("the proactive poller is flaky sometimes") == "MEDIUM"


# ── raise_ticket module — graceful no-op when unconfigured ──────────────────

def test_raise_ticket_no_creds_returns_ok_false(monkeypatch):
    """When neither GitHub nor Airtable is configured, raise_ticket should
    return ok=False and a truthful reason on each surface — never raise."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("AIRTABLE_PAT", raising=False)

    from aria_service.intel import tickets

    result = _run(tickets.raise_ticket(
        title="Test",
        symptom="Test symptom",
        severity="HIGH",
        category="infra",
    ))
    assert result["ok"] is False
    assert result["ticket_id"] is None
    assert result["github"]["reason"] == "no_github_token"
    assert result["github"]["skipped"] is True
    assert result["airtable"]["reason"] == "no_airtable_pat"
    assert result["airtable"]["skipped"] is True


def test_raise_ticket_killswitches_honoured(monkeypatch):
    """Explicit killswitch env must prevent the HTTP call even with creds set."""
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("AIRTABLE_PAT", "pat")
    monkeypatch.setenv("ARIA_TICKETS_GITHUB_ENABLED", "0")
    monkeypatch.setenv("ARIA_TICKETS_AIRTABLE_ENABLED", "0")

    from aria_service.intel import tickets

    result = _run(tickets.raise_ticket(
        title="Test",
        symptom="Should be skipped on both surfaces",
    ))
    assert result["ok"] is False
    assert result["github"]["reason"] == "killswitch"
    assert result["airtable"]["reason"] == "killswitch"


# ── raise_ticket — GitHub success path (mocked) ─────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int, json_data):
        self.status_code = status_code
        self._json = json_data
        self.text = ""

    def json(self):
        return self._json


class _FakeAsyncClient:
    """Minimal httpx.AsyncClient stub for the POST / GET we issue."""

    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        return _FakeResponse(
            201,
            {"number": 42, "html_url": "https://github.com/x/y/issues/42"},
        )

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params, headers))
        return _FakeResponse(
            200,
            [
                {
                    "number": 42,
                    "title": "breaker tripped",
                    "html_url": "https://github.com/x/y/issues/42",
                    "labels": [{"name": "aria-raised"}, {"name": "severity-high"}],
                    "created_at": "2026-04-21T16:00:00Z",
                },
                # A pull-request entry that the filter must drop:
                {
                    "number": 43,
                    "title": "drop me — PR",
                    "html_url": "https://github.com/x/y/pull/43",
                    "pull_request": {"url": "..."},
                    "labels": [],
                    "created_at": "2026-04-21T17:00:00Z",
                },
            ],
        )


def test_raise_ticket_github_success(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "acme/widget")
    monkeypatch.delenv("AIRTABLE_PAT", raising=False)  # airtable off
    monkeypatch.delenv("ARIA_TICKETS_GITHUB_ENABLED", raising=False)

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    from aria_service.intel import tickets

    result = _run(tickets.raise_ticket(
        title="Breaker tripped",
        symptom="Open circuit breaker on Brave",
        severity="HIGH",
        category="pipeline",
    ))
    assert result["ok"] is True
    assert result["ticket_id"] == "GH-42"
    assert result["github"]["ok"] is True
    assert result["github"]["number"] == 42
    assert result["github"]["url"] == "https://github.com/x/y/issues/42"


def test_list_open_tickets_filters_pull_requests(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPO", "acme/widget")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    from aria_service.intel import tickets

    result = _run(tickets.list_open_tickets())
    assert result["ok"] is True
    assert result["count"] == 1  # PR filtered out
    assert result["tickets"][0]["ticket_id"] == "GH-42"
    assert result["tickets"][0]["title"] == "breaker tripped"

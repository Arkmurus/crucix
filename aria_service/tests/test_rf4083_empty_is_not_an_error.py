"""R-F4083 (C-131) — an empty search result was counted, and rendered, as a
failure.

Caught reviewing my own R-F4064 (C-118). That change surfaced Brave's error
COUNT as a red "Fail rate 42%" — correctly reasoning that a bare count reads as
small next to a bigger count. What it did not check was what the count contained.

Measured live 2026-08-16:

    /search/health.brave_usage.monthly
        {"total": 234, "ok": 135, "empty": 99}
        rate_limited: 0   auth_failed: 0   http_error: 0   timeout: 0

Every single non-`ok` outcome was **`empty`** — Brave returned HTTP 200 and
found nothing. There were no errors at all. But `_record_spend` passed
`success=(outcome == "ok")`, so all 99 landed in `errors` on `/cost/external`,
and the panel painted 42% in red.

**A search engine answering "no results" was being reported as broken** — and
for an obscure DD subject, no results is frequently the correct answer.

This is the same defect class as the twelve this batch was opened to fix: a
state that is not a failure, rendered as one. Committed while fixing them, which
is the part worth remembering — the fix for a class of defect is exactly where
the next instance of that class gets introduced.

The empty RATE is still a real signal about search quality and is still
measured. It lives on `/search/health.brave_usage.monthly` under the name
`empty`, where it says what it is instead of being dressed as an error.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


async def _spend(outcome: str, status: int | None = 200):
    from aria_service.intel import brave_usage as bu
    from aria_service.intel import cost_tracker as ct

    rec = AsyncMock(return_value={})
    with patch.object(ct, "record_brave_call", rec):
        await bu._record_spend(outcome, status)
    assert rec.await_count == 1, "the call was not metered at all"
    return rec.await_args.kwargs


@pytest.mark.asyncio
async def test_empty_is_not_recorded_as_an_error():
    kw = await _spend("empty")
    assert kw["success"] is True, (
        "an empty result set is an answer, not an error; live this put 99 of "
        "234 calls into the error count and rendered 42% red")


@pytest.mark.asyncio
async def test_ok_is_still_a_success():
    assert (await _spend("ok"))["success"] is True


@pytest.mark.parametrize("outcome", ["rate_limited", "auth_failed",
                                     "http_error", "timeout"])
@pytest.mark.asyncio
async def test_real_failures_are_still_errors(outcome):
    """The guard must still be able to fire, or it stops being a guard: these
    are the outcomes that mean the engine genuinely did not answer."""
    kw = await _spend(outcome, status=None if outcome == "timeout" else 500)
    assert kw["success"] is False, outcome


@pytest.mark.asyncio
async def test_a_timeout_is_never_billed():
    """Pre-existing contract worth pinning while here: no HTTP response means
    no query was served, so it is an attempt at cost 0.0 — never hidden, never
    charged."""
    kw = await _spend("timeout", status=None)
    assert kw["cost_per_call_usd"] == 0.0
    assert kw["success"] is False


def test_the_panel_column_says_error_not_fail():
    """The label has to match the meaning, or the fix is half-done."""
    import pathlib
    page = (pathlib.Path(__file__).resolve().parents[2] / "public"
            / "aria-brain.html").read_text(encoding="utf-8")
    assert ">Error rate<" in page, "the column still claims a general failure rate"
    assert ">Fail rate<" not in page

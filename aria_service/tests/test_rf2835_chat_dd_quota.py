"""R-F2835 — a chat-triggered DD must consume the caller's plan quota.

THE DEFECT. The per-tier DD cap (5/month on free, lib/billing/tiers.mjs) was
enforced ONLY on the web route (server.mjs:3546). A DD triggered from CHAT runs as a
tool inside the brain and never traverses that route, so it consumed nothing — a
grep of aria_service/ for ddRunsPerMonth|dd_runs_per_month|dd_quota returned ZERO
hits. A free user capped at 50 messages/day could therefore trigger up to 50 DD runs
per day: TEN TIMES the monthly cap, every day. Revenue leak and §17 cost exposure.

WHERE THE GATE LIVES, AND WHY. Not in the two chat handlers. §13's stream-bypass
rule exists precisely because aria_chat and aria_chat_stream are a fork, and a hook
added to one and forgotten in the other is the recurring defect in this codebase.
Every DD — chat, web, watchlist trigger, and anything added later — funnels through
orchestrate_dd(), so a single gate there cannot be bypassed by a new caller.

DESIGN CONSTRAINTS THIS PINS DOWN:
  * a quota block is an EXCEPTION, never a report. A DD report saying "no findings"
    because the plan ran out would be a false clean.
  * the web path must NOT double-charge — it already consumed a unit before
    proxying, so it passes quota_charged=True.
  * system / autonomous runs have no user_id and are exempt; they are governed by
    the §17 cost cap, not a customer plan.
  * the quota hop FAILS OPEN. Denying a paying customer because an internal hop
    hiccuped is worse than one uncounted run, and the $300/mo cap is the backstop.
    But it is never silent (§21a).
"""
import asyncio

import pytest

from aria_service.intel import quota_client as QC


@pytest.mark.asyncio
async def test_no_user_id_is_exempt():
    """Autonomous/system runs carry no customer identity and must not be blocked."""
    out = await QC.consume_dd_quota("")
    assert out["allowed"] is True
    assert out.get("exempt") == "no_user_id"


@pytest.mark.asyncio
async def test_unconfigured_service_fails_open_but_says_so(monkeypatch):
    """Missing config must not silently stop counting — §21a."""
    monkeypatch.delenv("ARIA_WEB_INTERNAL_URL", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    out = await QC.consume_dd_quota("user-1")
    assert out["allowed"] is True, "a config gap must not deny a paying user"
    assert out.get("degraded") is True, (
        "an uncounted run must be reported as degraded, not as a clean allowance"
    )


@pytest.mark.asyncio
async def test_an_unrecognised_response_is_degraded_not_a_silent_pass(monkeypatch):
    """Never infer allowance from an absent field.

    'No verdict therefore allowed' is the certified-by-an-absence shape that
    produced three fabricated Phase A gates this month.
    """
    monkeypatch.setenv("ARIA_WEB_INTERNAL_URL", "http://web.test")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"something": "unexpected"}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    out = await QC.consume_dd_quota("user-1")
    assert out["allowed"] is True and out.get("degraded") is True


def test_orchestrator_gates_on_quota_at_the_single_choke_point():
    """The gate must live in orchestrate_dd, not in the two chat handlers."""
    import inspect
    from aria_service.intel import dd_orchestrator as DDO

    src = inspect.getsource(DDO.orchestrate_dd)
    assert "consume_dd_quota" in src, (
        "orchestrate_dd must consult the plan quota — gating in the chat handlers "
        "instead would repeat the §13 stream-bypass defect, where a hook added to "
        "aria_chat and forgotten in aria_chat_stream silently does nothing"
    )
    assert "quota_charged" in src, (
        "the web path already consumed a unit before proxying; without this flag it "
        "would be double-charged"
    )


def test_a_quota_block_is_an_exception_not_a_report():
    """A DD report produced by a quota block would read as 'no findings' — a false clean."""
    from aria_service.intel import dd_orchestrator as DDO

    assert hasattr(DDO, "DDQuotaExceeded")
    exc = DDO.DDQuotaExceeded("ddRun cap reached (5/5)", current=5, cap=5)
    assert exc.current == 5 and exc.cap == 5
    assert "cap reached" in str(exc), "the reason must reach the user, not a bare code"


def test_web_route_declares_itself_already_charged():
    """Belt and braces: the proxied web route must not be double-counted."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    text = src.read_text(encoding="utf-8")
    assert "quota_charged=True" in text, (
        "the /dd/orchestrate route is reached AFTER server.mjs:3546 has already "
        "consumed a unit; it must declare that or the user is charged twice"
    )

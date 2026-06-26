"""R-F1954 — per-user monthly cost sub-cap (capability test).

The global $300/month cap is SHARED, so one heavy user could drain it and
hard-fail the whole team. R-F1954 adds a per-user monthly budget, wired off the
real cost path (cost_tracker.record_call), enforced in the chat handler.

These tests drive the real accounting/enforcement helpers and assert the
user-visible property: a user who has spent their monthly budget is refused
while a different user under budget is unaffected; operators bypass the cap.
"""
import asyncio
import os

from aria_service.intel import cost_tracker as ct


def _fresh(user):
    # clear any in-process accumulation for a deterministic test
    ct._user_month_mem.pop(user, None)


def test_user_attribution_contextvar_roundtrips():
    tok = ct.set_user("wa_351900000001")
    try:
        assert ct.get_current_user() == "wa_351900000001"
    finally:
        ct._current_user.reset(tok)


def test_spend_accumulates_per_user_and_caps_one_without_the_other():
    async def _run():
        os.environ["ARIA_USER_MONTHLY_COST_USD_CAP"] = "20"
        a, b = "wa_aaa1111", "wa_bbb2222"
        _fresh(a); _fresh(b)

        # User A spends past the $20 cap; user B spends a little.
        await ct._record_user_month_spend(a, 21.0)
        await ct._record_user_month_spend(b, 2.0)

        assert await ct.get_user_month_spend(a) >= 20.0
        assert await ct.get_user_month_spend(b) == 2.0

        over_a, spent_a, cap_a = await ct.user_month_cap_exceeded(a)
        over_b, spent_b, cap_b = await ct.user_month_cap_exceeded(b)
        assert over_a is True, "user A is over their monthly budget -> capped"
        assert over_b is False, "user B is under budget -> MUST still be served"
        assert cap_a == 20.0
    asyncio.run(_run())


def test_unlimited_operator_never_capped():
    async def _run():
        os.environ["ARIA_USER_MONTHLY_COST_USD_CAP"] = "20"
        os.environ["ARIA_USER_QUOTA_UNLIMITED"] = "ops"
        u = "ops_console"
        _fresh(u)
        await ct._record_user_month_spend(u, 9999.0)
        over, spent, cap = await ct.user_month_cap_exceeded(u)
        assert over is False, "allow-listed operator must bypass the per-user cap"
    asyncio.run(_run())


def test_zero_cap_disables_enforcement():
    async def _run():
        os.environ["ARIA_USER_MONTHLY_COST_USD_CAP"] = "0"
        u = "wa_zzz9999"
        _fresh(u)
        await ct._record_user_month_spend(u, 500.0)
        over, _, _ = await ct.user_month_cap_exceeded(u)
        assert over is False, "cap<=0 means disabled — never refuse"
    asyncio.run(_run())

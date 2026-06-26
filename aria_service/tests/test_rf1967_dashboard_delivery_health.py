"""R-F1967 — surface delivery health on the ecosystem dashboard (capability test).

Delivery health was computable (/api/aria/outcome/health) but INVISIBLE on the
operator's ecosystem dashboard, so ARIA couldn't answer "did I deliver?" at a
glance. R-F1967 adds get_all_surface_health() and wires it into
get_ecosystem_status as `delivery_health`.
"""
import asyncio

from aria_service.intel import outcome_wire as ow
from aria_service.intel.outcome_wire import OutcomeRecord, record_outcome, get_all_surface_health


def test_all_surface_health_shape_and_worst_summary():
    async def run():
        # Record a success + a failure on one surface so it has a <100% rate.
        await record_outcome(OutcomeRecord("email", "rf1967-ok", "send", "delivered_real_answer", 10))
        await record_outcome(OutcomeRecord("email", "rf1967-bad", "send", "error", 10, "boom"))
        res = await get_all_surface_health(24)
        assert "surfaces" in res and "worst_surface" in res and "worst_success_rate" in res
        email = res["surfaces"].get("email")
        assert email is not None and email["total"] >= 2
        assert email["success_rate"] < 1.0, "a surface with a failure must show <100% success"
        # The worst surface summary must point at a real failing channel.
        assert res["worst_surface"] in res["surfaces"]
        assert res["worst_success_rate"] is not None
    asyncio.run(run())


def test_dashboard_includes_delivery_health_key():
    from aria_service.intel import ecosystem_dashboard as ed
    status = asyncio.run(ed.get_ecosystem_status())
    assert "delivery_health" in status, "ecosystem dashboard must expose per-channel delivery health"
    assert "surfaces" in status["delivery_health"]


def test_known_surfaces_cover_the_real_channels():
    for s in ("wa", "web", "tg", "email", "api"):
        assert s in ow.KNOWN_SURFACES

"""R-F4359 (C-305) — ARIA's own reasoning must not be capped on money that was
never spent, nor gated on somebody else's vendor bill.

OPERATOR, 2026-08-26: *"aria is no longer using third party reasoning therefore
there is no need to have a monthly cap in aria itself as well as the operators
and admin teams"*, and earlier: *"vendor spend will be assigned via their
respective accounts, their membership tier … we cannot have that cap on the
actual aria but on the users only"*.

TWO DEFECTS, ONE CONSEQUENCE — ARIA goes dark.

**1. The sovereign is billed at Claude rates.** `aria-llm-v0.4-dpo` is absent
from `PRICING`, so `_get_price` falls through to `DEFAULT_PRICING`, which
R-F2766 deliberately set to Claude Sonnet `(3.00, 15.00)` *"ahead of the
DeepSeek->Claude switch"*. The pod costs **$0.44/hr flat** — its marginal cost
per token is zero. The table already encodes this idea: `llama3.1:70b` is priced
`(0.0, 0.0)` because it is self-hosted. The sovereign was simply never added.

Measured live 2026-08-26: `aria_llm` had been charged **$14.02 for 3.5M tokens**
it did not cost. And with `ARIA_LLM_PRIMARY_ALL=1` all traffic is now hers —
this month's 323,380,129 DeepSeek tokens actually cost **$67.72**, and the same
volume at `DEFAULT_PRICING` prices out at **$2,134** against a **$600** cap.

**2. The cap gate is provider-blind.** `MeteredProvider._enforce_spend_caps`
calls `assert_daily_cap()` + `assert_monthly_cap()` before EVERY call, whatever
the provider. So even priced at zero, ARIA's reasoning would still be blocked
the moment unrelated PAID spend (a DD run pinned to Anthropic, per §17 RULE ONE)
crossed the cap. `assert_monthly_cap` RAISES — the call does not degrade, it
does not happen.

THE RULE, and it needs no policy carve-outs: **a cap counts dollars at risk. A
flat-rate provider puts none at risk, so it is neither metered nor gated.**
That one fact delivers every clause of the directive at once —

  * ARIA's reasoning is 100% sovereign, so it is never capped and never dark;
  * operator and admin reasoning rides the same provider, so it is uncapped too;
  * per-user caps still bind on VENDOR-backed work, which is the spend the
    operator is assigning to accounts by tier;
  * genuine vendor spend keeps its brake — §17 records Anthropic credit
    exhaustion taking DD down, and removing that brake would trade one outage
    for another.

DERIVED, NOT A SECOND LIST. Zero-cost is read from the PRICING table itself, so
a provider cannot be uncapped in one place and billed in another. An UNKNOWN
name resolves to `DEFAULT_PRICING`, i.e. NOT zero — an unrecognised provider is
treated as paid and stays capped, which is the safe direction.
"""
from __future__ import annotations

import pytest

from aria_service.intel import cost_tracker as ct


# ── 1. the sovereign costs nothing per token ───────────────────────────────

@pytest.mark.parametrize("model", [
    "aria-llm-v0.4-dpo",     # the live adapter
    "aria-llm-v0.1",         # the code default
    "aria-llm-base",         # what vLLM echoes back for a LoRA request
    "aria-llm-v0.7-grpo",    # a future adapter nobody has added yet
])
def test_every_sovereign_adapter_is_free(model: str) -> None:
    """Matched by SHAPE, not enumerated. A list would rot on the next adapter —
    which is exactly how this defect arrived, since the table already prices
    self-hosted llama at (0.0, 0.0) and the sovereign was never added."""
    assert ct._get_price(model) == (0.0, 0.0), (
        f"{model} is billed at vendor rates; it runs on a flat-rate pod")


def test_a_sovereign_call_estimates_zero() -> None:
    """The user-visible consequence: 3.5M tokens on the pod cost nothing."""
    assert ct.estimate_cost_usd("aria-llm-v0.4-dpo", 2_500_000, 1_000_000) == 0.0


def test_paid_vendors_are_untouched() -> None:
    """THE COUNTER-GUARD. This must not become a blanket zeroing of the meter —
    vendor spend is real, and §17 records an exhausted Anthropic key taking DD
    down."""
    assert ct._get_price("claude-opus-4-8") != (0.0, 0.0)
    assert ct._get_price("deepseek-v4-flash") != (0.0, 0.0)
    assert ct.estimate_cost_usd("claude-opus-4-8", 1_000_000, 100_000) > 0.0


def test_an_unknown_model_is_still_treated_as_paid() -> None:
    """FAIL-SAFE DIRECTION. An unrecognised name must not be assumed free —
    under-counting is the failure DEFAULT_PRICING exists to prevent."""
    assert ct._get_price("some-new-vendor-model-x") == ct.DEFAULT_PRICING
    assert ct.is_zero_marginal_cost("some-new-vendor-model-x") is False


# ── 2. zero-cost providers are not gated on vendor spend ───────────────────

@pytest.mark.parametrize("name", ["aria_llm", "aria-llm", "ARIA_LLM",
                                  "aria-llm-v0.4-dpo"])
def test_the_sovereign_is_recognised_however_it_is_spelled(name: str) -> None:
    """The chain calls it `aria_llm`; the model id is `aria-llm-*`. Both must
    resolve through the ONE pricing table, or a provider ends up uncapped in one
    place and billed in another."""
    assert ct.is_zero_marginal_cost(name) is True


@pytest.mark.parametrize("name", ["anthropic", "deepseek", "openai", "", None])
def test_paid_and_unknown_providers_stay_capped(name) -> None:
    assert ct.is_zero_marginal_cost(name) is False


@pytest.mark.asyncio
async def test_a_zero_cost_provider_is_not_gated_on_the_monthly_cap(monkeypatch) -> None:
    """THE DEFECT THAT TAKES ARIA DARK. With the month's cap already blown by
    somebody else's vendor bill, a sovereign call must still go through —
    otherwise unrelated DD spend silences ARIA's entire reasoning."""
    from aria_service.llm import metered

    async def _blown(*a, **k):
        raise ct.MonthlyCostCapExceeded(999.0, 600.0, "2026-08")

    monkeypatch.setattr(ct, "assert_monthly_cap", _blown)
    monkeypatch.setattr(ct, "assert_daily_cap", _blown)

    class _Sovereign:
        name = "aria_llm"
        is_configured = True

    m = metered.MeteredProvider(_Sovereign())
    await m._enforce_spend_caps()          # must NOT raise


@pytest.mark.asyncio
async def test_a_paid_provider_is_still_gated(monkeypatch) -> None:
    """THE COUNTER-GUARD, and the one that keeps real money safe. A vendor call
    must still hit the brake."""
    from aria_service.llm import metered

    async def _blown(*a, **k):
        raise ct.MonthlyCostCapExceeded(999.0, 600.0, "2026-08")

    monkeypatch.setattr(ct, "assert_monthly_cap", _blown)

    async def _ok(*a, **k):
        return None

    monkeypatch.setattr(ct, "assert_daily_cap", _ok)

    class _Vendor:
        name = "anthropic"
        is_configured = True

    m = metered.MeteredProvider(_Vendor())
    with pytest.raises(ct.MonthlyCostCapExceeded):
        await m._enforce_spend_caps()


@pytest.mark.asyncio
async def test_an_unknown_provider_is_still_gated(monkeypatch) -> None:
    """Fail safe: if we cannot prove a provider is free, it is treated as paid."""
    from aria_service.llm import metered

    async def _blown(*a, **k):
        raise ct.MonthlyCostCapExceeded(999.0, 600.0, "2026-08")

    async def _ok(*a, **k):
        return None

    monkeypatch.setattr(ct, "assert_monthly_cap", _blown)
    monkeypatch.setattr(ct, "assert_daily_cap", _ok)

    class _Mystery:
        name = "some-new-provider"
        is_configured = True

    m = metered.MeteredProvider(_Mystery())
    with pytest.raises(ct.MonthlyCostCapExceeded):
        await m._enforce_spend_caps()

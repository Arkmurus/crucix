"""R-F2759 + R-F2760 — DeepSeek->Claude switch preparation (cap-safety + caching).

Context: the operator is switching ARIA's primary LLM from DeepSeek to Claude
(Anthropic) with OpenAI fallback. Two prep changes make the flip safe and start
the "gets cheaper as it compounds" mechanism BEFORE anything goes live:

  R-F2759 — cost_tracker.PRICING gains the current-generation Claude rows. Without
    them, claude-opus-4-8 / claude-sonnet-5 fell through _get_price to
    DEFAULT_PRICING (DeepSeek rates), so Claude spend was under-counted ~5-25x and
    the $300/mo cap (assert_monthly_cap reads these same numbers) would go BLIND.

  R-F2760 — AnthropicProvider._payload sends the system prompt as a cacheable
    block so Anthropic prompt-caches ARIA's large stable persona/constitution
    prefix (cache reads bill ~0.1x input → ~10x input-cost cut on recurring prefixes).

Both tests drive the REAL code paths the switch depends on, not helpers.
"""
from __future__ import annotations

from aria_service.intel import cost_tracker as ct
from aria_service.llm.anthropic import AnthropicProvider


# ── R-F2759: cap-safety — the switch models must price at real Claude rates ──────
def test_claude_switch_models_priced_not_default():
    assert ct._get_price("claude-opus-4-8") == (5.00, 25.00)
    assert ct._get_price("claude-opus-4-7") == (5.00, 25.00)
    assert ct._get_price("claude-sonnet-5") == (3.00, 15.00)
    # dated snapshot variants must PREFIX-match, not fall to default
    assert ct._get_price("claude-sonnet-5-20260215") == (3.00, 15.00)
    assert ct._get_price("claude-opus-4-8-20260601") == (5.00, 25.00)
    # and none of them collapse to the DeepSeek default (the blind-cap failure)
    assert ct._get_price("claude-opus-4-8") != ct.DEFAULT_PRICING
    assert ct._get_price("claude-sonnet-5") != ct.DEFAULT_PRICING


def test_claude_cost_estimate_reflects_real_rate():
    # A realistic DD midpoint (~55k in / ~18k out) on Sonnet-5 must cost ~$0.44 —
    # NOT the ~$0.035 the DeepSeek default would have produced. The $300 cap reads
    # estimate_cost_usd, so this is what keeps the cap honest under Claude.
    usd = ct.estimate_cost_usd("claude-sonnet-5", 55_000, 18_000)
    assert 0.40 < usd < 0.48, usd
    default_usd = (55_000 / 1e6) * ct.DEFAULT_PRICING[0] + (18_000 / 1e6) * ct.DEFAULT_PRICING[1]
    assert usd > default_usd * 5, (usd, default_usd)  # real rate dwarfs the stale default


# ── R-F2760: the Anthropic request actually asks for prompt caching ─────────────
def test_anthropic_payload_requests_prompt_caching():
    p = AnthropicProvider(api_key="x", model="claude-sonnet-5")
    payload = p._payload("ARIA SYSTEM PERSONA", "hello", 4096)
    sys = payload["system"]
    # system must be a list of cacheable blocks, not a bare string
    assert isinstance(sys, list) and sys, sys
    blk = sys[0]
    assert blk["type"] == "text"
    assert blk["text"] == "ARIA SYSTEM PERSONA"
    assert blk["cache_control"] == {"type": "ephemeral"}
    # everything else the API needs is unchanged
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["model"] == "claude-sonnet-5"
    assert payload["max_tokens"] == 4096

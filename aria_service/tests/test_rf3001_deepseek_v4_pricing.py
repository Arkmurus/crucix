"""R-F3001 — the CURRENT DeepSeek models must be priced correctly, not at the
Claude-Sonnet DEFAULT_PRICING fallback.

The operator flagged the daily-cap warning ($50.47 spent) as not matching the live
DeepSeek balance. Root cause: the API serves + REPORTS `deepseek-v4-flash` (cost is
priced off the model name in the RESPONSE, metered.py:92), which was NOT in PRICING —
so it fell through _get_price to DEFAULT_PRICING (Claude Sonnet $3/$15) and every
DeepSeek call was over-counted ~21x input / ~54x output (live: $141.72/mo reported
vs ~$5.6 real). This test pins the correct rate and asserts the over-count is gone.
"""
from __future__ import annotations

from aria_service.intel import cost_tracker as ct


def test_rf3001_deepseek_v4_flash_priced_not_default():
    """deepseek-v4-flash resolves to its real cheap rate, NOT the Claude default."""
    price = ct._get_price("deepseek-v4-flash")
    assert price == (0.14, 0.28), f"deepseek-v4-flash must price at (0.14,0.28), got {price}"
    assert price != ct.DEFAULT_PRICING, "must NOT fall through to the Claude-Sonnet default"


def test_rf3001_deepseek_v4_pro_priced():
    assert ct._get_price("deepseek-v4-pro") == (0.435, 0.87)


def test_rf3001_overcount_is_gone_realistic_volume():
    """At a realistic monthly volume, the DeepSeek cost must be tens-of-dollars,
    not the ~$140 the Claude-rate default produced."""
    # ~35.3M input + ~2.4M output tokens (the live July split)
    in_tok, out_tok = 35_315_000, 2_385_000
    correct = ct.estimate_cost_usd("deepseek-v4-flash", in_tok, out_tok)
    at_default = (ct.DEFAULT_PRICING[0] * in_tok + ct.DEFAULT_PRICING[1] * out_tok) / 1_000_000
    assert correct < 8.0, f"corrected deepseek-v4-flash monthly cost should be <$8, got ${correct:.2f}"
    assert at_default > 100.0, "sanity: the old Claude-rate default was the >$100 overcount"
    assert at_default / correct > 15, "fix must remove a >15x over-count"


def test_rf3001_claude_models_still_correct():
    """Regression: adding DeepSeek rows must not disturb the Claude rows R-F2759 added."""
    assert ct._get_price("claude-opus-4-8") == (5.00, 25.00)
    assert ct._get_price("claude-haiku-4-5-20251001") == (1.00, 5.00)  # prefix match
    # a genuinely-unknown model still gets the conservative Claude default
    assert ct._get_price("some-brand-new-model-xyz") == ct.DEFAULT_PRICING

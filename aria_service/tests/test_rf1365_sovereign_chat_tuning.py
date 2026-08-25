"""R-F1365 — 14B chat viability: trim context + fast DeepSeek failover.

The sovereign 14B is chain-primary (ARIA_LLM_URL set) but slow for live chat:
it must read every char of the 20000-char intelligence context before
generating, and a slow/stuck 14B burned the full 120s before failing over.
This caused 120s timeouts + lock contention (state_store lock 20s, brain_hook
circuit tripped) → degraded chat. Fix: when sovereign is active, trim the
context budget (default 6000) and cap the per-provider timeout (default 40s) so
a slow 14B fails over to the funded DeepSeek fallback fast.

R-F3606 (2026-08-01) CORRECTION: the premise in the paragraph above — "the
sovereign 14B is chain-primary (ARIA_LLM_URL set)" — was FALSE in production.
Under both SHADOW and the R-F2410 TWO-TRACK default, DeepSeek is chain primary
and the sovereign is not in the chain at all (fallback.py:951-969); only
ARIA_LLM_PRIMARY_ALL=1 makes it primary, and that is unset. So the two knobs
gated on _compact_prompt_active() were tuning DEEPSEEK, not the sovereign:

  - the 800-token completion cap starved deepseek-v4-* (reasoning models) into
    returning empty content → total chat outage 2026-08-01;
  - the 40s timeout cap only ever bound DeepSeek, because
    aria_llm_provider.complete()/stream() have NO timeout parameter (**_kw
    swallows it) and always ran on _DEFAULT_TIMEOUT=120.0.

Both are now enforced at the sovereign's own boundary, where they bind the
sovereign in every mode and cannot reach another provider. The context trim
remains gated on _compact_prompt_active() and is unchanged.
"""
import importlib
import os

import pytest


def _fresh_engine():
    import aria_service.aria_engine as e
    return e


def test_compact_active_gates_on_aria_llm_url(monkeypatch):
    e = _fresh_engine()
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    monkeypatch.delenv("ARIA_LLM_COMPACT_PROMPT", raising=False)
    assert e._compact_prompt_active() is True
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)
    assert e._compact_prompt_active() is False


def test_sovereign_cap_is_enforced_where_the_sovereign_ACTUALLY_serves(monkeypatch):
    """R-F3606 — this test previously asserted `_completion_max_tokens(...) == 800`
    whenever ARIA_LLM_URL was set, which PINNED THE DEFECT AS THE DESIGN.

    The property R-F1360/R-F1365 actually care about is "the sovereign is not
    asked for more tokens than it can generate inside the chat timeout". Setting
    the caller-side budget to 800 did not achieve that property — it capped
    whichever provider served, and under SHADOW/TWO-TRACK that is DeepSeek, whose
    v4 reasoning models then returned EMPTY content (finish_reason='length') and
    took chat down completely on 2026-08-01.

    So assert the PROPERTY, at the boundary where it is real, not the old number.
    """
    e = _fresh_engine()
    from aria_service.llm.aria_llm_provider import (
        clamp_for_sovereign, SOVEREIGN_COMPLETION_CEILING,
    )

    # 1. A configured-but-not-serving sovereign must NOT starve the cloud chain.
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    assert e._completion_max_tokens("hello") == 4000, (
        "a sovereign that is merely CONFIGURED must not cap DeepSeek's budget — "
        "that is the 2026-08-01 outage"
    )
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)
    assert e._completion_max_tokens("hello") == 4000

    # 2. The sovereign cap itself is still enforced — at the sovereign boundary.
    # R-F4327 (C-275): the ceiling is now DERIVED from measured throughput and the chat timeout, so this asserts the CEILING BINDS rather than a frozen 800 — the constant was calibrated for ~10 tok/s hardware and the live box measures 26-33. The guard is unchanged in substance: remove or move the clamp and this still fails.
    from aria_service.llm.aria_llm_provider import sovereign_completion_ceiling
    ceiling = sovereign_completion_ceiling()
    assert ceiling > 0
    assert clamp_for_sovereign(4000) == ceiling, "R-F1360's ceiling must still bind"
    assert clamp_for_sovereign(4000) < 4000, "the ceiling must actually LOWER an over-large ask"
    assert clamp_for_sovereign(500) == 500, "the clamp must only ever LOWER"


def test_explicit_compact_flag_overrides(monkeypatch):
    e = _fresh_engine()
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)
    monkeypatch.setenv("ARIA_LLM_COMPACT_PROMPT", "1")
    assert e._compact_prompt_active() is True
    monkeypatch.setenv("ARIA_LLM_COMPACT_PROMPT", "0")
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    # explicit 0 wins even when the URL is set
    assert e._compact_prompt_active() is False

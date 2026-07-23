"""R-F2917 — DD runs on Claude; everything else stays on the DeepSeek head.

Operator directive 2026-07-23, after Claude spend ran to ~£17 in hours:
"self improve and autonomous should be moved to deepseek and ensure DD's run on
claude API".

Doing that per-call would mean editing ~9 LLM call sites across dd_orchestrator,
deep_researcher and the verification layers — and any site missed, or added
later, silently bills the wrong provider. That is the failure we can least
afford, so the preference is set ONCE for the whole DD run via a contextvar the
FallbackProvider already consults (the R-F1366 prefer_provider path).
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.llm import fallback as fb
from aria_service.llm.fallback import FallbackProvider, provider_scope


def _run(coro):
    return asyncio.run(coro)


class _P:
    """Minimal provider that records that it served."""

    def __init__(self, name):
        self.name = name
        self.is_configured = True
        self.served = 0

    async def complete(self, system_prompt, user_message, **kw):
        self.served += 1
        return type("R", (), {
            "text": f"from {self.name}", "model": self.name,
            "input_tokens": 1, "output_tokens": 1, "routed_via": "",
        })()


def _chain():
    """Mirrors production after the 2026-07-23 restructure: DeepSeek head,
    Anthropic present as a fallback member."""
    ds, an = _P("deepseek"), _P("anthropic")
    return FallbackProvider([ds, an]), ds, an


class TestDefaultStaysOnDeepSeek:
    def test_unscoped_calls_hit_the_chain_head(self):
        """Everything that is NOT a DD must stay on DeepSeek."""
        chain, ds, an = _chain()
        _run(chain.complete("s", "u"))
        assert ds.served == 1 and an.served == 0

    def test_scope_is_released_afterwards(self):
        """A leaked pin would route LATER non-DD work to Claude — the exact
        overspend this change prevents."""
        chain, ds, an = _chain()
        with provider_scope("anthropic"):
            _run(chain.complete("s", "u"))
        _run(chain.complete("s", "u"))
        assert an.served == 1, "the DD call should have gone to Claude"
        assert ds.served == 1, "the post-DD call should have gone to DeepSeek"

    def test_empty_scope_is_a_no_op(self):
        chain, ds, an = _chain()
        with provider_scope(""):
            _run(chain.complete("s", "u"))
        assert ds.served == 1 and an.served == 0


class TestDDScopeRoutesToClaude:
    def test_scoped_calls_go_to_anthropic(self):
        chain, ds, an = _chain()
        with provider_scope("anthropic"):
            _run(chain.complete("s", "u"))
        assert an.served == 1 and ds.served == 0

    def test_explicit_argument_still_wins(self):
        """The R-F1366 coder pin must be unaffected by the DD scope."""
        chain, ds, an = _chain()
        with provider_scope("anthropic"):
            _run(chain.complete("s", "u", prefer_provider="deepseek"))
        assert ds.served == 1 and an.served == 0

    def test_unknown_scope_falls_back_to_normal_order(self):
        chain, ds, an = _chain()
        with provider_scope("not-in-chain"):
            _run(chain.complete("s", "u"))
        assert ds.served == 1

    def test_a_failing_claude_degrades_to_deepseek(self):
        """A DD must never DIE because Anthropic is rate-limited — it degrades."""
        ds, an = _P("deepseek"), _P("anthropic")

        async def _boom(*a, **k):
            raise RuntimeError("529 overloaded")

        an.complete = _boom
        chain = FallbackProvider([ds, an])
        with provider_scope("anthropic"):
            out = _run(chain.complete("s", "u"))
        assert ds.served == 1, "DD did not degrade to DeepSeek"
        assert "deepseek" in out.text


class TestNesting:
    def test_nested_scopes_restore_correctly(self):
        assert fb.get_preferred_provider() == ""
        with provider_scope("anthropic"):
            assert fb.get_preferred_provider() == "anthropic"
            with provider_scope("groq"):
                assert fb.get_preferred_provider() == "groq"
            assert fb.get_preferred_provider() == "anthropic"
        assert fb.get_preferred_provider() == ""

    def test_scope_survives_an_await_boundary(self):
        """A DD is deeply async; the pin must hold across awaits."""
        async def _inner():
            await asyncio.sleep(0)
            return fb.get_preferred_provider()

        async def _drive():
            with provider_scope("anthropic"):
                return await _inner()

        assert _run(_drive()) == "anthropic"

    def test_scope_propagates_into_a_child_task(self):
        """DD fans out via asyncio tasks; those must inherit the pin."""
        async def _drive():
            with provider_scope("anthropic"):
                return await asyncio.create_task(_child())

        async def _child():
            return fb.get_preferred_provider()

        assert _run(_drive()) == "anthropic"


class TestDDOrchestratorWiring:
    def test_orchestrate_dd_sets_and_releases_the_pin(self):
        """Guard the actual wiring: the entry point must set the preference and
        release it on EVERY exit path."""
        import inspect
        from aria_service.intel import dd_orchestrator

        src = inspect.getsource(dd_orchestrator.orchestrate_dd)
        assert "_preferred_provider.set" in src, "DD no longer pins its provider"
        assert "_preferred_provider.reset" in src, "DD no longer releases the pin"
        assert "ARIA_DD_LLM_PROVIDER" in src, "the env override was removed"


# ──────────────────────────────────────────────────────────────────────────
# R-F2922 — Claude is DD-ONLY. Never a fallback for anything else.
# ──────────────────────────────────────────────────────────────────────────
class _StreamP(_P):
    async def stream(self, system_prompt, user_message, **kw):
        self.served += 1
        yield f"from {self.name}"


class TestClaudeIsDDOnly:
    """Operator directive 2026-07-23: "claude is only for DD reports not as a
    fall back, we dont want that for now". R-F2917 pinned DD to Claude, but
    ARIA_ANTHROPIC_ENABLED=1 also placed Claude SECOND in the chain — so any
    non-DD call with a failing/cooling DeepSeek would have been served by it."""

    def test_claude_is_absent_from_the_default_order(self):
        chain, ds, an = _chain()
        _run(chain.complete("s", "u"))
        assert an.served == 0

    def test_non_dd_FAILS_rather_than_falling_back_to_claude(self):
        """THE guarantee. With DeepSeek down, a non-DD call must raise — not
        quietly bill Claude."""
        from aria_service.llm.provider import ProviderError

        ds, an = _P("deepseek"), _P("anthropic")

        async def _boom(*a, **k):
            raise RuntimeError("deepseek down")

        ds.complete = _boom
        chain = FallbackProvider([ds, an])
        with pytest.raises(ProviderError):
            _run(chain.complete("s", "u"))
        assert an.served == 0, "Claude served a NON-DD call as a fallback"

    def test_streaming_chat_never_reaches_claude(self):
        """stream() is the chat path and had no preference concept at all."""
        ds, an = _StreamP("deepseek"), _StreamP("anthropic")
        chain = FallbackProvider([ds, an])

        async def _drive():
            out = []
            async for c in chain.stream("s", "u"):
                out.append(c)
            return out

        assert _run(_drive()) == ["from deepseek"]
        assert an.served == 0

    def test_dd_still_reaches_claude(self):
        chain, ds, an = _chain()
        with provider_scope("anthropic"):
            _run(chain.complete("s", "u"))
        assert an.served == 1 and ds.served == 0

    def test_dd_still_degrades_to_deepseek_when_claude_fails(self):
        """A DD must never DIE because Claude is rate-limited."""
        ds, an = _P("deepseek"), _P("anthropic")

        async def _boom(*a, **k):
            raise RuntimeError("529 overloaded")

        an.complete = _boom
        chain = FallbackProvider([ds, an])
        with provider_scope("anthropic"):
            out = _run(chain.complete("s", "u"))
        assert ds.served == 1 and "deepseek" in out.text

    def test_the_mechanism_is_env_disableable(self, monkeypatch):
        monkeypatch.setenv("ARIA_PREFERENCE_ONLY_PROVIDERS", "")
        chain, ds, an = _chain()

        async def _boom(*a, **k):
            raise RuntimeError("down")

        ds.complete = _boom
        _run(chain.complete("s", "u"))
        assert an.served == 1, "disabling the list should restore normal fallback"

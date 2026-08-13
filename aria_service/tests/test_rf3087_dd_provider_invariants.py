"""R-F3087 — DD always acquires Claude routing and scopes Brave safely.

Capability tests drive the real ``orchestrate_dd`` wrapper.  Layer internals are
replaced only to keep the test fast; assertions are made inside the actual DD
scope, where every production layer and child task would execute.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import web_search
from aria_service.intel.dd_schema import ARKDDReport
from aria_service.llm import fallback
from aria_service.llm.fallback import FallbackProvider
from aria_service.llm.provider import LLMResult


class _ConfiguredLLM:
    name = "fallback"
    is_configured = True


class _ServingProvider:
    is_configured = True

    def __init__(self, name: str):
        self.name = name
        self.served = 0

    async def complete(self, *args, **kwargs) -> LLMResult:
        self.served += 1
        return LLMResult(text=self.name, model=self.name)


async def _noop_finalize(*args, **kwargs) -> None:
    return None


async def _quiet_keepalive() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_orchestrate_resolves_live_llm_and_scopes_both_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAPABILITY: an llm-less background caller still enters Brave+Claude."""
    from aria_service.main import app

    configured = _ConfiguredLLM()
    monkeypatch.setattr(app.state, "llm_provider", configured, raising=False)
    monkeypatch.setenv("ARIA_DD_BRAVE_PRIMARY", "1")
    monkeypatch.setenv("ARIA_DD_LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "live-shaped-test-key")
    monkeypatch.setattr(ddo, "_finalize_dd_run", _noop_finalize)
    monkeypatch.setattr(ddo, "_dd_interactive_keepalive", _quiet_keepalive)

    observed: dict[str, object] = {}

    async def _fake_impl(target: dict, *, llm=None, **kwargs):
        observed["llm"] = llm
        observed["brave"] = web_search.brave_is_enabled()
        observed["provider"] = fallback.get_preferred_provider()
        return ARKDDReport(target=target)

    monkeypatch.setattr(ddo, "_orchestrate_dd_impl", _fake_impl)
    web_search.enable_brave_for_scope(False)

    await ddo.orchestrate_dd(
        {"name": "Invariant Test Limited", "type": "company"},
        llm=None,
        total_budget_s=5,
    )

    assert observed == {
        "llm": configured,
        "brave": True,
        "provider": "anthropic",
    }
    assert web_search._BRAVE_CTX.get() == "", (      # R-F3946 — "" is no-scope
        "the paid Brave scope leaked beyond the DD run"
    )
    assert fallback.get_preferred_provider() == "", (
        "the Claude preference leaked beyond the DD run"
    )


@pytest.mark.asyncio
async def test_orchestrate_preserves_an_explicit_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicitly injected provider remains authoritative."""
    from aria_service.main import app

    live = _ConfiguredLLM()
    explicit = _ConfiguredLLM()
    monkeypatch.setattr(app.state, "llm_provider", live, raising=False)
    monkeypatch.setattr(ddo, "_finalize_dd_run", _noop_finalize)
    monkeypatch.setattr(ddo, "_dd_interactive_keepalive", _quiet_keepalive)

    observed: dict[str, object] = {}

    async def _fake_impl(target: dict, *, llm=None, **kwargs):
        observed["llm"] = llm
        return ARKDDReport(target=target)

    monkeypatch.setattr(ddo, "_orchestrate_dd_impl", _fake_impl)

    await ddo.orchestrate_dd(
        {"name": "Explicit Provider Limited", "type": "company"},
        llm=explicit,
        total_budget_s=5,
    )

    assert observed["llm"] is explicit


def test_brave_scope_token_restores_nested_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scope primitive restores its caller's value, including nesting."""
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "live-shaped-test-key")
    web_search.enable_brave_for_scope(False)

    token = web_search.enable_brave_for_scope(True, purpose="dd")  # R-F3946
    assert web_search.brave_is_enabled() is True
    web_search.reset_brave_scope(token)

    assert web_search._BRAVE_CTX.get() == ""   # R-F3946 — "" is no-scope


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_route_brave_scope_restores_on_every_exit(
    monkeypatch: pytest.MonkeyPatch,
    raises: bool,
) -> None:
    """The user-facing route decorator cannot leak Brave after return/error.

    R-F3946 — the RESTORATION invariant this guards is unchanged and is still
    asserted below. What changed is the grant: the decorator opens a scope with
    NO purpose, and RULE ONE confines Brave to DD, so the handler must now see
    Brave OFF. That inversion is the point of C-40 — these eight routes
    (POST /chat, /explore, /explore-deep, /research/spawn, ...) were spending
    the paid DD key on general traffic. DD is unaffected: it opens its own
    purpose="dd" scope in dd_orchestrator, which the two tests either side of
    this one cover.
    """
    from aria_service.routes.aria import _brave_scope

    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "live-shaped-test-key")
    web_search.enable_brave_for_scope(False)
    observed: list[bool] = []

    @_brave_scope
    async def _handler():
        observed.append(web_search.brave_is_enabled())
        if raises:
            raise RuntimeError("route failed")
        return "ok"

    if raises:
        with pytest.raises(RuntimeError, match="route failed"):
            await _handler()
    else:
        assert await _handler() == "ok"

    assert observed == [False], (
        "R-F3946 — a non-DD route must NOT receive Brave. If this reads [True] "
        "again, RULE ONE's Brave half has been re-opened."
    )
    assert web_search._BRAVE_CTX.get() == ""   # R-F3946 — "" is no-scope


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_detached_adverse_followup_restores_brave_scope(
    monkeypatch: pytest.MonkeyPatch,
    raises: bool,
) -> None:
    """The detached real follow-up restores Brave after search success/failure."""
    from aria_service.intel import researcher

    monkeypatch.setenv("ARIA_DD_BRAVE_PRIMARY", "1")
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "live-shaped-test-key")
    web_search.enable_brave_for_scope(False)
    observed: list[bool] = []

    async def _search(**kwargs):
        observed.append(web_search.brave_is_enabled())
        if raises:
            raise RuntimeError("search failed")
        return {"ok": True, "findings_count": 0}

    async def _missing_report(run_id: str):
        return None

    monkeypatch.setattr(researcher, "run_adverse_media_deep_search", _search)
    monkeypatch.setattr(ddo, "get_report", _missing_report)

    await ddo._run_adverse_media_followup(
        "scope-test",
        entity_name="Scope Test Limited",
        director_names=[],
        ubo_names=[],
        sectors=["defence"],
        trigger_reason="test",
    )

    assert observed == [True]
    assert web_search._BRAVE_CTX.get() == ""   # R-F3946 — "" is no-scope


@pytest.mark.asyncio
async def test_dd_fails_closed_when_anthropic_is_absent_from_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAPABILITY: a Claude-pinned DD must never run its LLM work on DeepSeek."""
    deepseek = _ServingProvider("deepseek")
    chain = FallbackProvider([deepseek])
    monkeypatch.setenv("ARIA_DD_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ARIA_PREFERRED_MAY_DEGRADE", raising=False)
    monkeypatch.setattr(ddo, "_finalize_dd_run", _noop_finalize)
    monkeypatch.setattr(ddo, "_dd_interactive_keepalive", _quiet_keepalive)

    async def _fake_impl(target: dict, *, llm=None, **kwargs):
        await llm.complete("DD system", "DD evidence", timeout=1)
        return ARKDDReport(target=target)

    monkeypatch.setattr(ddo, "_orchestrate_dd_impl", _fake_impl)

    with pytest.raises(Exception, match="anthropic|preferred"):
        await ddo.orchestrate_dd(
            {"name": "Missing Claude Limited", "type": "company"},
            llm=chain,
            total_budget_s=5,
        )

    assert deepseek.served == 0, (
        "a Claude-pinned DD silently ran on DeepSeek when Anthropic was absent"
    )


@pytest.mark.asyncio
async def test_real_orchestrator_llm_call_is_served_only_by_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAPABILITY: drive orchestrate_dd through the real fallback selection."""
    deepseek = _ServingProvider("deepseek")
    anthropic = _ServingProvider("anthropic")
    chain = FallbackProvider([deepseek, anthropic])
    monkeypatch.setenv("ARIA_DD_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ARIA_PREFERRED_MAY_DEGRADE", raising=False)
    monkeypatch.setattr(ddo, "_finalize_dd_run", _noop_finalize)
    monkeypatch.setattr(ddo, "_dd_interactive_keepalive", _quiet_keepalive)

    async def _fake_impl(target: dict, *, llm=None, **kwargs):
        result = await llm.complete("DD system", "DD evidence", timeout=1)
        assert result.text == "anthropic"
        return ARKDDReport(target=target)

    monkeypatch.setattr(ddo, "_orchestrate_dd_impl", _fake_impl)

    await ddo.orchestrate_dd(
        {"name": "Claude Route Limited", "type": "company"},
        llm=chain,
        total_budget_s=5,
    )

    assert anthropic.served == 1
    assert deepseek.served == 0

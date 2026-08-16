"""R-F4071 (C-115) — the resilience verdict counted providers that cannot serve
general traffic.

Measured on aria-intel 2026-08-16, the SAME instant, two surfaces:

    /autonomy/surface.resilience
        providers: [{name: anthropic, status: active, calls: 0, failures: 0,
                     reliability: null},
                    {name: deepseek,  status: active, ...}]
        providers_active: 2   resilience_count: 3   verdict: "ROBUST"

    /health.llm_chain
        active_providers: ["deepseek"]
        chain_order:      ["deepseek"]
        preference_only_providers: ["anthropic"]
        general_vendor_depth: 1

The brain page rendered **"🛡️ Resilience floor: ROBUST (3 independent paths) ·
anthropic: active · deepseek: active"** while the chain a general call actually
walks was one provider deep.

`_resilience_floor` derived "active" from **API-key presence plus a cooldown
timestamp**. It never asked whether the provider is on the general path. Under
RULE ONE (§17) Anthropic is `preference_only`: reserved for DD and deliberately
unreachable by general dispatch (R-F3034/R-F3767). Counting it as a fallback
path is exactly the error R-F3634 fixed in `fallback.get_health()`:

    "it advertised a chain the request could not use ... The dispatcher was
     right and the surface describing it was wrong, which is the worst way
     round."

Two consequences, both real:

* **Overstated.** On 2026-08-12, with the Anthropic balance exhausted and DD
  down, this panel would still have said ROBUST — the key was present and no
  cooldown was set. `calls: 0, failures: 0, reliability: null` says plainly that
  the provider had served nothing.
* **Understated.** `deepseek_backup` served 1,591 calls this month and does not
  appear at all, because the panel enumerates a hardcoded `provider_keys` map
  rather than the chain.

And `deepseek` + `deepseek_backup` are two entries but ONE vendor — R-F3634's
`general_vendor_depth` already says so, because a vendor-side timeout takes both
and failing over between them cannot help.

The fix reads `FallbackProvider.get_health()`, the same method `/health`
publishes, instead of forking a second opinion from `os.getenv`. Reserved
providers are still SHOWN (hiding a configured provider would be its own lie)
but carry `role: "reserved_dd"` and do not count toward the floor.
"""
from __future__ import annotations

import pytest


class _FakeInner:
    """Stands in for FallbackProvider: only get_stats + get_health."""

    def __init__(self, health, stats=None):
        self._health = health
        self._stats = stats or {}

    def get_stats(self):
        return self._stats

    def get_health(self):
        return self._health


class _App:
    class state:  # noqa: N801 - mirrors FastAPI's app.state attribute access
        llm_provider = None


_LIVE_HEALTH = {
    "active_providers": ["deepseek"],
    "cooling_providers": [],
    "chain_order": ["deepseek"],
    "preference_only_providers": ["anthropic"],
    "general_vendor_depth": 1,
    "resilient": True,
}


@pytest.fixture
def wire(monkeypatch):
    """Point autonomy_surface at a fake app.state.llm_provider."""
    import sys
    import types

    def _install(health, stats=None):
        mod = sys.modules.get("aria_service.main")
        if mod is None:
            mod = types.ModuleType("aria_service.main")
            sys.modules["aria_service.main"] = mod
        app = _App()
        app.state.llm_provider = _FakeInner(health, stats)
        monkeypatch.setattr(mod, "app", app, raising=False)
        return app

    return _install


async def _floor():
    from aria_service.intel import autonomy_surface as asf
    return await asf._resilience_floor()


# ── 1. the live shape must not read ROBUST ─────────────────────────────────

@pytest.mark.asyncio
async def test_reserved_provider_does_not_count_as_a_fallback_path(wire):
    wire(_LIVE_HEALTH, {"deepseek": {}, "anthropic": {}})
    out = await _floor()

    assert out["general_vendor_depth"] == 1, out
    # one general vendor + local brain = 2 paths, not 3
    assert out["resilience_count"] <= 2, (
        "anthropic is preference_only and cannot serve a general call; "
        f"counting it overstates the floor. {out}")
    assert out["verdict"] != "ROBUST", (
        f"a one-vendor-deep chain is not ROBUST: {out}")


@pytest.mark.asyncio
async def test_the_reserved_provider_is_still_shown_with_its_role(wire):
    """Hiding a configured provider would be its own lie."""
    wire(_LIVE_HEALTH, {"deepseek": {}, "anthropic": {}})
    out = await _floor()

    by_name = {p["name"]: p for p in out["providers"]}
    assert "anthropic" in by_name, out["providers"]
    assert by_name["anthropic"]["role"] == "reserved_dd", by_name["anthropic"]
    assert by_name["deepseek"]["role"] == "general", by_name["deepseek"]


# ── 2. two entries, one vendor ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_vendor_twice_is_one_path(wire):
    """deepseek + deepseek_backup: a vendor-side timeout takes both."""
    wire({
        "active_providers": ["deepseek", "deepseek_backup"],
        "cooling_providers": [],
        "chain_order": ["deepseek", "deepseek_backup"],
        "preference_only_providers": ["anthropic"],
        "general_vendor_depth": 1,
        "resilient": True,
    }, {"deepseek": {}, "deepseek_backup": {}, "anthropic": {}})
    out = await _floor()
    assert out["general_vendor_depth"] == 1
    assert out["resilience_count"] <= 2, out


@pytest.mark.asyncio
async def test_a_genuinely_deep_chain_still_reads_robust(wire):
    """The verdict must still be reachable, or it is not a verdict."""
    wire({
        "active_providers": ["deepseek", "groq", "mistral"],
        "cooling_providers": [],
        "chain_order": ["deepseek", "groq", "mistral"],
        "preference_only_providers": [],
        "general_vendor_depth": 3,
        "resilient": True,
    }, {})
    out = await _floor()
    assert out["general_vendor_depth"] == 3
    assert out["verdict"] == "ROBUST", out


# ── 3. a cooling provider is not a resilience loss (§14 unchanged) ────────

@pytest.mark.asyncio
async def test_cooling_is_reported_but_the_chain_is_still_counted(wire):
    wire({
        "active_providers": ["deepseek"],
        "cooling_providers": [{"name": "groq", "reason": "timeout",
                               "seconds_remaining": 30}],
        "chain_order": ["deepseek", "groq"],
        "preference_only_providers": [],
        "general_vendor_depth": 2,
        "resilient": True,
    }, {})
    out = await _floor()
    assert out["providers_cooling"] == 1, out
    by_name = {p["name"]: p for p in out["providers"]}
    assert by_name["groq"]["status"] == "cooling"


# ── 4. unmeasurable is not healthy ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_chain_health_available_is_not_robust(wire):
    """If the live chain cannot be read, the floor is unknown — never ROBUST
    on the strength of an env var being set."""
    import sys
    import types
    mod = sys.modules.get("aria_service.main")
    if mod is None:
        mod = types.ModuleType("aria_service.main")
        sys.modules["aria_service.main"] = mod
    app = _App()
    app.state.llm_provider = None
    old = getattr(mod, "app", None)
    mod.app = app
    try:
        out = await _floor()
    finally:
        if old is not None:
            mod.app = old
    assert out["verdict"] != "ROBUST", out
    assert out.get("general_vendor_depth") in (0, None), out

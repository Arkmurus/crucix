"""R-F2410 — two-track model router capability tests.

Drive the REAL router path (route_decision / complete_synthesis / stream_synthesis)
with a MOCK sovereign endpoint and assert:
  - flag UNSET  -> DeepSeek always (byte-identical to today; sovereign never called)
  - flag SET + grounded synthesis -> sovereign
  - flag SET + closed-book       -> DeepSeek
  - sovereign error              -> DeepSeek fallback, reported operational (§14)
  - SHADOW                       -> ship DeepSeek, sovereign generated alongside
  - CANARY 0/100                 -> DeepSeek / sovereign
  - PRIMARY_ALL                  -> router defers (two_track inactive)
  - stream mirror (§13)          -> sovereign streams; pre-token error -> DeepSeek
"""
from __future__ import annotations

import pytest

from aria_service.llm import model_router as mr
from aria_service.llm.provider import LLMResult

_ENV = ("ARIA_LLM_URL", "ARIA_LLM_PROMOTION_STAGE",
        "ARIA_LLM_SHADOW", "ARIA_LLM_CANARY_PCT",
        "ARIA_LLM_PRIMARY_ALL", "ARIA_LLM_ROUTER_DISABLED", "ARIA_LLM_TIMEOUT",
        "ARIA_SHADOW_DISTILL_ENABLED")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)
    # R-F2521 — reset the module-global shadow accumulator between tests
    mr._shadow_stats_acc.update(samples=0, deepseek_sum=0.0, sovereign_sum=0.0,
                                sovereign_wins=0, sovereign_answered=0)
    mr._shadow_recent.clear()
    yield


def test_sovereign_configured_reflects_flag(monkeypatch):
    """R-F2410: sovereign_configured() is the activation gate — False unless ARIA_LLM_URL is set."""
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)
    assert mr.sovereign_configured() is False          # default = DeepSeek-only
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign:8000")
    assert mr.sovereign_configured() is True            # the one-var flip
    monkeypatch.setenv("ARIA_LLM_URL", "   ")           # whitespace-only = not configured
    assert mr.sovereign_configured() is False


def test_promotion_stage_defaults_and_aliases(monkeypatch):
    monkeypatch.delenv("ARIA_LLM_PROMOTION_STAGE", raising=False)
    assert mr.promotion_stage() == "shadow"
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "pilot")
    assert mr.promotion_stage() == "canary"
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "sovereign")
    assert mr.promotion_stage() == "serve"
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "garbage")
    assert mr.promotion_stage() == "shadow"


def test_is_grounded_synthesis_detects_retrieved_context():
    """R-F2410: a turn carrying substantial retrieved context is grounded-synthesis (→ sovereign when active)."""
    assert mr.is_grounded_synthesis("plain question", "") is False       # no tools, no context
    assert mr.is_grounded_synthesis("q", "x" * 5000) is True             # substantial retrieved context


def test_summary_reports_router_state(monkeypatch):
    """R-F2410: summary() exposes the router's activation state (proprioception, §25)."""
    monkeypatch.delenv("ARIA_LLM_URL", raising=False)
    s = mr.summary()
    assert isinstance(s, dict) and len(s) >= 1


class _BaseLLM:
    """Mock DeepSeek chain provider."""
    name = "deepseek"
    is_configured = True

    def __init__(self):
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(self, system, user, *, max_tokens=4096, timeout=60.0):
        self.complete_calls += 1
        return LLMResult(text="DEEPSEEK-ANSWER", model="deepseek-chat", routed_via="deepseek")

    async def stream(self, system, user, *, max_tokens=4096, timeout=120.0, on_done=None):
        self.stream_calls += 1
        for c in ("DEEP", "SEEK"):
            yield c
        if on_done:
            on_done(LLMResult(text="DEEPSEEK", model="deepseek-chat", routed_via="deepseek"))


def _mock_sovereign_ok(monkeypatch, text="SOVEREIGN-GROUNDED-ANSWER"):
    async def _c(prompt, *, system="", max_tokens=2048, temperature=0.3, timeout=None, **kw):
        return {"ok": True, "provider": "aria_llm", "text": text,
                "model": "aria-llm-grounded-dpo-v1", "tokens_in": 10, "tokens_out": 20}
    monkeypatch.setattr(mr.aria_llm_provider, "complete", _c)


def _mock_sovereign_error(monkeypatch):
    async def _c(prompt, *, system="", max_tokens=2048, temperature=0.3, timeout=None, **kw):
        return {"ok": False, "provider": "aria_llm", "error": "http_503", "text": ""}
    monkeypatch.setattr(mr.aria_llm_provider, "complete", _c)


async def _drain_shadow():
    """R-F2520 — await any fire-and-forget shadow-compare bg tasks so a test can
    assert what the background sampling did (deterministic, no sleeps)."""
    import asyncio
    for _ in range(10):
        if not mr._shadow_bg_tasks:
            return
        await asyncio.gather(*list(mr._shadow_bg_tasks), return_exceptions=True)


_GROUNDED_MSG = "Summarise findings. [TOOL: web_search] results: OFAC lists Entity A [from mem0:x]"
_GROUNDED_CTX = "• [1.04] web_search:x\n  ↳ source: mem0:session_eval_abc:2026\n" * 6
_CLOSED_MSG = "What is the capital of France?"


# ── flag UNSET: byte-identical DeepSeek-only ─────────────────────────────────

@pytest.mark.asyncio
async def test_flag_unset_always_deepseek(monkeypatch):
    called = {"sov": False}
    async def _c(*a, **k):
        called["sov"] = True
        return {"ok": True, "text": "SHOULD-NOT-HAPPEN"}
    monkeypatch.setattr(mr.aria_llm_provider, "complete", _c)
    base = _BaseLLM()
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX) == "deepseek"
    r = await mr.complete_synthesis(base, "sys", "user", message=_GROUNDED_MSG,
                                    context=_GROUNDED_CTX)
    assert r.text == "DEEPSEEK-ANSWER"
    assert base.complete_calls == 1
    assert called["sov"] is False   # sovereign endpoint never touched


# ── flag SET + grounded -> shadow by default ─────────────────────────────────

@pytest.mark.asyncio
async def test_grounded_defaults_to_shadow_not_user_serving(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    seen = {"sov": False}
    async def _c(prompt, *, system="", max_tokens=2048, temperature=0.3, timeout=None, **kw):
        seen["sov"] = True
        return {"ok": True, "text": "SOV", "model": "aria-llm", "tokens_in": 1, "tokens_out": 1}
    monkeypatch.setattr(mr.aria_llm_provider, "complete", _c)
    base = _BaseLLM()
    assert mr.two_track_active() is True
    assert mr.promotion_stage() == "shadow"
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX) == "shadow"
    r = await mr.complete_synthesis(base, "sys", "user", message=_GROUNDED_MSG,
                                    context=_GROUNDED_CTX)
    assert r.text == "DEEPSEEK-ANSWER"
    await _drain_shadow()               # R-F2520: sovereign compare is fire-and-forget
    assert seen["sov"] is True
    assert base.complete_calls == 1


@pytest.mark.asyncio
async def test_grounded_routes_to_sovereign_only_when_promoted(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "serve")
    _mock_sovereign_ok(monkeypatch)
    base = _BaseLLM()
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX) == "sovereign"
    r = await mr.complete_synthesis(base, "sys", "user", message=_GROUNDED_MSG,
                                    context=_GROUNDED_CTX)
    assert r.text == "SOVEREIGN-GROUNDED-ANSWER"
    assert r.routed_via == "sovereign"
    assert base.complete_calls == 0


# ── flag SET + closed-book -> DeepSeek ───────────────────────────────────────

@pytest.mark.asyncio
async def test_closed_book_stays_deepseek(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    _mock_sovereign_ok(monkeypatch)
    base = _BaseLLM()
    assert mr.route_decision(_CLOSED_MSG, "") == "deepseek"
    r = await mr.complete_synthesis(base, "sys", "user", message=_CLOSED_MSG, context="")
    assert r.text == "DEEPSEEK-ANSWER"
    assert base.complete_calls == 1


# ── sovereign error -> DeepSeek fallback, operational (§14) ───────────────────

@pytest.mark.asyncio
async def test_sovereign_error_falls_back_operational(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "serve")
    _mock_sovereign_error(monkeypatch)
    wired = {}
    monkeypatch.setattr(mr, "wire_failure",
                        lambda **kw: wired.update(kw))
    base = _BaseLLM()
    r = await mr.complete_synthesis(base, "sys", "user", message=_GROUNDED_MSG,
                                    context=_GROUNDED_CTX)
    assert r.text == "DEEPSEEK-ANSWER"      # fell back
    assert base.complete_calls == 1
    assert "operational" in (wired.get("detail", "").lower())   # §14, not "degraded"
    assert "degraded" not in (wired.get("detail", "").lower())


# ── SHADOW: ship DeepSeek, sovereign generated alongside ─────────────────────

@pytest.mark.asyncio
async def test_shadow_ships_deepseek_but_runs_sovereign(monkeypatch):
    """R-F2520: non-stream shadow ships DeepSeek with NO latency tax — the
    sovereign compare is FIRE-AND-FORGET (not awaited inline), then runs."""
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    monkeypatch.setenv("ARIA_LLM_SHADOW", "1")
    seen = {"sov": 0}
    async def _c(prompt, *, system="", max_tokens=2048, temperature=0.3, timeout=None, **kw):
        seen["sov"] += 1
        return {"ok": True, "text": "SOV", "model": "aria-llm", "tokens_in": 1, "tokens_out": 1}
    monkeypatch.setattr(mr.aria_llm_provider, "complete", _c)
    base = _BaseLLM()
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX) == "shadow"
    r = await mr.complete_synthesis(base, "sys", "user", message=_GROUNDED_MSG,
                                    context=_GROUNDED_CTX)
    assert r.text == "DEEPSEEK-ANSWER"   # user gets DeepSeek
    assert seen["sov"] == 0              # NOT awaited inline (fire-and-forget = no latency tax)
    await _drain_shadow()
    assert seen["sov"] == 1              # sovereign generated for comparison, in background


# ── CANARY 0 / 100 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_canary_zero_and_hundred(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "canary")
    _mock_sovereign_ok(monkeypatch)
    monkeypatch.setenv("ARIA_LLM_CANARY_PCT", "0")
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX, canary_key="s1") == "deepseek"
    monkeypatch.setenv("ARIA_LLM_CANARY_PCT", "100")
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX, canary_key="s1") == "sovereign"


# ── PRIMARY_ALL -> router defers (two_track inactive) ────────────────────────

def test_primary_all_deactivates_two_track(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    monkeypatch.setenv("ARIA_LLM_PRIMARY_ALL", "1")
    assert mr.two_track_active() is False
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX) == "deepseek"


def test_canary_notselected_captures_via_shadow(monkeypatch):
    """R-F2531: with the flywheel capturing, canary's NOT-selected grounded turns
    route through shadow (user still gets DeepSeek, sovereign generated for capture);
    without capture they stay plain deepseek (byte-identical to before)."""
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "canary")
    monkeypatch.setenv("ARIA_LLM_CANARY_PCT", "0")   # nobody selected → all not-selected
    # capture OFF → plain deepseek
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX, canary_key="s1") == "deepseek"
    # capture ON → shadow (so the sovereign side is generated + captured)
    monkeypatch.setenv("ARIA_SHADOW_DISTILL_ENABLED", "1")
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX, canary_key="s1") == "shadow"
    # non-grounded turns are unaffected even with capture on
    assert mr.route_decision(_CLOSED_MSG, "", canary_key="s1") == "deepseek"


def test_router_disabled_forces_deepseek(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    monkeypatch.setenv("ARIA_LLM_ROUTER_DISABLED", "1")
    assert mr.two_track_active() is False
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX) == "deepseek"


# ── STREAM mirror (§13) ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_unset_is_deepseek(monkeypatch):
    base = _BaseLLM()
    out = []
    async for c in mr.stream_synthesis(base, "sys", "user", message=_GROUNDED_MSG,
                                       context=_GROUNDED_CTX):
        out.append(c)
    assert "".join(out) == "DEEPSEEK"
    assert base.stream_calls == 1


@pytest.mark.asyncio
async def test_stream_grounded_routes_to_sovereign(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "serve")
    async def _s(prompt, *, system="", max_tokens=2048, temperature=0.3, **kw):
        for c in ("SOV", "-STREAM"):
            yield c
    monkeypatch.setattr(mr.aria_llm_provider, "stream", _s)
    base = _BaseLLM()
    done = {}
    out = []
    async for c in mr.stream_synthesis(base, "sys", "user", message=_GROUNDED_MSG,
                                       context=_GROUNDED_CTX,
                                       on_done=lambda r: done.update(text=r.text, via=r.routed_via)):
        out.append(c)
    assert "".join(out) == "SOV-STREAM"
    assert base.stream_calls == 0
    assert done.get("via") == "sovereign"


@pytest.mark.asyncio
async def test_stream_shadow_samples_sovereign_async(monkeypatch):
    """R-F2520 (the fix): a STREAMING grounded turn in shadow ships DeepSeek's
    stream to the user AND fires the sovereign compare in the BACKGROUND — the
    gap R-F2517 monitoring found (stream shadow used to be a no-op, so shadow
    collected 0 organic samples). Zero added user latency; the compare is logged."""
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    # default promotion stage == shadow (no PROMOTION_STAGE set)
    seen = {"sov": 0}
    async def _c(prompt, *, system="", max_tokens=2048, temperature=0.3, timeout=None, **kw):
        seen["sov"] += 1
        return {"ok": True, "text": "SOV-SHADOW", "model": "aria-llm", "tokens_in": 1, "tokens_out": 1}
    monkeypatch.setattr(mr.aria_llm_provider, "complete", _c)
    logged = []
    monkeypatch.setattr(mr, "wire_success", lambda **kw: logged.append(kw))
    # deterministic grounded scorer so the assertion doesn't depend on the real one
    class _Sc:
        def __init__(self, s): self.score = s
    monkeypatch.setattr("aria_service.intel.grounding_reward.score",
                        lambda text, ctx: _Sc(0.5 if text else 0.0))
    base = _BaseLLM()
    assert mr.route_decision(_GROUNDED_MSG, _GROUNDED_CTX) == "shadow"
    out = []
    async for c in mr.stream_synthesis(base, "sys", "user", message=_GROUNDED_MSG,
                                       context=_GROUNDED_CTX):
        out.append(c)
    assert "".join(out) == "DEEPSEEK"     # user got DeepSeek's stream, unaffected
    assert base.stream_calls == 1
    assert seen["sov"] == 0               # fire-and-forget — not called during the stream
    await _drain_shadow()
    assert seen["sov"] == 1               # sovereign SAMPLED in background (the new capability)
    assert any("SHADOW grounded-rate" in (k.get("summary") or "") for k in logged)
    # R-F2521 — the comparison is captured in a readable tally (not dropped)
    st = mr.shadow_stats()
    assert st["samples"] == 1
    assert st["sovereign_answered"] == 1


@pytest.mark.asyncio
async def test_stream_sovereign_pretoken_error_falls_back(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://mock-sovereign/v1")
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "serve")
    async def _s(prompt, *, system="", max_tokens=2048, temperature=0.3, **kw):
        raise RuntimeError("connection refused")
        yield  # pragma: no cover
    monkeypatch.setattr(mr.aria_llm_provider, "stream", _s)
    base = _BaseLLM()
    out = []
    async for c in mr.stream_synthesis(base, "sys", "user", message=_GROUNDED_MSG,
                                       context=_GROUNDED_CTX):
        out.append(c)
    assert "".join(out) == "DEEPSEEK"   # fell back cleanly
    assert base.stream_calls == 1

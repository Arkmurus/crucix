"""R-F3606 / R-F3607 / R-F3608 — the 2026-08-01 total chat outage.

THE LIVE SYMPTOM (operator WhatsApp, 07:15 2026-08-01):

    Antonio: "Aria, Tony is flying to Bulgaria today from London is there any
              concerns or risks he should be aware of?"
    Aria:    "⚠️ ARIA degraded mode — LLM error: [deepseek] reasoning consumed
              the token budget (model=deepseek-v4-flash, finish_reason=length,
              reasoning=3455 chars)"

...followed by a raw dump of ARIA's knowledge base, which itself contained a
PREVIOUS degraded message stored as a "[PROBABLE] verified fact".

THE CHAIN (each link proven, see the R-numbers in the source):

  1. ARIA_LLM_URL is set on aria-intel, so `_compact_prompt_active()` is True.
  2. `_completion_max_tokens()` therefore returned 800 for EVERY chat turn.
  3. But the sovereign is NOT chain primary — under SHADOW and the R-F2410
     TWO-TRACK default DeepSeek serves (fallback.py:951-969). So the 7B's
     latency cap was applied to DeepSeek.
  4. deepseek-v4-* are REASONING models: `max_tokens` covers reasoning_content
     + content, and reasoning is emitted FIRST. 800 tokens (~3,440 chars) was
     consumed entirely by reasoning — the live reasoning was 3455 chars on
     flash and 3676 on pro, which is the cap, measured.
  5. `content` came back EMPTY with finish_reason='length'; R-F3591 correctly
     refused to serve the chain of thought and raised.
  6. The backup model inherited the SAME 800 cap, so failover could not help.
     Chain exhausted → degraded mode. Deterministic, not intermittent.
  7. The degraded text was then absorbed via brain_hook with success=True /
     confidence=PROBABLE, so each outage taught itself to the brain and the
     next outage served it back as fact. It COMPOUNDED per occurrence.
"""
import asyncio

from aria_service.llm.openai_compat import (
    OpenAICompatProvider,
    _floor_completion_budget,
    _is_reasoning_model,
    _REASONING_MIN_COMPLETION_TOKENS,
)


def _run(coro):
    return asyncio.run(coro)


# ── A fake that behaves like a real reasoning model ──────────────────────────
#
# This is the heart of the capability test. A canned response proves nothing
# here — the defect only appears when the response DEPENDS on max_tokens, which
# is exactly how deepseek-v4-* behaved live. So the fake reproduces the real
# contract: reasoning is emitted first and consumes the budget; only if the
# budget outlasts the reasoning does any `content` appear.
_REASONING_TOKEN_COST = 900   # what the model spent deliberating, live


def _reasoning_model_client(seen: dict):
    """httpx.AsyncClient stub that answers like deepseek-v4-flash."""
    class _Resp:
        status_code = 200
        text = "stub"

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            body = k.get("json") or {}
            seen["max_tokens"] = body.get("max_tokens")
            seen["model"] = body.get("model")
            budget = int(body.get("max_tokens") or 0)
            if budget <= _REASONING_TOKEN_COST:
                # Budget died during deliberation — the live failure.
                return _Resp({
                    "choices": [{
                        "message": {
                            "content": "",
                            "reasoning_content": "The user asks about Bulgaria. " * 120,
                        },
                        "finish_reason": "length",
                    }],
                    "usage": {"prompt_tokens": 50, "completion_tokens": budget},
                    "model": "deepseek-v4-flash",
                })
            return _Resp({
                "choices": [{
                    "message": {
                        "content": (
                            "Bulgaria is an EU/NATO member; no entry visa is "
                            "required for UK citizens for short stays."
                        ),
                        "reasoning_content": "Deliberation elided.",
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 50, "completion_tokens": 120},
                "model": "deepseek-v4-flash",
            })

    return _Client


# ── THE CAPABILITY TEST (§3c) ────────────────────────────────────────────────


def test_capability_operator_question_gets_an_answer_not_degraded_mode(monkeypatch):
    """Drive the OPERATOR'S ACTUAL PATH: the budget aria_engine computes for a
    live chat turn, sent to a provider that behaves like the live model.

    FAILS BEFORE THE FIX: `_completion_max_tokens` returned 800 (because
    ARIA_LLM_URL is set), 800 <= 900 tokens of reasoning, so the provider
    raised ProviderError('reasoning consumed the token budget') — which is
    precisely the message the operator received on WhatsApp.
    """
    import aria_service.aria_engine as e
    import aria_service.llm.openai_compat as oc

    # The LIVE production configuration: sovereign URL set, sovereign NOT
    # chain-primary (no ARIA_LLM_PRIMARY_ALL) — i.e. DeepSeek serves.
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake-sovereign:8888/v1")
    monkeypatch.delenv("ARIA_LLM_PRIMARY_ALL", raising=False)
    monkeypatch.delenv("ARIA_LLM_COMPACT_PROMPT", raising=False)

    question = ("Aria, Tony is flying to Bulgaria today from London is there "
                "any concerns or risks he should be aware of?")
    budget = e._completion_max_tokens(question)

    seen: dict = {}
    monkeypatch.setattr(oc.httpx, "AsyncClient", _reasoning_model_client(seen))
    p = OpenAICompatProvider(name="deepseek", api_key="k",
                             model="deepseek-v4-flash",
                             base_url="https://api.deepseek.com")

    res = _run(p.complete("sys", question, max_tokens=budget))

    assert res.text.strip(), "the operator's question must produce an ANSWER"
    assert "Bulgaria" in res.text
    assert seen["max_tokens"] > _REASONING_TOKEN_COST, (
        f"the model was sent max_tokens={seen['max_tokens']}, which cannot "
        f"outlast its own reasoning — this is the outage"
    )


def test_capability_the_backup_model_is_not_starved_identically(monkeypatch):
    """The failover could not save the chain because the BACKUP inherited the
    same caller-side cap. Both live models must now clear their reasoning."""
    import aria_service.aria_engine as e
    import aria_service.llm.openai_compat as oc

    monkeypatch.setenv("ARIA_LLM_URL", "http://fake-sovereign:8888/v1")
    monkeypatch.delenv("ARIA_LLM_PRIMARY_ALL", raising=False)
    budget = e._completion_max_tokens("any normal question about travel risk?")

    for model in ("deepseek-v4-flash", "deepseek-v4-pro"):
        seen: dict = {}
        monkeypatch.setattr(oc.httpx, "AsyncClient", _reasoning_model_client(seen))
        p = OpenAICompatProvider(name="deepseek", api_key="k", model=model,
                                 base_url="https://api.deepseek.com")
        res = _run(p.complete("sys", "usr", max_tokens=budget))
        assert res.text.strip(), f"{model} was starved into an empty answer"


# ── R-F3606 — the budget follows the provider that actually serves ───────────


def test_configured_sovereign_does_not_cap_the_cloud_budget(monkeypatch):
    import aria_service.aria_engine as e
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    monkeypatch.delenv("ARIA_LLM_COMPACT_PROMPT", raising=False)
    assert e._completion_max_tokens("hello") == 4000


def test_periodic_brief_budget_survives_the_change(monkeypatch):
    """R-F865's 8000-token brief budget must be unaffected."""
    import aria_service.aria_engine as e
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    assert e._completion_max_tokens("send me the weekly intelligence brief") == 8000


def test_sovereign_ceiling_still_binds_at_its_own_boundary():
    """R-F1360's protection must survive — moved, not deleted."""
    from aria_service.llm.aria_llm_provider import (
        clamp_for_sovereign, SOVEREIGN_COMPLETION_CEILING,
    )
    # R-F4327 (C-275): the ceiling is now DERIVED from measured throughput and the chat timeout, so this asserts the CEILING BINDS rather than a frozen 800 — the constant was calibrated for ~10 tok/s hardware and the live box measures 26-33. The guard is unchanged in substance: remove or move the clamp and this still fails.
    from aria_service.llm.aria_llm_provider import sovereign_completion_ceiling
    ceiling = sovereign_completion_ceiling()
    assert clamp_for_sovereign(8000) == ceiling
    assert clamp_for_sovereign(4000) == ceiling
    assert ceiling < 4000, "the ceiling must still BIND, not merely exist"
    assert clamp_for_sovereign(256) == 256, "must only ever lower"


def test_the_ceiling_is_applied_on_the_sovereign_CHAT_path(monkeypatch):
    """R-F3606 — the ceiling belongs at model_router's sovereign chat call, which
    is the chokepoint for routed turns AND the shadow compare."""
    import aria_service.llm.model_router as mr

    seen: dict = {}

    async def _fake_complete(prompt, *, system="", max_tokens=2048, **kw):
        seen["max_tokens"] = max_tokens
        return {"ok": True, "text": "answer", "model": "aria-llm-v0.1"}

    monkeypatch.setattr(mr.aria_llm_provider, "complete", _fake_complete)
    res = _run(mr._sovereign_complete("sys", "usr", max_tokens=4000, timeout=40.0))
    assert res is not None
    from aria_service.llm.aria_llm_provider import sovereign_completion_ceiling
    assert seen["max_tokens"] == sovereign_completion_ceiling(), (
        f"sovereign chat call was sent {seen['max_tokens']} tokens, not the ceiling"
    )


def test_the_ceiling_does_NOT_truncate_the_self_coder(monkeypatch):
    """REGRESSION GUARD — caught in verify pass 1.

    An earlier draft applied the ceiling inside aria_llm_provider.complete()
    itself. That is wrong: the self-coder calls it directly with
    max_tokens=8192 to generate a WHOLE FILE (R-F1363). An 800-token cap there
    silently truncates generated code — the exact truncating-fix class R-F904
    blocks and §21c warns must be fixed before self-deploy is safe.

    The sovereign module must therefore pass a large batch budget through
    untouched; only the chat call sites clamp.
    """
    import aria_service.llm.aria_llm_provider as P

    captured: dict = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            captured.update(k.get("json") or {})

            class _R:
                status_code = 200

                @staticmethod
                def json():
                    return {"choices": [{"message": {"content": "code"},
                                         "finish_reason": "stop"}],
                            "usage": {}}
            return _R()

    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "32768")
    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _FakeClient)

    _run(P.complete("short prompt", system="", max_tokens=8192))
    assert captured["max_tokens"] == 8192, (
        "the self-coder's whole-file budget must reach the sovereign intact"
    )


# ── R-F3607 — a reasoning model can always afford its reasoning ──────────────


def test_reasoning_models_are_detected():
    assert _is_reasoning_model("deepseek-v4-flash")
    assert _is_reasoning_model("deepseek-v4-pro")
    assert _is_reasoning_model("deepseek-v4-pro-0801"), "point releases must match"
    assert _is_reasoning_model("deepseek-reasoner")
    assert not _is_reasoning_model("gpt-4")
    assert not _is_reasoning_model("aria-llm-v0.1")
    assert not _is_reasoning_model(None)


# ── SUPERSEDED BY R-F3627 (2026-08-01, same day) ────────────────────────────
#
# The three assertions below pinned EXACT EQUALITY with the floor — i.e. that a
# reasoning model is sent precisely `max(caller, 2048)` combined tokens. That
# contract was disproven within hours by the live page it was meant to prevent:
# deepseek-v4-pro spent a full 4,000-token budget on reasoning alone
# (13,527 chars, finish_reason=length) and produced no answer.
#
# The defect was never the size of the number — it was that ONE number had to
# cover the thinking AND the answer, so every value is a cliff. R-F3627 reserves
# the caller's budget for the answer and adds reasoning headroom on top. These
# now assert the PROPERTY that must hold at any tuning (the model can always
# outlast its own reasoning) rather than an arithmetic identity that changes
# whenever the headroom is retuned.


def test_small_budget_is_floored_for_a_reasoning_model():
    assert _floor_completion_budget("deepseek-v4-flash", 800) >= _REASONING_MIN_COMPLETION_TOKENS
    assert _floor_completion_budget("deepseek-v4-flash", 16) >= _REASONING_MIN_COMPLETION_TOKENS


def test_floor_never_lowers_and_never_touches_classic_models():
    assert _floor_completion_budget("deepseek-v4-flash", 8000) >= 8000, (
        "the caller's answer budget must never be LOWERED"
    )
    assert _floor_completion_budget("gpt-4", 16) == 16, (
        "a classic model returns its answer in `content`; raising its budget "
        "would be an unrequested cost increase"
    )


def test_a_small_budget_caller_can_no_longer_starve_a_reasoning_model(monkeypatch):
    """llm/structured.py defaults to max_tokens=1000 and has never been run
    against a reasoning model. Without the floor it would fail exactly as chat did."""
    import aria_service.llm.openai_compat as oc
    seen: dict = {}
    monkeypatch.setattr(oc.httpx, "AsyncClient", _reasoning_model_client(seen))
    p = OpenAICompatProvider(name="deepseek", api_key="k",
                             model="deepseek-v4-flash",
                             base_url="https://api.deepseek.com")
    res = _run(p.complete("sys", "usr", max_tokens=1000))
    assert res.text.strip(), "a 1000-token caller was starved into an empty answer"
    # R-F3627 — was `== _REASONING_MIN_COMPLETION_TOKENS`. The property that
    # matters is that the wire budget outlasts the model's reasoning, not that
    # it equals one particular constant.
    assert seen["max_tokens"] > _REASONING_TOKEN_COST


# ── R-F3608 — a failure surface is never absorbed as knowledge ───────────────


def test_the_exact_live_degraded_text_is_recognised():
    """Verify the instrument against the REAL string the operator received."""
    from aria_service.intel.reasoning_library import is_degraded_or_error_response as bad

    live = ("⚠️ ARIA degraded mode — LLM error: [deepseek] reasoning consumed "
            "the token budget (model=deepseek-v4-flash, finish_reason=length, "
            "reasoning=3455 chars)")
    assert bad(live)
    # the nested one that was stored as a [PROBABLE] fact
    assert bad("⚠️ ARIA degraded mode — LLM error: [deepseek_backup] ...")
    assert bad("ARIA encountered an error")
    assert bad("❌ failed")


def test_a_real_answer_is_still_absorbed():
    """The guard must not silence the R-F655 pay-once-remember-forever hook."""
    from aria_service.intel.reasoning_library import is_degraded_or_error_response as bad

    assert not bad("Bulgaria is an EU and NATO member. No visa is required.")
    assert not bad("")


def test_the_absorb_path_actually_calls_the_guard():
    """R-F3608 — the defect was that routes/aria.py had NO guard while
    reasoning_library did. Prove the absorb site now consults the shared one."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "routes" / "aria.py"
    text = src.read_text(encoding="utf-8")
    i = text.index('module="aria_chat"')
    block = text[i - 2500:i]
    assert "is_degraded_or_error_response" in block, (
        "the aria_chat brain_hook absorb must consult the shared degraded guard"
    )


def test_the_guard_is_shared_not_duplicated():
    """Two inline copies is how these drifted. reasoning_library must be the
    one definition, and record_response must use it rather than re-implement."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "intel" / "reasoning_library.py"
    text = src.read_text(encoding="utf-8")
    assert "def is_degraded_or_error_response" in text
    assert text.count('"degraded mode" in') == 1, (
        "the degraded-mode check must exist in exactly ONE place"
    )

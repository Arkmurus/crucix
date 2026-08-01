"""R-F3627 — the completion budget must RESERVE the answer, not be spent on it.

THE LIVE SYMPTOM (operator WhatsApp, 2026-08-01, ~6h after R-F3606 shipped):

    🚨 BLOCKED: LLM chain — every provider failed.
    STUCK: no provider served the last request (path=complete, attempts=1).
    TRIED: deepseek, deepseek_backup
    WHY: [deepseek_backup] reasoning consumed the token budget
         (model=deepseek-v4-pro, finish_reason=length, reasoning=13527 chars)

WHY R-F3606/R-F3607 DID NOT CLOSE IT. They raised the chat budget 800 -> 4000
and floored small callers at 2048. Both numbers cap the COMBINED
reasoning+content allowance, which is the quantity that runs out — so the cliff
moved and the failure recurred at the new number. The arithmetic that proved the
first diagnosis disproves its remedy:

    R-F3606 (fixed):   3,455 chars reasoning /   800-token cap = 4.3 chars/token
    R-F3627 (this):   13,527 chars reasoning / 4,000-token cap = 3.4 chars/token

Both are the whole budget, spent thinking, with `content` empty. And both
samples are CONDITIONED ON FAILURE — a turn whose reasoning finishes early never
raises — so they bound how long the model thinks from BELOW and can never
justify picking a bigger single number.

THREE DEFECTS, all on the operator's primary signal:
  1. `max_tokens` was one integer doing two jobs. The caller means "answer this
     long"; the model spends it thinking first.
  2. The chain's only recovery was the NEXT PROVIDER, which fallback.py hands
     the IDENTICAL max_tokens — so the backup died at the identical point. The
     fallback was structurally incapable of covering this failure.
  3. The page said `TRIED: deepseek, deepseek_backup` when ONE call was made,
     and its ACTION line said "check provider credit/cooldown" for a failure on
     a healthy, paid, reachable provider.
"""
import asyncio

import pytest

from aria_service.llm.openai_compat import (
    OpenAICompatProvider,
    KIND_REASONING_TRUNCATED,
    _floor_completion_budget,
    _is_reasoning_model,
    _REASONING_HEADROOM_TOKENS,
    _REASONING_MAX_COMPLETION_TOKENS,
)
from aria_service.llm.provider import ProviderError


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_page_cooldown():
    """The operator-page suppression window (`_last_chain_alert_at`, 900s) is a
    MODULE global. Without this reset the first paging test consumes the window
    and every later one asserts against an empty string — leaked state that
    would read as a broken fix rather than a broken test."""
    import aria_service.llm.fallback as fb
    fb._last_chain_alert_at = 0.0
    fb._last_redundancy_alert_at = 0.0
    yield
    fb._last_chain_alert_at = 0.0
    fb._last_redundancy_alert_at = 0.0


# ── A fake that behaves like deepseek-v4-*, measured from the live failure ────
#
# The defect only exists when the response DEPENDS on max_tokens, so a canned
# payload proves nothing. This reproduces the real contract: reasoning is emitted
# FIRST and consumes the budget; `content` appears only if the budget outlasts
# it. `reasoning_cost` is in TOKENS, and 4000 is what the live deepseek-v4-pro
# actually spent (13,527 chars) on the turn that paged the operator.
_LIVE_PRO_REASONING_TOKENS = 4000


def _reasoning_model_client(seen: dict, *, reasoning_cost: int = _LIVE_PRO_REASONING_TOKENS):
    """httpx.AsyncClient stub that answers like a DeepSeek V4 reasoning model."""
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
            budget = int(body.get("max_tokens") or 0)
            seen.setdefault("budgets", []).append(budget)
            seen["max_tokens"] = budget
            seen["model"] = body.get("model")
            seen["calls"] = seen.get("calls", 0) + 1
            if budget <= reasoning_cost:
                # The budget died mid-deliberation — the live failure. ~3.4
                # chars/token, the measured live ratio.
                return _Resp({
                    "choices": [{
                        "message": {
                            "content": "",
                            "reasoning_content": "x" * int(budget * 3.4),
                        },
                        "finish_reason": "length",
                    }],
                    "usage": {"prompt_tokens": 50, "completion_tokens": budget},
                    "model": body.get("model"),
                })
            return _Resp({
                "choices": [{
                    "message": {
                        "content": (
                            "Bulgaria is an EU and NATO member; UK citizens need "
                            "no visa for short stays."
                        ),
                        "reasoning_content": "Deliberation elided.",
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 50, "completion_tokens": 120},
                "model": body.get("model"),
            })

    return _Client


def _deepseek(model="deepseek-v4-pro", name="deepseek_backup"):
    return OpenAICompatProvider(name=name, api_key="k", model=model,
                                base_url="https://api.deepseek.com")


# ── THE CAPABILITY TEST (§3c) — the operator's actual path ───────────────────


def test_capability_the_paging_turn_now_produces_an_answer(monkeypatch):
    """Drive the budget aria_engine computes for a live chat turn into a
    provider that spends exactly what the live model spent.

    FAILS BEFORE THE FIX: `_completion_max_tokens` returns 4000, the model
    burns 4000 on reasoning, `content` is empty with finish_reason='length',
    and complete() raises the error that paged the operator.
    """
    import aria_service.aria_engine as e
    import aria_service.llm.openai_compat as oc

    monkeypatch.setenv("ARIA_LLM_URL", "http://fake-sovereign:8888/v1")
    monkeypatch.delenv("ARIA_LLM_PRIMARY_ALL", raising=False)

    question = ("Aria, Tony is flying to Bulgaria today from London is there "
                "any concerns or risks he should be aware of?")
    budget = e._completion_max_tokens(question)
    assert budget == 4000, "the caller-side budget under test must be the live one"

    seen: dict = {}
    monkeypatch.setattr(oc.httpx, "AsyncClient", _reasoning_model_client(seen))

    res = _run(_deepseek().complete("sys", question, max_tokens=budget))

    assert res.text.strip(), "the operator's question must produce an ANSWER"
    assert "Bulgaria" in res.text
    assert seen["max_tokens"] > _LIVE_PRO_REASONING_TOKENS, (
        f"sent max_tokens={seen['max_tokens']}, which cannot outlast the "
        f"{_LIVE_PRO_REASONING_TOKENS} tokens this model spends thinking"
    )
    assert seen["calls"] == 1, "one correctly-sized request, no retry needed"


def test_capability_both_live_models_clear_their_own_reasoning(monkeypatch):
    """flash AND pro — the backup inheriting an identical starved budget is
    precisely why failover could not save the chain."""
    import aria_service.aria_engine as e
    import aria_service.llm.openai_compat as oc

    budget = e._completion_max_tokens("any normal travel-risk question?")
    for model in ("deepseek-v4-flash", "deepseek-v4-pro"):
        seen: dict = {}
        monkeypatch.setattr(oc.httpx, "AsyncClient", _reasoning_model_client(seen))
        res = _run(_deepseek(model=model).complete("sys", "usr", max_tokens=budget))
        assert res.text.strip(), f"{model} was starved into an empty answer"


# ── The answer is RESERVED, not competed for ────────────────────────────────


def test_the_caller_budget_is_reserved_on_top_of_reasoning_headroom():
    """The contract R-F3606/R-F3607 got wrong: `max_tokens` is the ANSWER."""
    assert _floor_completion_budget("deepseek-v4-pro", 4000) == 4000 + _REASONING_HEADROOM_TOKENS
    assert _floor_completion_budget("deepseek-v4-pro", 8000) == 8000 + _REASONING_HEADROOM_TOKENS


def test_a_classic_model_is_never_touched():
    """A classic model returns its answer in `content`; raising its ceiling
    would be an unrequested cost increase."""
    assert _floor_completion_budget("gpt-4", 16) == 16
    assert _floor_completion_budget("gpt-4", 4000) == 4000
    assert not _is_reasoning_model("aria-llm-v0.1"), "the sovereign must stay exempt"
    assert _floor_completion_budget("aria-llm-v0.1", 800) == 800


def test_the_budget_never_lowers_and_is_bounded():
    """Two properties that must hold together: never starve a caller, never
    walk the budget up without limit."""
    assert _floor_completion_budget("deepseek-v4-pro", 99_000) >= 99_000, (
        "the ceiling must never LOWER what the caller asked for"
    )
    assert _floor_completion_budget("deepseek-v4-pro", 100, attempt=9) <= _REASONING_MAX_COMPLETION_TOKENS
    assert _floor_completion_budget("deepseek-v4-pro", 4000, attempt=1) > \
        _floor_completion_budget("deepseek-v4-pro", 4000, attempt=0), "escalation must escalate"


def test_a_small_budget_caller_cannot_be_starved(monkeypatch):
    """llm/structured.py defaults to max_tokens=1000."""
    import aria_service.llm.openai_compat as oc
    seen: dict = {}
    monkeypatch.setattr(oc.httpx, "AsyncClient", _reasoning_model_client(seen))
    res = _run(_deepseek().complete("sys", "usr", max_tokens=1000))
    assert res.text.strip(), "a 1000-token caller was starved into an empty answer"


# ── The bounded escalation ──────────────────────────────────────────────────


def test_an_unusually_long_reasoner_is_recovered_by_ONE_retry(monkeypatch):
    """A turn that outthinks even the first headroom rung must still answer —
    on the SAME provider, because the next provider inherits the same budget."""
    import aria_service.llm.openai_compat as oc
    seen: dict = {}
    # Costs more than 4000 + headroom, less than 4000 + 2*headroom.
    cost = 4000 + _REASONING_HEADROOM_TOKENS + 10
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _reasoning_model_client(seen, reasoning_cost=cost))

    res = _run(_deepseek().complete("sys", "usr", max_tokens=4000))

    assert res.text.strip(), "the escalation did not recover the answer"
    assert seen["calls"] == 2, f"expected exactly one retry, got {seen['calls']} calls"
    assert seen["budgets"][1] > seen["budgets"][0], "the retry must RAISE the budget"


def test_the_escalation_is_bounded_and_still_fails_honestly(monkeypatch):
    """A model that outthinks every rung must raise — never loop, never serve
    the chain of thought (R-F3591)."""
    import aria_service.llm.openai_compat as oc
    seen: dict = {}
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _reasoning_model_client(seen, reasoning_cost=10**9))

    with pytest.raises(ProviderError) as exc:
        _run(_deepseek().complete("sys", "usr", max_tokens=4000))

    assert exc.value.kind == KIND_REASONING_TRUNCATED
    assert seen["calls"] == 2, f"escalation must be bounded at 2, made {seen['calls']}"
    assert "reasoning consumed the token budget" in str(exc.value)
    assert "xxx" not in str(exc.value), "the chain of thought must never be served"


def test_a_NON_curable_failure_is_not_retried(monkeypatch):
    """The escalation must fire ONLY on the failure it can cure. An HTTP 400 is
    not made better by a bigger budget — retrying it would double the cost of
    every hard outage."""
    import aria_service.llm.openai_compat as oc
    calls = {"n": 0}

    class _Resp:
        status_code = 400
        text = '{"error":"bad request"}'

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            calls["n"] += 1
            return _Resp()

    monkeypatch.setattr(oc.httpx, "AsyncClient", _Client)
    with pytest.raises(ProviderError):
        _run(_deepseek().complete("sys", "usr", max_tokens=4000))
    assert calls["n"] == 1, "a 400 must not be retried"


# ── §13 — the stream fork inherits the fix, with no second implementation ────


def test_the_stream_fork_inherits_the_whole_treatment(monkeypatch):
    """LLMProvider.stream() delegates to complete() and this class does not
    override it — so budget AND retry cover streaming chat. §13: a fix that
    lands on only one fork is half a fix."""
    import aria_service.llm.openai_compat as oc
    seen: dict = {}
    monkeypatch.setattr(oc.httpx, "AsyncClient", _reasoning_model_client(seen))

    async def _drain():
        return "".join([c async for c in _deepseek().stream("sys", "usr", max_tokens=4000)])

    text = _run(_drain())
    assert "Bulgaria" in text, "streaming chat was starved into an empty answer"
    assert seen["max_tokens"] > _LIVE_PRO_REASONING_TOKENS


# ── The operator page tells the truth (§19e / §22) ──────────────────────────


def _chain_with_cooling_primary():
    """The live shape at page time: primary cooling, backup the only candidate."""
    from aria_service.llm.fallback import FallbackProvider
    import time as _t

    chain = FallbackProvider([
        _deepseek(model="deepseek-v4-flash", name="deepseek"),
        _deepseek(model="deepseek-v4-pro", name="deepseek_backup"),
    ])
    chain._stats["deepseek"]["cooldown_until"] = _t.time() + 60
    return chain


def test_the_page_names_who_was_CALLED_not_who_was_listed(monkeypatch):
    """`TRIED: deepseek, deepseek_backup` beside `attempts=1` was a page
    contradicting itself. One call was made; the page must say so."""
    import aria_service.llm.openai_compat as oc
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _reasoning_model_client({}, reasoning_cost=10**9))

    chain = _chain_with_cooling_primary()
    paged: dict = {}
    monkeypatch.setattr(chain, "_dispatch_operator_page",
                        lambda text, *, source: paged.update(text=text, source=source))
    monkeypatch.setattr(type(chain), "_MAX_FALLBACK_ATTEMPTS", 3)

    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "usr", max_tokens=4000))

    text = paged.get("text", "")
    assert text, "a total outage must page the operator (R-F3613)"
    assert "CALLED: deepseek_backup" in text, f"page did not name the dialled provider:\n{text}"
    assert "SKIPPED, cooling: deepseek" in text, (
        f"page did not distinguish the skipped provider:\n{text}"
    )


def test_the_page_ACTION_matches_the_CAUSE(monkeypatch):
    """The ACTION line sent the operator to clear a cooldown for a failure on a
    healthy, paid, reachable provider. A page that names the wrong remedy costs
    more than no page, because it is acted on."""
    import aria_service.llm.openai_compat as oc
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _reasoning_model_client({}, reasoning_cost=10**9))

    chain = _chain_with_cooling_primary()
    paged: dict = {}
    monkeypatch.setattr(chain, "_dispatch_operator_page",
                        lambda text, *, source: paged.update(text=text))

    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "usr", max_tokens=4000))

    text = paged.get("text", "")
    assert "NOT a credit or cooldown problem" in text, (
        f"the ACTION line still points at the wrong lever:\n{text}"
    )
    assert "clears a stale cooldown after a top-up" not in text


def test_a_billing_outage_still_gets_the_cooldown_ACTION(monkeypatch):
    """REGRESSION GUARD — making the page cause-aware must not lose the ACTION
    that IS right for a billing failure (R-F3513)."""
    from aria_service.llm.fallback import FallbackProvider

    chain = FallbackProvider([_deepseek()])
    paged: dict = {}
    monkeypatch.setattr(chain, "_dispatch_operator_page",
                        lambda text, *, source: paged.update(text=text))

    err = ProviderError("deepseek", "Insufficient Balance", kind="billing", retryable=False)
    chain._on_chain_exhausted(chain.providers, 1, "", err, path="complete",
                              called=["deepseek"])

    text = paged.get("text", "")
    assert "POST /api/aria/admin/llm/cooldown/clear" in text
    assert "does NOT clear on restart" in text

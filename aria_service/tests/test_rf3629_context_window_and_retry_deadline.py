"""R-F3629 — the two items R-F3627 left open, closed with measurement.

(1) THE CONTEXT WINDOW WAS UNDERSTATED 16x, AND WAS SECRETLY A COST CAP.

`prompt_budget._CONTEXT_WINDOWS` recorded 65,536 for deepseek-v4-*. MEASURED
live against the API from inside aria-intel (the key never left the box):

    flash  HTTP 200  prompt_tokens=200,094      -> 65,536 was already false
    flash  HTTP 400  "This model's maximum context length is 1048576 tokens"
    pro    HTTP 400  "This model's maximum context length is 1048565 tokens"

An oversized request is the cheapest honest probe available: the API names its
own ceiling in the 400 body, and a rejected request bills nothing. Note the
first probe was INCONCLUSIVE — 288k chars tokenised to 64,094, just under the
recorded limit — and reading that as confirmation would have been the
"instrument never proved non-zero" mistake.

Correcting it is not purely a win, which is the interesting part: 65,536 was
also, unstated, the thing bounding spend on the provider that serves nearly
everything. Raising it to the truth multiplies the worst-case prompt 16x as a
SIDE EFFECT of a correctness fix. That is one integer doing two jobs — exactly
the defect R-F3627 removed from `max_tokens` one module over. So capability
(_CONTEXT_WINDOWS) and permission (ARIA_MAX_PROMPT_TOKENS) are now separate.

(2) THE ESCALATION DOUBLED THE CALLER'S TIMEOUT.

R-F3627's retry gave each attempt the full `timeout`, so complete() could take
2x what the caller asked for. `timeout` is a contract: the chain sizes its
per-provider budget from it and callers above set it against a user-facing
deadline. The retry now shares one deadline, and is not started at all when too
little time remains — a larger budget takes LONGER to generate, so a retry
squeezed into the last moments is the least likely of all to succeed.
"""
import asyncio

import pytest

import aria_service.llm.openai_compat as oc
from aria_service.llm.openai_compat import (
    OpenAICompatProvider, KIND_REASONING_TRUNCATED, _MIN_RETRY_SECONDS,
)
from aria_service.llm.provider import ProviderError
from aria_service.llm import prompt_budget as pb


def _run(coro):
    return asyncio.run(coro)


# ── (1) The measured context window ─────────────────────────────────────────


def test_the_v4_windows_are_the_MEASURED_ones():
    """Pinned to what the API said about itself, not to a vendor doc."""
    assert pb.get_context_window("deepseek-v4-flash") == 1048576
    assert pb.get_context_window("deepseek-v4-pro") == 1048565


def test_pro_is_not_rounded_up_to_flash():
    """pro's ceiling is ELEVEN tokens below flash's. This is a truncation
    boundary: rounding UP would 400 the very calls the budget exists to
    prevent, so the difference is carried exactly."""
    assert pb.get_context_window("deepseek-v4-pro") < pb.get_context_window("deepseek-v4-flash")


def test_a_point_release_still_resolves():
    """R-F3032's prefix match must keep working for deepseek-v4-pro-0801."""
    assert pb.get_context_window("deepseek-v4-pro-0801") == 1048565


def test_unprobed_deepseek_models_were_NOT_tidied_up():
    """deepseek-chat is retired and deepseek-reasoner was never probed. Guessing
    1M for them would be the fabrication this R-number exists to remove."""
    assert pb.get_context_window("deepseek-chat") == 65536
    assert pb.get_context_window("deepseek-reasoner") == 65536


# ── (1b) Capability is not permission ───────────────────────────────────────


def test_the_spend_cap_bounds_the_prompt_even_though_the_model_could_take_more(monkeypatch):
    """THE CAPABILITY TEST for the cost half: a 1M-token model must not mean a
    1M-token bill. Without the cap this prompt would be sent whole."""
    monkeypatch.delenv("ARIA_MAX_PROMPT_TOKENS", raising=False)
    huge = "word " * 400_000          # ~660k tokens by the module's estimator
    assert pb.estimate_tokens(huge) > pb._DEFAULT_MAX_PROMPT_TOKENS

    _sys, user = pb.enforce_budget("sys", huge, model="deepseek-v4-flash",
                                   reserved_output=12192)
    assert pb.estimate_tokens(user) <= pb._DEFAULT_MAX_PROMPT_TOKENS, (
        "the runaway prompt was sent at full size — the spend cap did not bind"
    )


def test_the_cap_is_operator_tunable(monkeypatch):
    monkeypatch.setenv("ARIA_MAX_PROMPT_TOKENS", "250000")
    assert pb._max_prompt_tokens() == 250_000


def test_a_bad_cap_value_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("ARIA_MAX_PROMPT_TOKENS", "not-a-number")
    assert pb._max_prompt_tokens() == pb._DEFAULT_MAX_PROMPT_TOKENS
    monkeypatch.setenv("ARIA_MAX_PROMPT_TOKENS", "0")
    assert pb._max_prompt_tokens() == pb._DEFAULT_MAX_PROMPT_TOKENS


def test_the_cap_never_bites_a_normal_aria_prompt(monkeypatch):
    """The guard must be a runaway catch, not a routine constraint. ARIA's
    constitution is ~83k chars; a turn carrying it plus an attached contract
    must pass through UNTOUCHED."""
    monkeypatch.delenv("ARIA_MAX_PROMPT_TOKENS", raising=False)
    constitution = "x" * 83_519          # the real size, per R-F3045
    contract = "y" * 60_000              # the R-F944 attached-document case
    s, u = pb.enforce_budget(constitution, contract, model="deepseek-v4-flash",
                             reserved_output=12192)
    assert s == constitution, "ARIA's constitution was truncated on a normal turn"
    assert u == contract, "an ordinary attached document was truncated"


# ── (2) The escalation shares ONE deadline ──────────────────────────────────


class _Clock:
    """A controllable stand-in for the `time` module, so deadline behaviour is
    deterministic instead of wall-clock dependent."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


def _always_truncates(seen: dict, clock: _Clock, cost_per_call: float):
    """A client that burns `cost_per_call` seconds of the clock and always
    returns reasoning-only, i.e. the failure the escalation exists for."""
    class _Resp:
        status_code = 200
        text = "stub"

        def json(self):
            return {"choices": [{"message": {"content": "",
                                             "reasoning_content": "z" * 40000},
                                 "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                    "model": "deepseek-v4-pro"}

    class _Client:
        def __init__(self, *a, **k):
            seen.setdefault("timeouts", []).append(k.get("timeout"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            clock.now += cost_per_call
            return _Resp()

    return _Client


def _provider():
    return OpenAICompatProvider(name="deepseek_backup", api_key="k",
                                model="deepseek-v4-pro",
                                base_url="https://api.deepseek.com")


def test_the_retry_does_not_double_the_callers_timeout(monkeypatch):
    """THE CAPABILITY TEST for the latency half. Two attempts must fit inside
    ONE caller deadline, so the second is handed strictly less time."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _always_truncates(seen, clock, cost_per_call=20.0))

    with pytest.raises(ProviderError):
        _run(_provider().complete("sys", "usr", max_tokens=4000, timeout=90.0))

    timeouts = seen["timeouts"]
    assert len(timeouts) == 2, f"expected exactly 2 attempts, got {len(timeouts)}"
    # R-F4168 (C-182) — this line used to read `== 90.0`, "the first attempt gets
    # the full budget". That allocation was not an invariant, it was the DEFECT:
    # giving attempt 0 the whole deadline meant a turn that deliberates to its
    # token cap always left less than _MIN_RETRY_SECONDS, so the guard below
    # refused the escalation EVERY time. Measured in production 2026-08-19 as
    # ~18 ERRORs/day out of /coder/llm, each resetting Phase A gate #3.
    # The escalation now has a reserved slice (min(30s, timeout/3) = 30s here).
    # Do not "restore" the full budget — that re-closes the cure.
    reserve = oc._escalation_reserve("deepseek-v4-pro", 90.0)
    assert reserve == pytest.approx(30.0)
    assert timeouts[0] == pytest.approx(90.0 - reserve), (
        f"attempt 0 was handed {timeouts[0]}s; it must get the caller's deadline "
        f"MINUS the slice reserved for the escalation"
    )
    # NB these are per-call CEILINGS, not elapsed durations — summing them is
    # meaningless (an earlier draft of this test asserted on the sum and failed
    # against correct behaviour). The two properties that actually matter:
    assert timeouts[1] == pytest.approx(70.0), (
        f"the retry was handed {timeouts[1]}s; it must get exactly what remains "
        f"of the caller's deadline (90 - 20 consumed = 70)"
    )
    assert clock.now - 1000.0 <= 90.0, (
        f"the call consumed {clock.now - 1000.0}s against a 90s deadline — "
        f"the retry is extending the caller's clock instead of sharing it"
    )


def test_the_retry_is_skipped_when_the_deadline_is_nearly_spent(monkeypatch):
    """A bigger budget takes LONGER. Starting a call that cannot finish burns
    the remaining deadline and still returns nothing — fail honestly instead."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    # First attempt consumes all but ~5s of a 40s budget: below the floor.
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _always_truncates(seen, clock, cost_per_call=35.0))

    with pytest.raises(ProviderError) as exc:
        _run(_provider().complete("sys", "usr", max_tokens=4000, timeout=40.0))

    assert len(seen["timeouts"]) == 1, (
        "a retry was started with no time left to finish it"
    )
    assert exc.value.kind == KIND_REASONING_TRUNCATED, (
        "the ORIGINAL failure must surface, not a timeout or a generic error"
    )


def test_a_generous_deadline_still_gets_its_retry(monkeypatch):
    """REGRESSION GUARD — the deadline check must not disable the escalation
    R-F3627 added. With plenty of clock left, the retry still happens."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _always_truncates(seen, clock, cost_per_call=1.0))

    with pytest.raises(ProviderError):
        _run(_provider().complete("sys", "usr", max_tokens=4000, timeout=120.0))

    assert len(seen["timeouts"]) == 2, "the escalation was lost"
    assert seen["timeouts"][1] >= _MIN_RETRY_SECONDS

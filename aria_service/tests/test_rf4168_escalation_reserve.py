"""R-F4168 / C-182 - the cure existed, and a guard written for the OLD cure
refused to let it run. Measured in production, 2026-08-19.

**The live evidence.** `GET /api/aria/health/error-streak` on aria-intel:

    last_error: "[coder/llm] llm.complete failed: [deepseek] reasoning consumed
                 the token budget (model=deepseek-v4-flash, finish_reason=length,
                 reasoning=68349 chars, max_tokens=16384) - no answer produced."
    function:   coder_llm_ep       clean_since: 1787134789 (1.3h before the probe)
    level_breakdown_7d: {log:error: 3}  over a ledger holding only ~4.2h of history

That is ~18 ERRORs/day from one class, and every one of them RESETS Phase A exit
gate #3 ("0 fly ERRORs / 7 days"). The gate cannot close while this fires.

**Which branch raised it is PROVEN by arithmetic, not inferred.**
`sovereign_llm.DEFAULT_MAX_TOKENS` is 8192, so
`_floor_completion_budget(flash, 8192, attempt=0)` = 8192 + 8192 = **16384** -
the number the live error carries. Attempt 1 would be
`min(8192 + 16384, 32768)` = **24576**, which it does not. So the error object
is attempt 0's. At attempt 0 the old `_curable` test was True (kind matched,
attempt 0, 16384 < 32768), so `if not _curable: raise` cannot be the raiser.
The ONLY other statement that re-raises attempt 0's error is the R-F3629
deadline refusal. The escalation was refused on the clock, every time.

**Why the guard was wrong, and it is the C-98 shape.** R-F3629 refuses the retry
under `_MIN_RETRY_SECONDS` because "a larger budget takes LONGER to generate".
That was TRUE of the retry as it existed then: double the tokens. R-F3979 later
replaced the retry with `thinking: {"type": "disabled"}` and MEASURED it at
**13.9s against the baseline 79.2s** - the escalation became the FAST path.
Nothing revisited the guard sizing the clock for the slow one. The R-F3979
docstring names it "the guard that made attempts=1 permanent" and left it.

Attempt 0 was also handed the WHOLE deadline, so on any turn that deliberates to
its token cap there is nothing left and the refusal is certain. The two halves
compose into a cure that can never run.

**The fix, and the trap it has to clear.** Reserve a slice of the caller clock
for the escalation, so the cure always has room; the caller total deadline is
UNCHANGED, which is the R-F3629 invariant. The obvious objection was recorded
before this fix was attempted: shortening attempt 0 makes it end on the CLOCK
instead of the TOKEN CAP, converting a curable `reasoning_truncated` into an
uncurable `timeout` and firing the cure even less often. So a reasoning model
attempt-0 TIMEOUT is now curable too - same cause (it deliberated past what it
was given), same cure (stop it deliberating). Reserving without that is strictly
worse than doing nothing.

Third stale premise, same root: `_budget < _REASONING_MAX_COMPLETION_TOKENS`
refused the retry at the ceiling because "nothing would change about the
request". Since R-F3979 the retry ALSO turns thinking off, which changes the
request materially at any budget - measured: thinking-off produced a 4,743-char
answer at max_tokens=1024 where the baseline produced none.
"""
from __future__ import annotations

import asyncio

import pytest

import aria_service.llm.openai_compat as oc
from aria_service.llm.openai_compat import (
    KIND_REASONING_TRUNCATED,
    _MIN_RETRY_SECONDS,
    _REASONING_MAX_COMPLETION_TOKENS,
)
from aria_service.llm.provider import ProviderError


def _run(coro):
    return asyncio.run(coro)


class _Clock:
    """Deterministic stand-in for the `time` module (mirrors the R-F3629 one)."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now


def _stub_client(seen: dict, clock: _Clock, *, behaviour):
    """An httpx.AsyncClient stand-in.

    `behaviour(attempt_index)` returns "truncate" | "timeout" | "answer" and
    decides what each attempt does. Every attempt records the per-call timeout
    it was handed and the payload it sent, and burns that whole timeout off the
    fake clock - because the failures under test are exactly the ones that run
    until something stops them.
    """

    class _Resp:
        status_code = 200
        text = "stub"

        def __init__(self, kind: str):
            self._kind = kind

        def json(self):
            if self._kind == "truncate":
                return {
                    "choices": [{
                        "message": {"content": "",
                                    "reasoning_content": "z" * 68_349},
                        "finish_reason": "length",
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                    "model": "deepseek-v4-flash",
                }
            return {
                "choices": [{"message": {"content": "the answer"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                "model": "deepseek-v4-flash",
            }

    class _Client:
        def __init__(self, *a, **k):
            seen.setdefault("timeouts", []).append(k.get("timeout"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            idx = len(seen["timeouts"]) - 1
            seen.setdefault("payloads", []).append(k.get("json"))
            # The call runs until its own ceiling stops it: that IS the live
            # shape (attempt 0 deliberates until the token cap or the clock).
            clock.now += float(seen["timeouts"][idx] or 0.0)
            what = behaviour(idx)
            if what == "timeout":
                raise oc.httpx.ReadTimeout("stub read timeout")
            return _Resp(what)

    return _Client


def _provider(model: str = "deepseek-v4-flash"):
    return oc.OpenAICompatProvider(
        name="deepseek", api_key="k", model=model,
        base_url="https://example.invalid/v1",
    )


def _thinking_disabled(payload) -> bool:
    return bool((payload or {}).get("thinking", {}).get("type") == "disabled")


# -- THE CAPABILITY TEST - the exact production failure -----------------------

def test_the_live_coder_failure_now_reaches_the_cure(monkeypatch):
    """Replays the measured production call: the sovereign_llm max_tokens=8192
    on a 120s deadline, attempt 0 deliberating until something stops it.

    Before R-F4168 this produced ONE attempt and a 502 to the self-coder, ~18
    times a day, each one resetting Phase A gate #3.
    """
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(
        oc.httpx, "AsyncClient",
        _stub_client(seen, clock,
                     behaviour=lambda i: "truncate" if i == 0 else "answer"),
    )

    result = _run(_provider().complete(
        "sys", "usr", max_tokens=8192, timeout=120.0,
    ))

    assert result.text == "the answer", (
        "the escalation never ran: the self-coder still gets nothing"
    )
    assert len(seen["timeouts"]) == 2, (
        f"expected 2 attempts, got {len(seen['timeouts'])} - the R-F3629 clock "
        f"guard is still refusing the cure it was never sized for"
    )
    assert _thinking_disabled(seen["payloads"][1]), (
        "the retry ran but did not disable thinking - it is feeding the disease "
        "(R-F3979: more room buys more deliberation, not more answer)"
    )


def test_the_reserved_slice_is_big_enough_to_be_admitted(monkeypatch):
    """The reserve is only useful if it clears the escalation OWN floor.
    Reserving 10s against a 15s floor would shorten attempt 0 for a retry the
    guard then refuses - worse than reserving nothing."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(
        oc.httpx, "AsyncClient",
        _stub_client(seen, clock,
                     behaviour=lambda i: "truncate" if i == 0 else "answer"),
    )

    _run(_provider().complete("sys", "usr", max_tokens=8192, timeout=120.0))

    first, second = seen["timeouts"]
    assert first < 120.0, "attempt 0 was still handed the whole deadline"
    assert second >= _MIN_RETRY_SECONDS, (
        f"the retry was admitted with only {second}s - below the "
        f"{_MIN_RETRY_SECONDS}s floor the guard itself enforces"
    )


def test_the_caller_total_deadline_is_still_not_doubled(monkeypatch):
    """REGRESSION GUARD for the real R-F3629 invariant. Reserving clock must
    redistribute the caller deadline, never extend it - `timeout` is a contract
    the chain sizes its per-provider budget from."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(
        oc.httpx, "AsyncClient",
        _stub_client(seen, clock,
                     behaviour=lambda i: "truncate" if i == 0 else "answer"),
    )

    _run(_provider().complete("sys", "usr", max_tokens=8192, timeout=120.0))

    assert clock.now - 1000.0 <= 120.0, (
        f"the two attempts consumed {clock.now - 1000.0}s against a 120s "
        f"deadline - the retry is extending the caller clock, not sharing it"
    )


# -- THE TRAP: a reserved clock turns truncation into a timeout ---------------

def test_a_reasoning_model_that_runs_out_of_CLOCK_is_cured_too(monkeypatch):
    """Shortening attempt 0 makes it end on the CLOCK rather than the TOKEN CAP.
    If a timeout were not curable, this fix would fire the cure LESS often than
    doing nothing - the documented reason it was not attempted before."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(
        oc.httpx, "AsyncClient",
        _stub_client(seen, clock,
                     behaviour=lambda i: "timeout" if i == 0 else "answer"),
    )

    result = _run(_provider().complete("sys", "usr", max_tokens=8192,
                                       timeout=120.0))

    assert result.text == "the answer"
    assert len(seen["timeouts"]) == 2, (
        "a reasoning model that spent its whole clock deliberating was raised "
        "as an uncurable timeout - the trap this fix exists to clear"
    )
    assert _thinking_disabled(seen["payloads"][1])


def test_a_CLASSIC_model_timeout_is_NOT_retried(monkeypatch):
    """A model with no deliberation to disable has nothing to cure: the retry
    would be byte-identical, so it is a wasted call and a doubled outage."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(
        oc.httpx, "AsyncClient",
        _stub_client(seen, clock, behaviour=lambda i: "timeout"),
    )

    with pytest.raises(ProviderError) as exc:
        _run(_provider(model="gpt-4o-mini").complete(
            "sys", "usr", max_tokens=8192, timeout=120.0))

    assert exc.value.kind == "timeout"
    assert len(seen["timeouts"]) == 1, (
        "a classic model timeout was retried - nothing about the request "
        "would have changed"
    )


def test_a_classic_model_keeps_its_whole_clock(monkeypatch):
    """The reserve exists for the escalation. A model that has no escalation
    must not have its one and only attempt shortened for it."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(
        oc.httpx, "AsyncClient",
        _stub_client(seen, clock, behaviour=lambda i: "answer"),
    )

    _run(_provider(model="gpt-4o-mini").complete(
        "sys", "usr", max_tokens=8192, timeout=120.0))

    assert seen["timeouts"][0] == pytest.approx(120.0), (
        "a non-reasoning model lost part of its deadline to a reserve it can "
        "never use"
    )


# -- A SHORT DEADLINE MUST NOT BE CARVED UP ----------------------------------

def test_no_reserve_when_the_slice_would_be_unusable(monkeypatch):
    """On a 30s deadline a 15s floor cannot be met without halving attempt 0.
    Taking a slice the guard will then refuse is pure loss: attempt 0 gets
    everything, and the honest failure surfaces."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(
        oc.httpx, "AsyncClient",
        _stub_client(seen, clock, behaviour=lambda i: "truncate"),
    )

    with pytest.raises(ProviderError) as exc:
        _run(_provider().complete("sys", "usr", max_tokens=8192, timeout=30.0))

    assert seen["timeouts"][0] == pytest.approx(30.0), (
        f"attempt 0 was cut to {seen['timeouts'][0]}s to reserve a slice too "
        f"small for the retry to be admitted with"
    )
    assert exc.value.kind == KIND_REASONING_TRUNCATED, (
        "the ORIGINAL failure must surface, not a timeout"
    )


# -- THE THIRD STALE PREMISE (same root) -------------------------------------

def test_the_ceiling_budget_still_escalates(monkeypatch):
    """`_budget < _REASONING_MAX_COMPLETION_TOKENS` refused the retry at the
    ceiling because "nothing would change about the request". Since R-F3979 the
    retry turns thinking OFF, which changes it at every budget - and budget was
    never the lever anyway."""
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    seen: dict = {}
    monkeypatch.setattr(
        oc.httpx, "AsyncClient",
        _stub_client(seen, clock,
                     behaviour=lambda i: "truncate" if i == 0 else "answer"),
    )

    # Chosen so the attempt-0 budget lands exactly on the ceiling.
    ceiling_caller = (_REASONING_MAX_COMPLETION_TOKENS
                      - oc._REASONING_HEADROOM_TOKENS)
    assert oc._floor_completion_budget(
        "deepseek-v4-flash", ceiling_caller, attempt=0,
    ) == _REASONING_MAX_COMPLETION_TOKENS

    result = _run(_provider().complete(
        "sys", "usr", max_tokens=ceiling_caller, timeout=120.0))

    assert result.text == "the answer"
    assert len(seen["timeouts"]) == 2, (
        "a turn already at the token ceiling was refused the ONE lever that "
        "does not depend on the budget"
    )
    assert _thinking_disabled(seen["payloads"][1])

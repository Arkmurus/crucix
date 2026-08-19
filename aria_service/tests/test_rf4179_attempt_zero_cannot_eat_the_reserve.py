"""R-F4179 / C-190 - the escalation reserve was computed but never enforced, so
attempt 0 ate it.

R-F4168 reserves a slice of the caller's clock so the disable-thinking
escalation can run. The arithmetic is right. The ceiling it rests on is not a
bound, so attempt 0 routinely overran and the cure was refused anyway.

**Captured live 2026-08-19** by STREAMING the logs (the buffer holds only
minutes; two earlier attempts to go back for it had already failed):

    [R-F3629] deepseek (deepseek-v4-flash) spent its whole allowance
              (kind=timeout), but only -0.5s of the caller's 30.0s timeout remains
    [R-F3629] deepseek (deepseek-v4-flash) spent its whole allowance
              (kind=timeout), but only  6.9s of the caller's 60.0s timeout remains

On the 60s call the reserve is `min(30, 60/3)` = 20s, so attempt 0 is handed a
**40s** ceiling and 20s should survive. Only **6.9s** did - attempt 0 consumed
**53.1s**, a 13-second overrun, dropping under the 15s floor so the backstop
refused the cure. On the 30s call the reserve is correctly declined (10s is under
the floor) and attempt 0 returns at **-0.5s**, past the caller's deadline
outright.

That also explains the `coder_llm_ep` errors that persisted after R-F4168 while
still reporting `max_tokens=16384` - attempt 0's budget. On a 120s caller its
90s ceiling overran by enough to leave under 15s.

**Root cause.** `_one_completion` bounds the call with
`httpx.AsyncClient(timeout=X)`. An httpx `Timeout` is **per-phase** - connect,
read, write, pool - not a total-request deadline. A response whose every phase
stays under X can still exceed X in wall clock, and event-loop scheduling delay
adds to it. The number the reserve arithmetic rests on was a hope.

**The trap, which is why this is its own R-number.** `asyncio.TimeoutError` is
not a `ProviderError`, so a bare `wait_for` would escape `complete()`'s
`except ProviderError` and become an UNCURABLE error - firing the cure LESS
often than doing nothing, which is precisely the regression R-F4168's own entry
warns about. It is mapped to `ProviderError(kind="timeout")`, which R-F4168
already made curable for a reasoning model.
"""
from __future__ import annotations

import asyncio

import pytest

import aria_service.llm.openai_compat as oc
from aria_service.llm.provider import ProviderError


def _run(coro):
    return asyncio.run(coro)


def _provider(model: str = "deepseek-v4-flash"):
    return oc.OpenAICompatProvider(
        name="deepseek", api_key="k", model=model,
        base_url="https://example.invalid/v1",
    )


def _overrunning_client(seen: dict, *, overrun: float, then: str = "answer"):
    """A client whose POST takes LONGER than the timeout it was handed - the
    live behaviour httpx's per-phase timeout does not prevent."""

    class _Resp:
        status_code = 200
        text = "stub"

        def json(self):
            return {"choices": [{"message": {"content": "the answer"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                    "model": "deepseek-v4-flash"}

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
            if idx == 0:
                # Sleep past the ceiling. Without a real bound this returns
                # normally and the caller's clock is already gone.
                await asyncio.sleep((seen["timeouts"][0] or 0) + overrun)
            return _Resp()

    return _Client


# ── THE CAPABILITY TEST ─────────────────────────────────────────────────────

def test_attempt_zero_is_cut_off_at_its_ceiling(monkeypatch):
    """Attempt 0 tries to run 0.6s past a 0.4s ceiling. It must be STOPPED at
    the ceiling, so the reserve it was supposed to leave still exists."""
    seen: dict = {}
    monkeypatch.setattr(oc, "_ESCALATION_RESERVE_S", 0.30)
    monkeypatch.setattr(oc, "_MIN_RETRY_SECONDS", 0.10)
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _overrunning_client(seen, overrun=0.6))

    result = _run(_provider().complete("sys", "usr", max_tokens=8192,
                                       timeout=0.9))

    assert len(seen["timeouts"]) == 2, (
        f"attempt 0 ran past its ceiling and ate the reserve; only "
        f"{len(seen['timeouts'])} attempt(s) ran"
    )
    assert result.text == "the answer"


def test_the_cut_off_is_curable_not_a_new_uncurable_error(monkeypatch):
    """THE TRAP. asyncio.TimeoutError is not a ProviderError. If the bound is
    added without mapping it, the escalation stops firing entirely and this fix
    is worse than doing nothing."""
    seen: dict = {}
    monkeypatch.setattr(oc, "_ESCALATION_RESERVE_S", 0.30)
    monkeypatch.setattr(oc, "_MIN_RETRY_SECONDS", 0.10)
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _overrunning_client(seen, overrun=0.6))

    _run(_provider().complete("sys", "usr", max_tokens=8192, timeout=0.9))

    assert (seen["payloads"][1] or {}).get("thinking", {}).get("type") == "disabled", (
        "the retry ran but did not disable thinking - the cut-off was not "
        "recognised as the curable timeout it is"
    )


def test_a_classic_model_surfaces_the_cut_off_as_a_provider_timeout(monkeypatch):
    """A model with no thinking to disable has nothing to cure, so the cut-off
    must surface as an honest ProviderError - never as a bare
    asyncio.TimeoutError escaping the chain."""
    seen: dict = {}
    monkeypatch.setattr(oc, "_ESCALATION_RESERVE_S", 0.30)
    monkeypatch.setattr(oc, "_MIN_RETRY_SECONDS", 0.10)
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _overrunning_client(seen, overrun=0.6))

    with pytest.raises(ProviderError) as exc:
        _run(_provider(model="gpt-4o-mini").complete(
            "sys", "usr", max_tokens=8192, timeout=0.9))

    assert exc.value.kind == "timeout"
    assert len(seen["timeouts"]) == 1, "a classic model's cut-off was retried"


def test_the_callers_total_deadline_is_still_respected(monkeypatch):
    """The whole point of the reserve: two attempts inside ONE caller deadline."""
    seen: dict = {}
    monkeypatch.setattr(oc, "_ESCALATION_RESERVE_S", 0.30)
    monkeypatch.setattr(oc, "_MIN_RETRY_SECONDS", 0.10)
    monkeypatch.setattr(oc.httpx, "AsyncClient",
                        _overrunning_client(seen, overrun=5.0))

    import time as _t
    t0 = _t.monotonic()
    _run(_provider().complete("sys", "usr", max_tokens=8192, timeout=0.9))
    elapsed = _t.monotonic() - t0

    assert elapsed < 3.0, (
        f"the call took {elapsed:.1f}s against a 0.9s deadline while attempt 0 "
        f"tried to run 5s over - the bound is not being enforced"
    )


# ── the healthy path is untouched ───────────────────────────────────────────

def _fast_client(seen: dict):
    """A client that answers immediately — the overwhelming majority of calls."""

    class _Resp:
        status_code = 200
        text = "stub"

        def json(self):
            return {"choices": [{"message": {"content": "the answer"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                    "model": "deepseek-v4-flash"}

    class _Client:
        def __init__(self, *a, **k):
            seen.setdefault("timeouts", []).append(k.get("timeout"))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    return _Client


def test_a_prompt_call_is_not_slowed_or_wrapped_away(monkeypatch):
    """REGRESSION GUARD — the healthy path is the overwhelming majority of
    calls. The bound must be invisible to it: one attempt, no retry, no added
    latency.

    (The first draft of this test reused the overrunning stub with `overrun=0`,
    which still slept the FULL ceiling — so it exercised the bound rather than
    the fast path, and took 30s to fail.)"""
    seen: dict = {}
    monkeypatch.setattr(oc.httpx, "AsyncClient", _fast_client(seen))

    import time as _t
    t0 = _t.monotonic()
    result = _run(_provider().complete("sys", "usr", max_tokens=8192,
                                       timeout=30.0))
    elapsed = _t.monotonic() - t0

    assert result.text == "the answer"
    assert len(seen["timeouts"]) == 1, "a healthy call was retried"
    assert elapsed < 1.0, f"the bound added {elapsed:.2f}s to a healthy call"

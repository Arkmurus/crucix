"""R-F4317 / C-265 - a prompt too big for the window was SENT anyway.

Found in the deep review before flipping ARIA_LLM_PRIMARY_ALL=1 (routing every
turn to the sovereign model rather than only grounded synthesis).

R-F1363 clamps `max_tokens` so prompt+completion fit the served window. Its
arithmetic is right for the case it was written for - a large-but-fitting prompt.
It is wrong for the case that matters here:

    safe_max_tokens = max_model_len - est_prompt_tokens - 256
    if safe_max_tokens < 256:
        safe_max_tokens = 256          # <-- and then it SENDS the request

When the PROMPT ALONE exceeds the window, `safe_max_tokens` is negative. The code
floors it at 256 and posts anyway, and vLLM answers HTTP 400 "maximum context
length exceeded". R-F1363's own comment records what that costs: it "soft-cooled
the provider and failed every self-coding fix at the plan step."

WHY IT MATTERS NOW. Measured demand is a mean of 5,671 tokens per call against a
16,384-token window, and `research_extraction` alone is 9,861 calls a month.
Under the current promotion stage those never reach her, so the defect is
dormant. Flipping PRIMARY_ALL routes them to her and wakes it - every oversized
prompt becomes a 400, and enough 400s cool the provider and take the whole
sovereign path down.

THE FIX IS ELIGIBILITY, NOT TRUNCATION. If the prompt cannot fit, the sovereign
is simply not a candidate for that call: return the ordinary not-ok result so the
caller falls back to a larger-window provider, exactly as it already does for an
unreachable endpoint. No new plumbing.

TRUNCATING WOULD BE WORSE THAN FAILING. Silently dropping the tail of a research
extraction or a DD prompt returns a confident answer computed from part of the
evidence, and nothing downstream can tell. That is the readiness note's rule -
"fail CLOSED ... never truncate to fit" - and it is the same principle as
refusing to invent a missing prompt in the corpus builder (C-257).
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.llm import aria_llm_provider as p  # noqa: E402


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", "http://sovereign.invalid/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "16384")


def _run(coro):
    return asyncio.run(coro)


def _chars_for_tokens(tok: int) -> str:
    # the provider estimates ~4 chars/token
    return "x" * (tok * 4)


# -- the defect: an oversized prompt must never be sent --------------------

def test_an_oversized_prompt_is_refused_before_any_request(monkeypatch) -> None:
    """THE CAPABILITY TEST. A 20k-token prompt into a 16k window used to be
    posted with max_tokens=256 and answered HTTP 400, which cools the provider."""
    posted = []

    class _Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            posted.append(a)
            raise AssertionError("a request was sent for a prompt that cannot fit")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Boom)

    out = _run(p.complete(_chars_for_tokens(20000), max_tokens=512))
    assert out["ok"] is False
    assert posted == [], "the provider posted an unfittable prompt"


def test_the_refusal_names_the_window_and_the_overflow() -> None:
    """A caller (and a human reading a gap) must be able to tell this apart from
    an unreachable endpoint - the remedy is completely different."""
    out = _run(p.complete(_chars_for_tokens(20000), max_tokens=512))
    err = (out.get("error") or "").lower()
    assert "window" in err or "context" in err, out.get("error")
    assert "16384" in (out.get("error") or ""), out.get("error")


def test_it_reports_not_ok_so_the_caller_falls_back() -> None:
    """Reuse the existing failover contract rather than inventing one: callers
    already treat ok=False as 'try the next provider'."""
    out = _run(p.complete(_chars_for_tokens(20000)))
    assert out["ok"] is False
    assert out.get("provider") == "aria_llm"
    assert out.get("text") == ""


def test_the_prompt_is_never_truncated(monkeypatch) -> None:
    """Silently dropping the tail returns a confident answer computed from part
    of the evidence, and nothing downstream can tell.

    Asserted as a PROPERTY, not a word: an earlier version banned the substring
    "truncat" and went red on a message that says "Not truncating" - the same
    prose-versus-code confusion that tripped R-F4297 and R-F4305. The real proof
    is that no request carrying a shortened prompt is ever sent.
    """
    sent = []

    class _Capture:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            sent.append(json)
            raise AssertionError("a request was sent for an unfittable prompt")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Capture)

    big = _chars_for_tokens(20000)
    out = _run(p.complete(big))
    assert out["ok"] is False
    assert sent == [], "a (necessarily truncated) prompt was transmitted"


# -- the fitting case must be untouched ------------------------------------

def test_a_fitting_prompt_still_proceeds(monkeypatch) -> None:
    """R-F1363's clamp must survive: this fix narrows eligibility, it does not
    disable the sovereign for ordinary large prompts."""
    seen = {}

    class _OK:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            seen["body"] = json
            class _R:
                status_code = 200
                @staticmethod
                def json():
                    return {"choices": [{"message": {"content": "ok"}}],
                            "usage": {"total_tokens": 10}}
                text = ""
            return _R()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _OK)

    out = _run(p.complete(_chars_for_tokens(4000), max_tokens=2048))
    assert out["ok"] is True, out.get("error")
    assert seen["body"]["max_tokens"] <= 16384 - 4000 - 200


def test_the_boundary_is_not_off_by_one(monkeypatch) -> None:
    """A prompt that leaves room for a real answer must still be served; the
    guard must not creep inward and disable her for ordinary work."""
    sent = {"n": 0}

    class _OK:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            sent["n"] += 1
            class _R:
                status_code = 200
                @staticmethod
                def json():
                    return {"choices": [{"message": {"content": "ok"}}], "usage": {}}
                text = ""
            return _R()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _OK)
    # ~15,000 tokens into a 16,384 window: tight, but a 256-token answer fits.
    out = _run(p.complete(_chars_for_tokens(15000), max_tokens=2048))
    assert out["ok"] is True, out.get("error")
    assert sent["n"] == 1


def test_the_window_comes_from_env_not_a_literal(monkeypatch) -> None:
    """ARIA_LLM_MAX_MODEL_LEN must match the vLLM --max-model-len; a hardcoded
    ceiling would silently disagree with the server the moment either moves."""
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "8192")
    out = _run(p.complete(_chars_for_tokens(9000)))
    assert out["ok"] is False
    assert "8192" in (out.get("error") or "")


def test_streaming_refuses_the_same_way(monkeypatch) -> None:
    """The streaming path shares the window and must share the rule - §13's
    stream-bypass lesson: a guard added to one path and not the other."""
    async def _collect():
        chunks = []
        async for c in p.stream(_chars_for_tokens(20000)):
            chunks.append(c)
        return chunks

    class _Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **k):
            raise AssertionError("stream sent a prompt that cannot fit")

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    out = _run(_collect())
    assert out == [] or all(not c for c in out), out


# -- window_overflow called DIRECTLY ---------------------------------------
#
# The R-F1958 pre-commit gate requires a capability test that INVOKES the new
# function rather than reaching it through a caller, and it is right to: a
# predicate exercised only via complete() is proven as far as complete() happens
# to use it, and the streaming path calls it independently.

def test_window_overflow_refuses_an_unfittable_prompt() -> None:
    reason = p.window_overflow("", _chars_for_tokens(20000))
    assert reason, "a 20k prompt in a 16,384 window must be refused"
    assert "16384" in reason


def test_window_overflow_allows_a_fitting_prompt() -> None:
    assert p.window_overflow("", _chars_for_tokens(4000)) == ""


def test_window_overflow_counts_the_system_prompt_too() -> None:
    """The system prompt shares the window. Counting only the user turn would
    pass a request that then overflows on the server."""
    assert p.window_overflow("", _chars_for_tokens(15000)) == ""
    assert p.window_overflow(_chars_for_tokens(15000), _chars_for_tokens(15000))


def test_window_overflow_reserves_room_to_answer() -> None:
    """A prompt that exactly fills the window 'fits' only in a useless sense:
    there would be no tokens left to reply with."""
    just_under = p._max_model_len() - 100          # no room for a 256-tok answer
    assert p.window_overflow("", _chars_for_tokens(just_under))


def test_window_overflow_tracks_the_env_window(monkeypatch) -> None:
    monkeypatch.setenv("ARIA_LLM_MAX_MODEL_LEN", "8192")
    assert p.window_overflow("", _chars_for_tokens(9000))
    assert p.window_overflow("", _chars_for_tokens(2000)) == ""

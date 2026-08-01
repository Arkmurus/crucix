"""R-F3614 + R-F3616 — the last two open items from the 2026-08-01 outage.

R-F3614 — THE SOVEREIGN NEVER HONOURED ITS TIMEOUT.
`aria_llm_provider.complete()`/`stream()` ended in `**_kw` with no `timeout`
parameter, so the value `model_router._sovereign_complete` has always passed
(`timeout=_sovereign_timeout(timeout)`) was silently DISCARDED and every
sovereign call ran on the 120s client default.

R-F1365 existed to "cap the per-provider budget ... so a slow/stuck 14B fails
over to the funded DeepSeek fallback fast instead of burning 120s". It never
bound the sovereign at all. Worse, because the cap was applied at the CALLER
(aria_engine) it only ever bound DEEPSEEK — the exact inverse of the intent,
and half of what took chat down (R-F3606 removed that half).

R-F3616 — PAGE BEFORE THE OUTAGE, NOT ONLY DURING IT.
R-F3613 pages when EVERY provider has failed. By then the user has already had
a degraded reply — which is exactly how 2026-08-01 was discovered. The state
worth catching is the one just before: redundancy exists in config, and it is
gone, so one more failure is a total outage.

The trigger is deliberately "redundancy lost", NOT a failure rate. §14 says a
cooling provider covered by a fallback is "operational, never degraded"; a
raw failure-rate alarm would page every time the chain did its job. "You have
no second chance left" is a different, honest claim.
"""
import asyncio

import pytest

from aria_service.llm import aria_llm_provider as P
from aria_service.llm import fallback as fb
from aria_service.llm.provider import LLMProvider, ProviderError


def _run(coro):
    return asyncio.run(coro)


# ── R-F3614 — the timeout reaches the socket ─────────────────────────────────


def test_effective_timeout_prefers_the_caller_then_falls_back():
    assert P._effective_timeout(40.0) == 40.0
    assert P._effective_timeout(None) == P._DEFAULT_TIMEOUT
    # a nonsense budget must not create an instantly-expiring client
    assert P._effective_timeout(0) == P._DEFAULT_TIMEOUT
    assert P._effective_timeout(-5) == P._DEFAULT_TIMEOUT
    assert P._effective_timeout("nope") == P._DEFAULT_TIMEOUT


def test_complete_and_stream_accept_timeout_explicitly():
    """The defect was structural: no parameter existed, so **_kw ate it."""
    import inspect
    for fn in (P.complete, P.stream):
        assert "timeout" in inspect.signature(fn).parameters, (
            f"{fn.__name__} must take timeout explicitly, not via **_kw"
        )


def _capture_client(monkeypatch, seen):
    class _Client:
        def __init__(self, *a, **k):
            seen["timeout"] = k.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            class _R:
                status_code = 200

                @staticmethod
                def json():
                    return {"choices": [{"message": {"content": "hi"},
                                         "finish_reason": "stop"}], "usage": {}}
            return _R()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _Client)


def test_capability_the_routers_budget_reaches_the_socket(monkeypatch):
    """FAILS BEFORE: the client was always built with _DEFAULT_TIMEOUT=120.0
    no matter what the router computed."""
    monkeypatch.setenv("ARIA_LLM_URL", "http://fake:8888/v1")
    seen: dict = {}
    _capture_client(monkeypatch, seen)

    _run(P.complete("hi", system="", max_tokens=64, timeout=40.0))

    assert seen["timeout"] == 40.0, (
        f"the sovereign was given {seen['timeout']}s, not the router's 40s — "
        "a stuck sovereign would burn the full default before failover"
    )


def test_capability_model_router_actually_passes_it_through(monkeypatch):
    """Drive the REAL caller, not a proxy: _sovereign_complete computes
    _sovereign_timeout(timeout) and must now have it honoured."""
    import aria_service.llm.model_router as mr
    seen: dict = {}

    async def _fake_complete(prompt, *, system="", max_tokens=2048,
                             timeout=None, **kw):
        seen["timeout"] = timeout
        return {"ok": True, "text": "answer", "model": "aria-llm-v0.1"}

    monkeypatch.setattr(mr.aria_llm_provider, "complete", _fake_complete)
    monkeypatch.delenv("ARIA_LLM_TIMEOUT", raising=False)

    res = _run(mr._sovereign_complete("sys", "usr", max_tokens=800, timeout=40.0))
    assert res is not None
    assert seen["timeout"] == 40.0, (
        "the router's per-call sovereign budget must arrive as a real argument"
    )


# ── R-F3616 — the pre-outage page ────────────────────────────────────────────


class _P(LLMProvider):
    def __init__(self, name):
        self.name = name

    @property
    def is_configured(self):
        return True

    async def complete(self, *a, **k):
        raise ProviderError(self.name, "boom", kind="other", retryable=True)

    async def stream(self, *a, **k):
        raise ProviderError(self.name, "boom", kind="other", retryable=True)
        yield ""  # pragma: no cover


@pytest.fixture(autouse=True)
def _reset_windows(monkeypatch):
    monkeypatch.setattr(fb, "_last_chain_alert_at", 0.0, raising=False)
    monkeypatch.setattr(fb, "_last_redundancy_alert_at", 0.0, raising=False)


def _capture_pages(monkeypatch):
    pages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        fb.FallbackProvider, "_dispatch_operator_page",
        lambda self, text, *, source: pages.append((source, text)),
        raising=True,
    )
    return pages


def test_capability_losing_the_last_fallback_pages_before_the_outage(monkeypatch):
    """Two providers; cool ONE. The chain is still serving — but it has no
    second chance, and that must reach the operator."""
    pages = _capture_pages(monkeypatch)
    chain = fb.FallbackProvider([_P("deepseek"), _P("deepseek_backup")])

    # Cool exactly ONE provider. NB the survivor's failure must be a SOFT
    # first failure (kind="other", failures=1) — a rate_limit cools immediately,
    # which would leave 0 available and be the R-F3613 outage path instead.
    chain._stats["deepseek_backup"] = {"cooldown_until": fb.time.time() + 300}
    chain._record_failure(_P("deepseek"), chain._stats.setdefault("deepseek", {}),
                          ProviderError("deepseek", "boom", kind="other"))

    assert pages, "losing the last fallback must page"
    source, text = pages[0]
    assert source == "llm_chain_redundancy_lost"
    assert "no fallback left" in text.lower()
    assert "NOT degraded" in text, (
        "§14 — the page must say the chain is still serving, or it reads as "
        "an outage that is not happening"
    )


def test_a_still_redundant_chain_does_not_page(monkeypatch):
    """Cry-wolf guard: two available providers is the healthy state."""
    pages = _capture_pages(monkeypatch)
    chain = fb.FallbackProvider([_P("a"), _P("b"), _P("c")])
    chain._record_failure(_P("a"), chain._stats.setdefault("a", {}),
                          ProviderError("a", "boom", kind="other"))
    assert not pages, "2+ providers still available is not a redundancy loss"


def test_a_single_provider_chain_is_silent_by_construction(monkeypatch):
    """Documented limitation, pinned so it is a decision and not a surprise:
    with no fallback configured there is no redundancy to lose."""
    pages = _capture_pages(monkeypatch)
    chain = fb.FallbackProvider([_P("only")])
    chain._record_failure(_P("only"), chain._stats.setdefault("only", {}),
                          ProviderError("only", "boom", kind="rate_limit"))
    assert not pages


def test_the_redundancy_page_is_rate_limited(monkeypatch):
    pages = _capture_pages(monkeypatch)
    chain = fb.FallbackProvider([_P("a"), _P("b")])
    chain._stats["b"] = {"cooldown_until": fb.time.time() + 300}
    chain._stats.setdefault("a", {})
    # Drive the check directly and hold the state steady: routing further
    # failures through _record_failure would cool the survivor too and flip
    # this into the outage path, which is a different alert.
    for _ in range(20):
        chain._check_redundancy_lost()
    assert len(pages) == 1, f"expected one page, got {len(pages)}"


def test_redundancy_and_outage_pages_use_separate_windows(monkeypatch):
    """A 'no fallback left' warning must never suppress the 'everything is
    down' page that may follow minutes later — two claims, two clocks."""
    assert fb._last_chain_alert_at is not None
    assert fb._last_redundancy_alert_at is not None
    import inspect
    src = inspect.getsource(fb.FallbackProvider._check_redundancy_lost)
    assert "_last_redundancy_alert_at" in src
    assert "_last_chain_alert_at" not in src, (
        "the pre-outage page must not consume the outage page's window"
    )


def test_both_pages_share_ONE_sender():
    """The drift that caused this session's other defects. One sender only."""
    import inspect
    for meth in (fb.FallbackProvider._alert_operator_chain_down,
                 fb.FallbackProvider._check_redundancy_lost):
        assert "_dispatch_operator_page" in inspect.getsource(meth)
    sender = inspect.getsource(fb.FallbackProvider._dispatch_operator_page)
    assert "wa_notifier" in sender
    assert "is_configured" in sender, "unconfigured channel must stay non-silent"

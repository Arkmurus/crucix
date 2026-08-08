"""R-F3613 — a total LLM outage must PAGE the operator, on BOTH forks.

WHY THIS EXISTS. On 2026-08-01 the chain failed on every turn for hours. The
gap was filed (R-F3036) and the health metric moved (R-F3477) — and the operator
still discovered it by asking ARIA in WhatsApp and receiving a degraded reply.
CLAUDE.md §19e names that the worst outcome: "a blocker the operator has to find
himself". Recording an outage is not reporting it.

TWO defects are closed here:

1. NOBODY WAS PAGED. Every existing sink was a surface you have to go and read
   (a gap list, a health endpoint, a stats page). None of them reaches out.

2. THE STREAM FORK EXHAUSTED SILENTLY (§13). `complete()` recorded the
   exhaustion and wired it; `stream()` raised "all LLM providers failed
   (stream)" and did NEITHER. So during a streaming outage:
     - get_health().resilient stayed True,
     - no gap was filed,
     - and R-F3612's self_introspect block would have reported the chain FINE.
   Web chat streams, so this was the likelier fork to be hit. Both now route
   through one shared handler.
"""
import asyncio

import pytest

from aria_service.llm import fallback as fb
from aria_service.llm.provider import LLMProvider, LLMResult, ProviderError

# R-F3789/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _run(coro):
    return asyncio.run(coro)


class _DeadProvider(LLMProvider):
    """A provider that fails every call, like deepseek-v4 did on 2026-08-01."""

    def __init__(self, name):
        self.name = name

    @property
    def is_configured(self):
        return True

    async def complete(self, system_prompt, user_message, **kw):
        raise ProviderError(self.name, "reasoning consumed the token budget",
                            kind="other", retryable=True)

    async def stream(self, system_prompt, user_message, **kw):
        raise ProviderError(self.name, "reasoning consumed the token budget",
                            kind="other", retryable=True)
        yield ""  # pragma: no cover — unreachable, keeps this an async generator


@pytest.fixture(autouse=True)
def _reset_alert_cooldown(monkeypatch):
    """Each test starts with the page window OPEN. Without this the module-level
    cooldown leaks between tests — the order-dependence class that cost 15
    failures before."""
    monkeypatch.setattr(fb, "_last_chain_alert_at", 0.0, raising=False)


def _chain():
    c = fb.FallbackProvider([_DeadProvider("deepseek"),
                             _DeadProvider("deepseek_backup")])
    return c


def _capture_alerts(monkeypatch):
    """Capture pages instead of sending them. Patches the ALERTER, so the
    exhaustion path either calls it or the test fails — no network."""
    sent: list[str] = []

    def _fake_alert(self, tried, attempted, last_error, path):
        sent.append(f"{path}|{tried}|{last_error}")

    monkeypatch.setattr(fb.FallbackProvider, "_alert_operator_chain_down",
                        _fake_alert, raising=True)
    return sent


# ── THE CAPABILITY TEST — both forks page ────────────────────────────────────


def test_capability_total_outage_pages_the_operator_on_the_complete_path(monkeypatch):
    sent = _capture_alerts(monkeypatch)
    chain = _chain()

    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "Tony is flying to Bulgaria — any risks?"))

    assert sent, "a total outage must page the operator; nothing was sent"
    assert sent[0].startswith("complete|")
    assert "deepseek" in sent[0]


def test_capability_total_outage_pages_the_operator_on_the_STREAM_path(monkeypatch):
    """FAILS BEFORE THE FIX: the stream fork raised without recording or wiring
    anything at all, so no page could ever have been sent from it."""
    sent = _capture_alerts(monkeypatch)
    chain = _chain()

    async def _drain():
        async for _ in chain.stream("sys", "usr"):
            pass

    with pytest.raises(ProviderError):
        _run(_drain())

    assert sent, "the STREAM fork must page too — web chat streams"
    assert sent[0].startswith("stream|")


# ── The stream fork must stop lying to get_health() ──────────────────────────


def test_stream_exhaustion_marks_the_chain_NOT_resilient(monkeypatch):
    """The §13 bypass in its most damaging form: before this, a streaming
    outage left resilient=True, so R-F3612's introspection block would have
    told the operator the chain was healthy mid-outage."""
    _capture_alerts(monkeypatch)
    chain = _chain()
    assert chain.get_health()["resilient"] is True, "precondition: starts healthy"

    async def _drain():
        async for _ in chain.stream("sys", "usr"):
            pass

    with pytest.raises(ProviderError):
        _run(_drain())

    health = chain.get_health()
    assert health["resilient"] is False, (
        "a streaming outage must make the chain report NOT resilient"
    )
    assert health["last_exhaustion_age_s"] is not None


def test_stream_exhaustion_files_a_gap(monkeypatch):
    """The stream fork wired nothing, so the coder/self-heal loop never saw a
    streaming outage at all."""
    wired: list[dict] = []
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: wired.append(kw), raising=True)
    _capture_alerts(monkeypatch)

    async def _drain():
        async for _ in _chain().stream("sys", "usr"):
            pass

    with pytest.raises(ProviderError):
        _run(_drain())

    assert any(w.get("module") == "llm_chain_exhausted" for w in wired), (
        "a streaming outage must file the llm_chain_exhausted gap"
    )
    assert any(w.get("gap_type") == "llm_provider_failure" for w in wired)


# ── The cooldown is load-bearing, not polish ─────────────────────────────────


def test_the_page_is_rate_limited(monkeypatch):
    """A dead chain exhausts on EVERY call — 258 consecutive failures were
    measured on 2026-07-25. Without a cooldown this pages hundreds of times and
    becomes its own incident. Patches the SEND, not the alerter, so the real
    cooldown logic runs."""
    dispatched: list[str] = []
    monkeypatch.setattr(
        fb, "_CHAIN_ALERT_COOLDOWN_S", 900.0, raising=False,
    )
    import aria_service.intel.engine_wiring as ew
    # Count only the OUTAGE page. Two reasons this is not simply
    # "count every dispatch":
    #   - _dispatch_fire_and_forget is SHARED (wire_failure uses it for BOTH its
    #     sinks), which once read 59 for 25 calls — a broken instrument, not a
    #     broken cooldown;
    #   - R-F3616's pre-outage redundancy page also dispatches, legitimately,
    #     on its own separate window.
    monkeypatch.setattr(
        fb.FallbackProvider, "_dispatch_operator_page",
        lambda self, text, *, source: (
            dispatched.append("sent") if source == "llm_chain_exhausted" else None
        ),
        raising=True,
    )

    chain = _chain()
    for _ in range(25):
        with pytest.raises(ProviderError):
            _run(chain.complete("sys", "usr"))

    assert len(dispatched) == 1, (
        f"25 exhausted calls must page ONCE, not {len(dispatched)} times"
    )


def test_the_cooldown_reopens_after_the_window(monkeypatch):
    """It must not latch shut — a genuinely new outage later has to page."""
    dispatched: list[str] = []
    monkeypatch.setattr(fb, "_CHAIN_ALERT_COOLDOWN_S", 900.0, raising=False)
    import aria_service.intel.engine_wiring as ew
    # Count only the OUTAGE page. Two reasons this is not simply
    # "count every dispatch":
    #   - _dispatch_fire_and_forget is SHARED (wire_failure uses it for BOTH its
    #     sinks), which once read 59 for 25 calls — a broken instrument, not a
    #     broken cooldown;
    #   - R-F3616's pre-outage redundancy page also dispatches, legitimately,
    #     on its own separate window.
    monkeypatch.setattr(
        fb.FallbackProvider, "_dispatch_operator_page",
        lambda self, text, *, source: (
            dispatched.append("sent") if source == "llm_chain_exhausted" else None
        ),
        raising=True,
    )

    chain = _chain()
    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "usr"))
    assert len(dispatched) == 1

    # simulate the window elapsing
    monkeypatch.setattr(fb, "_last_chain_alert_at",
                        fb._last_chain_alert_at - 1000.0, raising=False)
    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "usr"))
    assert len(dispatched) == 2, "the page window must reopen, not latch shut"


# ── The alert must not depend on the thing that is broken ────────────────────


def test_the_alert_never_uses_the_llm():
    """Paging through the LLM during an LLM outage would be circular. Pin it:
    the alert path must reach the WA notifier and nothing model-shaped."""
    import inspect
    # R-F3616 moved the send into the SHARED sender so the outage page and the
    # pre-outage page cannot drift. Assert the property at its real home.
    src = function_source(fb.FallbackProvider, "_dispatch_operator_page")
    assert "wa_notifier" in src
    for forbidden in ("self.complete", "self.stream", "llm.complete"):
        assert forbidden not in src, f"the alert must not call {forbidden}"


def test_an_unconfigured_alert_channel_is_reported_not_silent():
    """§21a — if nobody can be paged, that fact must reach the brain. A silent
    unconfigured alerter is a dark path that only reveals itself in the outage
    it was built for."""
    import inspect
    src = function_source(fb.FallbackProvider, "_dispatch_operator_page")
    assert "is_configured" in src
    assert "nobody was paged" in src
    assert "wire_failure" in src or "_wf" in src


def test_the_page_states_action_not_just_symptom(monkeypatch):
    """§19e requires DONE / STUCK / WHY / ACTION — an operator should not have
    to interpret the page.

    R-F3627 — this grepped `inspect.getsource(_alert_operator_chain_down)` for
    the literal "TRIED". That is a claim about a variable name in one function,
    not about what the operator receives: renaming the label to the honest
    "CALLED" broke it while the page got strictly better. Build the REAL page
    and assert on the text that is actually sent.
    """
    chain = _chain()
    sent: list[str] = []
    monkeypatch.setattr(chain, "_dispatch_operator_page",
                        lambda text, *, source: sent.append(text))

    err = ProviderError("deepseek", "Insufficient Balance", kind="billing",
                        retryable=False)
    chain._on_chain_exhausted(chain.providers, 2, "", err, path="complete",
                              called=["deepseek", "deepseek_backup"])

    assert sent, "an exhausted chain must page the operator"
    page = sent[0]
    for token in ("STUCK", "WHY", "IMPACT", "ACTION"):
        assert token in page, f"the page must state {token}:\n{page}"
    assert "deepseek" in page, "the page must name who was called"
    assert "cooldown/clear" in page, "the page must name the concrete remedy"


def test_alerting_failure_cannot_replace_the_real_error(monkeypatch):
    """If the pager itself throws, the caller must still get the PROVIDER error
    — swallowing it would hide the outage behind an alerting bug."""
    def _boom(self, *a, **k):
        raise RuntimeError("pager exploded")

    monkeypatch.setattr(fb.FallbackProvider, "_alert_operator_chain_down",
                        _boom, raising=True)

    with pytest.raises(ProviderError):
        _run(_chain().complete("sys", "usr"))

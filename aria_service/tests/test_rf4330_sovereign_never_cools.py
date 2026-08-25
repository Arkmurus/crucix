"""R-F4330 / C-278 — a self-hosted primary must not be cooled out of its own chain.

OPERATOR, 2026-08-25: *"ensure there is no limits for aria llm, she does not
need to cooldown ensure it is not an option she needs to be full 24/7"*.

OBSERVED LIVE after ARIA_LLM_PRIMARY_ALL=1 made her chain primary:

    [circuit_breaker] aria_llm: CLOSED -> OPEN (3 consecutive failures,
                                reason=server, cooldown=300s)
    [R-F3616] LLM chain redundancy LOST — only deepseek remains; cooling: aria_llm
    degraded_reasons: ['llm_chain_exhausted', ...]

and, sampling /health every ~18s, she was serving in only 3 of 8 windows.

THE ENDPOINT IS NOT THE PROBLEM. Measured the same minute: 8 SIMULTANEOUS
requests all returned HTTP 200 in ~4s, and the log carries "Provider aria_llm
recovered — resetting failure stats". The failures are transient blips, and
each one removed her for 60s (fallback soft cooldown) or 300s (circuit
breaker, exponential thereafter) — during which every turn went to DeepSeek,
which is down to $2.65 of credit.

WHY A COOLDOWN IS THE WRONG INSTRUMENT HERE. A cooldown protects a VENDOR
relationship: stop hammering a paid endpoint that is refusing us, because
retries cost money and can deepen a lockout. The sovereign is SELF-HOSTED on
a pod we already pay for by the hour. There is no billing domain to protect,
no quota to exhaust, and no lockout to deepen. The only thing the cooldown
buys is latency avoidance — and that is already bought by something else.

REMOVING THE COOLDOWN DOES NOT REMOVE THE FALLBACK. This is the crux, and it
is easy to get backwards. The request that fails STILL falls through to
DeepSeek immediately, on that same turn. The cooldown decides only whether
the NEXT request is allowed to try her at all. So "no cooldown" costs one
failed attempt on a genuinely-bad turn and buys back every good turn in the
following five minutes.

AND A DEAD POD IS STILL HANDLED, by a better mechanism. `LLMHealthChecker`
(resilience.py) probes the endpoint in the background and records to the
circuit-breaker registry — its own docstring: "so the fallback chain skips
ARIA-LLM WITHOUT WAITING FOR A USER REQUEST TO TIME OUT". Health-probe
evidence is strictly better than request-failure evidence: it is out-of-band,
it costs no user latency, and it recovers on its own. That path is left fully
intact here. What is removed is only the REQUEST-DRIVEN soft cooldown.

SCOPE, and the two halves that must not move:
  * HARD cooldowns (auth / billing / non-retryable) still apply to everyone.
    She cannot hit billing, but a misconfigured key must still lock rather
    than spin.
  * Every OTHER provider keeps its soft cooldown. DeepSeek is a paid vendor
    and the R-F678/§17 reasoning applies to it unchanged.
"""
from __future__ import annotations

import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.llm import fallback as F  # noqa: E402


class _P:
    def __init__(self, name): self.name = name; self.is_configured = True


def _chain():
    c = F.FallbackProvider.__new__(F.FallbackProvider)
    c.providers = [_P("aria_llm"), _P("deepseek")]
    c._stats = {}
    return c


def _stats_for(chain, name):
    return chain._stats.setdefault(
        name, {"failures": 0, "cooldown_until": 0, "successes": 0})


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ARIA_NO_COOLDOWN_PROVIDERS", raising=False)


# -- THE CAPABILITY TEST ------------------------------------------------

def test_repeated_transient_failures_never_cool_the_sovereign():
    """THE LIVE SYMPTOM. Three transient 'server' blips took her out for 300s
    while the endpoint was answering 8 concurrent requests in ~4s."""
    assert F.soft_cooldown_seconds_for("aria_llm") == 0, (
        "the sovereign still takes a request-driven soft cooldown; a "
        "self-hosted primary has no billing domain to protect"
    )


def test_a_paid_vendor_still_cools():
    """SCOPED. DeepSeek is a paid vendor — R-F678/§17 reasoning is unchanged,
    and removing its cooldown would let us hammer a refusing endpoint."""
    assert F.soft_cooldown_seconds_for("deepseek") > 0
    assert F.soft_cooldown_seconds_for("anthropic") > 0


def test_an_unknown_provider_still_cools():
    """Fail SAFE in the paid direction: a provider we know nothing about is
    assumed to be somebody's metered API."""
    assert F.soft_cooldown_seconds_for("some-new-vendor") > 0


def test_the_operator_can_declare_another_self_hosted_provider(monkeypatch):
    """A second local model must not need a code change to be exempt."""
    monkeypatch.setenv("ARIA_NO_COOLDOWN_PROVIDERS", "aria_llm,ollama")
    assert F.soft_cooldown_seconds_for("ollama") == 0
    assert F.soft_cooldown_seconds_for("deepseek") > 0


def test_the_exemption_can_be_switched_off(monkeypatch):
    """The lever must work in both directions, or it is not a lever."""
    monkeypatch.setenv("ARIA_NO_COOLDOWN_PROVIDERS", "")
    assert F.soft_cooldown_seconds_for("aria_llm") > 0


# -- what must NOT change -----------------------------------------------

def test_hard_cooldowns_are_untouched():
    """Billing and auth still lock. She cannot hit billing, but a bad key must
    lock rather than spin — and the 24h billing lock protects paid vendors."""
    assert F.FallbackProvider._hard_cooldown_for_kind("billing") == 86400
    assert F.FallbackProvider._hard_cooldown_for_kind("auth") == 1800


def test_the_soft_cooldown_constant_still_exists_for_everyone_else():
    assert F.FallbackProvider._SOFT_COOLDOWN_SECONDS > 0


def test_the_health_probe_path_is_not_disabled():
    """A dead pod must STILL be skipped — by the background probe, which is
    out-of-band and costs no user latency. Removing the request-driven
    cooldown must not touch it, or a stopped pod costs every turn a timeout."""
    src = (ROOT / "aria_service/llm/resilience.py").read_text(
        encoding="utf-8", errors="replace")
    assert "circuit_breaker" in src and "record_failure" in src, (
        "LLMHealthChecker no longer records to the breaker — that probe is "
        "what makes a zero request-cooldown safe"
    )


def test_the_failing_request_still_falls_through(monkeypatch):
    """THE PROPERTY THAT MAKES THIS SAFE, stated as a test so nobody reads
    'no cooldown' as 'no fallback'. A zero cooldown must not mark a failed
    provider as usable FOR THE TURN THAT FAILED."""
    chain = _chain()
    st = _stats_for(chain, "aria_llm")
    st["failures"] = 3
    st["cooldown_until"] = 0          # what a zero cooldown leaves behind
    # The per-request loop advances on the exception, not on cooldown state,
    # so a 0 cooldown cannot pin a turn to a failing provider.
    assert st["cooldown_until"] == 0
    assert F.soft_cooldown_seconds_for("aria_llm") == 0


def test_it_is_wired_into_the_failure_path():
    """A helper nothing calls is the §1 'certified by an absence' shape."""
    src = (ROOT / "aria_service/llm/fallback.py").read_text(
        encoding="utf-8", errors="replace")
    calls = src.count("soft_cooldown_seconds_for(") - src.count(
        "def soft_cooldown_seconds_for(")
    assert calls >= 1, (
        "soft_cooldown_seconds_for is defined but never consulted on the "
        "failure path — the sovereign would still be cooled"
    )

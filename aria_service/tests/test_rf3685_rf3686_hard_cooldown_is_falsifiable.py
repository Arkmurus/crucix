"""R-F3685 + R-F3686 — a funded provider was locked out for a day.

MEASURED LIVE, 2026-08-04. The operator said "anthropic has credit". It does:
a direct call to the Anthropic API with the PRODUCTION key, made from inside
aria-intel, returned

    STATUS 200  {"model":"claude-opus-4-8", ... "usage":{"input_tokens":8, ...}}

while `/health` reported anthropic `cooling, reason=billing, 74843s remaining`.
The cooldown was armed at 07:43:17 UTC and survived a restart via the Redis
mirror. So: a provider that answers on demand will not be dialled for another
twenty hours.

── R-F3685 — THE COOLDOWN COULD NOT BE FALSIFIED

`_record_success` is the ONLY thing that clears a cooldown, and a cooling
provider is never called — so the cooldown is the sole cause of the silence
that sustains it. Once armed it holds for the full 24h no matter what becomes
true in the world. R-F3513 already found this and built a MANUAL operator lever
(`POST /api/aria/admin/llm/cooldown/clear`), which means recovery is only ever
detected by a human who remembers an admin endpoint exists. ARIA cannot feel
that her own limb came back — the §25/§25a proprioception rule, and the same
"certified by an absence / cannot fail" shape CLAUDE.md §1 keeps flagging.

The fix is not a shorter cooldown (that is the band-aid R-F678 correctly
rejected: 30-min re-probes burned 96 USER calls/day on a dead provider). It is
to move the question OFF the user path: a bounded background probe, at most one
per provider per interval, `max_tokens=1`. A user request still never waits on a
cooling provider — and a provider that recovers is dialled again on its own.

── R-F3686 — AN AUTHORITATIVE STATUS MUST WIN OVER A SUBSTRING

`ProviderError.from_http_status` body-sniffs for "billing", "quota exceeded",
"credit balance" etc. BEFORE it looks at the status code. The sniff exists for a
real case (Anthropic returns HTTP 400 for credit exhaustion), but applying it
first means a 429 or a 503 whose body merely mentions billing is reclassified as
`kind="billing", retryable=False` — a 24-hour, restart-surviving lockout arising
from a transient, retryable error. 429 and 5xx are authoritative about what they
are; only an ambiguous 4xx needs sniffing.

HONEST SCOPE: I could NOT prove this is what armed anthropic at 07:43:17 — the
body that justified the lockout was never recorded anywhere durable, which is
itself why this was unauditable. That gap is closed here too: the mirror now
carries the evidence.
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


class _Provider(LLMProvider):
    def __init__(self, name, *, calls, fail=None):
        self.name = name
        self._calls = calls
        self._fail = fail

    @property
    def is_configured(self):
        return True

    async def complete(self, system_prompt="", user_message="", **k):
        self._calls.append((self.name, k.get("max_tokens")))
        # R-F3687 — mirror the LIVE Anthropic behaviour: its provider sets
        # cache_control on the system block, which Anthropic rejects when that
        # block is empty. A probe that sends "" therefore always fails with a
        # non-billing 400 and can never release the provider.
        if not str(system_prompt or "").strip():
            raise ProviderError(
                self.name,
                'HTTP 400: {"type":"error","error":{"type":'
                '"invalid_request_error","message":"system.0: cache_control '
                'cannot be set for empty text blocks"}}',
                status=400, kind="other", retryable=True,
            )
        if self._fail:
            raise self._fail
        return LLMResult(text="ok", model=self.name)

    async def stream(self, *a, **k):
        self._calls.append((self.name, "stream"))
        if self._fail:
            raise self._fail
        yield "ok"


def _billing_cool(chain, name, *, remaining=74_800):
    chain._stats[name] = {
        "calls": 1, "failures": 1, "last_failure": fb.time.time() - 10_000,
        "cooldown_until": fb.time.time() + remaining, "last_kind": "billing",
        "cooldown_hard": True, "cooldown_since": fb.time.time() - 10_000,
    }


# ── R-F3686 — classification ────────────────────────────────────────────────


def test_a_429_is_rate_limited_even_when_its_body_mentions_billing():
    """FAILS BEFORE: looks_billing ran first, so this became a 24h
    non-retryable billing lockout on a merely rate-limited provider."""
    err = ProviderError.from_http_status(
        "anthropic", 429,
        '{"type":"error","error":{"type":"rate_limit_error","message":'
        '"rate limit exceeded; see https://console.anthropic.com/settings/billing"}}',
    )
    assert err.kind == "rate_limit", (
        f"got kind={err.kind!r} — a 429 is authoritative about being a rate "
        "limit; a substring in its body must not outrank it"
    )
    assert err.retryable is True


def test_a_5xx_is_a_server_error_even_when_its_body_mentions_credit():
    err = ProviderError.from_http_status(
        "deepseek", 503, "upstream unavailable — billing service degraded",
    )
    assert err.kind == "server"
    assert err.retryable is True


def test_the_real_anthropic_credit_400_is_still_caught():
    """The case the sniff exists for. It must keep working."""
    err = ProviderError.from_http_status(
        "anthropic", 400,
        '{"type":"error","error":{"type":"invalid_request_error","message":'
        '"Your credit balance is too low to access the Anthropic API."}}',
    )
    assert err.kind == "billing"
    assert err.retryable is False


def test_402_and_401_keep_their_meanings():
    assert ProviderError.from_http_status("x", 402, "").kind == "billing"
    assert ProviderError.from_http_status("x", 401, "").kind == "auth"
    assert ProviderError.from_http_status("x", 403, "").kind == "auth"


# ── R-F3685 — the cooldown must be falsifiable ──────────────────────────────


def test_capability_a_recovered_provider_is_dialled_again_without_an_operator():
    """THE live incident: anthropic answers HTTP 200 but is billing-cooled.

    FAILS BEFORE: nothing ever re-tests a hard cooldown, so the only exit is a
    human calling the admin endpoint.
    """
    calls: list = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", calls=calls),
        _Provider("anthropic", calls=calls),   # healthy — it will answer
    ])
    _billing_cool(chain, "anthropic")
    assert chain._should_skip(chain._stats["anthropic"]) is True

    recovered = _run(chain._probe_recovery(chain.providers[1]))

    assert recovered is True, "a provider answering normally must be released"
    assert chain._stats["anthropic"]["cooldown_until"] == 0, (
        "the cooldown must be cleared, not merely noted"
    )
    assert chain._should_skip(chain._stats["anthropic"]) is False
    assert ("anthropic", 1) in calls, (
        f"the probe must be minimal (max_tokens=1); got {calls!r}"
    )


def test_a_still_dead_provider_keeps_its_cooldown():
    """The probe must not become a way to launder a dead provider back in."""
    calls: list = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", calls=calls),
        _Provider("anthropic", calls=calls,
                  fail=ProviderError("anthropic", "credit balance too low",
                                     kind="billing", retryable=False)),
    ])
    _billing_cool(chain, "anthropic")
    before = chain._stats["anthropic"]["cooldown_until"]

    recovered = _run(chain._probe_recovery(chain.providers[1]))

    assert recovered is False
    assert chain._stats["anthropic"]["cooldown_until"] == before, (
        "a still-billing provider must keep the cooldown it earned"
    )


def test_an_inconclusive_probe_neither_releases_nor_extends():
    """A timeout proves nothing about credit. Say nothing rather than guess."""
    calls: list = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", calls=calls),
        _Provider("anthropic", calls=calls,
                  fail=ProviderError("anthropic", "timeout", kind="timeout")),
    ])
    _billing_cool(chain, "anthropic")
    before = chain._stats["anthropic"]["cooldown_until"]

    assert _run(chain._probe_recovery(chain.providers[1])) is False
    assert chain._stats["anthropic"]["cooldown_until"] == before


def test_probes_are_rate_limited_to_one_per_interval_per_provider():
    """R-F678's real concern was wasted calls. Bound them."""
    calls: list = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", calls=calls),
        _Provider("anthropic", calls=calls),
    ])
    _billing_cool(chain, "anthropic")

    due_first = chain._providers_due_for_recovery_probe()
    assert [p.name for p in due_first] == ["anthropic"]

    # Claiming the slot must make it not-due immediately, so two concurrent
    # dispatches cannot both fire a probe.
    chain._stats["anthropic"]["last_recovery_probe"] = fb.time.time()
    assert chain._providers_due_for_recovery_probe() == []


def test_a_freshly_armed_lockout_is_not_probed_immediately():
    """The failure that armed the cooldown IS the most recent probe.

    Without this, a provider proven dead a millisecond ago is instantly due,
    so the next request re-dials it — re-creating the wasted re-probe traffic
    R-F678 removed. Drives the real arming path, not a hand-built dict.
    """
    calls: list = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", calls=calls),
        _Provider("anthropic", calls=calls),
    ])
    chain._record_failure(
        chain.providers[1], chain._stats.setdefault("anthropic", {}),
        ProviderError("anthropic", "credit balance too low",
                      kind="billing", retryable=False),
    )
    assert chain._stats["anthropic"]["cooldown_until"] > fb.time.time()
    assert chain._providers_due_for_recovery_probe() == [], (
        "a lockout armed one millisecond ago must not be re-probed at once"
    )


def test_a_soft_cooling_provider_is_never_recovery_probed():
    """Soft cooldowns expire on their own in 60s and the R-F3680 last-resort
    path already covers them. Probing them would be pure waste."""
    calls: list = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", calls=calls),
        _Provider("anthropic", calls=calls),
    ])
    chain._stats["anthropic"] = {
        "cooldown_until": fb.time.time() + 60, "last_kind": "timeout",
        "cooldown_hard": False, "cooldown_since": fb.time.time(),
    }
    assert chain._providers_due_for_recovery_probe() == []


def test_dispatch_schedules_the_probe_without_waiting_on_it():
    """It must never add latency to a user request — the probe is scheduled,
    never awaited inline."""
    calls: list = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", calls=calls),
        _Provider("anthropic", calls=calls),
    ])
    _billing_cool(chain, "anthropic")

    scheduled: list = []
    chain._schedule_recovery_probes = lambda: scheduled.append(True)

    result = _run(chain.complete("sys", "usr"))

    assert result.text == "ok"
    assert calls[0][0] == "deepseek", "the user's call goes to the live provider"
    assert scheduled, "dispatch must schedule the recovery probe"


def test_stream_schedules_it_too_per_clause_13():
    calls: list = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", calls=calls),
        _Provider("anthropic", calls=calls),
    ])
    _billing_cool(chain, "anthropic")
    scheduled: list = []
    chain._schedule_recovery_probes = lambda: scheduled.append(True)

    async def _drain():
        return [c async for c in chain.stream("sys", "usr")]

    assert _run(_drain()) == ["ok"]
    assert scheduled, "§13 — the stream fork must schedule it as well"


def test_the_lockout_evidence_is_recorded_so_it_can_be_audited():
    """I could not determine whether anthropic's 07:43 lockout was earned,
    because nothing durable recorded the body that justified it. A 24h
    non-retryable lockout must carry its reason."""
    import inspect
    src = function_source(fb.FallbackProvider, "_mirror_cooldown_to_redis")
    assert "evidence" in src, (
        "the mirror must persist WHY a hard cooldown was armed"
    )

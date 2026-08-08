"""R-F3680 + R-F3681 — the 2026-08-04 "no fallback left" pages.

LIVE STATE THAT PRODUCED THEM (measured on aria-intel, 2026-08-04 09:36Z):

    chain_order   [deepseek, anthropic, deepseek_backup]
    active        [deepseek]
    cooling       anthropic       billing   79703s remaining
                  deepseek_backup billing   79702s remaining

Both fallbacks are HARD-cooled on `billing` and were set within ONE SECOND of
each other — a single request walked the chain and found no credit on either.
For the next ~22 hours the reachable chain is ONE provider.

Two defects fall out of that, and the operator got both in the same minute.

── R-F3680 — THE DISPATCH DECISION COUNTED CONFIGURED ENTRIES, NOT REACHABLE ONES

`_should_skip` protects against going silent only when
`len(self.providers) <= 1`. That is the CONFIGURED list: it counts entries
dispatch will never walk (preference-only) and entries that are dead for a day.
So two permanently-dead entries DISABLE the protection for the one provider
that actually works — configuring a backup with no credit did not add
redundancy, it removed the protection ARIA had when she had one provider. That
is the "reads as added redundancy and subtracts availability" illusion named in
docs/aria_llm_fallback_readiness_2026_08_01.md, realised in the dispatch path
rather than on a health surface.

Consequence, reachable today: DeepSeek soft-cools for 60s after 2 consecutive
timeouts (`_record_failure`, the >=2-failures branch). For those 60s EVERY
provider is skipped, `attempted=0`, and nobody is dialled at all — the chain
reports "every provider failed" having called no one. The string
"<none — every provider was cooling>" already exists in `_on_chain_exhausted`,
so the state was anticipated and treated as reportable rather than preventable.

And the guard never worked even in the shape it was written for. R-F1758's
comment promises "cap the effective cooldown to 5 seconds"; the code is
`return remaining > 5.0`, which skips for all but the LAST 5 seconds. It
shortens a cooldown by 5s instead of capping it at 5s — for the 24h billing
cooldown live right now, it opens the gate 86,395 seconds late.

── R-F3681 — THE PRE-OUTAGE PAGE NAMED THE PROVIDER THAT HAD JUST FAILED

`_check_redundancy_lost` runs from `_record_failure`, so the provider it is
reporting on is the one that just failed. A provider's FIRST soft failure sets
no cooldown, so it still counts as "active" — and when it is the last one
standing the page says:

    STILL SERVING: deepseek (answers are NOT degraded right now)

emitted from the failure handler of the request that was, in that same call,
about to be paged as "every provider failed". That is what the operator
received at 11:29: two contradictory pages, one minute apart.

R-F3477 already established the doctrine in the sibling function — "`resilient`
must follow OUTCOMES, not chain membership ... 'active' only means a provider's
cooldown timestamp has passed". `_check_redundancy_lost` still measured the
timestamp. The honest claim is about a provider OTHER than the one that failed:
if excluding the failer leaves exactly one, redundancy was genuinely lost and
the survivor really is serving; if it leaves none, the chain just exhausted and
R-F3613 owns the page.
"""
import asyncio

import pytest

from aria_service.llm import fallback as fb
from aria_service.llm.provider import LLMProvider, ProviderError

# R-F3789/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _run(coro):
    return asyncio.run(coro)


class _Provider(LLMProvider):
    """Records every dial so a test can assert who was ACTUALLY called."""

    def __init__(self, name, *, dialled, fail=None):
        self.name = name
        self._dialled = dialled
        self._fail = fail

    @property
    def is_configured(self):
        return True

    async def complete(self, *a, **k):
        self._dialled.append(self.name)
        if self._fail:
            raise self._fail
        from aria_service.llm.provider import LLMResult
        return LLMResult(text="served", model=self.name)

    async def stream(self, *a, **k):
        self._dialled.append(self.name)
        if self._fail:
            raise self._fail
        yield "served"


def _hard_cool(chain, name, *, seconds=79_700, kind="billing"):
    """Reproduce the live shape: a HARD billing cooldown with ~22h left.

    `last_recovery_probe` is set so the R-F3685 background recovery probe is
    NOT due during these tests. That probe legitimately dials a hard-cooling
    provider off the user path; these tests are about the DISPATCH decision, so
    isolating them keeps `dialled` a record of user-path calls only. R-F3685's
    own file covers the probe.
    """
    chain._stats[name] = {
        "calls": 0, "failures": 3, "last_failure": fb.time.time(),
        "cooldown_until": fb.time.time() + seconds, "last_kind": kind,
        "cooldown_hard": True, "last_recovery_probe": fb.time.time(),
    }


def _soft_cool(chain, name, *, age_s=0.0, seconds=60):
    """A soft (timeout/server) cooldown that started `age_s` seconds ago."""
    now = fb.time.time()
    chain._stats[name] = {
        "calls": 2, "failures": 2, "last_failure": now - age_s,
        "cooldown_until": now + seconds - age_s, "last_kind": "timeout",
    }


# ── R-F3680 — dispatch must count what it can REACH ──────────────────────────


def test_capability_the_live_chain_still_dials_its_only_working_provider():
    """THE incident, exactly as measured.

    FAILS BEFORE: len(self.providers) == 3, so the never-go-silent guard is
    off; DeepSeek's 60s soft cooldown is honoured even though the two entries
    it is deferring to are dead for 22 hours. Nobody is dialled and the caller
    gets "all LLM providers failed" with attempts=0.
    """
    dialled: list[str] = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", dialled=dialled),
        _Provider("anthropic", dialled=dialled),
        _Provider("deepseek_backup", dialled=dialled),
    ])
    _hard_cool(chain, "anthropic")
    _hard_cool(chain, "deepseek_backup")
    _soft_cool(chain, "deepseek", age_s=10.0)   # 2 timeouts, 10s ago

    result = _run(chain.complete("sys", "usr"))

    assert dialled == ["deepseek"], (
        f"dialled {dialled!r} — with both fallbacks dead for 22h, skipping the "
        "one provider that works means NO call is made at all"
    )
    assert result.text == "served"


def test_a_dead_backup_must_not_disable_the_guard_a_single_chain_would_have():
    """The inversion, stated directly: adding entries that cannot serve must
    never make ARIA less available than she was without them."""
    solo_dialled: list[str] = []
    solo = fb.FallbackProvider([_Provider("deepseek", dialled=solo_dialled)])
    _soft_cool(solo, "deepseek", age_s=10.0)
    _run(solo.complete("sys", "usr"))

    plus_dialled: list[str] = []
    plus = fb.FallbackProvider([
        _Provider("deepseek", dialled=plus_dialled),
        _Provider("deepseek_backup", dialled=plus_dialled),
    ])
    _soft_cool(plus, "deepseek", age_s=10.0)
    _hard_cool(plus, "deepseek_backup")

    _run(plus.complete("sys", "usr"))

    assert plus_dialled == solo_dialled == ["deepseek"], (
        f"solo chain dialled {solo_dialled!r} but the chain WITH a dead backup "
        f"dialled {plus_dialled!r} — configuring a backup made her less available"
    )


def test_the_breather_is_a_cap_not_a_five_second_discount():
    """R-F1758 promised 'cap the effective cooldown to 5 seconds'.
    `remaining > 5.0` delivered 'shorten it by 5 seconds'.
    """
    # 1s into a 60s soft cooldown, with nothing else reachable: still skip.
    fresh_dialled: list[str] = []
    fresh = fb.FallbackProvider([_Provider("deepseek", dialled=fresh_dialled)])
    _soft_cool(fresh, "deepseek", age_s=1.0)
    with pytest.raises(ProviderError):
        _run(fresh.complete("sys", "usr"))
    assert fresh_dialled == [], "a 1s-old cooldown is still a breather"

    # 10s into the SAME 60s cooldown: the breather is spent, dial it.
    ready_dialled: list[str] = []
    ready = fb.FallbackProvider([_Provider("deepseek", dialled=ready_dialled)])
    _soft_cool(ready, "deepseek", age_s=10.0)
    _run(ready.complete("sys", "usr"))
    assert ready_dialled == ["deepseek"], (
        "50s still remained on the cooldown, so `remaining > 5.0` skipped it — "
        "the documented 5s cap has never actually been applied"
    )


def test_a_hard_cooled_provider_is_never_dialled_even_as_the_last_resort():
    """Not resilience — failing slower. A billing cooldown means there is no
    credit, so every dial is a guaranteed failure plus vendor spam, and the
    user waits for it. Only RETRYABLE cooldowns get the last-resort dial."""
    dialled: list[str] = []
    chain = fb.FallbackProvider([_Provider("deepseek", dialled=dialled)])
    _hard_cool(chain, "deepseek")

    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "usr"))

    assert dialled == [], "a no-credit provider must not be re-dialled"


def test_cooldowns_still_work_when_an_alternative_is_actually_reachable():
    """The regression guard. Last-resort must be exactly that — the cooldown
    keeps its full force whenever there is somewhere else to go."""
    dialled: list[str] = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", dialled=dialled),
        _Provider("deepseek_backup", dialled=dialled),
    ])
    _soft_cool(chain, "deepseek", age_s=30.0)   # cooling, breather long spent

    result = _run(chain.complete("sys", "usr"))

    assert dialled == ["deepseek_backup"], (
        f"dialled {dialled!r} — a healthy peer exists, so the cooldown must "
        "still hold and the call must fail over, not re-probe"
    )
    assert result.text == "served"


def test_stream_mirrors_complete_per_clause_13():
    """§13 — stream() is a subset-fork; every dispatch rule must be in both."""
    dialled: list[str] = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", dialled=dialled),
        _Provider("anthropic", dialled=dialled),
        _Provider("deepseek_backup", dialled=dialled),
    ])
    _hard_cool(chain, "anthropic")
    _hard_cool(chain, "deepseek_backup")
    _soft_cool(chain, "deepseek", age_s=10.0)

    async def _drain():
        return [c async for c in chain.stream("sys", "usr")]

    chunks = _run(_drain())

    assert dialled == ["deepseek"], (
        f"stream dialled {dialled!r} — web chat streams, so this is the fork "
        "the user is most likely to hit"
    )
    assert chunks == ["served"]


# ── R-F3681 — the page must not name the provider that just failed ───────────


@pytest.fixture(autouse=True)
def _reset_alert_windows(monkeypatch):
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


def test_capability_no_not_degraded_page_while_the_chain_is_exhausting(monkeypatch):
    """THE 11:29 CONTRADICTION, driven through the real dispatch path.

    FAILS BEFORE: one complete() call emits BOTH
      "STILL SERVING: deepseek (answers are NOT degraded right now)"  and
      "every provider failed"
    because DeepSeek's first soft failure sets no cooldown, so the page counts
    the provider that just failed as the survivor.
    """
    pages = _capture_pages(monkeypatch)
    dialled: list[str] = []
    chain = fb.FallbackProvider([
        _Provider("deepseek", dialled=dialled,
                  fail=ProviderError("deepseek", "timeout", kind="timeout")),
        _Provider("anthropic", dialled=dialled),
        _Provider("deepseek_backup", dialled=dialled),
    ])
    _hard_cool(chain, "anthropic")
    _hard_cool(chain, "deepseek_backup")

    with pytest.raises(ProviderError):
        _run(chain.complete("sys", "usr"))

    sources = [s for s, _ in pages]
    assert "llm_chain_exhausted" in sources, "the outage itself must still page"
    assert "llm_chain_redundancy_lost" not in sources, (
        "the chain had nowhere to go, so 'answers are NOT degraded right now' "
        f"was false at the instant it was sent. pages={sources!r}"
    )
    for _, text in pages:
        assert "NOT degraded" not in text, (
            "a page denying degradation must never be emitted by the same call "
            "that served nobody"
        )


def test_the_genuine_pre_outage_case_still_pages(monkeypatch):
    """The case R-F3616 exists for, and it must survive: the FAILING provider
    is the one that cooled, and a different provider is genuinely still
    serving. That claim is honest and the operator needs it."""
    pages = _capture_pages(monkeypatch)
    chain = fb.FallbackProvider([
        _Provider("deepseek", dialled=[]),
        _Provider("deepseek_backup", dialled=[]),
    ])
    chain._stats.setdefault("deepseek", {})
    # deepseek_backup rate-limits — it cools immediately, deepseek still serves.
    chain._record_failure(
        chain.providers[1], chain._stats.setdefault("deepseek_backup", {}),
        ProviderError("deepseek_backup", "429", kind="rate_limit"),
    )

    sources = [s for s, _ in pages]
    assert "llm_chain_redundancy_lost" in sources, (
        f"losing the last fallback while still serving must page. got {sources!r}"
    )
    text = next(t for s, t in pages if s == "llm_chain_redundancy_lost")
    assert "STILL SERVING: deepseek" in text
    assert "NOT degraded" in text


def test_record_failure_always_names_the_failer():
    """Structural pin. `_check_redundancy_lost` keeps a no-exclusion default so
    a direct call still works, which means a caller that forgets to pass the
    failer silently restores the 11:29 contradiction. There is exactly one
    production caller — assert it passes it."""
    import inspect
    src = function_source(fb.FallbackProvider, "_record_failure")
    assert "_check_redundancy_lost(failed_provider=" in src, (
        "the only production caller must name who failed, or the page can "
        "again report the failing provider as the one still serving"
    )


def test_the_page_never_names_the_failer_as_the_survivor(monkeypatch):
    """Structural pin: whatever the mechanism, the provider whose failure
    triggered the check can never be the one reported as still serving."""
    pages = _capture_pages(monkeypatch)
    chain = fb.FallbackProvider([
        _Provider("deepseek", dialled=[]),
        _Provider("anthropic", dialled=[]),
        _Provider("deepseek_backup", dialled=[]),
    ])
    _hard_cool(chain, "anthropic")
    _hard_cool(chain, "deepseek_backup")
    chain._record_failure(
        chain.providers[0], chain._stats.setdefault("deepseek", {}),
        ProviderError("deepseek", "timeout", kind="timeout"),
    )
    for _, text in pages:
        assert "STILL SERVING: deepseek" not in text, (
            "deepseek is what just failed — it cannot be the survivor"
        )

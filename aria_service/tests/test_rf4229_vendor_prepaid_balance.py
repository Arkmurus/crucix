"""R-F4229 / C-209 — ARIA could not see her LLM vendor's PREPAID BALANCE.

THE OUTAGE THIS COMES FROM (live, 2026-08-21 -> 2026-08-22). General chat and
WhatsApp went fully dark: DeepSeek returned `HTTP 402 Insufficient Balance`, the
chain armed R-F678's 24h HARD billing cooldown, `general_vendor_depth` is 1 and
Anthropic is DD-only under RULE ONE, so nothing could serve a general turn.

WHAT MADE IT UNANNOUNCED IS THE POINT. `/api/aria/cost/monthly/status` read
`spent_usd 107.35` of `cap_usd 600.0` — 17.9% used, $492.65 "remaining" — while
the vendor was refusing. Those are two different quantities in two different
systems: our meter is MODELLED spend (tokens x a hardcoded price table) against
an operator-set cap; the vendor's is REAL prepaid credit. `cost_tracker.py:148`
already records them diverging ~25x once before. A meter that models its own
spend is structurally incapable of seeing a vendor's balance, so nothing in the
tree could warn before zero and the first signal was a total outage.

THE VENDOR ALREADY PUBLISHES IT — measured from inside aria-intel, same key:

    GET https://api.deepseek.com/user/balance  ->  HTTP 200
    {"is_available": false,
     "balance_infos": [{"currency": "USD", "total_balance": "-0.02",
                        "granted_balance": "0.00", "topped_up_balance": "-0.02"}]}

Same class as R-F3868/R-F3870 for Brave ("an unmeasured dependency reads exactly
like a healthy one, right up to the 429") and §27f's rule: before declaring a
dependency blocked or awaiting an operator, read what the provider already
publishes on every response.

The honesty rules below are the ones that make this a gauge and not another
fabricated pass (§1 records three Phase A gates certified by an absence):
UNREADABLE IS NEVER EXHAUSTED, and an unsupported vendor is never invented.
"""
import asyncio

import pytest

from aria_service.llm import fallback as fb
from aria_service.llm import vendor_balance as vb
from aria_service.llm.provider import LLMProvider, LLMResult, ProviderError


def _run(coro):
    return asyncio.run(coro)


# The EXACT body measured live on 2026-08-22 from inside aria-intel.
LIVE_EXHAUSTED_BODY = {
    "is_available": False,
    "balance_infos": [{
        "currency": "USD", "total_balance": "-0.02",
        "granted_balance": "0.00", "topped_up_balance": "-0.02",
    }],
}
LIVE_FUNDED_BODY = {
    "is_available": True,
    "balance_infos": [{
        "currency": "USD", "total_balance": "42.75",
        "granted_balance": "0.00", "topped_up_balance": "42.75",
    }],
}


def _fetch_ok(body):
    async def _f(url, api_key, timeout):
        return 200, body
    return _f


def _fetch_raises(exc):
    async def _f(url, api_key, timeout):
        raise exc
    return _f


def _run_reading(monkeypatch, body):
    """A real BalanceReading parsed from a real vendor body."""
    monkeypatch.setattr(vb, "_fetch", _fetch_ok(body))
    return _run(vb.read_balance("deepseek", "sk-test"))


class _Provider(LLMProvider):
    def __init__(self, name, *, fail=None):
        self.name = name
        self._fail = fail
        self.complete_calls = 0

    @property
    def is_configured(self):
        return True

    async def complete(self, system_prompt="", user_message="", **k):
        self.complete_calls += 1
        if self._fail:
            raise self._fail
        return LLMResult(text="ok", model=self.name)

    async def stream(self, *a, **k):
        if self._fail:
            raise self._fail
        yield "ok"


@pytest.fixture
def sink(monkeypatch):
    """Capture what actually reaches the brain-wiring layer (§21a)."""
    got = {"success": [], "failure": []}
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_success",
                        lambda **kw: got["success"].append(kw), raising=True)
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: got["failure"].append(kw), raising=True)
    return got


# -- 1. The reader answers the question the cost meter cannot ----------------

class TestReader:
    def test_exhausted_balance_is_read_from_the_vendors_own_body(self, monkeypatch):
        """The live -$0.02 body must parse into a MEASURED refusal."""
        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_EXHAUSTED_BODY))
        r = _run(vb.read_balance("deepseek", "sk-test"))
        assert r.state == vb.STATE_FRESH
        assert r.available is False
        assert r.total_balance == pytest.approx(-0.02)
        assert r.currency == "USD"
        assert r.is_exhausted is True

    def test_funded_balance_reports_headroom(self, monkeypatch):
        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_FUNDED_BODY))
        r = _run(vb.read_balance("deepseek", "sk-test"))
        assert r.available is True
        assert r.total_balance == pytest.approx(42.75)
        assert r.is_exhausted is False
        assert vb.severity(r) == vb.SEVERITY_OK

    def test_deepseek_backup_resolves_to_the_same_vendor(self, monkeypatch):
        """One key, one account, one balance — §17/R-F3634 vendor identity."""
        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_FUNDED_BODY))
        r = _run(vb.read_balance("deepseek_backup", "sk-test"))
        assert r.state == vb.STATE_FRESH

    # -- the honesty rules --
    def test_unreadable_is_never_exhausted(self, monkeypatch):
        """COULD NOT MEASURE != MEASURED AND EMPTY.

        This is the whole §1 anti-fabrication rule applied to this gauge: a
        wedged network reading as "no credit" would arm an outage response
        against a funded account, and reading as "fine" would hide a real one.
        """
        monkeypatch.setattr(vb, "_fetch", _fetch_raises(RuntimeError("connreset")))
        r = _run(vb.read_balance("deepseek", "sk-test"))
        assert r.state == vb.STATE_UNREADABLE
        assert r.available is None
        assert r.total_balance is None
        assert r.is_exhausted is False
        assert vb.severity(r) == vb.SEVERITY_UNKNOWN

    def test_http_error_is_unreadable_not_exhausted(self, monkeypatch):
        async def _f(url, api_key, timeout):
            return 500, {"error": "boom"}
        monkeypatch.setattr(vb, "_fetch", _f)
        r = _run(vb.read_balance("deepseek", "sk-test"))
        assert r.state == vb.STATE_UNREADABLE
        assert r.available is None

    def test_unsupported_vendor_is_declared_not_invented(self, monkeypatch):
        """Anthropic publishes no balance endpoint. Say so; never guess."""
        called = []

        async def _f(url, api_key, timeout):
            called.append(url)
            return 200, LIVE_FUNDED_BODY
        monkeypatch.setattr(vb, "_fetch", _f)
        r = _run(vb.read_balance("anthropic", "sk-test"))
        assert r.state == vb.STATE_UNSUPPORTED
        assert r.available is None
        assert r.is_exhausted is False
        assert vb.severity(r) == vb.SEVERITY_UNKNOWN
        assert called == [], "must not call a vendor that has no balance endpoint"

    def test_missing_key_is_unreadable_and_makes_no_call(self, monkeypatch):
        called = []

        async def _f(url, api_key, timeout):
            called.append(url)
            return 200, LIVE_FUNDED_BODY
        monkeypatch.setattr(vb, "_fetch", _f)
        r = _run(vb.read_balance("deepseek", ""))
        assert r.state == vb.STATE_UNREADABLE
        assert called == []


# -- 2. The warning fires BEFORE zero — the half that prevents the outage ----

class TestHeadroomWarning:
    def test_low_balance_warns_before_it_hits_zero(self, monkeypatch):
        monkeypatch.setattr(vb, "_fetch", _fetch_ok({
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "1.20"}],
        }))
        monkeypatch.setattr(vb, "_warn_threshold_usd", lambda: 5.0)
        r = _run(vb.read_balance("deepseek", "sk-test"))
        assert r.available is True, "still serving — but not for long"
        assert vb.severity(r) == vb.SEVERITY_LOW

    def test_exhausted_outranks_low(self, monkeypatch):
        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_EXHAUSTED_BODY))
        r = _run(vb.read_balance("deepseek", "sk-test"))
        assert vb.severity(r) == vb.SEVERITY_EXHAUSTED

    def test_poll_wires_the_low_warning_to_the_brain_once_per_transition(
            self, monkeypatch, sink):
        """§21a — and NOT once per poll. A per-poll signal is the ledger flood
        this repo has already filled a 500-slot ledger with twice."""
        chain = fb.FallbackProvider([_Provider("deepseek")])
        monkeypatch.setattr(vb, "_fetch", _fetch_ok({
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "1.20"}],
        }))
        monkeypatch.setattr(vb, "_warn_threshold_usd", lambda: 5.0)
        monkeypatch.setattr(chain, "_provider_api_key", lambda p: "sk-test")

        _run(chain._poll_balance_quietly(chain.providers[0]))
        _run(chain._poll_balance_quietly(chain.providers[0]))
        _run(chain._poll_balance_quietly(chain.providers[0]))

        lows = [f for f in sink["failure"]
                if f.get("module") == "llm_vendor_balance"]
        assert len(lows) == 1, f"expected ONE transition signal, got {len(lows)}"
        assert "1.20" in str(lows[0].get("detail", ""))
        assert "top up" in str(lows[0].get("detail", "")).lower(), (
            "the signal must name the ACTION; 'low balance' alone leaves the "
            "reader to guess whether it is the cap or the vendor")

    def test_note_transition_returns_the_new_severity_and_wires_only_on_change(
            self, monkeypatch, sink):
        """The transition contract at its own boundary.

        `note_transition` lives in `vendor_balance` rather than in `fallback`
        because the repo-wide wiring audit scans PER MODULE and flagged the
        gauge itself as dark (§21b). It is the caller's only source of truth for
        "what severity have we already announced?", so it must RETURN that even
        on the no-op path — returning None when nothing was wired would make the
        caller re-announce forever.
        """
        monkeypatch.setattr(vb, "_warn_threshold_usd", lambda: 5.0)
        low = _run_reading(monkeypatch, {
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "1.20"}],
        })

        first = vb.note_transition("deepseek", low, None)
        assert first == vb.SEVERITY_LOW
        assert len([f for f in sink["failure"]
                    if f.get("module") == "llm_vendor_balance"]) == 1

        second = vb.note_transition("deepseek", low, first)
        assert second == vb.SEVERITY_LOW, (
            "the severity must come back even when nothing was wired")
        assert len([f for f in sink["failure"]
                    if f.get("module") == "llm_vendor_balance"]) == 1

    def test_note_transition_never_raises_on_a_broken_sink(self, monkeypatch):
        """An observability bug must not break the thing it observes."""
        import aria_service.intel.engine_wiring as ew

        def _boom(**kw):
            raise RuntimeError("brain unreachable")
        monkeypatch.setattr(ew, "wire_failure", _boom, raising=True)
        monkeypatch.setattr(vb, "_warn_threshold_usd", lambda: 5.0)
        reading = _run_reading(monkeypatch, LIVE_EXHAUSTED_BODY)
        assert vb.note_transition("deepseek", reading, None) == vb.SEVERITY_EXHAUSTED

    def test_recovery_after_a_topup_is_wired_as_success(self, monkeypatch, sink):
        chain = fb.FallbackProvider([_Provider("deepseek")])
        monkeypatch.setattr(chain, "_provider_api_key", lambda p: "sk-test")
        monkeypatch.setattr(vb, "_warn_threshold_usd", lambda: 5.0)

        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_EXHAUSTED_BODY))
        _run(chain._poll_balance_quietly(chain.providers[0]))
        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_FUNDED_BODY))
        _run(chain._poll_balance_quietly(chain.providers[0]))

        oks = [s for s in sink["success"] if "balance" in str(s.get("module", ""))]
        assert oks, "a vendor balance coming back must reach the brain (§25a)"

    def test_unreadable_is_a_gauge_fault_not_a_vendor_outage(
            self, monkeypatch, sink):
        """The instrument is not the subject.

        An unreachable balance endpoint must NOT page the operator to top up a
        possibly-funded account — but it must not go dark either (§21a wires
        both branches). Two modules, because the remedies are opposite: this one
        needs a code or network fix, the other needs the wallet. Merging them is
        the R-F3693 mistake of folding `inconclusive` into `still_locked_out`.
        """
        chain = fb.FallbackProvider([_Provider("deepseek")])
        monkeypatch.setattr(chain, "_provider_api_key", lambda p: "sk-test")
        monkeypatch.setattr(vb, "_fetch", _fetch_raises(RuntimeError("dns")))
        _run(chain._poll_balance_quietly(chain.providers[0]))

        assert not [f for f in sink["failure"]
                    if f.get("module") == "llm_vendor_balance"], \
            "an unreadable gauge must never be reported as a vendor refusal"
        gauge = [f for f in sink["failure"]
                 if f.get("module") == "llm_vendor_balance_gauge"]
        assert gauge, "a dark gauge is itself a reportable fault (§21a)"
        assert "NOTHING" in str(gauge[0].get("detail", "")), (
            "the signal must say explicitly that it implies nothing about credit")

    def test_a_persistently_dark_gauge_does_not_flood_the_ledger(
            self, monkeypatch, sink):
        chain = fb.FallbackProvider([_Provider("deepseek")])
        monkeypatch.setattr(chain, "_provider_api_key", lambda p: "sk-test")
        monkeypatch.setattr(vb, "_fetch", _fetch_raises(RuntimeError("dns")))
        for _ in range(4):
            _run(chain._poll_balance_quietly(chain.providers[0]))
        gauge = [f for f in sink["failure"]
                 if f.get("module") == "llm_vendor_balance_gauge"]
        assert len(gauge) == 1, f"expected ONE transition signal, got {len(gauge)}"


# -- 3. Recovery on the vendor's own evidence, without burning a paid call ---

class TestRecoveryUsesTheBalance:
    def _hard_cool(self, chain, name):
        now = fb.time.time()
        chain._stats[name] = {
            "calls": 1, "failures": 1, "last_failure": now - 10_000,
            "cooldown_until": now + 80_000, "last_kind": "billing",
            "cooldown_hard": True, "cooldown_since": now - 10_000,
            "last_recovery_probe": 0,
        }

    def test_a_funded_balance_releases_the_lockout_with_no_llm_call(
            self, monkeypatch, sink):
        """The operator tops up; ARIA comes back on the vendor's own word.

        R-F3685's probe can only learn this by SPENDING a call, and while the
        balance is empty every one of those fails. The balance endpoint is free
        and definitive, and it is the same evidence class that set the lock
        (the vendor's own accounting), which is the C-41 latch-retire rule.
        """
        p = _Provider("deepseek")
        chain = fb.FallbackProvider([p])
        self._hard_cool(chain, "deepseek")
        monkeypatch.setattr(chain, "_provider_api_key", lambda pr: "sk-test")
        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_FUNDED_BODY))

        released = _run(chain._probe_recovery(p))

        assert released is True
        assert chain._stats["deepseek"]["cooldown_until"] == 0
        assert p.complete_calls == 0, "must not spend a paid call to learn this"

    def test_an_empty_balance_keeps_the_lockout_and_names_the_number(
            self, monkeypatch, sink):
        p = _Provider("deepseek", fail=ProviderError(
            "deepseek", "Insufficient Balance", kind="billing", retryable=False))
        chain = fb.FallbackProvider([p])
        self._hard_cool(chain, "deepseek")
        monkeypatch.setattr(chain, "_provider_api_key", lambda pr: "sk-test")
        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_EXHAUSTED_BODY))

        released = _run(chain._probe_recovery(p))

        assert released is False
        assert p.complete_calls == 0, "the vendor already answered — don't ask twice"
        locked = [f for f in sink["failure"]
                  if "still_locked_out" in str(f.get("source", ""))]
        assert locked, "the operator page must still fire"
        assert "-0.02" in str(locked[0].get("detail", "")), (
            "the page must carry the NUMBER — 'billing' alone does not tell the "
            "operator this is a $0.02 top-up rather than a broken key")

    def test_an_unreadable_balance_falls_back_to_the_paid_probe(self, monkeypatch):
        """Never WORSE than before the gauge existed."""
        p = _Provider("deepseek")
        chain = fb.FallbackProvider([p])
        self._hard_cool(chain, "deepseek")
        monkeypatch.setattr(chain, "_provider_api_key", lambda pr: "sk-test")
        monkeypatch.setattr(vb, "_fetch", _fetch_raises(RuntimeError("dns")))

        released = _run(chain._probe_recovery(p))

        assert released is True
        assert p.complete_calls == 1, "unreadable gauge must not disable the probe"

    def test_an_auth_lockout_still_uses_the_paid_probe(self, monkeypatch):
        """A balance says nothing about a REVOKED KEY. Different question."""
        p = _Provider("deepseek")
        chain = fb.FallbackProvider([p])
        self._hard_cool(chain, "deepseek")
        chain._stats["deepseek"]["last_kind"] = "auth"
        monkeypatch.setattr(chain, "_provider_api_key", lambda pr: "sk-test")
        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_FUNDED_BODY))

        released = _run(chain._probe_recovery(p))

        assert released is True
        assert p.complete_calls == 1


# -- 4. It is on a surface a human reads ------------------------------------

class TestHealthSurface:
    def test_health_publishes_the_balance_tri_state(self):
        chain = fb.FallbackProvider([_Provider("deepseek")])
        h = chain.get_health()
        assert "vendor_balance" in h, (
            "a gauge nobody can read is the C-96 defect: /health reported "
            "operational beside a starved loop in the same payload")
        assert h["vendor_balance"]["deepseek"]["state"] == vb.STATE_NEVER_OBSERVED
        assert h["vendor_balance"]["deepseek"]["available"] is None

    def test_health_reports_a_measured_exhaustion(self, monkeypatch):
        chain = fb.FallbackProvider([_Provider("deepseek")])
        monkeypatch.setattr(chain, "_provider_api_key", lambda p: "sk-test")
        monkeypatch.setattr(vb, "_fetch", _fetch_ok(LIVE_EXHAUSTED_BODY))
        _run(chain._poll_balance_quietly(chain.providers[0]))
        h = chain.get_health()
        assert h["vendor_balance"]["deepseek"]["state"] == vb.STATE_FRESH
        assert h["vendor_balance"]["deepseek"]["available"] is False
        assert h["vendor_balance"]["deepseek"]["total_balance"] == pytest.approx(-0.02)
        assert h["vendor_balance"]["deepseek"]["severity"] == vb.SEVERITY_EXHAUSTED


# -- 5. It cannot become its own incident -----------------------------------

class TestNoSelfDOS:
    def test_polling_is_throttled_to_one_read_per_interval(self, monkeypatch):
        calls = []

        async def _f(url, api_key, timeout):
            calls.append(url)
            return 200, LIVE_FUNDED_BODY
        chain = fb.FallbackProvider([_Provider("deepseek")])
        monkeypatch.setattr(chain, "_provider_api_key", lambda p: "sk-test")
        monkeypatch.setattr(vb, "_fetch", _f)

        async def _burst():
            # A REAL loop, so the spawned tasks actually run. Scheduling five
            # times with no running loop would pass vacuously — the test would
            # be proving that nothing happens, not that throttling works.
            for _ in range(5):
                chain._schedule_balance_poll()
            for _ in range(4):
                await asyncio.sleep(0)

        _run(_burst())
        assert len(calls) == 1, (
            f"a burst of concurrent dispatches must produce ONE vendor read, "
            f"got {len(calls)}")

    def test_the_outbound_client_declares_its_breaker_stance(self):
        """Constitutional rule: a breaker or an explicit '# no-breaker: <why>'."""
        import pathlib
        src = pathlib.Path(vb.__file__).read_text(encoding="utf-8")
        client_lines = [l for l in src.splitlines() if "httpx.AsyncClient(" in l]
        assert client_lines
        for line in client_lines:
            assert "no-breaker:" in line, line


# -- 6. R-F4230 / C-210 — the stream fork must not be dark (§13) -------------

class TestStreamBypassMirror:
    """§13: `stream()` is a subset-fork of `complete()`; every new hook goes in BOTH.

    R-F4229 shipped `_schedule_balance_poll()` into `complete()` only — one line
    below its own §13-mirrored sibling `_schedule_recovery_probes()`. Streaming
    is the CHAT path, so on a deployment whose user traffic is predominantly
    streamed the headroom gauge would be fed only by the autonomous loops and
    DD. That is worse than not firing at all: a gauge that reads plausibly while
    missing the busiest path gets trusted.

    These drive the REAL entry points rather than grepping for the call, so they
    keep working if the hook moves, and they FAIL if either fork loses it.
    """

    def _chain_and_calls(self, monkeypatch):
        calls = []

        async def _f(url, api_key, timeout):
            calls.append(url)
            return 200, LIVE_FUNDED_BODY
        chain = fb.FallbackProvider([_Provider("deepseek")])
        monkeypatch.setattr(chain, "_provider_api_key", lambda p: "sk-test")
        monkeypatch.setattr(vb, "_fetch", _f)
        return chain, calls

    def test_complete_feeds_the_gauge(self, monkeypatch):
        chain, calls = self._chain_and_calls(monkeypatch)

        async def _drive():
            await chain.complete("sys", "hi")
            for _ in range(6):
                await asyncio.sleep(0)

        _run(_drive())
        assert len(calls) == 1

    def test_stream_feeds_the_gauge_too(self, monkeypatch):
        chain, calls = self._chain_and_calls(monkeypatch)

        async def _drive():
            async for _ in chain.stream("sys", "hi"):
                pass
            for _ in range(6):
                await asyncio.sleep(0)

        _run(_drive())
        assert len(calls) == 1, (
            "the streaming chat path must feed the vendor-balance gauge too "
            "(§13) — got no vendor read, so the gauge is dark on the busiest "
            "user path")

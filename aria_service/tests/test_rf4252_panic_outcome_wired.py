"""R-F4252 / C-219 — an SOS that reached NOBODY was invisible to the brain.

`guardian/panic.py` is a live safety path: `POST /api/aria/guardian/panic` ->
`panic.trigger()` alerts every contact in the user's trusted circle. Its own
module docstring names the failure it was built to prevent:

    "Empty-circle safe: with nobody to alert it returns a clear 'circle is empty'
     so the user knows their SOS reached no one (the worst silent failure to
     avoid)."

**That was the one branch that reached nothing.** It returned a dict and emitted
no log, no gap and no brain signal — so a panic alert that woke nobody was
invisible to ARIA unless a caller happened to render the return value. The success
branch was equally dark.

## Why the module's other wiring did not cover it

Every OTHER panic outcome flows through the Action Gateway, whose
`_escalate_safety_failure` calls `record_gap`. The empty-circle branch returns
**before** the gateway is ever reached, so the transitive coverage the rest of the
file enjoys stops exactly at the most dangerous case.

And what the gateway records is per-CONTACT: from inside a single delivery,
0-of-3 and 3-of-3 look identical. Nothing said whether the SOS **as a whole**
reached the circle.

## §25 makes this the sharpest possible instance

*"For ANY action ARIA takes that produces a result for a user ... she must KNOW
whether the intended result was actually produced."* An emergency alert is that
rule's limit case.

## Deliberate choices, each asserted below

* **One signal per SOS, not per contact** — the gateway already reports each
  delivery failure; per-contact reporting here would double-count.
* **No debounce.** A panic is rare, so the flood shape that has twice filled a
  500-slot ledger does not apply — and a debounce would be actively harmful,
  because two SOS events in a minute is precisely when you want both.
* **`dry_run` emits nothing.** R-F1992 established that an operator self-test must
  not enter the production error ledger; the same reasoning covers the gap ledger.
  A test that pages like a real emergency trains people to ignore it.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.guardian import panic as gp


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def sink(monkeypatch):
    got = {"success": [], "failure": []}
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_success",
                        lambda **kw: got["success"].append(kw), raising=True)
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: got["failure"].append(kw), raising=True)
    return got


def _install(monkeypatch, *, contacts, deliver):
    """Stub the circle and the Action Gateway; drive the REAL trigger()."""
    async def _list_circle(user):
        return [{"jid": f"{i}@s.whatsapp.net", "name": f"c{i}"}
                for i in range(contacts)]
    monkeypatch.setattr(gp._circle, "list_circle", _list_circle, raising=True)

    calls = {"n": 0}

    async def _execute(req, send_fn):
        calls["n"] += 1
        ok = deliver(calls["n"] - 1) if callable(deliver) else deliver
        return {"ok": ok}
    monkeypatch.setattr(gp._gw, "execute", _execute, raising=True)
    return calls


class TestTheEmptyCircleBranch:
    """The branch the docstring calls the worst silent failure."""

    def test_an_sos_that_alerted_nobody_reaches_the_brain(
            self, monkeypatch, sink):
        _install(monkeypatch, contacts=0, deliver=True)
        out = _run(gp.trigger("u1", lambda *a, **k: None))

        assert out["error"] == "empty_circle" and out["alerted"] == 0
        assert sink["failure"], (
            "an SOS that reached NOBODY must reach the brain — it returned a "
            "dict and emitted nothing, which is invisible unless a caller "
            "renders the return value")
        f = sink["failure"][0]
        assert f["module"] == "guardian_panic"
        assert "empty_circle" in f["source"]

    def test_it_says_the_remedy_is_a_contact_not_a_retry(
            self, monkeypatch, sink):
        """Empty circle and failed delivery need OPPOSITE responses."""
        _install(monkeypatch, contacts=0, deliver=True)
        _run(gp.trigger("u1", lambda *a, **k: None))
        detail = sink["failure"][0]["detail"]
        assert "EMPTY" in detail
        assert "retrying is not" in detail, (
            "a reader must not treat a configuration gap as a delivery fault")


class TestTheOtherOutcomes:

    def test_a_fully_delivered_sos_is_wired_as_success(self, monkeypatch, sink):
        _install(monkeypatch, contacts=3, deliver=True)
        out = _run(gp.trigger("u1", lambda *a, **k: None))
        assert out["ok"] is True and out["alerted"] == 3
        assert [s for s in sink["success"] if s["module"] == "guardian_panic"]
        assert not sink["failure"]

    def test_zero_of_n_reached_is_a_failure(self, monkeypatch, sink):
        _install(monkeypatch, contacts=3, deliver=False)
        out = _run(gp.trigger("u1", lambda *a, **k: None))
        assert out["ok"] is False and out["alerted"] == 0
        assert "none_reached" in sink["failure"][0]["source"]

    def test_a_partial_delivery_is_a_failure(self, monkeypatch, sink):
        _install(monkeypatch, contacts=3, deliver=lambda i: i == 0)
        out = _run(gp.trigger("u1", lambda *a, **k: None))
        assert out["alerted"] == 1 and out["ok"] is False
        assert "partial" in sink["failure"][0]["source"]
        assert "1/3" in sink["failure"][0]["detail"], (
            "the aggregate must carry alerted/total — that is the thing the "
            "gateway's per-contact gap cannot say")


class TestItDoesNotDoubleCountTheGateway:

    def test_one_signal_per_sos_not_one_per_contact(self, monkeypatch, sink):
        _install(monkeypatch, contacts=5, deliver=False)
        _run(gp.trigger("u1", lambda *a, **k: None))
        mine = [f for f in sink["failure"] if f["module"] == "guardian_panic"]
        assert len(mine) == 1, (
            f"expected ONE aggregate signal per SOS, got {len(mine)} — the "
            f"gateway already records each contact's delivery failure")


class TestASelfTestDoesNotPage:
    """R-F1992's stance, extended from the error ledger to the gap ledger."""

    def test_dry_run_emits_nothing_on_failure(self, monkeypatch, sink):
        _install(monkeypatch, contacts=3, deliver=False)
        out = _run(gp.trigger("u1", lambda *a, **k: None, dry_run=True))
        assert out["dry_run"] is True and out["ok"] is False
        assert not sink["failure"] and not sink["success"], (
            "a panic self-test must not page like a real emergency — that is "
            "how people learn to ignore the alert")

    def test_dry_run_emits_nothing_on_an_empty_circle(self, monkeypatch, sink):
        _install(monkeypatch, contacts=0, deliver=True)
        _run(gp.trigger("u1", lambda *a, **k: None, dry_run=True))
        assert not sink["failure"] and not sink["success"]


class TestObservabilityCannotBreakTheEmergency:

    def test_a_broken_sink_does_not_break_the_sos(self, monkeypatch):
        """An emergency path must survive its own instrumentation."""
        import aria_service.intel.engine_wiring as ew

        def _boom(**kw):
            raise RuntimeError("brain unreachable")
        monkeypatch.setattr(ew, "wire_failure", _boom, raising=True)
        monkeypatch.setattr(ew, "wire_success", _boom, raising=True)

        _install(monkeypatch, contacts=2, deliver=True)
        out = _run(gp.trigger("u1", lambda *a, **k: None))
        assert out["ok"] is True and out["alerted"] == 2, (
            "the SOS result must survive a wiring failure")

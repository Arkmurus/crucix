"""R-F4125 (C-160) — three public functions shipped dark; the gate caught them.

R-F4102 (C-154) widened the stolen-decorator gate beyond `safety.py` and
R-F4103 (C-155) greened the two blocking wiring gates. Within hours GATE A
reported three public sync functions with no route to the brain:

    companies_house.py:448  resolve_company_search()
    intel_ledger.py:338     tmp_orphan_report()
    intel_ledger.py:354     sweep_tmp_orphans()

That is the gate doing the job C-155 restored to it, on code that landed after
it went green — so it is live enforcement, not decoration.

`resolve_company_search` is the one that matters. It decides whether a registry
search result is SAFE to treat as an identity, returning `(None, decision)` for
empty, dead, partial or ambiguous matches precisely so a caller cannot feed an
inferred registration number into identity-dependent downstream work. If it
raises, that safety decision is lost and, unwired, nothing anywhere learns of
it.

Preconditions checked before wiring rather than assumed:

* `fail_wire` RE-RAISES (`wire.py`, both wrappers end in a bare `raise`), so a
  decorator cannot silently convert a raise into a `None` return and break the
  `company, decision = ...` unpacking at the three call sites.
* All three are sync non-generators, so they take `sync_wrapper`; the decorator
  refuses generators at decoration time, which is the class that once caused a
  boot outage.
* gap_type comes from the REGISTRY, not from a guess: `get_gap_type()` returns
  `api_missing` for companies_house (all 19 sibling decorators agree) and
  `engine_failure` for intel_ledger (12 of 13). A wrong type merely trades a
  GATE A violation for a GATE B one.
* No gap-spam risk: `_wire_failure` fires only on an exception, and `record_gap`
  is deduped (1h) and capped (500).

These tests INVOKE the functions and force a failure, rather than grepping for
a decorator — §3c: a test that asserts a helper's shape does not prove the
user-visible symptom is fixed.
"""
from __future__ import annotations

import pytest

from aria_service.intel import companies_house as ch
from aria_service.intel import intel_ledger as il
from aria_service.intel import wiring_harness as wh


@pytest.fixture
def signals(monkeypatch):
    """Capture brain failure signals instead of emitting them."""
    seen: list[dict] = []
    import aria_service.intel.wire as _wire
    monkeypatch.setattr(_wire, "_wire_failure",
                        lambda **kw: seen.append(kw))
    return seen


def test_gate_a_reports_no_blocking_violations():
    """The user-visible symptom: three wiring gates were red on these."""
    results = wh.run_all_gates()
    assert not wh.has_blocking_violations(results), results


@pytest.mark.parametrize("fn_name", [
    "resolve_company_search", "tmp_orphan_report", "sweep_tmp_orphans",
])
def test_each_function_declares_its_registered_gap_type(fn_name):
    """A wrong gap_type trades a GATE A violation for a GATE B one."""
    mod, module_name = (
        (ch, "companies_house") if fn_name == "resolve_company_search"
        else (il, "intel_ledger")
    )
    path = mod.__file__
    decs = wh.fail_wire_decorators(path)
    assert fn_name in decs, f"{fn_name} carries no @fail_wire"
    assert decs[fn_name]["gap_type"] == wh.get_gap_type(module_name, fn_name)


def test_resolve_company_search_reaches_the_brain_when_it_raises(
        signals, monkeypatch):
    """THE capability test: force the identity-safety decision to fail and
    prove the brain hears about it."""
    def _boom(*a, **k):
        raise RuntimeError("registry resolution blew up")

    monkeypatch.setattr(ch, "_pick_best_company", _boom)

    with pytest.raises(RuntimeError):
        ch.resolve_company_search("Acme Ltd", [{"title": "ACME LTD"}])

    assert signals, (
        "resolve_company_search failed and NOTHING reached the brain — the "
        "identity-safety decision was lost silently")
    assert signals[0]["module"] == "companies_house"
    assert signals[0]["func_name"] == "resolve_company_search"


def test_the_decorator_does_not_swallow_the_exception(signals, monkeypatch):
    """`fail_wire` must RE-RAISE. If it swallowed, the caller's
    `company, decision = resolve_company_search(...)` would unpack None and the
    identity path would read a crash as 'no match found' — strictly worse than
    the dark version."""
    def _boom(*a, **k):
        raise ValueError("nope")

    monkeypatch.setattr(ch, "_pick_best_company", _boom)
    with pytest.raises(ValueError):
        ch.resolve_company_search("X", [{"title": "X"}])


def test_the_healthy_path_emits_nothing(signals):
    """A working call must not signal — otherwise every DD search would file a
    gap and refill the 500-slot ledger."""
    company, decision = ch.resolve_company_search("", [])
    assert company is None            # empty results -> no match, not an error
    assert signals == [], signals


def test_ledger_housekeeping_reaches_the_brain_when_it_raises(
        signals, monkeypatch):
    """`sweep_tmp_orphans` documents 'Never raises'. Wiring costs nothing if
    that holds, and tells the brain if it does not."""
    def _boom(*a, **k):
        raise OSError("volume gone")

    monkeypatch.setattr(il, "_tmp_orphans", _boom)

    with pytest.raises(OSError):
        il.tmp_orphan_report()
    assert any(s["func_name"] == "tmp_orphan_report" for s in signals), signals

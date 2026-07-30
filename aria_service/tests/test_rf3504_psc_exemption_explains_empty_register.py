"""R-F3504 — an empty PSC register read as opacity. R-F2830 built the fix and nobody called it.

THE DEFECT, from the delivered Babcock report. The Network & Ownership section's entire
visible content was::

    UBO chain nodes traversed 11

Babcock is a listed PLC. Its PSC register is lawfully empty because it trades on a UK
regulated market and discloses ownership through market rules instead. A reader was left
to infer something from an absence that has a checkable explanation, and the natural
inference — undisclosed ownership — is the wrong one.

R-F2830 ALREADY BUILT THE ANSWER. `companies_house.get_psc_exemptions` exists, is
documented, and states the stakes in its own docstring: a company disclosing no beneficial
owners looks opaque, potentially evasive, whereas one exempt because it trades on a
regulated market is behaving entirely normally — "reporting the first when the truth is
the second is a false ACCUSATION, the mirror of a false clean". **Nothing ever called it.**

THREE OUTCOMES, and all three are distinct answers a reader must be able to tell apart:
  * exemption ACTIVE   — the absence is lawful and normal; say so
  * no exemption       — ownership is genuinely undisclosed; a real gap
  * check DID NOT RUN  — unknown. Claim NEITHER, because an unperformed check that
                         resolves to "lawful" is a false clean and one that resolves to
                         "opaque" is a false accusation

COST DISCIPLINE: asked only when the register is EMPTY. An exemption explains nothing when
controllers are disclosed, so the call would be pure waste.

═══════════════════════════════════════════════════════════════════════════════════════
R-F3515 — WHY THIS FILE WAS REWRITTEN, AND IT IS THE MORE IMPORTANT LESSON.

Every test here was originally a `grep` over `dd_orchestrator.py` source text. All eight
passed. Then a real DD on Chemring Group PLC — a UK listed PLC, reg 00086662 — came back
with `psc_exemptions: {}`, no finding and no gap. The block had never executed, on any run.

R-F3504 had placed it inside the ``else`` of ``if jurisdiction_iso2 == "GB"`` — the
MULTI-JURISDICTION adapter branch — and then guarded it on ``jurisdiction in (GB, UK, "")``.
A UK-only check on the non-UK path. It was unreachable by construction, and a source grep
cannot tell reachable code from unreachable code: the strings were all present.

So these tests now CALL `_explain_empty_psc_register` and assert what lands on the report
(§3c: a test that does not invoke the broken path does not verify it). The two structural
greps that remain are about REACHABILITY — the property text alone could never establish.
"""
from __future__ import annotations

import asyncio
import pathlib
import re

import pytest

from aria_service.intel import dd_orchestrator as orch
from aria_service.intel.dd_schema import ARKDDReport

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")


def _report(*, shareholders=None, reg="00086662", iso2="GB") -> ARKDDReport:
    r = ARKDDReport()
    r.identity.shareholders = list(shareholders or [])
    r.identity.registration_number = reg
    r.identity.jurisdiction_iso2 = iso2
    return r


def _run(report, exemptions, monkeypatch):
    """Drive the real function with a stubbed reader; return the mutated report."""
    from aria_service.intel import companies_house as ch
    calls: list[str] = []

    async def _fake(reg_no):
        calls.append(reg_no)
        if isinstance(exemptions, BaseException):
            raise exemptions
        return exemptions

    monkeypatch.setattr(ch, "get_psc_exemptions", _fake)
    asyncio.run(orch._explain_empty_psc_register(report))
    return report, calls


# ── the three outcomes, driven through the real function ────────────────────

def test_capability_an_active_exemption_is_reported_as_lawful_and_normal(monkeypatch):
    r, calls = _run(_report(), {
        "checked": True, "has_active_exemption": True,
        "active": [{"exemption_type": "PSC_EXEMPT_AS_TRADING_ON_REGULATED_MARKET",
                    "exempt_from": "2016-06-30"}],
    }, monkeypatch)

    assert calls == ["00086662"], "the reader was not called with the registration number"
    assert r.identity.psc_exemptions.get("has_active_exemption") is True
    titles = [f.title for f in r.identity.findings]
    assert any("LAWFUL exemption" in t for t in titles), titles
    body = next(f for f in r.identity.findings if "LAWFUL exemption" in f.title).detail
    assert "must not be read as opacity" in body
    assert "regulated market" in body
    assert "TRADING_ON_REGULATED_MARKET" in body, "the exemption type must be quoted"
    assert not r.identity.data_gaps, (
        "a lawfully explained absence is not a data gap — recording one re-creates the "
        "false accusation this fixes")


def test_capability_no_exemption_is_reported_as_genuinely_undisclosed(monkeypatch):
    """The direction where silence would hide a REAL signal."""
    r, _ = _run(_report(), {"checked": True, "has_active_exemption": False, "active": []},
                monkeypatch)

    assert not r.identity.findings, "no exemption is not a positive finding"
    assert any("undisclosed rather than lawfully exempt" in g
               for g in r.identity.data_gaps), r.identity.data_gaps


def test_capability_an_unperformed_check_claims_neither(monkeypatch):
    """An unperformed check that resolves to 'lawful' is a false clean; one that resolves
    to 'opaque' is a false accusation. It must resolve to neither."""
    r, _ = _run(_report(), {"checked": False}, monkeypatch)

    assert not r.identity.findings, "an unchecked register cannot produce a clearance"
    gap = " ".join(r.identity.data_gaps)
    assert "could NOT be checked" in gap
    assert "Not a finding of opacity and not a clearance" in gap
    assert "undisclosed rather than lawfully exempt" not in gap, (
        "an unperformed check must not fall through to the opacity gap")


# ── the guards ──────────────────────────────────────────────────────────────

def test_capability_it_is_only_asked_when_the_register_is_empty(monkeypatch):
    """Cost discipline: an exemption explains nothing when controllers are disclosed."""
    r, calls = _run(_report(shareholders=[{"name": "Someone"}]),
                    {"checked": True, "has_active_exemption": True, "active": [{}]},
                    monkeypatch)
    assert calls == [], "the register has controllers — the lookup is wasted spend"
    assert r.identity.psc_exemptions == {}
    assert not r.identity.data_gaps


@pytest.mark.parametrize("reg,iso2,why", [
    ("", "GB", "no registration number to look up"),
    ("00086662", "DE", "a German company has no UK PSC exemption register"),
])
def test_capability_it_is_not_asked_when_it_cannot_apply(reg, iso2, why, monkeypatch):
    r, calls = _run(_report(reg=reg, iso2=iso2), {"checked": True}, monkeypatch)
    assert calls == [], why
    assert not r.identity.data_gaps, (
        "an inapplicable check must stay silent, not manufacture a gap")


def test_capability_the_lookup_cannot_fail_the_identity_layer(monkeypatch):
    """An explanatory lookup must never cost the report."""
    r, _ = _run(_report(), RuntimeError("companies house 503"), monkeypatch)
    assert r.identity.psc_exemptions == {}
    assert not r.identity.findings


def test_it_uses_the_readers_real_keys():
    """The reader returns `checked` / `has_active_exemption` / `active`. My first cut
    invented `active_exemptions`, which would have silently matched nothing and made the
    exemption branch unreachable — a wiring that looks done and does nothing."""
    from aria_service.intel import companies_house as ch
    import inspect
    reader = inspect.getsource(ch.get_psc_exemptions)
    for key in ('"checked"', '"has_active_exemption"', '"active"'):
        assert key in reader, f"the reader no longer returns {key}"
    assert "active_exemptions" not in SRC, "the invented key is back"


def test_the_result_is_persisted_on_the_report(monkeypatch):
    """A reader of the STORED report must be able to see WHY the register was empty —
    this is the field that was `{}` on the live Chemring run."""
    payload = {"checked": True, "has_active_exemption": False, "active": []}
    r, _ = _run(_report(), payload, monkeypatch)
    assert r.identity.psc_exemptions == payload


# ── R-F3515: REACHABILITY. Behaviour tests cannot see an uncalled function. ──

def test_rf3515_the_call_site_is_outside_the_jurisdiction_branch():
    """THE DEFECT R-F3504 SHIPPED: unreachable code that greps as present.

    The call must sit at the identity-stage indent level — after BOTH the Companies House
    branch and the multi-jurisdiction adapter branch have written `identity` — not nested
    inside either. Nesting is what made a UK-only check live on the non-UK path.
    """
    calls = [ln for ln in SRC.splitlines()
             if "_explain_empty_psc_register(report)" in ln and "def " not in ln]
    assert len(calls) == 1, f"expected exactly one call site, found {len(calls)}: {calls}"
    indent = len(calls[0]) - len(calls[0].lstrip())
    assert indent == 4, (
        f"the call is nested {indent} deep — it is inside a branch again, which is "
        "exactly how R-F3504 became unreachable")
    assert calls[0].lstrip().startswith("await "), "a coroutine that is never awaited"


def test_rf3515_the_logic_lives_in_one_named_function():
    """Inline blocks cannot be tested by calling them, which is why R-F3504's tests were
    all source greps and all passed against dead code."""
    assert SRC.count("async def _explain_empty_psc_register") == 1
    assert SRC.count("get_psc_exemptions(") == 1, (
        "a second inline lookup has appeared — the single call site is the property")

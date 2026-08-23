"""R-F4265 / C-226 - the financial layer screened the name the CUSTOMER TYPED.

THE LIVE SYMPTOM, from ``ARIA_DD_Vigilo_Solutions_Limited_dd_9fe0e61e4a0c.pdf``.
The report's subject block reads::

    Entity              Vigilo Solutions Limited
    Registration number SC215104
    Canonical ID        company:GB:SC215104

and its financial-health finding, on the next page, reads::

    Vigilo Security Solutions is not found in SEC EDGAR (not US-listed).

"Vigilo Security Solutions" is not the subject and appears nowhere else in the
report. It is the string the requester supplied, before identity resolution
corrected it against Companies House.

THE MECHANISM. `_enrich_target` (dd_orchestrator) cleans a supplied name of URLs
and descriptors but NEVER replaces it with the resolved legal name - by design,
because the supplied string is evidence of what was asked for. The resolved name
lives on `report.identity.entity_name`, and every other layer reads it. The
financial block alone inverted the precedence::

    _fin_name = (target.get("name") or target.get("entity")
                 or getattr(report.identity, "entity_name", "") or "")

so `identity.entity_name` was only ever a fallback for a MISSING supplied name.

WHY IT IS WORTH A FIXTURE AND NOT A TYPO FIX. Two distinct harms, and the second
is the one that costs a customer:

  1. The report asserts a company name that is not the subject, in the one section
     a reader checks hardest. The same orchestrator already knows the two can
     differ - the deep-research topic builder appends "formerly or supplied as
     {original}" precisely on `_original_name != name`.
  2. `_assess_sec_edgar` resolves a CIK BY NAME. A near-miss supplied name misses
     a genuine US filer, and `_verdict()` then returns UNKNOWN - "financial capacity
     could not be established" for an entity whose filings are public. That is an
     absence of coverage produced by our own input handling, which is the failure
     direction R-F2782 was raised to close.

The test drives the real `_run_compliance` and asserts on the name that actually
reaches `financial_health.assess`.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import dd_orchestrator as d
from aria_service.intel import dd_schema as ds
from aria_service.intel import financial_health as fh


_SUPPLIED = "Vigilo Security Solutions"          # what the requester typed
_REGISTERED = "Vigilo Solutions Limited"         # what Companies House returned


def _report() -> ds.ARKDDReport:
    rep = ds.ARKDDReport()
    rep.identity.entity_name = _REGISTERED
    rep.identity.registration_number = "SC215104"
    rep.identity.jurisdiction_iso2 = "GB"
    return rep


@pytest.mark.asyncio
async def test_financial_layer_screens_the_registry_name_not_the_supplied_one(monkeypatch):
    """THE CAPABILITY TEST - drives the real compliance layer.

    Asserting on the string handed to `financial_health.assess` is the whole
    defect: it is both what SEC EDGAR is searched for and what the finding prints.
    """
    seen: dict = {}

    async def _capture(name, **kw):
        seen["name"] = name
        return {"data_available": False, "reason": "stub - not the subject of this test"}

    monkeypatch.setattr(fh, "assess", _capture)

    target = {"name": _SUPPLIED, "type": "company",
              "jurisdiction_iso2": "GB", "registration_number": "SC215104"}
    await asyncio.wait_for(d._run_compliance(target, _report()), timeout=240)

    assert seen.get("name") == _REGISTERED, (
        f"financial health was assessed for {seen.get('name')!r} while the report's "
        f"subject is {_REGISTERED!r} (SC215104). The report prints this name verbatim "
        "('X is not found in SEC EDGAR') and EDGAR resolves a CIK by name, so a "
        "supplied near-miss both misstates the subject and can manufacture an "
        "UNKNOWN financial capacity for a company whose filings are public."
    )


@pytest.mark.asyncio
async def test_supplied_name_is_still_used_when_identity_did_not_resolve(monkeypatch):
    """The fallback must survive.

    Identity resolution legitimately fails (unregistered trading name, foreign
    entity, registry outage). Preferring an EMPTY resolved name would take the
    financial layer dark for exactly those subjects - trading one defect for a
    worse one. `len(_fin_name) >= 3` then skips the layer entirely.
    """
    seen: dict = {}

    async def _capture(name, **kw):
        seen["name"] = name
        return {"data_available": False, "reason": "stub"}

    monkeypatch.setattr(fh, "assess", _capture)

    rep = ds.ARKDDReport()
    rep.identity.entity_name = ""          # unresolved
    rep.identity.jurisdiction_iso2 = "GB"
    target = {"name": _SUPPLIED, "type": "company", "jurisdiction_iso2": "GB"}
    await asyncio.wait_for(d._run_compliance(target, rep), timeout=240)

    assert seen.get("name") == _SUPPLIED, (
        "with no resolved legal name the supplied name is the only name there is; "
        f"got {seen.get('name')!r}"
    )

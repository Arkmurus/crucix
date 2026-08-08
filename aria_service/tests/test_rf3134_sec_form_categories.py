"""R-F3134 — the Identity section promised financial DD material an ADR form cannot hold.

LIVE on the Babcock DD (dd_8c7242c2b45b, read back from the stored report). Two
findings, BOTH tagged CONFIRMED, in the same report:

    Identity    "SEC filings found: 5 recent (Babcock International Group PLC/ADR)
                 Most recent: F-6EF filed 2025-03-19.
                 Full filings available for financial DD review."

    Compliance  "Financial health — no US-listed (SEC EDGAR) filings
                 No SEC/EDGAR (US-listed) financials available — UNKNOWN, not a
                 clean bill."

A reader cannot reconcile those and concludes the report is broken — the operator's
standing complaint that the reports are "confusing" and must be "coherent and sound".

THE COMPLIANCE LAYER WAS RIGHT. Form F-6EF is a registration statement filed by a
DEPOSITARY BANK to register American Depositary Receipts; the "/ADR" suffix on the
entity name is the tell. It contains no financial statements at all. Babcock is
UK-listed, is not a US reporting issuer, and its accounts live at Companies House.

So the identity prose asserted coverage the evidence could not support — the same shape
as R-F3129 (sanctions clearance offered on lists nothing showed were queried). The fix
is not to go silent about real filings (that is R-F1696 in reverse) but to state WHAT
they are and withhold the financial-review promise unless a form that actually carries
statements is present.
"""
import asyncio
import inspect

import pytest

from aria_service.intel import dd_orchestrator as ddo

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


# ── the real filings EDGAR returned for Babcock, verbatim from the stored report ──
BABCOCK_HITS = [
    {"form": "F-6EF", "filing_date": "2025-03-19",
     "company_name": "Babcock International Group PLC/ADR", "severity_hint": "INFO"},
    {"form": "F-6EF", "filing_date": "2019-11-01",
     "company_name": "Babcock International Group PLC/ADR", "severity_hint": "INFO"},
    {"form": "F-6 POS", "filing_date": "2018-06-12",
     "company_name": "Babcock International Group PLC/ADR", "severity_hint": "INFO"},
]

US_ISSUER_HITS = [
    {"form": "10-K", "filing_date": "2025-02-14",
     "company_name": "Some US Issuer Inc", "severity_hint": "INFO — annual report"},
    {"form": "10-Q", "filing_date": "2025-05-02",
     "company_name": "Some US Issuer Inc", "severity_hint": "INFO — quarterly report"},
]


def _run_screen(hits, monkeypatch, name="Babcock International Group plc"):
    """Drive the REAL broken function end-to-end.

    §3c: a test of the `_sec_form_summary` helper alone would NOT count — the defect was
    in the finding this screen EMITS. Every other source is stubbed to a clean miss so
    only the SEC branch produces output.
    """
    # Module names verified against dd_orchestrator's own import block (§3b):
    # the UK list adapter is `fcdo_sanctions`, NOT `uk_ofsi`.
    from aria_service.intel.sources import (
        acled as _acled, fcdo_sanctions as _ofsi, ofac_sdn as _ofac,
        sec_edgar as _sec, un_sc_sanctions as _un, worldbank_debarred as _wb,
    )

    async def _sec_lookup(*a, **k):
        return {"ok": True, "hits": hits}

    async def _empty(*a, **k):
        return {"ok": True, "hits": []}

    monkeypatch.setattr(_sec, "lookup", _sec_lookup)
    for mod in (_ofac, _ofsi, _un, _wb, _acled):
        monkeypatch.setattr(mod, "lookup", _empty)

    # ARKDDReport carries the subject on `target`, not a `subject` kwarg (§3b).
    report = ddo.ARKDDReport(target={"name": name, "jurisdiction_iso2": "GB"})
    asyncio.run(ddo._identity_primary_source_screen(name, "GB", report))
    return [
        f for f in report.identity.findings
        if "sec_edgar" in str(getattr(f, "source", "") or "")
    ]


def test_rf3134_capability_adr_filings_do_not_promise_financials(monkeypatch):
    """THE LIVE DEFECT: F-6EF filings advertised as financial DD material."""
    findings = _run_screen(BABCOCK_HITS, monkeypatch)
    assert findings, "the SEC finding must still be emitted — real filings exist"
    blob = " ".join(f"{f.title} {f.detail}" for f in findings)

    assert "Full filings available for financial DD review" not in blob, (
        "R-F3134 REGRESSION: ADR registration forms advertised as financial DD "
        f"material again.\n{blob}")
    assert "financial statements" in blob.lower(), (
        f"the finding must say the forms carry no financial statements.\n{blob}")
    assert "F-6EF" in blob, f"the actual form must be named.\n{blob}"


def test_rf3134_capability_it_explains_what_the_form_is(monkeypatch):
    """A bare form code is not usable by a customer; say what it means."""
    findings = _run_screen(BABCOCK_HITS, monkeypatch)
    blob = " ".join(f"{f.title} {f.detail}" for f in findings)
    assert "ADR depositary registration" in blob, (
        f"the reader must learn F-6EF is an ADR depositary registration.\n{blob}")
    assert "registry" in blob.lower(), (
        f"the finding must route the reader to the home-jurisdiction registry.\n{blob}")


def test_rf3134_capability_no_longer_contradicts_the_financial_layer(monkeypatch):
    """The two sections must be reconcilable by a reader.

    Compliance says "No SEC/EDGAR financials available". Identity must now agree that
    EDGAR does not evidence financials, rather than assert the opposite.
    """
    findings = _run_screen(BABCOCK_HITS, monkeypatch)
    blob = " ".join(f"{f.title} {f.detail}" for f in findings).lower()
    assert "does not evidence" in blob or "none of these form types contain" in blob, (
        f"identity must concede EDGAR holds no financials here.\n{blob}")
    assert "not shown to be a us reporting issuer" in blob, (
        f"the reason must be stated, not merely the conclusion.\n{blob}")


def test_rf3134_capability_a_real_us_issuer_still_gets_the_promise(monkeypatch):
    """The fix must not over-correct: 10-K/10-Q DO carry financial statements.

    Suppressing the pointer for genuine US reporting issuers would trade one false
    statement for another.
    """
    findings = _run_screen(US_ISSUER_HITS, monkeypatch, name="Some US Issuer Inc")
    assert findings, "a US issuer's filings must still be reported"
    blob = " ".join(f"{f.title} {f.detail}" for f in findings)
    assert "available for financial DD review" in blob, (
        f"10-K/10-Q must still be offered for financial review.\n{blob}")
    assert "none carrying financial statements" not in blob, (
        f"a 10-K carries financial statements.\n{blob}")


@pytest.mark.parametrize(
    "forms,expected",
    [
        (["F-6EF"], False),
        (["F-6EF", "F-6 POS"], False),
        (["SC 13G", "4"], False),
        (["10-K"], True),
        (["20-F"], True),          # foreign private issuer annual report DOES carry them
        (["40-F"], True),
        (["F-6EF", "20-F"], True),  # mixed: one qualifying form is enough
    ],
)
def test_rf3134_form_classification(forms, expected):
    hits = [{"form": f} for f in forms]
    _forms, has_fin, _desc = ddo._sec_form_summary(hits)
    assert has_fin is expected, f"{forms} -> {has_fin}, expected {expected}"
    assert _forms == forms, "distinct forms must be preserved in filing order"


def test_rf3134_form_summary_survives_junk():
    """Adapters return what they return; a missing/blank form must not raise."""
    forms, has_fin, desc = ddo._sec_form_summary(
        [{}, {"form": ""}, None, {"form": "F-6EF"}, {"form": "F-6EF"}]
    )
    assert forms == ["F-6EF"], forms
    assert has_fin is False
    assert "ADR" in desc


def test_rf3134_the_old_promise_string_is_gone_unconditionally():
    """Belt and braces: the unconditional string must not exist in the module."""
    src = module_source(ddo)
    # The surviving occurrence is inside the has-financial-statements branch and is
    # prefixed; the bare unconditional form must not reappear.
    assert "Full filings available for financial DD review" not in src, (
        "R-F3134 REGRESSION: the unconditional financial-review promise is back")

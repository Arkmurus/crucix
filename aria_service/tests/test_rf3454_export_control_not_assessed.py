"""R-F3454 — the report called a nuclear-submarine builder "civilian or unclassified".

THE DEFECT, from a delivered report on Babcock International Group PLC — one of the UK's
largest defence primes (nuclear submarine support, warships, Type 31)::

    Export control: civilian or unclassified

R-F3040 already exists to stop exactly this, and it could not fire. It keys on a MILITARY
SIC code, but the legal entity 02342138 is the GROUP HOLDING company: its registered
activity is head-office administration and contains no military words. The classifier was
handed that text, found no export-control hits, and returned its no-hits value. Every
layer behaved correctly and the report still printed the most misleading sentence a
defence-DD product can print.

THE ERROR IS ASSERTING A NEGATIVE FROM AN INPUT THAT COULD NEVER HAVE PRODUCED A
POSITIVE. No product or transaction was supplied, so nothing about the goods was
classified. "No military words in a head-office SIC code" is an absence of COVERAGE, not
evidence of civilian use — the identical rule this same report already applies to adverse
media ("an absence of coverage, not proof of good standing") and to an unsearched
judgment register ("an unsearched register is not a clean one").

ASYMMETRY IS DELIBERATE. A POSITIVE hit derived from a SIC code or self-description still
stands, because that is real evidence. Only the negative is downgraded.
"""
from __future__ import annotations

import pytest


def _ec_block(report):
    return report.compliance.export_control or {}


def test_the_no_hits_value_is_unchanged_in_the_classifier():
    """The classifier is HONEST about the text it was given — the defect was never here,
    and moving the fix into it would corrupt a correct component."""
    from aria_service.intel import tech_classifier
    ec = tech_classifier.classify_export_control("activities of head offices")
    assert "civilian" in str(ec.get("recommendation", "")).lower(), (
        "classifying free text with no military content as civilian is correct behaviour "
        "for the classifier in isolation")


@pytest.mark.parametrize("supplied_product", [None, "", "   "])
def test_civilian_is_downgraded_when_no_product_was_specified(supplied_product):
    """THE FIX: with no goods supplied, the report must not conclude 'civilian'."""
    rec = _run_export_control(product=supplied_product,
                              sic_text="70100 — Activities of head offices")
    assert "civilian" not in rec.lower(), rec
    assert "NOT ASSESSED" in rec


def test_a_military_sic_is_never_downgraded_to_not_assessed():
    """Asymmetry, and a REGRESSION GUARD on R-F3040.

    R-F3040 raises an amber contradiction when the registry declares military manufacture
    but the automated read says civilian — and it detects that by looking for the words
    "civilian"/"unclassified" in the recommendation string. The first cut of R-F3454
    rewrote that string first, which silently disarmed R-F3040. A military SIC therefore
    suppresses the downgrade entirely: it IS evidence bearing on export control.
    """
    rec = _run_export_control(product=None,
                              sic_text="30400 — Manufacture of military fighting vehicles",
                              military_sic=True)
    assert "NOT ASSESSED" not in rec, (
        "a registry-declared military activity was rewritten to NOT ASSESSED, which "
        "removes the R-F3040 contradiction finding")
    assert "civilian" in rec.lower(), (
        "the string R-F3040 keys on must survive so its amber finding still fires")


def test_a_supplied_product_is_classified_normally():
    """When the operator DOES supply goods, a civilian answer is a real answer."""
    rec = _run_export_control(product="office stationery and paper", sic_text="")
    assert "NOT ASSESSED" not in rec
    assert "civilian" in rec.lower()


def _run_export_control(product, sic_text, military_sic=False):
    """Classify with the REAL classifier, then apply the REAL orchestrator predicate.

    No mirrored logic: `_export_control_is_unsupported_negative` is the same function the
    orchestrator calls, so this cannot drift from production.
    """
    from aria_service.intel import tech_classifier
    from aria_service.intel.dd_orchestrator import _export_control_is_unsupported_negative

    text = product or sic_text or ""
    ec = tech_classifier.classify_export_control(text) if text else {}
    if _export_control_is_unsupported_negative(
            product_supplied=bool(product and str(product).strip()),
            military_sic_present=military_sic,
            recommendation=str(ec.get("recommendation") or "")):
        ec["recommendation"] = "NOT ASSESSED — no product or transaction specified"
    return str(ec.get("recommendation") or "")


def test_the_orchestrator_contains_the_downgrade_and_states_the_reason():
    """Anchor the helper above to the REAL code path.

    A helper that mirrors production logic is worth nothing if production stops doing it,
    so assert the orchestrator carries the same predicate and, critically, that it records
    a GAP and a FINDING — a silent downgrade would replace a false statement with an
    absent one.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8", errors="replace")
    assert "R-F3454" in src
    assert 'ec["assessed"] = False' in src
    assert "NOT ASSESSED — no product or transaction specified" in src
    assert "Export-control exposure NOT ASSESSED" in src, "no finding is raised"
    assert "Export-control classification NOT PERFORMED" in src, "no data gap is recorded"
    # The orchestrator must CONSULT the shared predicate rather than re-deciding inline —
    # an inline copy is what disarmed R-F3040 in the first cut.
    assert "_export_control_is_unsupported_negative(" in src
    assert "military_sic_present=bool(_military_sic_codes(" in src, (
        "the military-SIC suppression is not wired into the call site, so R-F3040 can be "
        "disarmed again")

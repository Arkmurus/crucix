"""R-F1903 (last-layer #4b) — export-control classification from the entity's
self-described activity.

The Modirum DD reported 'Export control: skipped — no product/goods description'
even though the site states 'AI-Powered Full-Spectrum Defence Solutions / C2 and
ISR solutions'. Same under-extraction as the jurisdiction gap (R-F1895): the DD
mined that text and discarded it. Now the identity layer seeds declared_activity
from the site, and the compliance layer falls back to it so export-control RUNS
(tagged self-described / indicative) instead of being skipped. Non-fabricating —
it's the entity's OWN stated activity, and the result is flagged indicative.
"""
from __future__ import annotations

import inspect

import aria_service.intel.dd_orchestrator as dd
from aria_service.intel import tech_classifier

# R-F3755/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def test_classifier_runs_on_self_described_defence_activity():
    # the layer must be able to classify a self-described activity string (dict result)
    r = tech_classifier.classify_export_control("AI-Powered Full-Spectrum Defence Solutions — C2 and ISR systems")
    assert isinstance(r, dict) and "recommendation" in r


def test_identity_seeds_declared_activity_from_site():
    src = function_source(dd, "_run_identity")
    assert "R-F1903" in src, "declared_activity seeding from site missing"
    assert "report.identity.declared_activity = _act" in src
    # only when empty (don't clobber a registry-derived activity)
    assert "if not report.identity.declared_activity:" in src


def test_compliance_export_control_falls_back_to_declared_activity():
    src = function_source(dd, "_run_compliance")
    # product_text still falls back to declared_activity when nothing was supplied...
    assert "report.identity.declared_activity" in src
    # R-F3040 — the fallback CHAIN gained one earlier link: the registry's own SIC
    # code, expanded to its official description. This assertion used to pin the
    # exact expression text, which said nothing about behaviour and broke the moment
    # a better source was added ahead of it. What R-F1903 actually protects is that
    # declared_activity remains the fallback and that a self-described read is
    # labelled as such — both still true, and now asserted as such.
    assert 'target.get("product_description") or target.get("goods")' in src
    assert "_sic_text or (report.identity.declared_activity" in src, (
        "declared_activity must remain the last-resort fallback")
    # ...and the result is tagged as self-described / indicative, not authoritative
    assert "_ec_from_self_desc" in src
    assert "SELF-DESCRIBED" in src


def test_rf3040_registry_sic_outranks_self_description():
    """R-F3040 — a registry SIC code is a primary-source declaration by the company
    to the registrar; the website blurb is not. SIC must be consulted FIRST, and a
    self-described read must not be labelled self-described when SIC supplied it."""
    src = function_source(dd, "_run_compliance")
    i = src.index("product_text = (target.get")
    window = src[i:i + 500]
    assert "_sic_text" in window
    assert "and not _sic_text and" in window, (
        "when SIC supplied the text, the read is registry-derived, not self-described")

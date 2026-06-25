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


def test_classifier_runs_on_self_described_defence_activity():
    # the layer must be able to classify a self-described activity string (dict result)
    r = tech_classifier.classify_export_control("AI-Powered Full-Spectrum Defence Solutions — C2 and ISR systems")
    assert isinstance(r, dict) and "recommendation" in r


def test_identity_seeds_declared_activity_from_site():
    src = inspect.getsource(dd._run_identity)
    assert "R-F1903" in src, "declared_activity seeding from site missing"
    assert "report.identity.declared_activity = _act" in src
    # only when empty (don't clobber a registry-derived activity)
    assert "if not report.identity.declared_activity:" in src


def test_compliance_export_control_falls_back_to_declared_activity():
    src = inspect.getsource(dd._run_compliance)
    # product_text now falls back to declared_activity...
    assert "report.identity.declared_activity" in src
    assert "target.get(\"product_description\") or target.get(\"goods\") or (report.identity.declared_activity" in src
    # ...and the result is tagged as self-described / indicative, not authoritative
    assert "_ec_from_self_desc" in src
    assert "SELF-DESCRIBED" in src

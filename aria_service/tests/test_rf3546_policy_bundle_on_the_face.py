"""R-F3546 — two reports 94 minutes apart embodied different rules and neither said so.

THE FINDING, from an operator review of four reports issued within ~2 hours. The same
evidence situations were decided by DIFFERENT policies:

    waived sanctions     -> "AMBER, proceed with enhanced DD"   vs "NOT CLEARED"
    unreadable accounts  -> stale WEAK verdict at CONFIRMED     vs principled UNKNOWN
    export control       -> "NOT ASSESSED"                      vs "civilian or unclassified"
    adverse-media sweep  -> 30/48 templates                     then 12/48, unexplained

Most of that was same-day fixes landing between runs — expected during Phase 0. **The
defect is not the variation, it is the SILENCE.** Nothing on either document named the
rules it was issued under, so a client or auditor cannot tell a deliberate policy change
from drift, and cannot attribute a behavioural difference to anything at all.

WHY A BUNDLE AND NOT ONE NUMBER. R-F3496 pinned `verdict_logic_version`, which was the
right instinct — but a verdict is one component. A report's behaviour is also decided by
which sources were attempted and how evidence was graded, and those move independently of
the verdict rules. A single number would go stale silently the first time one of the
others changed, which is the failure it exists to prevent.

UNCONDITIONAL BY DESIGN. A version that only prints when something looks wrong is an
alert, not a version — and the entire point is that two CLEAN-looking reports can differ.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import (
    DD_POLICY_BUNDLE,
    DD_VERDICT_LOGIC_VERSION,
    ARKDDReport,
    dd_policy_bundle_line,
)


def _rendered():
    r = ARKDDReport()
    r.identity.entity_name = "Test Ltd"
    return r.render_markdown()


# ── it is ON THE FACE ───────────────────────────────────────────────────────

def test_capability_the_bundle_appears_on_the_rendered_report():
    """A version held only in a JSON blob is invisible to the person holding the PDF."""
    md = _rendered()
    assert "Policy bundle:" in md, "the policy bundle is not on the document face"


def test_it_sits_with_the_readiness_line_not_buried():
    """It belongs where a reader already looks for the report's standing."""
    md = _rendered()
    lines = md.splitlines()
    dr = next(i for i, l in enumerate(lines) if "Decision Readiness:" in l)
    pb = next(i for i, l in enumerate(lines) if "Policy bundle:" in l)
    assert 0 < pb - dr <= 2, f"the bundle is {pb - dr} lines from readiness"


def test_it_prints_unconditionally():
    """Even a clean, fully-cleared report must carry it — the point is that two
    clean-looking reports can differ."""
    r = ARKDDReport()
    r.identity.entity_name = "Spotless Ltd"
    assert "Policy bundle:" in r.render_markdown()


# ── the bundle is a BUNDLE ─────────────────────────────────────────────────

@pytest.mark.parametrize("component", [
    "verdict_logic", "evidence_standard", "adverse_media_templates", "ownership_walk",
])
def test_every_moving_policy_is_named(component):
    """One number goes stale silently the first time a different component changes."""
    assert component in DD_POLICY_BUNDLE
    assert component in dd_policy_bundle_line()


def test_the_verdict_component_tracks_the_existing_pin():
    """R-F3496's version must not fork into a second source of truth — that is the
    two-implementations-of-one-question defect this codebase keeps hitting."""
    assert DD_POLICY_BUNDLE["verdict_logic"] == DD_VERDICT_LOGIC_VERSION


def test_the_line_is_a_single_compact_line():
    line = dd_policy_bundle_line()
    assert "\n" not in line
    assert len(line) < 200, "a header stamp nobody reads is not a stamp"


def test_every_component_has_a_non_empty_value():
    for k, v in DD_POLICY_BUNDLE.items():
        assert isinstance(v, str) and v.strip(), f"{k} has no version"


def test_the_bundle_survives_a_report_with_no_data():
    """It is a property of the RULES, not of the evidence — an empty report still had
    rules applied to it."""
    assert "Policy bundle:" in ARKDDReport().render_markdown()

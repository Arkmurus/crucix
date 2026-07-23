"""R-F2930 — provenance must be earned by content, not granted by which module ran.

R-F2899 stopped classifier templates reaching the Telegram channel by tagging every
signal with `why_action_provenance`, and gating publication on "source_adapter". But
golden_intel_bridge set that flag UNCONDITIONALLY for anything it normalised, with a
comment asking adapters to keep their text item-specific.

A comment is not a control. An adapter emitting a canned `why` would be labelled as
ARIA's own analysis and become publishable — the exact hole R-F2899 closed for
news_monitor, reopened one layer down. The flag now depends on the TEXT.

The failure this exists for is a real one that reached production: "Security conditions
may affect delivery risk, end-use risk, or market timing." was published-ready for a UN
News roundup. It describes every conflict item ever written and nothing in particular.
"""
from __future__ import annotations

import pytest

from aria_service.intel.golden_intel_bridge import _is_item_specific, _normalize_finding_to_signal


# ── the discriminator ──────────────────────────────────────────────────────

@pytest.mark.parametrize("why,target,entities", [
    ("BKM Budapesti Közművek Nonprofit Zrt. (Hungary) — value undisclosed, "
     "deadline 2026-08-11. Matched products: surveillance_systems.",
     "BKM Budapesti Közművek Nonprofit Zrt.", {}),
    ("New OFAC designation. Programs: UKRAINE-EO13662. Listed 2026-07-20.", "VTB Bank", {}),
    ("Rosoboronexport: now appears on a sanctions list", "Rosoboronexport", {}),
    ("Newly flagged in the Ukraine programme", "x",
     {"oems": ["Rosoboronexport"], "countries": ["Ukraine"]}),
])
def test_rf2930_item_specific_text_is_accepted(why, target, entities):
    """A date, a value, the target's name or an extracted entity — any one is enough.
    Deliberately permissive: a false negative suppresses a real finding, and the
    channel's completeness and evidence-URL checks catch the rest."""
    assert _is_item_specific(why, target, entities) is True


@pytest.mark.parametrize("why", [
    "Security conditions may affect delivery risk, end-use risk, or market timing.",
    "Procurement activity may create a near-term commercial window.",
    "Compliance status may have changed and should be checked before engagement.",
    "Budget movement can signal upcoming procurement or programme acceleration.",
    "",
    "   ",
    "short",
])
def test_rf2930_category_prose_is_rejected(why):
    """These are the real _SIGNAL_RULES templates. Each describes ANY event of its
    type, which is precisely what must never publish as ARIA's analysis."""
    assert _is_item_specific(why, "Congo", {}) is False


def test_rf2930_empty_why_is_never_item_specific():
    """A finding with nothing to say about itself cannot be ARIA's analysis."""
    assert _is_item_specific("", "Acme Ltd", {"countries": ["Poland"]}) is False


# ── the flag it drives ─────────────────────────────────────────────────────

def _finding(**over) -> dict:
    base = {
        "source_key": "tender_monitor", "source": "Procurement: TED",
        "signal_type": "active_tender", "priority": "HIGH", "confidence": "MEDIUM",
        "source_tier": "tier_1a", "title": "Hungary - surveillance systems",
        "why_it_matters": "BKM Budapesti Közművek (Hungary) — deadline 2026-08-11.",
        "recommended_action": "Assess bid/no-bid — review scope, eligibility and deadline.",
        "target": "BKM Budapesti Közművek", "evidence_url": "https://ted.europa.eu/x",
        "ref": "t1",
    }
    base.update(over)
    return base


def test_rf2930_adapter_finding_with_real_analysis_earns_source_adapter():
    sig = _normalize_finding_to_signal(_finding())
    assert sig["why_action_provenance"] == "source_adapter"


def test_rf2930_adapter_finding_with_canned_why_fails_closed():
    """The whole point: being produced by an adapter is no longer sufficient."""
    sig = _normalize_finding_to_signal(_finding(
        why_it_matters="Procurement activity may create a near-term commercial window.",
        target="signal",
    ))
    assert sig["why_action_provenance"] == "classifier_template", (
        "an adapter emitting canned prose was still labelled as ARIA's own analysis — "
        "it would be publishable to the channel"
    )


def test_rf2930_adapter_finding_with_no_why_fails_closed():
    sig = _normalize_finding_to_signal(_finding(why_it_matters="", target="signal"))
    assert sig["why_action_provenance"] == "classifier_template"


def test_rf2930_the_gate_actually_blocks_the_canned_finding():
    """End-to-end on the property that matters: a canned adapter finding must not be
    distribution-ready, whatever its source tier or priority claim."""
    canned = _normalize_finding_to_signal(_finding(
        why_it_matters="Security conditions may affect delivery risk.", target="signal"))
    assert canned["why_action_provenance"] == "classifier_template"

    real = _normalize_finding_to_signal(_finding())
    assert real["why_action_provenance"] == "source_adapter"
    # And the flag is the only difference — the fix must not have altered grading.
    assert canned["priority"] == real["priority"]
    assert canned["source_tier"] == real["source_tier"]

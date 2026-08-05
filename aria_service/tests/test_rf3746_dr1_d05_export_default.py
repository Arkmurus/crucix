"""R-F3746 — DR-1 **D-05 adjudicated**: the export classifier does not default to clean.

D-05 was UNADJUDICATED: "Export-control classifier (no default 'civilian')", P1,
suspected `aria_service/intel/tech_classifier.py`, no fixture. Adjudicated the same
way as D-03 (R-F3745) — from this repo's code, because the entry names a testable
invariant rather than a symptom needing the missing register.

THE ADJUDICATION: the invariant is already satisfied. `classify_export_control`
does NOT assert "civilian" when nothing matched. Its no-hits branch
(`tech_classifier.py:639-640,650`) emits THREE separate honesty signals:

  recommendation = "civilian or unclassified"   ambiguous, not an assertion
  confidence     = 0.40                          low, and explicitly so
  notes         += "No export-control hits — verify classification with
                    specific product datasheet"

That is the correct shape. "No hits" means the text did not match a controlled
category — which is NOT the same as the item being civilian, and the code says so
rather than resolving the ambiguity in the reader's favour. A bare "civilian"
would be the false-clean D-05 fears: an UNEXAMINED item read as CLEARED.

WHAT REMAINS A REAL RISK, and why this fixture exists anyway: the string contains
the word "civilian". Any downstream consumer that substring-matches, truncates to
the first word, or maps this to a boolean turns "civilian or unclassified" into
"civilian" and re-creates the defect outside this module. The renderer currently
prints the full string (`dd_schema.py` core-section "Export control" ->
`ec.get("recommendation")`), so it is honest today. These checks pin all three
signals so a future simplification cannot quietly drop them.

Run: python -m pytest aria_service/tests/test_rf3746_dr1_d05_export_default.py -v
"""
from __future__ import annotations

import pytest

from aria_service.intel.tech_classifier import classify_export_control

#: text with no controlled-category tokens at all
BENIGN = "office stationery, paper clips and A4 printer paper"


def test_no_hits_does_not_assert_civilian():
    """THE INVARIANT: an unexamined item must not be reported as civilian."""
    out = classify_export_control(BENIGN)
    rec = str(out.get("recommendation") or "")
    assert rec != "civilian", (
        "the no-hits branch asserts a bare 'civilian'. No export-control hits "
        "means the text did not match a controlled category — NOT that the item "
        "is civilian. Asserting it converts UNEXAMINED into CLEARED."
    )
    assert "unclassified" in rec.lower(), (
        f"recommendation {rec!r} no longer declares the ambiguity; D-05's "
        f"false-clean is back the moment this reads as a clean verdict"
    )


def test_no_hits_carries_low_confidence():
    out = classify_export_control(BENIGN)
    conf = out.get("confidence")
    assert conf is not None, "confidence disappeared — the caller cannot weigh this"
    assert float(conf) <= 0.5, (
        f"confidence {conf} on a no-hits classification is too high. The signal "
        f"that nothing was found must not read as a confident finding."
    )


def test_no_hits_tells_the_reader_to_verify():
    out = classify_export_control(BENIGN)
    notes = " ".join(str(n) for n in (out.get("notes") or []))
    assert "no export-control hits" in notes.lower(), (
        f"the explicit no-hits note was dropped; notes={notes!r}. Without it a "
        f"reader cannot tell 'checked and clear' from 'matched nothing'."
    )
    assert "verify" in notes.lower(), (
        "the note no longer directs the reader to verify against a datasheet — "
        "that instruction is what stops a non-match being treated as clearance"
    )


def test_a_real_controlled_item_is_still_classified():
    """Negative control: the guard must not be satisfiable by a broken classifier.

    Without this, a classify_export_control that returned "unclassified" for
    EVERYTHING would pass all three checks above.
    """
    out = classify_export_control(
        "shipment of 5.56x45mm rifle ammunition and night vision optics")
    rec = str(out.get("recommendation") or "").lower()
    hits = (out.get("wassenaar_ml") or []) + (out.get("usml") or []) + \
           (out.get("ear_ccl") or [])
    assert hits or rec not in ("", "civilian or unclassified"), (
        f"a controlled item classified as {rec!r} with no regime hits — the "
        f"classifier is not discriminating, which would make the checks above "
        f"vacuously true"
    )

"""R-F3550 — the footer said "Confidence" over an EVIDENCE-STATUS vocabulary.

`confidence_tag` takes CONFIRMED / ASSESSED / PROBABLE / UNCERTAIN / UNVERIFIED. Those
describe how a claim was ESTABLISHED — corroborated against a primary source, inferred,
not checked — not how LIKELY it is to be true. Two different axes shared one word, on the
line that closes the report.

The misreading it invites is asymmetric and dangerous in one direction: a reader who
takes "Confidence: UNVERIFIED" as a probability hears "probably fine", when UNVERIFIED
means nobody looked. That is the same false-clean shape this session has removed
elsewhere, expressed as vocabulary.

The VALUE and its computation are untouched — still the weakest tag across all sections,
so the headline cannot oversell. Only the name it is given, plus a legend, on the same
principle as the evidence grade (R-F3549): a label a reader cannot audit is not a
disclosure.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import ARKDDReport


def _md():
    r = ARKDDReport()
    r.identity.entity_name = "Test Ltd"
    return r.render_markdown()


def test_the_footer_no_longer_calls_it_confidence():
    md = _md()
    assert "*Confidence:*" not in md, (
        "the footer still labels evidence-status values as confidence")
    assert "*Evidence status:*" in md


def test_the_legend_states_the_distinction_explicitly():
    md = _md()
    assert "not how likely they are to be true" in md, (
        "the legend must say what the scale is NOT, or the misreading persists")


@pytest.mark.parametrize("term,gloss", [
    ("CONFIRMED", "corroborated against a primary source"),
    ("ASSESSED", "derived from evidence gathered"),
    ("PROBABLE", "inferred, not directly evidenced"),
    ("UNCERTAIN", "evidence conflicts or is thin"),
    ("UNVERIFIED", "not checked"),
])
def test_every_value_in_the_vocabulary_is_glossed(term, gloss):
    """A partial legend is worse than none — the unglossed term looks like the odd one."""
    md = _md()
    assert term in md and gloss in md


def test_the_weakest_tag_rule_is_still_stated():
    """R-F(header): the report carries the WEAKEST section tag so the headline cannot
    oversell. If the legend dropped that, a reader would take the tag as an average."""
    assert "WEAKEST status of any section" in _md()


def test_the_value_itself_is_unchanged():
    """A relabel must not re-derive anything."""
    r = ARKDDReport()
    assert r.confidence_tag == "ASSESSED"
    r.confidence_tag = "UNVERIFIED"
    assert "*Evidence status:* [UNVERIFIED]" in r.render_markdown()

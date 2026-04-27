"""Regression test: CORE_MASTERY_TAGS must have a working lift path.

The 2026-04-27 investigation surfaced four CORE_MASTERY_TAGS (sanctions,
nato_standards, strategic_geography, export_control) defined in
CORE_MASTERY_TAGS but missing from _TOPIC_PATTERNS. detect_topics()
literally could not emit them, so reading_session() never called
update_mastery() with these tags, and they sat at the 0.491 floor
(= INITIAL_MASTERY 0.5 minus one quiz-fail bump).

Each tag has ONE of three lift paths:
  - regex via _TOPIC_PATTERNS  (capability tags)
  - Unicode-script counters in detect_topics()  (lang:ru, lang:zh, lang:ar)
  - brain_hook.absorb(extra_topics=[...])       (lang:pt, lang:fr, lang:es —
    Latin-script langs that web_search.search_multilingual lifts)

This test guards the regex+script paths. The brain_hook path for the
Latin-script lang tags is exercised by other integration tests against
web_search.
"""
from __future__ import annotations

import pytest

from aria_service.intel.student import CORE_MASTERY_TAGS, detect_topics


# Tags with a regex or script-detection path (i.e. detect_topics MUST emit
# them from representative text). Latin-script lang tags are lifted by a
# different code path (web_search → brain_hook.absorb) and are not in this
# set.
DETECTABLE_TAGS = {
    "lang:ar", "lang:ru", "lang:zh",  # Unicode-script counters
    "sanctions", "nato_standards", "strategic_geography", "export_control",
}

LATIN_SCRIPT_LANG_TAGS = {"lang:pt", "lang:fr", "lang:es"}

# Representative real-world text for each detectable tag. The kind of
# phrasing that appears in defense procurement / OSINT feeds (TASS, Janes,
# ECJU notices, EU consolidated sanctions lists, etc.).
REPRESENTATIVE_TEXT: dict[str, str] = {
    "lang:ar": "أعلنت وزارة الدفاع الإماراتية عن اتفاقية جديدة لشراء أنظمة دفاع جوي متقدمة من خلال برنامج التعاون الصناعي الأوروبي.",
    "lang:ru": "Министерство обороны России объявило о новом контракте на поставку зенитных ракетных комплексов через программу европейского промышленного сотрудничества.",
    "lang:zh": "中国国防部宣布了一项新的合同，通过欧洲工业合作计划采购防空导弹系统。该合同涉及多个先进型号。",
    "sanctions": "OFAC adds Rostec subsidiary to the SDN list following CAATSA designation; secondary sanctions risk for European intermediaries identified via OpenSanctions.",
    "nato_standards": "Procurement specification cites STANAG 4569 Level 3 protection and AQAP 2110 quality assurance; NSN to be assigned post-acceptance per NATO codification.",
    "strategic_geography": "The Bab-el-Mandeb strait remains a critical maritime chokepoint for Red Sea trade; forward posture in the Horn of Africa is under review.",
    "export_control": "ECCN classification for the Wassenaar-listed dual-use sensors requires a SIEL under UK export-control law before re-export to a third country.",
}


@pytest.mark.parametrize("tag", sorted(DETECTABLE_TAGS))
def test_detectable_tag_is_actually_detected(tag: str):
    """Each tag in DETECTABLE_TAGS must come back from detect_topics() when
    fed representative text. Adding a new capability tag to CORE_MASTERY_TAGS
    + DETECTABLE_TAGS without a pattern (or representative-text fixture)
    fails this test, preventing the silent-floor regression the live
    2026-04-27 review exposed."""
    text = REPRESENTATIVE_TEXT.get(tag)
    assert text, f"No representative text fixture for tag {tag!r}"
    detected = detect_topics(text)
    assert tag in detected, (
        f"Tag {tag!r} was not detected from representative text. "
        f"detect_topics returned {detected!r}. Either the regex in "
        f"_TOPIC_PATTERNS is wrong or the script-detection threshold is "
        f"too high. reading_session() will never lift this tag's mastery "
        f"until detect_topics() can emit it."
    )


def test_no_core_mastery_tag_is_left_orphaned():
    """Every CORE_MASTERY_TAG must have a documented lift path.
    DETECTABLE_TAGS covers the regex+script paths; LATIN_SCRIPT_LANG_TAGS
    is lifted via brain_hook.absorb extra_topics from web_search. If a new
    tag is added to CORE_MASTERY_TAGS but neither bucket, this test fails
    so the operator knows the tag will sit at the 0.5 floor forever."""
    accounted_for = DETECTABLE_TAGS | LATIN_SCRIPT_LANG_TAGS
    orphaned = set(CORE_MASTERY_TAGS) - accounted_for
    assert not orphaned, (
        f"CORE_MASTERY_TAGS {sorted(orphaned)!r} have no documented lift "
        f"path. Add them to either DETECTABLE_TAGS (with a pattern in "
        f"_TOPIC_PATTERNS or script counter in detect_topics) or "
        f"LATIN_SCRIPT_LANG_TAGS (and ensure web_search.search_multilingual "
        f"forwards them via brain_hook.absorb extra_topics)."
    )

"""R-F800 (2026-05-22): Phase-A core-mastery topics added to HARD_FLOORS.

Pre-R-F800 'sanctions' (and the other core-mastery tags) were not
in the HARD_FLOORS dict — the existing post-update check used
`HARD_FLOORS.get(topic, 0.50)` so the default kicked in. Live log
2026-05-22 16:00:35 UTC: `BREACH: sanctions (41% < 50%)` — the 50%
came from the default.

R-F796's clamping logic used the same default; consistent but
implicit. R-F800 makes the floors explicit so:
- HARD_FLOORS is the single source of truth (no implicit defaults)
- Future code that iterates HARD_FLOORS for reporting / dashboards
  sees these load-bearing topics
- A topic dropping below floor is signalled with the same 50% value
  used elsewhere
"""
from __future__ import annotations

from aria_service.intel import student


def test_rf800_sanctions_in_hard_floors():
    assert "sanctions" in student.HARD_FLOORS, (
        "R-F800: 'sanctions' is a Phase-A core mastery tag — must be "
        "explicitly in HARD_FLOORS, not relying on the dict.get default."
    )
    assert student.HARD_FLOORS["sanctions"] == 0.50


def test_rf800_other_core_tags_in_hard_floors():
    """The four cross-cutting capability tags from CORE_MASTERY_TAGS
    (sanctions, nato_standards, strategic_geography, export_control)
    should all be in HARD_FLOORS now."""
    for tag in ("sanctions", "nato_standards", "strategic_geography", "export_control"):
        assert tag in student.HARD_FLOORS, (
            f"R-F800: '{tag}' should be in HARD_FLOORS"
        )
        assert student.HARD_FLOORS[tag] == 0.50


def test_rf800_existing_floors_preserved():
    """Regression guard — the pre-R-F800 floors are unchanged."""
    expected = {
        "procurement": 0.65, "compliance": 0.70, "osint": 0.65,
        "technical": 0.65, "market_intel": 0.65,
        "competitor_intel": 0.65, "geopolitics": 0.60,
        "finance": 0.60, "relationships": 0.55,
        "legal": 0.70, "general": 0.50,
    }
    for topic, floor in expected.items():
        assert student.HARD_FLOORS[topic] == floor, (
            f"R-F800: regression — HARD_FLOORS['{topic}'] was {floor}, "
            f"is now {student.HARD_FLOORS[topic]}"
        )

"""R-F4063 (C-113) — the 82% mastery headline is ceiling-saturated and
floor-clamped, and said neither.

Measured on aria-intel 2026-08-16, the ten `CORE_MASTERY_TAGS`:

    lang:pt   0.980  samples 3794  correct 3793  wrong  1   <- MASTERY_CEILING
    lang:ar   0.980  samples 1089  correct 1085  wrong  4   <- ceiling
    lang:fr   0.980  samples  378  correct  377  wrong  1   <- ceiling
    lang:es   0.980  samples 1029  correct 1029  wrong  0   <- ceiling, 100%
    lang:zh   0.980  samples  473  correct  462  wrong 11   <- ceiling
    lang:ru   0.968  samples  293  correct  290  wrong  3
    sanctions 0.845  samples 3092  correct 2945  wrong 147  <- the one free score
    nato_standards      0.500  samples  68   <- HARD_FLOORS 0.50
    strategic_geography 0.500  samples  76   <- HARD_FLOORS 0.50
    export_control      0.509  samples 281   <- HARD_FLOORS 0.50
                                        mean = 0.8222 -> the 82% headline

Six of ten are LANGUAGE tags welded to the ceiling. A grader that returns
"correct" on 3793 of 3794 samples is measuring participation, not comprehension,
and a tag at its ceiling cannot move, so it carries no information. Three more
sit at exactly their hard floor despite 68 / 76 / 281 graded observations at
~90% correct — arithmetically impossible under `MASTERY_LR_POSITIVE = 0.18`
unless something is pushing them down (C-112's hourly calibration drop is the
measured candidate: `crucix:calibration:last_correction` had fired 24 minutes
before the reading).

So the number driving Phase A gate #1, `autonomy_scorer` and
`calibration_review` is held up at one end by a ceiling and at the other by a
floor, with exactly ONE freely-moving capability tag underneath it.

And `0.500` means two contradictory things in the same system: "never measured"
(the `INITIAL_MASTERY` scaffold that /health's `core_mastery_all_scaffolded`
check looks for) and "clamped at floor after 68 observations". `samples`
separates them, and nothing was reporting it.

**The VALUE is deliberately unchanged.** §1 forbids closing a gate by measuring
less, and dropping the language tags would RAISE the headline, not lower it.
This publishes the composition so the number can be read for what it is.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def live_shape(monkeypatch):
    """The exact live mastery cache from 2026-08-16."""
    from aria_service.intel import student

    cache = {
        "lang:pt": {"score": 0.98, "samples": 3794, "correct": 3793, "wrong": 1},
        "lang:ar": {"score": 0.98, "samples": 1089, "correct": 1085, "wrong": 4},
        "lang:fr": {"score": 0.98, "samples": 378, "correct": 377, "wrong": 1},
        "lang:es": {"score": 0.98, "samples": 1029, "correct": 1029, "wrong": 0},
        "lang:zh": {"score": 0.98, "samples": 473, "correct": 462, "wrong": 11},
        "lang:ru": {"score": 0.968, "samples": 293, "correct": 290, "wrong": 3},
        "sanctions": {"score": 0.845, "samples": 3092, "correct": 2945, "wrong": 147},
        "nato_standards": {"score": 0.5, "samples": 68, "correct": 65, "wrong": 3},
        "strategic_geography": {"score": 0.5, "samples": 76, "correct": 60, "wrong": 16},
        "export_control": {"score": 0.509, "samples": 281, "correct": 255, "wrong": 26},
    }

    async def _load():
        return cache

    monkeypatch.setattr(student, "_load_mastery", _load)
    return cache


@pytest.mark.asyncio
async def test_headline_value_is_unchanged(live_shape):
    """Regression guard on the fix itself: reporting composition must not move
    the number. Removing the language tags would RAISE it to ~0.63->0.71 and
    that is exactly the kind of 'measure less' §1 forbids."""
    from aria_service.intel import student
    report = await student.get_mastery_report()
    assert report["core_mastery"] == pytest.approx(0.822, abs=0.001)
    assert report["headline_mastery"] == pytest.approx(0.822, abs=0.001)


@pytest.mark.asyncio
async def test_composition_names_the_ceiling_saturated_tags(live_shape):
    from aria_service.intel import student
    report = await student.get_mastery_report()
    comp = report["core_mastery_composition"]

    assert set(comp["at_ceiling"]) == {
        "lang:pt", "lang:ar", "lang:fr", "lang:es", "lang:zh"}, comp["at_ceiling"]
    assert comp["ceiling"] == student.MASTERY_CEILING
    assert comp["total"] == len(student.CORE_MASTERY_TAGS)


@pytest.mark.asyncio
async def test_a_floored_tag_with_samples_is_reported_as_clamped(live_shape):
    """0.500 after 68 graded observations is a clamp, not a scaffold. The two
    are the same number and the opposite situation."""
    from aria_service.intel import student
    report = await student.get_mastery_report()
    floored = {f["topic"]: f for f in
               report["core_mastery_composition"]["at_floor"]}

    assert set(floored) == {"nato_standards", "strategic_geography",
                            "export_control"}, sorted(floored)
    for topic in floored:
        assert floored[topic]["clamped"] is True, floored[topic]
        assert floored[topic]["floor"] == 0.50, floored[topic]
        assert floored[topic]["samples"] > 0


@pytest.mark.asyncio
async def test_an_untouched_tag_is_floored_but_not_clamped(monkeypatch):
    """The other reading of 0.500: never measured. It must be distinguishable
    from the clamped case, which is the whole point."""
    from aria_service.intel import student

    async def _load():
        return {t: {"score": student.INITIAL_MASTERY, "samples": 0,
                    "correct": 0, "wrong": 0}
                for t in student.CORE_MASTERY_TAGS}

    monkeypatch.setattr(student, "_load_mastery", _load)
    report = await student.get_mastery_report()
    floored = report["core_mastery_composition"]["at_floor"]
    assert floored, "a scaffolded core set must still be reported"
    assert all(f["clamped"] is False for f in floored), floored


@pytest.mark.asyncio
async def test_only_two_core_tags_move_freely_in_the_live_shape(live_shape):
    """The finding in one assertion: eight of ten cells are pinned."""
    from aria_service.intel import student
    report = await student.get_mastery_report()
    comp = report["core_mastery_composition"]
    assert set(comp["freely_measured"]) == {"lang:ru", "sanctions"}, comp
    assert len(comp["at_ceiling"]) + len(comp["at_floor"]) == 8, comp


@pytest.mark.asyncio
async def test_the_floor_band_is_published(live_shape):
    """`export_control` sat 0.9pp above its 0.50 floor with 281 samples. A
    strict equality test would have called that freely measured and understated
    the finding by a rounding error — so the band is a stated judgement, and it
    has to be visible to be disagreed with."""
    from aria_service.intel import student
    comp = (await student.get_mastery_report())["core_mastery_composition"]
    assert comp["floor_band"] == 0.02
    floored = {f["topic"] for f in comp["at_floor"]}
    assert "export_control" in floored, comp["at_floor"]


@pytest.mark.asyncio
async def test_a_low_but_moving_score_is_not_called_floored(monkeypatch):
    """The band must not sweep in a genuinely low score that is still moving,
    or 'at floor' stops meaning anything."""
    from aria_service.intel import student

    async def _load():
        base = {t: {"score": 0.72, "samples": 100, "correct": 72, "wrong": 28}
                for t in student.CORE_MASTERY_TAGS}
        # 0.56 against a 0.50 floor: low, but 6pp clear and demonstrably moving.
        base["nato_standards"] = {"score": 0.56, "samples": 40,
                                  "correct": 25, "wrong": 15}
        return base

    monkeypatch.setattr(student, "_load_mastery", _load)
    comp = (await student.get_mastery_report())["core_mastery_composition"]
    assert comp["at_floor"] == [], comp["at_floor"]
    assert "nato_standards" in comp["freely_measured"]


@pytest.mark.asyncio
async def test_a_healthy_spread_is_not_flagged(monkeypatch):
    """The report must be able to say "nothing is pinned", or it is not a
    measurement either."""
    from aria_service.intel import student

    async def _load():
        return {t: {"score": 0.72, "samples": 100, "correct": 72, "wrong": 28}
                for t in student.CORE_MASTERY_TAGS}

    monkeypatch.setattr(student, "_load_mastery", _load)
    comp = (await student.get_mastery_report())["core_mastery_composition"]
    assert comp["at_ceiling"] == []
    assert comp["at_floor"] == []
    assert len(comp["freely_measured"]) == len(student.CORE_MASTERY_TAGS)


# ── the page must read the key the backend actually publishes ──────────────

def test_panel_reads_the_published_field_name():
    """R-F4076 — the row read `q.core_composition`; /health publishes
    `core_mastery_composition`. The panel rendered nothing on the live page
    while the backend served the data correctly.

    `_coreCompositionRow` returns '' for an absent payload by design, so an
    older backend degrades instead of rendering an empty claim — which is
    precisely why a wrong key could not announce itself. The previous guard
    asserted the CALL existed and was blind to the name.

    This asserts the cross-file contract: whatever key the page reads must be a
    key /health puts inside `quality`.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    page = (repo / "public" / "aria-brain.html").read_text(encoding="utf-8")
    routes = (repo / "aria_service" / "routes" / "aria.py").read_text(
        encoding="utf-8")

    m = re.search(r"_coreCompositionRow\(q\.([A-Za-z_]+)\)", page)
    assert m, "the Quality panel no longer renders the composition row"
    key = m.group(1)

    assert f'"{key}": mastery.get(' in routes or f'"{key}":' in routes, (
        f"the page reads q.{key} but /health does not publish that key inside "
        "quality — the row will silently render nothing")

    # And pin the actual name, so a rename has to be deliberate on both sides.
    assert key == "core_mastery_composition", key

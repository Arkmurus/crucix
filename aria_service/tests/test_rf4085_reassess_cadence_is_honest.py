"""R-F4085 (C-132) — a task named HOURLY runs every six hours, and I built a
threshold on the name.

`tasks.yaml` contradicted itself inside a single block:

    comment:     "every hour on the hour"
    name:        "Ecosystem reassessment (hourly)"
    description: "Every 6 hours"          <- the only accurate line
    cron:        "0 */6 * * *"            <- 6-hourly
    enabled:     "safe to fire hourly"

Measured live 2026-08-17: `crucix:aria:operating_mode:last_evaluated_at` =
`00:00:16Z`, age 3.4h — correct for a 6-hourly schedule, and the stamp R-F4065
added was working exactly as intended.

Two things were built on the wrong premise, both mine:

* **C-112 relocated the mastery correction onto this task** because the name
  said hourly. Landing on a 6-hourly schedule is SAFE (fewer corrections, never
  more) and `_CORRECT_COOLDOWN` still floors it, but the record said something
  untrue about how often mastery moves.
* **R-F4065's "Last evaluated" warned above 3h**, so it would have shown WARN
  for most of every 6-hour cycle. A verdict that cries wolf is one nobody reads
  (R-F4024) — and it was calibrated against the task's NAME rather than its cron.

The cron is authoritative and unchanged: nobody asked for this to run more
often, and quietly making it hourly to match a label would be changing behaviour
to protect a name. The prose is corrected instead, and the id is kept because
tests and persisted task state reference it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]


def _task_block() -> str:
    y = (REPO / "aria_service" / "autonomous" / "tasks.yaml").read_text(
        encoding="utf-8")
    i = y.index("- id: HOURLY-ECOSYSTEM-REASSESS")
    return y[max(0, i - 500):i + 700]


def test_the_cron_is_still_six_hourly():
    """Pin the behaviour so a later 'fix' cannot quietly make it hourly to
    match the name."""
    assert 'cron: "0 */6 * * *"' in _task_block()


def test_no_prose_in_the_block_claims_hourly():
    block = _task_block()
    # The id itself is historical and deliberately kept.
    prose = block.replace("HOURLY-ECOSYSTEM-REASSESS", "")
    assert not re.search(r"\bhourly\b", prose, re.I), (
        "the block still claims hourly somewhere while the cron is 6-hourly:\n"
        + prose)


def test_the_module_docstring_matches_the_cron():
    src = (REPO / "aria_service" / "intel" / "ecosystem_reassess.py").read_text(
        encoding="utf-8")
    head = src[:600].replace("HOURLY-ECOSYSTEM-REASSESS", "")
    assert "every 6 hours" in head.lower(), head[:300]
    assert not re.search(r"fires hourly", head, re.I), head[:300]


def test_the_panel_threshold_allows_a_full_cycle():
    """A 6-hourly task must not be flagged for being 4 hours old."""
    page = (REPO / "public" / "aria-brain.html").read_text(encoding="utf-8")
    i = page.index("'Last evaluated'")
    window = page[i:i + 500]
    m = re.search(r"_ageHours\(d\.last_evaluated_at\) > (\d+)", window)
    assert m, window[:300]
    hours = int(m.group(1))
    assert hours >= 12, (
        f"warns at {hours}h on a 6-hourly task — that is WARN for most of every "
        "cycle, which is the cry-wolf shape R-F4024 records")


def test_an_absent_stamp_is_still_bad():
    """Relaxing the threshold must not relax the real signal: no stamp at all
    still means the only route out of DEGRADED has not run."""
    page = (REPO / "public" / "aria-brain.html").read_text(encoding="utf-8")
    i = page.index("'Last evaluated'")
    window = page[i:i + 500]
    assert "'bad'" in window, window[:300]
    assert "not in the last 72h" in window

"""R-F4323 / C-271 — "absent from the registry" is not "operator decides".

MEASURED LIVE 2026-08-25 on aria-intel, 16 occurrences inside a 1.4-hour
ledger window:

    [aria_coder] R-F4115: 4 SEVERE gap(s) are not auto_fixable —
    gap_type(s) ['missing_capability'] are absent from AUTONOMY_LEVEL.
    This is a registry gap, not a triage decision.

`missing_capability` is NOT absent from AUTONOMY_LEVEL. It is registered at
`gap_detector.py:96` as ``(False, True, False)`` — deliberately not
auto-fixable, deliberately requiring operator approval, with the comment
"operator decides". The same is true of `opportunity`. So the loudest
recurring warning the self-coding loop emits describes a working policy as
a rotted registry.

WHY THIS MATTERS MORE THAN A WRONG STRING. R-F4115 (C-148) added this
warning for a real and dangerous case: `auto_fixable` is derived from
``AUTONOMY_LEVEL.get(type, (False, ...))``, so a renamed or unknown
gap_type silently returns False and a CRITICAL gap is dropped while the
loop still looks healthy — the R-F3791 goes-blind-rather-than-fails shape.
That intent is correct and is preserved here.

But a warning that fires constantly on a deliberate policy is a warning
nobody reads, and it CANNOT distinguish the case it was built to catch:
when a gap_type really does rot out of the registry, the message will look
exactly like the 16 benign ones before it. A guard whose alarm is always on
has the same value as one that never fires (§1: a guard that cannot fail).

THE SPLIT IS THE FIX. Two populations, two different responses:
  * gap_type ABSENT from AUTONOMY_LEVEL  -> registry rot. WARN. Someone
    must add it, and until they do, severe gaps are being dropped silently.
  * gap_type PRESENT with auto_fixable=False -> working as designed. These
    are waiting on an operator, not on a registry edit. Report them as
    such, so §21e escalation stays visible without crying wolf.
"""
from __future__ import annotations

import logging
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.autonomous import self_coder as sc            # noqa: E402
from aria_service.autonomous.gap_detector import (              # noqa: E402
    AUTONOMY_LEVEL, Gap, GapSeverity, GapType,
)

#: Matched case-insensitively everywhere: the guard is about the PHRASE, so an
#: emphasis edit to the log line must not silently disarm these tests.
_REGISTRY_PHRASE = "absent from autonomy_level"


def _gap(gap_type: str, severity=GapSeverity.HIGH, module="widget") -> Gap:
    return Gap(
        gap_id=f"g-{gap_type}",
        gap_type=gap_type,
        severity=severity,
        title=f"a {gap_type} gap",
        description="",
        module=module,
    )


# -- the premise this whole fix rests on --------------------------------

def test_missing_capability_is_registered_not_absent():
    """If this ever fails, the live warning was right and this fix is wrong."""
    assert GapType.MISSING_CAPABILITY in AUTONOMY_LEVEL, (
        "missing_capability is absent from AUTONOMY_LEVEL — the premise of "
        "R-F4323 no longer holds; re-read before trusting the split"
    )
    auto_fixable, requires_approval, _ = AUTONOMY_LEVEL[GapType.MISSING_CAPABILITY]
    assert auto_fixable is False and requires_approval is True, (
        "missing_capability is registered as a deliberate operator-decides "
        "policy; that is what makes the 'registry gap' wording wrong"
    )


# -- THE CAPABILITY TEST ------------------------------------------------

def test_a_registered_operator_decides_gap_is_not_called_a_registry_gap(caplog):
    """THE LIVE SYMPTOM, verbatim: 4 severe missing_capability gaps."""
    gaps = [_gap(GapType.MISSING_CAPABILITY, module=f"m{i}") for i in range(4)]
    with caplog.at_level(logging.INFO):
        sc._report_unfixable_severe(gaps)
    text = caplog.text
    assert _REGISTRY_PHRASE not in text.lower(), (
        "a gap_type that IS registered was reported as absent from the "
        "registry — this is the live 16x/1.4h false alarm"
    )
    assert "registry gap" not in text.lower(), text


def test_a_genuinely_unregistered_type_still_warns(caplog):
    """THE GUARD MUST STILL FIRE. R-F4115 exists for a rotted registry, and
    a split that silenced that case would trade a false alarm for a blind
    spot — the strictly worse outcome."""
    gaps = [_gap("renamed_or_typo_gap_type", severity=GapSeverity.CRITICAL)]
    with caplog.at_level(logging.WARNING):
        sc._report_unfixable_severe(gaps)
    text = caplog.text
    assert _REGISTRY_PHRASE in text.lower(), (
        "an unregistered gap_type no longer raises the registry alarm — "
        "R-F4115's actual purpose has been lost"
    )
    assert "renamed_or_typo_gap_type" in text, "the offending type must be named"


def test_a_mixed_batch_separates_the_two_populations(caplog):
    """Both present at once: the unregistered one must be named in the
    registry warning, the registered one must not be."""
    gaps = [
        _gap(GapType.MISSING_CAPABILITY),
        _gap("ghost_type"),
        _gap(GapType.OPPORTUNITY, severity=GapSeverity.CRITICAL),
    ]
    with caplog.at_level(logging.INFO):
        sc._report_unfixable_severe(gaps)
    registry_lines = [r.getMessage() for r in caplog.records
                      if _REGISTRY_PHRASE in r.getMessage().lower()]
    assert registry_lines, "the unregistered type must still be reported"
    joined = " ".join(registry_lines)
    assert "ghost_type" in joined
    assert "missing_capability" not in joined, (
        "a registered operator-decides type was swept into the registry alarm"
    )
    assert "opportunity" not in joined


def test_operator_decides_gaps_are_still_reported_somewhere(caplog):
    """Quieting the false alarm must not make these gaps INVISIBLE. They are
    waiting on a human (§21e), so they still have to be said out loud."""
    gaps = [_gap(GapType.MISSING_CAPABILITY, module=f"m{i}") for i in range(4)]
    with caplog.at_level(logging.INFO):
        sc._report_unfixable_severe(gaps)
    assert "missing_capability" in caplog.text, (
        "severe operator-decides gaps vanished entirely — the fix silenced "
        "them instead of reclassifying them"
    )


def test_the_registry_alarm_stays_at_warning_level(caplog):
    """Severity carries the response. Registry rot is actionable now; an
    operator-decides backlog is not urgent, so they must not share a level."""
    with caplog.at_level(logging.INFO):
        sc._report_unfixable_severe([_gap("ghost_type")])
    levels = {r.levelno for r in caplog.records
              if _REGISTRY_PHRASE in r.getMessage().lower()}
    assert levels and max(levels) >= logging.WARNING, (
        f"registry rot demoted below WARNING ({levels}) — it is the case "
        "R-F4115 was written to surface"
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        sc._report_unfixable_severe([_gap(GapType.MISSING_CAPABILITY)])
    warn_or_worse = [r.getMessage() for r in caplog.records
                     if r.levelno >= logging.WARNING]
    assert not warn_or_worse, (
        f"a deliberate operator-decides policy still logs at WARNING+: "
        f"{warn_or_worse}"
    )


def test_nothing_severe_means_nothing_said(caplog):
    """No severe unfixable gaps: silence. A per-cycle line would be another
    ledger flood (the sanctions_coverage_degraded shape)."""
    with caplog.at_level(logging.INFO):
        sc._report_unfixable_severe([])
    assert not caplog.records, f"unexpected output: {caplog.text}"


def test_low_severity_gaps_do_not_raise_the_alarm(caplog):
    """R-F4115 scoped this to HIGH+ deliberately; a MEDIUM unregistered gap
    is ordinary triage, not rot."""
    with caplog.at_level(logging.INFO):
        sc._report_unfixable_severe([_gap("ghost_type", severity=GapSeverity.MEDIUM)])
    assert _REGISTRY_PHRASE not in caplog.text.lower()

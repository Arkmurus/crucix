"""R-F4115 (C-148) — CAPABILITY: the coder must count the gaps it drops.

Measured live on aria-intel 2026-08-17, two log lines seconds apart:

    [gap_detector] scan complete: 146 actionable gaps (from 216 raw signals)
    [aria_coder]   1 actionable gaps -- fixing top 6 (budget=6, cap=6)

145 gaps vanished between them with no instrument showing it. The reduction
happens at `self_coder.py`:

    if g.severity < GapSeverity.MEDIUM or not g.auto_fixable:
        continue

The two ADJACENT filters both log their counts — `protected_file_gaps`
("N gap(s) target protected files") and `pending_skip` ("R-F1294: skipped N").
This one logs nothing, so the two modules disagree on the word "actionable"
and nobody can see the disagreement.

WHY IT MATTERS EVEN THOUGH THE LOOP IS DRAINING: the loop IS working, so this
is not the §21c P0. But `auto_fixable` is a property derived from
`AUTONOMY_LEVEL.get(gap_type, (False, False, False))` — an unknown gap_type
silently returns False. If a gap_type were renamed, every gap of that type
would become invisible and the loop would go quiet while looking exactly as
healthy as it does today. That is the R-F3791 "guard goes blind rather than
fails" shape.

Run: python -m pytest aria_service/tests/test_rf4104_self_coder_drop_counter.py -v
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from aria_service.autonomous.gap_detector import Gap, GapSeverity


def _gap(gid, gap_type, severity):
    return Gap(gap_id=gid, gap_type=gap_type, severity=severity,
               title=f"gap {gid}", description="d", module="intel_ledger")


class _StubDetector:
    def __init__(self, gaps):
        self._gaps = gaps
        self.attempted = []

    async def scan(self):
        return self._gaps

    async def publish_latest(self, gaps):
        return None

    async def mark_attempted(self, gap_id):
        self.attempted.append(gap_id)


def _coder(gaps, monkeypatch):
    from aria_service.autonomous import self_coder as sc

    coder = sc.ARIACoder.__new__(sc.ARIACoder)      # skip heavy __init__
    coder.gap_detector = _StubDetector(gaps)

    async def _noop_scoreboard(*a, **kw):
        return None

    async def _noop_fix(gap, **kw):
        return sc.FixResult(success=False, fix_id="stub", gap_id=gap.gap_id,
                            failure_reason="stubbed in test")

    coder._record_scoreboard = _noop_scoreboard
    coder.fix_gap = _noop_fix
    return coder, sc


def _run_cycle(gaps, monkeypatch, caplog):
    coder, sc = _coder(gaps, monkeypatch)

    async def _budget(coder=False):
        return 6

    from aria_service.autonomous import safety as _safety
    monkeypatch.setattr(_safety, "remaining_fix_budget", _budget)
    caplog.set_level(logging.INFO, logger="aria.autonomous.self_coder")
    asyncio.run(coder._one_cycle())
    return "\n".join(r.getMessage() for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════
# THE DEFECT — 145 gaps must not vanish silently
# ══════════════════════════════════════════════════════════════════════

def test_the_dropped_gaps_are_counted(monkeypatch, caplog):
    """One actionable gap, many below the bar — the live 146-vs-1 shape."""
    gaps = [_gap("keep1", "module_bug", GapSeverity.HIGH)]
    gaps += [_gap(f"low{i}", "module_bug", GapSeverity.LOW) for i in range(9)]

    logged = _run_cycle(gaps, monkeypatch, caplog)

    assert "9" in logged, (
        "9 of 10 gaps were dropped below the severity/auto_fixable bar and no "
        "log line said so. The gap_detector reports its count and the coder "
        "reports a different one; nothing reconciles them."
    )
    assert "below" in logged.lower() or "dropped" in logged.lower() or \
           "not actionable" in logged.lower(), (
        "the drop needs a NAMED counter, like the two adjacent filters have"
    )


def test_the_two_drop_reasons_are_distinguishable(monkeypatch, caplog):
    """'Too minor to fix' and 'this type is not auto-fixable at all' demand
    different responses — one is fine, the other can mean a rotted gap_type."""
    gaps = [_gap("keep1", "module_bug", GapSeverity.HIGH),
            _gap("low1", "module_bug", GapSeverity.LOW),
            _gap("unknown1", "a_type_that_does_not_exist", GapSeverity.CRITICAL)]

    logged = _run_cycle(gaps, monkeypatch, caplog)

    low = "severity" in logged.lower()
    nofix = "auto_fixable" in logged.lower() or "not auto-fixable" in logged.lower()
    assert low and nofix, (
        "a CRITICAL gap dropped for an unrecognised gap_type is a rotted "
        "registry, not a triage decision — it must not be pooled with "
        "low-severity noise"
    )


# ══════════════════════════════════════════════════════════════════════
# THE GUARD — do not widen what the coder attempts
# ══════════════════════════════════════════════════════════════════════

def test_the_bar_itself_is_unchanged(monkeypatch, caplog):
    """This is an OBSERVABILITY fix. A coder that starts attempting LOW or
    non-auto-fixable gaps is a behaviour change nobody asked for."""
    gaps = [_gap("keep1", "module_bug", GapSeverity.HIGH),
            _gap("low1", "module_bug", GapSeverity.LOW),
            _gap("unknown1", "a_type_that_does_not_exist", GapSeverity.CRITICAL)]
    coder, sc = _coder(gaps, monkeypatch)

    async def _budget(coder=False):
        return 6

    from aria_service.autonomous import safety as _safety
    monkeypatch.setattr(_safety, "remaining_fix_budget", _budget)
    asyncio.run(coder._one_cycle())

    assert coder.gap_detector.attempted == ["keep1"], (
        f"the coder attempted {coder.gap_detector.attempted} — the bar moved"
    )

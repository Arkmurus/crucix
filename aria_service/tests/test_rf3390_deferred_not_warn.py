"""R-F3390 — the diagnostic could never read GREEN, so AMBER stopped meaning anything.

THE DEFECT. `run_diagnostic()` computes:

    overall = "RED" if critical_fails else ("AMBER" if fail_count or warn_count else "GREEN")

and `_check_env_var` returns WARN for ANY unset variable. Two modules are
permanently in that state by DESIGN, not by fault:

  - `acled` (ACLED_EMAIL) — operator-DEFERRED: CLAUDE.md §18 records "we won't be
    signing up to it as yet until we have the MVP launched."
  - `worldbank_debarred` (WORLDBANK_SUBSCRIPTION_KEY) — the module's own docstring
    records a completed investigation: apigwext returns 403, there is no
    self-service signup, and OpenSanctions covers the same debarment signal.

So the health surface reported AMBER 76/2/0 indefinitely, and no amount of
correct engineering could move it. A gauge that cannot reach green is a gauge
people stop reading — the same cry-wolf failure already fixed for the SearXNG
"all engines blocked" warning (R-F3361) and the crawl DNS-miss gap.

THE FIX IS A DISTINCT STATE, NOT A SUPPRESSION. `DEFERRED` is a fourth outcome
alongside PASS/WARN/FAIL. It is:

  - EXPLICITLY DECLARED per module with a written reason — never inferred, so
    nothing becomes invisible by accident;
  - COUNTED and rendered in its own bucket, so a reader sees exactly what is
    parked and why;
  - NARROW — it applies ONLY to the expected checks (a missing credential, and
    the upstream probe that must fail without it). An import error, a missing
    entry point, an unregistered brain wiring or a genuine code bug on a
    deferred module still WARNs, because none of those are what was deferred.

After it, GREEN means "everything working or knowingly parked" and AMBER means
"something needs attention" — which is the only reading that makes the number
worth looking at.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import self_diagnostic as SD


def _run(coro):
    return asyncio.run(coro)


# ── the deferral register: declared, with reasons ─────────────────────────

def test_deferred_modules_are_declared_with_a_reason():
    reg = SD.DEFERRED_MODULES
    assert isinstance(reg, dict) and reg, "no deferral register"
    for name, reason in reg.items():
        assert isinstance(reason, str) and len(reason) > 25, (
            f"{name} is parked without a written justification — a deferral with "
            f"no reason is indistinguishable from a bug someone hid"
        )


def test_the_two_known_deferrals_are_registered():
    assert "acled" in SD.DEFERRED_MODULES
    assert "worldbank_debarred" in SD.DEFERRED_MODULES


def test_reasons_cite_their_source():
    assert "18" in SD.DEFERRED_MODULES["acled"] or "operator" in SD.DEFERRED_MODULES["acled"].lower()
    wb = SD.DEFERRED_MODULES["worldbank_debarred"].lower()
    assert "403" in wb or "self-service" in wb or "opensanctions" in wb


# ── a missing credential on a DEFERRED module is not a warning ───────────

def test_env_var_check_defers_for_a_registered_module(monkeypatch):
    monkeypatch.delenv("ACLED_EMAIL", raising=False)
    status, note = SD._check_env_var("ACLED_EMAIL", module_name="acled")
    assert status == "DEFERRED", (status, note)
    assert "defer" in note.lower()


def test_env_var_check_still_warns_for_an_unregistered_module(monkeypatch):
    """The guard must not go blind: an ordinary module missing its credential
    is still a warning."""
    monkeypatch.delenv("SOME_OTHER_KEY", raising=False)
    status, _note = SD._check_env_var("SOME_OTHER_KEY", module_name="some_other_module")
    assert status == "WARN"


def test_env_var_check_passes_when_the_var_is_actually_set(monkeypatch):
    monkeypatch.setenv("ACLED_EMAIL", "someone@example.com")
    status, _n = SD._check_env_var("ACLED_EMAIL", module_name="acled")
    assert status == "PASS", "a deferral must never mask a var that IS set"


def test_env_var_check_is_backward_compatible_without_a_module_name(monkeypatch):
    monkeypatch.delenv("ANYTHING", raising=False)
    assert SD._check_env_var("ANYTHING")[0] == "WARN"


# ── the upstream probe that must fail without the credential ─────────────

def test_smoke_defers_for_a_deferred_module():
    class _Mod:
        async def is_available(self):
            return False

    status, _n = _run(SD._check_smoke(_Mod(), module_name="acled"))
    assert status == "DEFERRED"


def test_smoke_still_warns_for_a_normal_module():
    class _Mod:
        async def is_available(self):
            return False

    status, _n = _run(SD._check_smoke(_Mod(), module_name="ofac_sdn"))
    assert status == "WARN"


def test_a_deferred_module_with_a_real_code_bug_is_not_deferred():
    """Deferral covers a missing credential — NOT a broken module. A TypeError is
    a defect whoever deferred the source never signed off on.

    R-F1627 is explicit that a genuine code bug must stay FAIL (only network-level
    errors degrade to WARN), so the property asserted here is that deferral does
    not swallow it — not a particular severity word.
    """
    class _Broken:
        async def is_available(self):
            raise TypeError("genuine bug")

    status, note = _run(SD._check_smoke(_Broken(), module_name="acled"))
    assert status != "DEFERRED", (status, note)
    assert status == "FAIL", (status, note)


# ── overall: GREEN when only deferrals remain ────────────────────────────

def test_overall_is_green_when_only_deferrals_remain():
    assert SD._compute_overall(critical_fails=[], fail_count=0, warn_count=0,
                               deferred_count=2) == "GREEN"


def test_overall_is_amber_for_a_real_warning():
    assert SD._compute_overall(critical_fails=[], fail_count=0, warn_count=1,
                               deferred_count=2) == "AMBER"


def test_overall_is_amber_for_a_failure():
    assert SD._compute_overall(critical_fails=[], fail_count=1, warn_count=0,
                               deferred_count=0) == "AMBER"


def test_overall_is_red_for_a_critical_failure():
    assert SD._compute_overall(critical_fails=["x"], fail_count=1, warn_count=0,
                               deferred_count=0) == "RED"


def test_deferrals_never_downgrade_a_red():
    assert SD._compute_overall(critical_fails=["x"], fail_count=1, warn_count=1,
                               deferred_count=5) == "RED"


# ── it must be VISIBLE, not silently swallowed ───────────────────────────

def test_deferred_is_counted_separately():
    report = _run(SD.run_diagnostic())
    assert "deferred" in report["counts"], (
        "a parked module that appears nowhere in the counts is hidden, not deferred"
    )


def test_summary_exposes_the_deferred_count():
    summary = _run(SD.run_diagnostic_summary())
    assert "deferred" in summary.get("counts", {}), summary

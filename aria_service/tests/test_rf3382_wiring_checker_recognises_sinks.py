"""R-F3382 — the wiring gate flagged modules wired the PREFERRED way.

CLAUDE.md §21a defines a wired path as one reaching any of brain_hook.absorb /
capability_gaps.record_gap / mistake_ledger.record / a metric / a brain signal.
`scripts/pre_commit_checks.check_wiring_present` matched only the literal
`wire_success(` / `wire_failure(`, so it measured one IMPLEMENTATION of wiring.

R-F3381 taught it the failure-side sinks. It was still incomplete: it did not know
`@wired`, which `engine_wiring.wired()` documents as "the PREFERRED way to wire a
module ... guarantees both paths are covered" and whose body does exactly that —
`wire_failure()` in the `except`, `wire_success()` on the success path. A module
using it contains NEITHER literal, so 11 correctly-wired modules were reported as
violations and written into the R-F3381 backlog as real work: academic, acled,
cert_transparency, court_records, fcdo_sanctions, sec_edgar, un_sc_sanctions and
the worldbank_* family.

Count over the live tree: 72 flagged originally -> 68 after R-F3381 -> 56 now.

THE DISTINCTION THIS TEST EXISTS TO PROTECT — three sink classes, three different
strengths, and collapsing them is how this gate becomes either a liar or a clamp:

  @wired                     covers BOTH branches by construction -> clears the
                             module outright.
  @fail_wire / record_gap /  FAILURE-side only -> clears "no wiring at all", and
  brain_hook.absorb etc.     must NOT clear the half-wired categories, because
                             those exist to ask whether the SUCCESS branch is
                             wired. An earlier cut of R-F3381 cleared them too
                             and took the report from 72 to 52; that was a clamp
                             and was reverted.
  wire_success/wire_failure  the literals; each covers its own branch only.

KNOWN LIMIT, stated rather than implied: this is textual. A module that decorates
SOME entry points with @wired and leaves others bare reads as fully wired here.
Closing that needs per-function analysis, which is the backlog's job, not the
gate's.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pre_commit_checks import check_wiring_present  # noqa: E402


def _probe(tmp_path: pathlib.Path, body: str) -> list[str]:
    """Run the checker over one synthetic intel module."""
    d = tmp_path / "intel"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "probe_module.py"
    f.write_text(body, encoding="utf-8")
    return check_wiring_present([f])


def test_rf3382_wired_decorator_clears_the_module(tmp_path):
    """The exact false positive: @wired, neither literal present."""
    issues = _probe(tmp_path, (
        "from .engine_wiring import wired\n\n"
        "@wired(module='probe', summary='done')\n"
        "async def run():\n"
        "    return {'ok': True}\n"
    ))
    assert issues == [], f"a module using the PREFERRED decorator was flagged: {issues}"


def test_rf3382_a_failure_only_sink_does_not_clear_the_success_gap(tmp_path):
    """The clamp this must never become. A failure-side sink says nothing about
    the success branch, so a module with wire_failure + record_gap stays flagged
    for the missing success wiring."""
    issues = _probe(tmp_path, (
        "from .engine_wiring import wire_failure\n"
        "from . import capability_gaps\n\n"
        "async def run():\n"
        "    try:\n"
        "        return 1\n"
        "    except Exception:\n"
        "        wire_failure(module='p', detail='x')\n"
        "        await capability_gaps.record_gap(gap_type='engine_failure')\n"
    ))
    assert issues, "a failure-only sink must NOT clear the missing-success verdict"
    assert "NO wire_success" in issues[0], issues[0]


def test_rf3382_a_failure_only_sink_does_clear_no_wiring_at_all(tmp_path):
    """R-F3381's legitimate half: such a module is not 'unwired', so the harsher
    'NO brain wiring found' verdict is wrong for it."""
    issues = _probe(tmp_path, (
        "from .wire import fail_wire\n\n"
        "@fail_wire(module='p', gap_type='engine_failure')\n"
        "async def run():\n"
        "    return 1\n"
    ))
    assert issues == [], f"a module with a real §21a sink was called unwired: {issues}"


def test_rf3382_a_genuinely_dark_module_is_still_caught(tmp_path):
    """The gate must still fire — 27 modules in the live tree are genuinely dark
    and this fix must not have hidden them."""
    issues = _probe(tmp_path, (
        "async def run():\n"
        "    try:\n"
        "        return 1\n"
        "    except Exception:\n"
        "        return None\n"
    ))
    assert issues, "a module with no sink of any kind must be flagged"
    assert "NO brain wiring found" in issues[0], issues[0]


def test_rf3382_the_live_tree_matches_the_recorded_triage():
    """Anchor the measured count so a future 'simplification' that quietly
    shrinks or inflates it fails here. Modules genuinely wired later will lower
    the first number — update this and docs/wiring_backlog when that happens."""
    intel = ROOT / "aria_service" / "intel"
    issues = check_wiring_present(sorted(intel.rglob("*.py")))
    flagged = {i.strip().split(":")[0] for i in issues}
    # R-F3567 — THE BACKLOG IS CLOSED: 17 -> 0. Every remaining module was
    # triaged individually: 10 engines wired at their real success branch, one
    # detector class fixed (`absorb(success=True)` is a SUCCESS report, not a
    # failure-side sink), and 2 modules exempted because they ARE the wiring
    # machinery. History: 72 -> 56 -> 50 -> 26 (R-F3563) -> 17 (R-F3565) -> 0.
    #
    # A count of zero is the one result that must never be taken on trust — it
    # is indistinguishable from a detector that stopped detecting. The
    # non-vacuity tests in this file and in test_rf3556_precommit_gate.py are
    # what make it mean something, so this bound is paired with them and a
    # regression must show up as a NEW name here, not as a widened number.
    assert len(flagged) == 0, (
        f"{len(flagged)} module(s) are missing a §21 brain-wiring branch: "
        f"{sorted(flagged)}. The backlog was closed at R-F3567; a module "
        f"appearing here is new debt. Wire its success AND failure branch, or "
        f"— if it genuinely does nothing externally — add it to "
        f"WIRING_EXEMPT_MODULES with its own stated reason. Do not batch-exempt."
    )


def test_rf3567_the_audit_is_not_vacuous():
    """A gate that reports zero must be shown to still be capable of reporting.

    The wiring audit went green at R-F3567 for the first time. Green because the
    work was done and green because the scan broke look identical from the
    outside, so the detector is exercised against a module that is definitely
    unwired and one that is definitely half-wired.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td) / "intel"
        d.mkdir(parents=True)
        bare = d / "definitely_unwired.py"
        bare.write_text("def run():\n    return 1\n", encoding="utf-8")
        half = d / "definitely_half_wired.py"
        half.write_text(
            "from .engine_wiring import wire_success\n"
            "def run():\n    wire_success(module='m', summary='ok')\n",
            encoding="utf-8",
        )
        issues = check_wiring_present([bare, half])
    assert len(issues) == 2, f"the detector missed a known-bad module: {issues}"
    assert any("NO brain wiring found" in i for i in issues), issues
    assert any("NO wire_failure" in i for i in issues), issues


def test_rf3567_the_scan_actually_reaches_the_intel_tree():
    """The other way a zero can lie: scanning an empty file list."""
    intel = ROOT / "aria_service" / "intel"
    scanned = [p for p in intel.rglob("*.py") if "tests" not in p.parts]
    assert len(scanned) > 200, (
        f"only {len(scanned)} intel modules found — the audit's input has "
        f"collapsed, which would make its green result meaningless"
    )

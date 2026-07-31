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
    # R-F3565 — 26 -> 17: NINE of the 26 were the DETECTOR, not the tree.
    # Two classes, both demanding work that was already done: an aliased import
    # (`wire_failure as _wf`) was invisible to a literal substring scan, and
    # `@fail_wire` — a genuine failure-side sink — did not credit the FAILURE
    # branch when a wire_success was already present. The deliberate asymmetry
    # survives: a failure sink still never satisfies the SUCCESS branch (that
    # was the reverted R-F3382 clamp, 72 -> 52).
    # R-F3563 — 50 -> 26: the NO-WIRING tier is closed (14 modules wired for
    # real, 14 pure transforms exempted with a per-module reason). The remaining
    # 17 are all ONE-BRANCH cases: each has a real failure sink and no success
    # signal. The upper bound RATCHETS DOWN with the backlog and must never be
    # widened; docs/wiring_backlog_2026_07_28.md is regenerated in the same
    # change, which is what this bound guards.
    assert 12 <= len(flagged) <= 17, (
        f"flagged module count is {len(flagged)}; it was 17 after R-F3565 "
        f"(26 after R-F3563, 56 at the 2026-07-28 triage, 72 before R-F3381). "
        f"A large move means either the backlog was worked or the detector "
        f"changed — both need the backlog doc regenerated, not this bound widened."
    )

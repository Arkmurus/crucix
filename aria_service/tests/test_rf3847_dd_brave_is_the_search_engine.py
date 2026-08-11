"""R-F3847 — Brave is THE search engine for DD. Nothing substitutes for it.

OPERATOR DIRECTIVE, 2026-08-11: "ensure anthropic and brave API are the designated
tools for DD reports, ensure that is clear and nothing else".

WHAT THIS CHANGES. R-F3122 made Brave the DD primary and kept ARIA's own SearXNG as a
FALLBACK for when Brave yielded nothing — deliberately, and honestly, with
`brave_fallback_used` surfaced so a report could state the primary had not served
(§14: cooling is not breaking, but it must be visible). That was a reasonable design.
It is no longer the policy, and it had a measured cost.

WHY IT IS BEING REMOVED, with evidence rather than preference. The fallback is the
exact path by which noise reached a customer-facing report:

    dd_92f9d77b8886  "Silverbrook Capital Management"
      .digital.press_coverage[3].url =
         https://support.google.com/chrome/answer/95346?hl=fr

A French Chrome cookies help page, cited as press coverage. Chain, each link read in
code: DD enables Brave exclusively (`ARIA_DD_BRAVE_EXCLUSIVE`, dd_orchestrator:14916)
→ Brave yields nothing for that query → `_brave_fallback_tasks` runs SearXNG →
SearXNG was returning query-independent noise (R-F3844: the same query four times gave
four unrelated result sets, 14 engines erroring) → noise entered the report.

R-F3844 already stops SearXNG returning noise as success. This closes the same hole
from the other side, and makes the POLICY explicit rather than emergent: on the DD
path there is one search engine, and when it has nothing to say ARIA says nothing —
it does not quietly ask a degraded backend instead.

THE THING THIS MUST NOT DO. Silence. An empty result set from the designated engine
is a DATA GAP and has to be reported as one; a report that simply omits a section
reads identically to a report where the section was clean. So the suppression is
counted and surfaced, never dropped on the floor.

The autonomous/free stack is UNTOUCHED — it still uses SearXNG, because it is not
producing customer-facing DD and must not burn paid quota (R-F2318).
"""
from __future__ import annotations

import os

import pytest

from aria_service.intel import web_search


def test_the_dd_brave_only_switch_defaults_ON(monkeypatch):
    """The operator designated Brave as THE DD engine, so the default must express
    that. A policy that only holds when an env var is set is not a policy."""
    monkeypatch.delenv("ARIA_DD_BRAVE_ONLY", raising=False)
    assert web_search._dd_brave_only() is True


@pytest.mark.parametrize("value,expected", [
    ("0", False), ("false", False), ("no", False),
    ("1", True), ("", True), ("anything", True),
])
def test_the_switch_is_operator_reversible(value, expected, monkeypatch):
    """Reversible without a deploy — but it takes a DELIBERATE opt-out, and only the
    explicit falsey words count. A typo must not silently re-enable substitution."""
    monkeypatch.setenv("ARIA_DD_BRAVE_ONLY", value)
    assert web_search._dd_brave_only() is expected


def test_the_free_stack_is_not_affected():
    """The autonomous loop still uses SearXNG — it produces no customer-facing DD and
    must not burn paid quota (R-F2318). This directive is about the DD path only."""
    from aria_service.tests._source_probe import function_source

    src = function_source(web_search, "search")
    # the suppression must be conditioned on the Brave/DD scope, never global
    assert "_dd_brave_only()" in src
    assert "_brave_exclusive" in src


def test_a_suppressed_fallback_is_COUNTED_not_silent():
    """The half that keeps this honest. An empty result set from the designated
    engine is a DATA GAP; a silently-omitted section is indistinguishable from a
    clean one, which is the §22 failure this whole incident was."""
    from aria_service.tests._source_probe import function_source

    src = function_source(web_search, "search")
    assert "brave_fallback_suppressed" in src, (
        "suppressing the substitution must be reported, or ARIA cannot tell "
        "'Brave found nothing' from 'nobody looked'")


def test_the_silverbrook_path_is_named_in_the_code():
    """Whoever re-adds a fallback here should meet the evidence first."""
    from aria_service.tests._source_probe import function_source

    src = function_source(web_search, "search")
    assert "dd_92f9d77b8886" in src or "R-F3847" in src

"""R-F3571 — one check consumed 3 of the 10 key-findings slots.

Measured on a live report (Chemring, dd2_uk): three of the ten key findings were
`network_walker` "Director X has N+ cross-linked appointments" lines — all amber, all
the same point about three different officers — pushing the sanctions screen, the LEI
and the federal-contract exposure down and a financial-health gap off the list.

They are not duplicates: they concern different people. But at summary granularity they
are ONE signal, and a BLUF that states one signal three times is a worse BLUF. That is
what the operator read as duplication.

NOTHING IS HIDDEN by this: it re-orders a 10-item view of a list that stays complete in
its own section.
"""
from __future__ import annotations

import types

from aria_service.intel import dd_orchestrator as dd

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _f(sev, source, title):
    return types.SimpleNamespace(severity=sev, source=source, title=title, detail="")


def test_one_source_cannot_monopolise_the_summary():
    """PROVE RED: `all_findings[:10]` gave network_walker three slots."""
    pool = [_f("amber", "network_walker", f"Director {n} has 10+ appointments")
            for n in ("A", "B", "C")]
    pool += [_f("info", f"src_{i}", f"signal {i}") for i in range(9)]

    assert sum(1 for f in pool[:10] if f.source == "network_walker") == 3
    out = dd._rollup_key_findings(pool)
    assert sum(1 for f in out if f.source == "network_walker") == 2, (
        "one check still monopolises the summary"
    )
    assert len(out) == 10, "the summary must still be full"


def test_severity_primacy_is_absolute():
    """A deferred REPEAT must never let a lower-severity finding outrank a higher one."""
    pool = [_f("hard_stop", "same", "stop 1"), _f("hard_stop", "same", "stop 2"),
            _f("hard_stop", "same", "stop 3"), _f("info", "other", "trivia")]
    out = dd._rollup_key_findings(pool)
    assert out[0].severity == "hard_stop" and out[1].severity == "hard_stop"
    # the third hard_stop is deferred, but must be BACKFILLED ahead of nothing being
    # lost — it is still present in the output because there are spare slots
    assert any(f.title == "stop 3" for f in out), "a hard_stop was dropped entirely"


def test_a_report_with_few_sources_is_unchanged():
    """The cap must not damage the common case."""
    pool = [_f("red", f"src_{i}", f"finding {i}") for i in range(6)]
    assert [f.title for f in dd._rollup_key_findings(pool)] == [f.title for f in pool]


def test_deferred_items_backfill_rather_than_leaving_gaps():
    """An under-filled summary helps nobody."""
    pool = [_f("amber", "one_source", f"item {i}") for i in range(12)]
    out = dd._rollup_key_findings(pool)
    assert len(out) == 10, f"summary left short at {len(out)} by the per-source cap"


def test_the_from_url_suffix_is_stripped_for_grouping_only():
    """R-F1946 — the ` [from <url>]` suffix on Finding.source is load-bearing for
    other consumers. It groups as one source but must not be rewritten."""
    src = "worldbank.country_risk [from https://api.worldbank.org/v2/x]"
    pool = [_f("amber", src, "a"), _f("amber", src, "b"), _f("amber", src, "c"),
            _f("info", "other", "d")]
    out = dd._rollup_key_findings(pool)
    assert sum(1 for f in out if f.source == src) >= 2
    assert out[0].source == src, "the source string was mutated"


def test_an_unattributed_finding_does_not_group_with_everything():
    """Findings with no source must not all collapse into one bucket and get capped."""
    pool = [_f("amber", "", f"orphan {i}") for i in range(4)]
    out = dd._rollup_key_findings(pool)
    assert len(out) == 4, "unattributed findings were wrongly capped away"


def test_an_empty_pool_is_safe():
    assert dd._rollup_key_findings([]) == []


def test_the_synthesis_call_site_uses_the_rollup():
    """Reachability — the lesson from R-F3515/R-F3566: a helper nothing calls is
    indistinguishable from no fix at all."""
    import inspect

    src = function_source(dd, "_run_synthesis")
    assert "_rollup_key_findings(all_findings)" in src
    assert "key_findings = all_findings[:10]" not in src, (
        "the un-capped slice is still assigning key_findings"
    )

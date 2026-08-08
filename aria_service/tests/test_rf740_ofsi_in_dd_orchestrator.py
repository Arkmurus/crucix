"""R-F740 (2026-05-20) — wire fcdo_sanctions.lookup() into dd_orchestrator.

LIVE BUG 2026-05-20 in operator's dd_orchestrate output:

  GAPS
    | OFSI UK sanctions | UK list not queried — tool has no OFSI adapter |

The "no OFSI adapter" claim is FALSE: fcdo_sanctions.lookup() is
wired and operational (live since R-F569). It just wasn't called
from dd_orchestrator — the company-identity-layer screen went
through `sanctions.screen_with_aliases()` which uses OpenSanctions
as an aggregate, NOT the direct OFSI primary source. UK DD clients
need a citation_url anchored at ofsi.gov.uk, not a third-party
aggregator URL.

R-F740 adds an explicit fcdo_sanctions.lookup() call right after
the OpenSanctions screen completes. Hits are merged into the
screen["matches"] list with `list="UK_OFSI"` and the
citation_url=ofsi.gov.uk feed URL. "uk_ofsi" is appended to
verified_sources whenever the lookup completes (zero hits still
counts as a verified clean check).

Fail-open: OFSI lookup exception is debug-logged and the screen
continues with whatever OpenSanctions returned.

These tests guard against:
  - Removing the fcdo_sanctions import / call
  - Forgetting to add 'uk_ofsi' to verified_sources
  - Mis-shaping the merged hit dicts (downstream classify_matches
    expects name/list/score)
"""
from __future__ import annotations

import inspect

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_rf740_wiring_present_in_dd_orchestrator():
    """R-F740: fcdo_sanctions import + lookup call must be in the
    company identity layer."""
    from aria_service.intel import dd_orchestrator

    src = module_source(dd_orchestrator)
    assert "R-F740" in src, "R-F740 marker missing from dd_orchestrator.py"
    assert "fcdo_sanctions" in src, (
        "R-F740 regression: fcdo_sanctions module not imported by "
        "dd_orchestrator — OFSI gap restored."
    )
    assert "_fcdo.lookup" in src or "fcdo_sanctions.lookup" in src, (
        "R-F740 regression: fcdo_sanctions.lookup() call missing."
    )


def test_rf740_verified_sources_includes_uk_ofsi():
    """R-F740: 'uk_ofsi' must be appended to screen['verified_sources']."""
    from aria_service.intel import dd_orchestrator

    src = module_source(dd_orchestrator)
    assert "uk_ofsi" in src, (
        "R-F740 regression: 'uk_ofsi' string not appended to "
        "verified_sources — UI will still list OFSI as a gap."
    )


def test_rf740_failure_is_fail_open():
    """R-F740: a fcdo_sanctions.lookup exception must NOT crash the
    DD orchestrator — should be try/except wrapped with a debug log
    fallback."""
    from aria_service.intel import dd_orchestrator

    src = module_source(dd_orchestrator)
    import ast

    tree = ast.parse(src)
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        body = "\n".join(ast.unparse(statement) for statement in node.body)
        handlers = "\n".join(ast.unparse(handler) for handler in node.handlers)
        if "_fcdo.lookup(name)" in body and "except Exception" in handlers:
            guarded = "fcdo_sanctions.lookup failed" in handlers
            if guarded:
                break
    assert guarded, "the OFSI lookup must remain inside its failure-tolerant exception boundary"


def test_rf740_merged_hits_have_uk_ofsi_list_marker():
    """R-F740: OFSI hits merged into screen['matches'] must use
    list='UK_OFSI' so downstream classify_matches treats them
    correctly + the report renderer labels them properly."""
    from aria_service.intel import dd_orchestrator

    src = module_source(dd_orchestrator)
    assert '"list":   "UK_OFSI"' in src or "'list':   'UK_OFSI'" in src or '"UK_OFSI"' in src, (
        "R-F740 regression: merged OFSI hits missing the 'UK_OFSI' "
        "list label."
    )


def test_rf740_citation_url_anchored_at_ofsi():
    """R-F740: hit citation_url must point at ofsi.gov.uk OR the
    ofsistorage blob feed — NOT at an OpenSanctions aggregator URL.
    This is the load-bearing reason for R-F740."""
    from aria_service.intel import dd_orchestrator

    src = module_source(dd_orchestrator)
    assert "ofsistorage" in src or "ofsi.gov.uk" in src, (
        "R-F740 regression: OFSI citation_url anchor missing."
    )


def test_rf740_fcdo_sanctions_lookup_signature_unchanged():
    """R-F740: the upstream fcdo_sanctions.lookup signature must remain
    compatible with the call site in dd_orchestrator (name positional +
    threshold/max_hits kwargs)."""
    from aria_service.intel.sources import fcdo_sanctions as fcdo

    sig = inspect.signature(fcdo.lookup)
    params = list(sig.parameters.keys())
    assert params and params[0] == "name", (
        "R-F740 regression: fcdo_sanctions.lookup() first param must "
        "be `name` (dd_orchestrator passes it positionally)."
    )
    # async function
    assert inspect.iscoroutinefunction(fcdo.lookup), (
        "R-F740 regression: fcdo_sanctions.lookup must remain async — "
        "dd_orchestrator awaits it."
    )

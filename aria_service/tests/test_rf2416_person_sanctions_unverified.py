"""R-F2416 — person DD must NOT emit a CLEAN clearance when the sanctions
source is UNAVAILABLE.

Capability test: drives the REAL broken path (`_run_identity_person`) with a
sanctions screen that soft-returns `source_unavailable` (the way
`screen_with_aliases`/`fuzzy_screen` actually behave when OpenSanctions is
breaker-open / rate-limited — they do NOT raise). Pre-fix this stamped every
list CLEAN and emitted a CONFIRMED "treat as clearance" finding on a screen that
never ran (never-false-clean breach). Post-fix it must emit the
SANCTIONS_SOURCE_UNVERIFIED marker + an amber finding and NO clearance.
"""
import asyncio
import types

import aria_service.intel.sanctions as _sanc
import aria_service.intel.person_resolver as _pr
from aria_service.intel import dd_orchestrator
from aria_service.intel.dd_schema import ARKDDReport


def _fake_resolution(name):
    return types.SimpleNamespace(
        canonical=name, script="latin", variants=[name],
        components=types.SimpleNamespace(given=name, particles="", surname=""),
    )


def _new_report(name):
    r = ARKDDReport(target={"name": name, "type": "person"}, orchestrator_mode="person", trace_id="t-rf2416")
    r.identity.entity_name = name
    r.identity.entity_type = "person"
    return r


def _clearance_findings(report):
    return [f for f in report.identity.findings
            if "treat as clearance" in (f.detail or "").lower()
            or "POSITIVE CLEAN" in (f.detail or "")]


def _unverified_gaps(report):
    return [g for g in (report.identity.data_gaps or []) if "SANCTIONS_SOURCE_UNVERIFIED" in str(g)]


async def _run(name, screen_return):
    async def _fake_screen(variant):
        return dict(screen_return)  # fresh copy per call
    orig_screen = getattr(_sanc, "screen_with_aliases", None)
    orig_fuzzy = getattr(_sanc, "fuzzy_screen", None)
    orig_resolve = _pr.resolve
    _sanc.screen_with_aliases = _fake_screen  # type: ignore[attr-defined]
    _sanc.fuzzy_screen = _fake_screen  # type: ignore[attr-defined]
    _pr.resolve = lambda *a, **k: _fake_resolution(name)  # type: ignore[assignment]
    try:
        report = _new_report(name)
        await dd_orchestrator._run_identity_person({"name": name, "type": "person"}, report)
        return report
    finally:
        if orig_screen is not None:
            _sanc.screen_with_aliases = orig_screen  # type: ignore[attr-defined]
        if orig_fuzzy is not None:
            _sanc.fuzzy_screen = orig_fuzzy  # type: ignore[attr-defined]
        _pr.resolve = orig_resolve  # type: ignore[assignment]


def test_source_unavailable_person_is_not_cleared():
    """The bug: unavailable source → fabricated CLEAN clearance."""
    report = asyncio.run(_run(
        "Zzqx Vellamin Torbraith",
        {"matches": [], "screened": False, "source_unavailable": True},
    ))
    assert _unverified_gaps(report), "expected a SANCTIONS_SOURCE_UNVERIFIED data_gap"
    assert not _clearance_findings(report), "must NOT emit a CLEAN/clearance finding when source is unavailable"
    ss = report.identity.sanctions_screen or {}
    assert ss.get("source_unavailable") is True
    # No verified_source may be stamped CLEAN when nothing screened.
    vs = ss.get("verified_sources") or []
    clean = [v for v in vs if isinstance(v, dict) and str(v.get("status", "")).upper() == "CLEAN"]
    assert not clean, f"no list may be CLEAN on an unrun screen, got {clean}"
    amber = [f for f in report.identity.findings if f.severity == "amber" and "UNVERIFIED" in (f.title or "")]
    assert amber, "expected an amber UNVERIFIED finding"


def test_genuine_clean_still_clears_no_regression():
    """Control: a screen that actually ran with no matches still clears."""
    report = asyncio.run(_run(
        "Zzqx Vellamin Torbraith",
        {"matches": [], "screened": True, "source_unavailable": False},
    ))
    assert not _unverified_gaps(report), "genuine clean screen must NOT be flagged unverified"
    assert _clearance_findings(report), "a genuinely-screened clean person should still get a CLEAN finding"


if __name__ == "__main__":
    test_source_unavailable_person_is_not_cleared()
    print("PASS test_source_unavailable_person_is_not_cleared")
    test_genuine_clean_still_clears_no_regression()
    print("PASS test_genuine_clean_still_clears_no_regression")
    print("ALL PASS")

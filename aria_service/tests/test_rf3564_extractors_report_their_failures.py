"""R-F3564 — the DD evidence extractors were dark on every failure path.

`extractors/structured.py` and `extractors/facts.py` run on the DD evidence path via
`researcher.extract_url_deep`. Between them they caught Exception 19 times, wrote a
`logger.debug`, and substituted an EMPTY result. They re-raised ZERO times, and the
caller swallowed the outer call too — so nothing propagated to any wired frame.

WHY IT MATTERS MORE THAN A GATE TICK: `tables = []` after a crash is byte-identical to
`tables = []` from a page that genuinely has no tables. The same holds for
`reg_numbers` and `ceos`. A broken extractor therefore manufactured an ABSENCE OF
EVIDENCE that the report renders as EVIDENCE OF ABSENCE — an unverified clean.

These tests drive the REAL functions with a REAL induced failure and assert the signal
lands, rather than grepping the source for the string `wire_failure` (a grep cannot
tell reachable code from unreachable code — R-F3515).
"""
from __future__ import annotations

import pytest

from aria_service.intel import engine_wiring
from aria_service.intel.extractors import facts as facts_mod
from aria_service.intel.extractors import structured as struct_mod


@pytest.fixture
def sink(monkeypatch):
    """Capture wire_failure/wire_success. monkeypatch so it is RESTORED (R-F3449)."""
    failures: list[dict] = []
    successes: list[dict] = []
    monkeypatch.setattr(
        engine_wiring, "wire_failure",
        lambda **kw: failures.append(kw), raising=True,
    )
    monkeypatch.setattr(
        engine_wiring, "wire_success",
        lambda **kw: successes.append(kw), raising=True,
    )
    return {"failures": failures, "successes": successes}


_HTML = "<html><body><table><tr><td>x</td></tr></table><p>Acme Ltd</p></body></html>"


# ── structured.py ─────────────────────────────────────────────────────────────

def test_a_broken_sub_extractor_reaches_the_brain(monkeypatch, sink):
    """CAPABILITY: the exact defect — a sub-extractor dies and the caller gets []."""
    def _boom(*a, **k):
        raise ValueError("induced")
    monkeypatch.setattr(struct_mod, "_extract_tables", _boom, raising=True)

    out = struct_mod.extract(_HTML, base_url="https://example.com")

    assert out["tables"] == [], "the empty-substitute behaviour must be preserved"
    named = [f for f in sink["failures"] if "tables" in f.get("detail", "")]
    assert named, (
        "a crashed sub-extractor produced an empty result with NO brain signal — "
        f"captured: {sink['failures']}"
    )
    assert named[0]["module"] == "extractors_structured"
    assert "ValueError" in named[0]["detail"], "the detail must name the real cause"


def test_extraction_still_succeeds_when_one_part_dies(monkeypatch, sink):
    """Wiring must not convert a partial failure into a total one."""
    monkeypatch.setattr(
        struct_mod, "_extract_json_ld",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("induced")), raising=True,
    )
    out = struct_mod.extract(_HTML, base_url="https://example.com")
    assert isinstance(out, dict) and "tables" in out
    assert out["json_ld"] == []


def test_the_success_signal_carries_counts_not_just_a_tick(sink):
    """A bare tick cannot distinguish 'found 12 tables' from 'found nothing' —
    which is the same blindness this R-number closes."""
    struct_mod.extract(_HTML, base_url="https://example.com")
    assert sink["successes"], "a completed extraction emitted no success signal"
    detail = sink["successes"][-1]["detail"]
    assert "tables=" in detail, f"counts missing from the success signal: {detail!r}"


def test_a_total_parse_failure_is_reported(monkeypatch, sink):
    """bs4 absent or both parsers rejecting the document returns the SAME empty
    dict as a blank page, so it must be said out loud."""
    monkeypatch.setattr(struct_mod, "_parse_html", lambda *a, **k: None, raising=True)
    out = struct_mod.extract(_HTML, base_url="https://example.com")
    assert out["tables"] == []
    assert any("html_parse" in f.get("detail", "") for f in sink["failures"]), (
        f"an unparseable document was reported as an empty one: {sink['failures']}"
    )


# ── facts.py ──────────────────────────────────────────────────────────────────

def test_a_broken_fact_extractor_reaches_the_brain(monkeypatch, sink):
    """reg_numbers/ceos silently emptying is a DD-visible false absence."""
    monkeypatch.setattr(
        facts_mod, "_extract_reg_numbers",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("induced")), raising=True,
    )
    out = facts_mod.extract("Acme Ltd, company number 01234567.", base_url="https://x.com")

    assert out["reg_numbers"] == []
    assert any("reg_numbers" in f.get("detail", "") for f in sink["failures"]), (
        f"a crashed reg-number extractor was invisible: {sink['failures']}"
    )


def test_facts_reports_counts_on_success(sink):
    facts_mod.extract("Acme Ltd was founded in 1999.", base_url="https://x.com")
    assert sink["successes"], "fact extraction emitted no success signal"
    assert "reg_numbers=" in sink["successes"][-1]["detail"]


def test_empty_input_is_not_reported_as_a_failure(sink):
    """'Nobody asked' must stay distinguishable from 'errored' — wiring an empty
    input would flood the ledger with non-events."""
    assert facts_mod.extract("", base_url="") == facts_mod._empty_result()
    assert not sink["failures"]


# ── researcher.py: the OUTER swallow ──────────────────────────────────────────

def test_the_outer_helper_reports_an_unavailable_extractor(sink):
    """If the whole call dies, nothing inside the extractor runs — this branch is
    the only place the loss can be reported."""
    from aria_service.intel import researcher

    researcher._extractor_unavailable("structured", ImportError("no module"), "https://x.com")
    assert sink["failures"], "a missing extractor package was a silent degrade"
    rec = sink["failures"][-1]
    assert rec["module"] == "researcher"
    assert rec["gap_type"] == "source_failure"
    assert "https://x.com" in rec["detail"], "the failing URL must be identifiable"


# ── the wiring must never break extraction ────────────────────────────────────

def test_a_failing_brain_sink_cannot_break_extraction(monkeypatch):
    """R-F2149 class: the sink itself raising must not take the extractor down."""
    def _explode(**kw):
        raise RuntimeError("sink down")
    monkeypatch.setattr(engine_wiring, "wire_failure", _explode, raising=True)
    monkeypatch.setattr(engine_wiring, "wire_success", _explode, raising=True)
    monkeypatch.setattr(
        struct_mod, "_extract_tables",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("induced")), raising=True,
    )
    out = struct_mod.extract(_HTML, base_url="https://example.com")
    assert isinstance(out, dict), "a dead brain sink must not break extraction"


# ── the exemption that hid this is gone ───────────────────────────────────────

def test_the_extractors_are_no_longer_exempt_from_the_wiring_gate():
    """They were exempted as 'pure transforms'. They are wired now, so being green
    must come from the wiring — not from being unmeasured."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from pre_commit_checks import WIRING_EXEMPT_MODULES, check_wiring_present

    assert "structured" not in WIRING_EXEMPT_MODULES
    assert "facts" not in WIRING_EXEMPT_MODULES

    intel = Path(__file__).resolve().parents[1] / "intel"
    flagged = {
        i.strip().split(":")[0]
        for i in check_wiring_present(sorted(intel.rglob("*.py")))
    }
    assert "structured.py" not in flagged and "facts.py" not in flagged, (
        "removed from the exemption list but not actually wired — that is a "
        "regression, not progress"
    )

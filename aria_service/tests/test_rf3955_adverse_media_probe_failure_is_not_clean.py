"""R-F3955 / C-46 — an adverse-media sweep where every probe FAILED was written
as `[]` and read as a completed screen.

The R-F445 polyglot sweep runs one search per language and swallows each
failure individually:

    dd_orchestrator.py:9927
        except Exception as _wse:
            logger.debug(...)
            return lang, []                       # ← failure becomes "no hits"

then writes the aggregate unconditionally:

    dd_orchestrator.py:9951
        report.digital.web_footprint["adverse_media_hits"] = _adverse_hits

and the R-F2779 never-false-clean guard is keyed on `is not None`:

    dd_orchestrator.py:10669
        _am_screened = (_am_inline is not None) or _am_deep_ran

An empty list is not None. So a sweep in which every single language probe
raised produced `screened=True`, the guard was skipped, and the report carried
no "adverse-media screening did NOT complete" statement. **A total sweep
failure and a genuinely clean subject rendered identically.**

Scope is DEEP mode — the paid, most-trusted tier. It is mitigated when the
whole search ecosystem is detected as dead, but not in the case that actually
happens: Brave alone failing while the free backends answer.

Same shape as C-39 (a screen attributed to sources it never queried), and the
fix is the same one: **record the coverage, always, including on the healthy
path.** A block that only appears on failure cannot describe the dangerous
case — a sweep that partly succeeded.
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator as DD
from aria_service.intel.dd_schema import ARKDDReport

from ._source_probe import function_code


def _synth_ready(rep: ARKDDReport) -> ARKDDReport:
    """Enough identity substance that the confidence gate is not the actor."""
    rep.identity.entity_type = "company"
    rep.identity.entity_name = "Probe Test Ltd"
    rep.identity.registration_status = "active"
    rep.identity.incorporation_date = "2011-04-02"
    rep.identity.directors = [{"name": "A Director", "is_current": True}]
    rep.identity.ghost_score = {"total": 2, "indicators": []}
    return rep


# ── the coverage record ──────────────────────────────────────────────────────

def test_probe_coverage_is_recorded_on_the_HEALTHY_path_too():
    """C-39's lesson: failure-only telemetry cannot describe a partial success."""
    assert DD._adverse_probe_screened({"attempted": 4, "succeeded": 4, "failed_langs": []}) is True


def test_all_probes_failed_is_not_a_screen():
    assert DD._adverse_probe_screened(
        {"attempted": 4, "succeeded": 0, "failed_langs": ["ru", "pt", "fr", "ar"]},
    ) is False


def test_partial_success_still_counts_as_screened():
    """One language answering is thin, but it is not nothing — and the report
    discloses the failed languages separately. Treating partial as unscreened
    would fire the gap on nearly every real run and teach the reader to skip it."""
    assert DD._adverse_probe_screened(
        {"attempted": 4, "succeeded": 1, "failed_langs": ["ru", "pt", "fr"]},
    ) is True


def test_absent_coverage_keeps_the_LEGACY_meaning():
    """Reports written before this fix must not be retroactively re-judged.

    Absence means "this run did not record coverage", which is not evidence of
    failure. Same absence rule as C-39.
    """
    assert DD._adverse_probe_screened(None) is True
    assert DD._adverse_probe_screened({}) is True


def test_attempted_zero_is_not_a_screen():
    """Nothing was probed, so nothing was screened."""
    assert DD._adverse_probe_screened({"attempted": 0, "succeeded": 0, "failed_langs": []}) is False


def test_malformed_coverage_fails_CLOSED():
    """An undeterminable record is never full coverage."""
    assert DD._adverse_probe_screened({"attempted": "four", "succeeded": None}) is False


# ── the guard, driven through the real synthesis layer ───────────────────────

@pytest.mark.asyncio
async def test_total_sweep_failure_is_disclosed():
    """The capability test: every probe raised, so the report must say so."""
    rep = _synth_ready(ARKDDReport())
    rep.digital.web_footprint = {
        "adverse_media_hits": [],                       # what the sweep wrote
        "adverse_media_probe": {
            "attempted": 4, "succeeded": 0,
            "failed_langs": ["ru", "pt", "fr", "ar"],
        },
    }

    await DD._run_synthesis({"name": "Probe Test Ltd", "type": "company"}, rep)

    gaps = " ".join(rep.digital.data_gaps).lower()
    assert "adverse-media screening did not complete" in gaps, (
        "a sweep in which every probe failed was reported as a completed screen"
    )
    assert any("adverse-media screening incomplete" in (f.title or "").lower()
               for f in rep.digital.findings)


@pytest.mark.asyncio
async def test_a_real_clean_sweep_is_not_flagged():
    """The guard must still be able to stay quiet — otherwise it is noise."""
    rep = _synth_ready(ARKDDReport())
    rep.digital.web_footprint = {
        "adverse_media_hits": [],
        "adverse_media_probe": {"attempted": 4, "succeeded": 4, "failed_langs": []},
    }

    await DD._run_synthesis({"name": "Probe Test Ltd", "type": "company"}, rep)

    gaps = " ".join(rep.digital.data_gaps).lower()
    assert "adverse-media screening did not complete" not in gaps, (
        "a sweep that ran cleanly was flagged as incomplete"
    )


@pytest.mark.asyncio
async def test_legacy_report_without_coverage_behaves_as_before():
    rep = _synth_ready(ARKDDReport())
    rep.digital.web_footprint = {"adverse_media_hits": []}

    await DD._run_synthesis({"name": "Probe Test Ltd", "type": "company"}, rep)

    gaps = " ".join(rep.digital.data_gaps).lower()
    assert "adverse-media screening did not complete" not in gaps


@pytest.mark.asyncio
async def test_no_sweep_at_all_still_flagged():
    """The original R-F2779 behaviour must survive the change."""
    rep = _synth_ready(ARKDDReport())
    rep.digital.web_footprint = {}

    await DD._run_synthesis({"name": "Probe Test Ltd", "type": "company"}, rep)

    gaps = " ".join(rep.digital.data_gaps).lower()
    assert "adverse-media screening did not complete" in gaps


# ── the sweep must actually record what it did ───────────────────────────────

def test_the_sweep_writes_the_coverage_block():
    src = function_code(DD, "_run_digital")
    assert '"adverse_media_probe"' in src, (
        "the polyglot sweep does not record probe coverage, so a total failure "
        "is again indistinguishable from a clean result"
    )


def test_the_guard_reads_the_coverage_block():
    src = function_code(DD, "_run_synthesis")
    assert "_adverse_probe_screened(" in src, (
        "the never-false-clean guard is not consulting probe coverage"
    )

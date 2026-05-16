"""R-F584/F585/F586/F587 (2026-05-16) — dd_layer_extensions integration tests.

These pin the contract between dd_orchestrator and the 4 capability
shims. Real modules are mocked at the source-module boundary so
tests are hermetic + fast.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_report(entity_name: str = "Acme Defence Ltd"):
    """Minimal report stub matching the attribute access the shims use."""
    return SimpleNamespace(
        identity=SimpleNamespace(entity_name=entity_name),
        layers_run=[],
        layers_skipped=[],
    )


# ── R-F584 court_records wire ──────────────────────────────────────────


def test_court_records_wire_returns_none_on_empty_entity():
    from aria_service.intel.dd_layer_extensions import run_court_records_check
    report = _make_report(entity_name="")
    out = asyncio.run(run_court_records_check({}, report))
    assert out is None


def test_court_records_wire_returns_none_when_under_3_chars():
    from aria_service.intel.dd_layer_extensions import run_court_records_check
    report = _make_report(entity_name="AB")
    out = asyncio.run(run_court_records_check({}, report))
    assert out is None


def test_court_records_wire_no_hits_returns_summary():
    """Capability: empty hit list still emits a finding so the
    operator sees "we checked and there was nothing", not silence."""
    async def _run():
        with patch(
            "aria_service.intel.sources.court_records.search_all",
            new=AsyncMock(return_value={"entity": "Acme", "hits": [],
                                         "us_count": 0, "uk_count": 0,
                                         "sources": ["courtlistener", "bailii"]}),
        ):
            from aria_service.intel.dd_layer_extensions import run_court_records_check
            return await run_court_records_check({}, _make_report())
    out = asyncio.run(_run())
    assert out is not None
    assert out["module"] == "court_records"
    assert out["r_number"] == "R-F584"
    assert out["severity"] == "NONE"
    assert out["hits"] == []


def test_court_records_wire_federal_hit_marks_elevated():
    """Capability: a US federal-court hit (S.D.N.Y.) lifts severity
    to ELEVATED for the compliance layer to factor in."""
    async def _run():
        with patch(
            "aria_service.intel.sources.court_records.search_all",
            new=AsyncMock(return_value={
                "entity": "Acme",
                "hits": [{
                    "title": "United States v. Acme Defence",
                    "court": "S.D.N.Y.",
                    "date": "2023-08-15",
                    "citation_url": "https://courtlistener.com/x",
                    "snippet": "", "jurisdiction": "US",
                    "source": "courtlistener", "tier": "1a",
                }],
                "us_count": 1, "uk_count": 0,
                "sources": ["courtlistener", "bailii"],
            }),
        ):
            from aria_service.intel.dd_layer_extensions import run_court_records_check
            return await run_court_records_check({}, _make_report())
    out = asyncio.run(_run())
    assert out["severity"] == "ELEVATED"
    assert out["us_count"] == 1
    assert "Federal court" in out["summary"]


# ── R-F585 cert_transparency wire ──────────────────────────────────────


def test_extract_domain_prefers_explicit_field():
    from aria_service.intel.dd_layer_extensions import _extract_domain
    target = {"website": "https://www.example.com/about"}
    assert _extract_domain(target, _make_report()) == "example.com"


def test_extract_domain_falls_back_to_entity_name():
    from aria_service.intel.dd_layer_extensions import _extract_domain
    out = _extract_domain({}, _make_report(entity_name="Acme via shellcorp.io front"))
    assert out == "shellcorp.io"


def test_extract_domain_empty_when_no_signal():
    from aria_service.intel.dd_layer_extensions import _extract_domain
    out = _extract_domain({}, _make_report(entity_name="Just a Name"))
    assert out == ""


def test_cert_transparency_wire_shell_pattern_elevates():
    async def _run():
        with patch(
            "aria_service.intel.sources.cert_transparency.search_certs",
            new=AsyncMock(return_value=[
                {"common_name": f"shell{i}.io", "alt_names": [],
                 "issuer": "Let's Encrypt R3", "not_before": "2024-01-01",
                 "not_after": "2024-04-01", "ct_url": "x",
                 "source": "crt.sh", "tier": "1a"}
                for i in range(20)
            ]),
        ):
            from aria_service.intel.dd_layer_extensions import run_cert_transparency_check
            return await run_cert_transparency_check({"website": "shell0.io"}, _make_report())
    out = asyncio.run(_run())
    assert out is not None
    assert out["module"] == "cert_transparency"
    assert out["r_number"] == "R-F585"
    assert out["cert_count"] == 20
    assert out["severity"] in ("LOW", "ELEVATED")


def test_cert_transparency_wire_no_data_returns_none_severity():
    async def _run():
        with patch(
            "aria_service.intel.sources.cert_transparency.search_certs",
            new=AsyncMock(return_value=[]),
        ):
            from aria_service.intel.dd_layer_extensions import run_cert_transparency_check
            return await run_cert_transparency_check({"website": "example.com"}, _make_report())
    out = asyncio.run(_run())
    assert out is not None
    assert out["severity"] == "NONE"


# ── R-F586 eccn_lookup wire ────────────────────────────────────────────


def test_eccn_wire_pure_function_no_hits():
    from aria_service.intel.dd_layer_extensions import run_eccn_indicator_check
    target = {"description": "office supplies for school district"}
    out = asyncio.run(run_eccn_indicator_check(target, _make_report()))
    assert out is not None
    assert out["module"] == "eccn_lookup"
    assert out["r_number"] == "R-F586"
    assert out["severity"] == "NONE"
    assert out["hits"] == []


def test_eccn_wire_uav_match_elevates():
    """Capability: 'Bayraktar TB2 drone' description should hit
    9A010 and lift severity to ELEVATED (multiple keyword matches)."""
    from aria_service.intel.dd_layer_extensions import run_eccn_indicator_check
    target = {
        "equipment_description":
            "We're sourcing Bayraktar TB2 drone systems with associated UAV "
            "datalink and ground control station components.",
    }
    out = asyncio.run(run_eccn_indicator_check(target, _make_report()))
    assert out is not None
    assert len(out["hits"]) >= 1
    eccns = [h["eccn"] for h in out["hits"]]
    assert "9A010" in eccns
    assert out["severity"] == "ELEVATED"


def test_eccn_wire_returns_none_for_empty_text():
    from aria_service.intel.dd_layer_extensions import run_eccn_indicator_check
    out = asyncio.run(run_eccn_indicator_check({}, _make_report(entity_name="")))
    assert out is None


def test_eccn_wire_gathers_from_multiple_fields():
    """Capability: equipment text comes from several possible target
    fields; the gatherer must consider all of them."""
    from aria_service.intel.dd_layer_extensions import _gather_equipment_text
    target = {
        "equipment": "thermal imager night vision",
        "narrative": "for surveillance applications",
    }
    text = _gather_equipment_text(target, _make_report())
    assert "thermal imager" in text
    assert "surveillance" in text


# ── R-F587 ais_gap_detector wire ───────────────────────────────────────


def test_ais_wire_skipped_when_no_vessel_context():
    """Capability: entity-only DD with no vessel data returns None
    silently — no false-positive signal."""
    from aria_service.intel.dd_layer_extensions import run_ais_gap_check
    out = asyncio.run(run_ais_gap_check({"entity": "Acme"}, _make_report()))
    assert out is None


def test_ais_wire_runs_on_vessel_context():
    from aria_service.intel.dd_layer_extensions import run_ais_gap_check
    target = {
        "vessel_imo": "9837492",
        "ais_positions": [
            {"timestamp": 1_700_000_000, "lat": 0.0, "lon": 0.0},
            {"timestamp": 1_700_000_000 + 4 * 3600, "lat": 0.1, "lon": 0.1},
        ],
    }
    out = asyncio.run(run_ais_gap_check(target, _make_report()))
    assert out is not None
    assert out["module"] == "ais_gap_detector"
    assert out["r_number"] == "R-F587"
    assert out["vessel_id"] == "9837492"


def test_ais_wire_critical_gap_elevates():
    """Capability: any critical-severity gap → severity=ELEVATED
    regardless of overall score."""
    from aria_service.intel.dd_layer_extensions import run_ais_gap_check
    target = {
        "vessel_name": "MV Ghost",
        "ais_positions": [
            {"timestamp": 1_700_000_000, "lat": 0.0, "lon": 0.0},
            # Impossible jump: 1000 km in 2h
            {"timestamp": 1_700_000_000 + 2 * 3600, "lat": 0.0, "lon": 9.0},
        ],
    }
    out = asyncio.run(run_ais_gap_check(target, _make_report()))
    assert out is not None
    assert out["severity"] == "ELEVATED"


# ── Bundle wrapper ─────────────────────────────────────────────────────


def test_run_all_extensions_aggregates_results():
    """Capability: run_all_extensions returns ran/skipped breakdown
    + max_severity rollup that dd_orchestrator can attach to report."""
    async def _run():
        with patch(
            "aria_service.intel.sources.court_records.search_all",
            new=AsyncMock(return_value={"entity": "Acme", "hits": [],
                                         "us_count": 0, "uk_count": 0,
                                         "sources": []}),
        ), patch(
            "aria_service.intel.sources.cert_transparency.search_certs",
            new=AsyncMock(return_value=[]),
        ):
            from aria_service.intel.dd_layer_extensions import run_all_extensions
            return await run_all_extensions(
                {"description": "Bayraktar TB2 drone", "website": "shellcorp.io"},
                _make_report(),
            )
    out = asyncio.run(_run())
    # court_records + cert_transparency + eccn_lookup → ran
    # ais_gap_detector → skipped (no vessel)
    assert "ais_gap_detector" in out["skipped"]
    assert "eccn_lookup" in out["ran"]    # got hits from "Bayraktar TB2 drone"
    assert out["max_severity"] in ("NONE", "LOW", "ELEVATED")
    assert isinstance(out["by_module"], dict)


def test_run_all_extensions_handles_one_module_failure():
    """Capability: if one shim throws, the others still complete."""
    async def _run():
        with patch(
            "aria_service.intel.sources.court_records.search_all",
            new=AsyncMock(side_effect=RuntimeError("network blip")),
        ), patch(
            "aria_service.intel.sources.cert_transparency.search_certs",
            new=AsyncMock(return_value=[]),
        ):
            from aria_service.intel.dd_layer_extensions import run_all_extensions
            return await run_all_extensions(
                {"description": "radar AESA", "website": "example.com"},
                _make_report(),
            )
    out = asyncio.run(_run())
    # eccn_lookup should still surface a hit
    assert "eccn_lookup" in out["ran"]
    # court_records: the shim catches its own exception and returns None,
    # so it appears in skipped (not errors). That's by design — fail-open.


def test_run_all_extensions_severity_rollup():
    """Capability: max_severity reflects the highest tier across all
    ran modules."""
    async def _run():
        with patch(
            "aria_service.intel.sources.court_records.search_all",
            new=AsyncMock(return_value={"entity": "Acme", "hits": [], "us_count": 0,
                                         "uk_count": 0, "sources": []}),
        ), patch(
            "aria_service.intel.sources.cert_transparency.search_certs",
            new=AsyncMock(return_value=[]),
        ):
            from aria_service.intel.dd_layer_extensions import run_all_extensions
            return await run_all_extensions(
                # Strong ECCN match → ELEVATED
                {"description": "Bayraktar TB2 drone with autopilot and gimbal payload"},
                _make_report(),
            )
    out = asyncio.run(_run())
    # eccn_lookup with ≥2 keyword matches lifts to ELEVATED
    assert out["max_severity"] == "ELEVATED"


# ── dd_orchestrator wiring (static-source) ─────────────────────────────


def test_dd_orchestrator_imports_and_calls_run_all_extensions():
    """Capability proof: the single insertion point in dd_orchestrator
    is present so the extension bundle actually fires during a DD run."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("intel", "dd_orchestrator.py").read_text(encoding="utf-8")
    assert "from . import dd_layer_extensions as _dlx" in src, (
        "R-F584-F587 wire: dd_orchestrator must import dd_layer_extensions"
    )
    assert "_dlx.run_all_extensions(target, report" in src, (
        "R-F584-F587 wire: dd_orchestrator must call run_all_extensions"
    )
    # And it must fire BEFORE verification so the new findings feed it
    assert src.index("_dlx.run_all_extensions") < src.index('layer_name = "verification"'), (
        "R-F584-F587 wire: must call run_all_extensions BEFORE verification"
    )

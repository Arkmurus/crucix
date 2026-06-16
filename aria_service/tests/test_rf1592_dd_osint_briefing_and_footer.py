"""R-F1592 — DD surfaces OSINT (sources + briefing) instead of burying it.

Operator 2026-06-15: a WhatsApp DD on gozensecurity.com gathered data (12
press items, clean sanctions) but reported only "INSUFFICIENT EVIDENCE" with a
footer of "0 grounded / NO_TOOL / from memory" — looked like ARIA "can't do
anything." Verified root cause (NOT the LLM, which works): (a) render_markdown
emitted "Press coverage: N item(s)" with NO URLs, so source_verifier counted 0
cited → the honest footer said 0 grounded; (b) the confidence-gate BLUF
declared INSUFFICIENT on missing registry data without surfacing the OSINT it
gathered.

R-F1592: (1) render the press source URLs into the DD markdown so the footer
counts them; (2) when the gate triggers BUT OSINT exists, lead the BLUF with a
"LIMITED REGISTRY DATA — OPEN-SOURCE briefing" instead of bare INSUFFICIENT;
(3) register the codebase_health gap type.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import (
    ARKDDReport, Evidence, LayerStatus, RiskClassification,
)
from aria_service.intel.dd_orchestrator import _assemble_bluf
from aria_service.intel.capability_gaps import VALID_GAP_TYPES

# The confidence-gate BLUF only fires on an AMBER-LIGHT + gate-triggered run
# (the exact gozensecurity case): data too thin to issue GREEN.
_AMBER = RiskClassification.AMBER_LIGHT.value


def _report_with_press():
    r = ARKDDReport(target={"name": "subject", "type": "company"})
    r.identity.entity_name = "subject"   # skips the R-F398 fallback web search
    r.digital.meta.status = LayerStatus.OK.value if hasattr(LayerStatus, "OK") else "complete"
    r.digital.press_coverage = [
        Evidence(source="Reuters", source_tier="T1", url="https://reuters.com/article-x"),
        Evidence(source="AP", source_tier="T1", url="https://apnews.com/article-y"),
    ]
    return r


def test_rf1592_render_markdown_includes_press_urls():
    """FIX #1: the source URLs must appear in the markdown so the downstream
    source_verifier can count them (otherwise footer = 0 grounded)."""
    md = _report_with_press().render_markdown(concise=False)
    assert "https://reuters.com/article-x" in md, (
        "R-F1592: press URLs not rendered — source_verifier will find 0 cited "
        "and the footer will say '0 grounded / from memory' on a data-rich DD."
    )
    assert "https://apnews.com/article-y" in md


@pytest.mark.asyncio
async def test_rf1592_bluf_surfaces_osint_when_registry_absent():
    """FIX #2: gate triggered + OSINT present → 'LIMITED REGISTRY DATA' briefing
    that names the counts, NOT a bare 'INSUFFICIENT EVIDENCE'."""
    r = _report_with_press()
    r.risk_classification = _AMBER
    r.confidence_gate_triggered = True
    r.confidence_gate_reasons = ["registry verification incomplete (missing: directors)"]
    await _assemble_bluf(r)
    assert "LIMITED REGISTRY DATA" in r.bottom_line, (
        f"R-F1592: OSINT was gathered but BLUF buried it. Got: {r.bottom_line[:200]}"
    )
    assert "2 press item" in r.bottom_line
    assert "INSUFFICIENT EVIDENCE" not in r.bottom_line
    # Still honest about the registry gap.
    assert "registry" in r.bottom_line.lower()


@pytest.mark.asyncio
async def test_rf1592_bluf_still_insufficient_when_truly_empty():
    """Regression: gate triggered AND no OSINT → keep the honest INSUFFICIENT
    EVIDENCE verdict (don't claim a briefing when nothing was found)."""
    r = ARKDDReport(target={"name": "subject", "type": "company"})
    r.identity.entity_name = "subject"
    r.risk_classification = _AMBER
    r.confidence_gate_triggered = True
    r.confidence_gate_reasons = ["registry verification incomplete"]
    await _assemble_bluf(r)
    assert "INSUFFICIENT EVIDENCE" in r.bottom_line
    assert "LIMITED REGISTRY DATA" not in r.bottom_line


def test_rf1592_codebase_health_gap_registered():
    assert "codebase_health" in VALID_GAP_TYPES, (
        "R-F1592: codebase_health missing — eagle_eye's gaps get rejected."
    )

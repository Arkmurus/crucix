"""R-F398 — DD orchestrator fallback web-search before INSUFFICIENT_EVIDENCE.

Live evidence 2026-05-13: ARIA self-reported that "GROUNDING_CHECK
returns 0% on many entities. User sees 'INSUFFICIENT EVIDENCE' — does
not trust the output. Insert a fallback full web search BEFORE
emitting the 'INSUFFICIENT EVIDENCE' verdict. The dd_orchestrate has
9 layers but the web-search layer appears to run only on real
domains, not on person names. Run [search] on each name + 'LinkedIn'
variation."

Fix: before the AMBER_LIGHT + confidence_gate_triggered BLUF emission
in `_assemble_bluf()`, run a fallback web search on the entity name
(+ LinkedIn variants). Stitch any hits into `report.verification.
data_gaps` so the operator sees the public-intel surface that the
9-layer DD missed. The verdict stays INSUFFICIENT (gate triggered
legitimately — registry / director data is still missing) but the
report no longer pretends nothing was findable.

Source-level checks pin the wiring. Behavioural check is partial —
we can't fire `_web_search` in a unit test without network — so we
assert the call site exists and the fallback path is structurally
correct.
"""
from __future__ import annotations

import pathlib


def _src() -> str:
    return pathlib.Path(
        "C:/code/crucix/aria_service/intel/dd_orchestrator.py"
    ).read_text(encoding="utf-8", errors="ignore")


def _bluf_block() -> str:
    """The AMBER_LIGHT + confidence_gate_triggered branch of
    _assemble_bluf — where R-F398 must wire in."""
    src = _src()
    idx = src.find("elif risk == RiskClassification.AMBER_LIGHT.value:")
    assert idx > 0
    # R-F1592: the AMBER_LIGHT gate handler now contains a nested if/else
    # (OSINT-briefing vs INSUFFICIENT EVIDENCE), so bounding on the first
    # "    else:" cut the block before the INSUFFICIENT branch. Use a generous
    # fixed window that covers the whole AMBER_LIGHT handler.
    # R-F2361: widened 6500→8000 after the data-starved reframe added the
    # _data_starved substance check at the top of the handler.
    return src[idx:idx + 8000]


def test_rf398_block_contains_fallback_search_wiring():
    """The AMBER_LIGHT/gate-triggered branch must contain the R-F398
    fallback web search before emitting the verdict."""
    block = _bluf_block()
    assert "R-F398" in block, (
        "R-F398 regression: fallback search marker missing from BLUF block."
    )
    assert "_web_search" in block, (
        "R-F398: web-search function not invoked in fallback path."
    )


def test_rf398_uses_entity_name_variants():
    """The fallback must search both the bare entity name AND a
    LinkedIn-targeted variant (ARIA's spec: 'name + LinkedIn')."""
    block = _bluf_block()
    assert "LinkedIn" in block, (
        "R-F398: LinkedIn variant missing — spec said name + LinkedIn."
    )
    assert "site:linkedin.com" in block or "linkedin.com" in block.lower(), (
        "R-F398: site:linkedin.com targeted query missing."
    )


def test_rf398_results_stitched_into_data_gaps():
    """Hits from the fallback search must be appended to
    `report.verification.data_gaps` so the operator sees them.
    Critical for the honesty story — without this, the verdict is
    INSUFFICIENT but the report has no breadcrumb that public intel
    actually exists."""
    block = _bluf_block()
    assert "report.verification.data_gaps.append" in block, (
        "R-F398: fallback hits not stitched into data_gaps."
    )


def test_rf398_never_breaks_bluf_on_search_error():
    """The fallback must be wrapped so a search exception (network
    timeout, backend down) doesn't crash BLUF generation. This is the
    discipline that prevents R-F398 from regressing the BLUF path
    when the network is flaky."""
    block = _bluf_block()
    # The try / except wrapper must exist
    assert "try:" in block
    assert "except Exception:" in block, (
        "R-F398: fallback search not wrapped in except — a network "
        "timeout would crash _assemble_bluf and orphan the report."
    )
    # And there must be a comment naming the discipline (never break)
    assert "never break" in block.lower() or "BLUF on fallback-search error" in block, (
        "R-F398: missing the 'never break BLUF' comment — future "
        "contributors might remove the except as 'unnecessary'."
    )


def test_rf398_timeout_on_each_variant_search():
    """Each fallback search variant must have a tight timeout so a
    hung backend doesn't add 30+ seconds to BLUF generation."""
    block = _bluf_block()
    assert "asyncio.wait_for" in block, (
        "R-F398: missing asyncio.wait_for — fallback search could "
        "hang indefinitely on a slow backend."
    )
    assert "timeout=8.0" in block or "timeout=8" in block, (
        "R-F398: timeout value drifted from 8s. If intentional, "
        "update this assertion."
    )


def test_rf398_bluf_suffix_when_hits_found():
    """When fallback search returns hits, the BLUF headline must
    explicitly mention them — otherwise the operator reading just
    the headline misses the breadcrumb."""
    block = _bluf_block()
    assert "_fallback_suffix" in block or "fallback search found" in block.lower(), (
        "R-F398: BLUF headline doesn't surface fallback hits."
    )


def test_rf398_does_not_lift_the_gate():
    """The gate is triggered by missing registry/director data — not
    by missing web hits. R-F398 must NOT lift the INSUFFICIENT_EVIDENCE
    verdict when fallback hits are found; that would be dishonest.
    The hits are surfaced in data_gaps, not promoted to the verdict."""
    block = _bluf_block()
    # The BLUF still says INSUFFICIENT EVIDENCE even with hits
    assert "INSUFFICIENT EVIDENCE" in block, (
        "R-F398 regression: INSUFFICIENT EVIDENCE verdict removed — "
        "the gate must stay honest about the registry/director gap."
    )
    # And the BLUF is the SAME string regardless of fallback success —
    # only the suffix changes.
    assert "AMBER is a placeholder" in block

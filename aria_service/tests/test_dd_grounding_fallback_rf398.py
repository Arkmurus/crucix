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


def test_rf398_does_not_lift_the_gate(monkeypatch):
    """The gate is triggered by missing registry/director data — not by missing
    web hits. R-F398 must NOT lift the INSUFFICIENT/gate verdict when fallback
    hits are found; that would be dishonest. The hits are surfaced in data_gaps,
    not promoted to the verdict.

    Rewritten R-F2784 (2026-07-19): the previous check asserted the historical
    source phrase ``"AMBER is a placeholder"`` inside an 8000-char source window.
    The phrase still exists (dd_orchestrator.py) but the AMBER_LIGHT handler grew
    past the window, so the match drifted off the end — a stale source-coupling,
    not a real defect. Worse, the old claim "the BLUF is the SAME string
    regardless of fallback success" is itself obsolete: R-F1592 reframes to
    "LIMITED REGISTRY DATA" once OSINT (incl. fallback hits) exists. This drives
    _assemble_bluf and asserts the actual invariant instead (§23).
    """
    import asyncio

    from aria_service.intel import researcher
    from aria_service.intel.dd_orchestrator import _assemble_bluf
    from aria_service.intel.dd_schema import ARKDDReport, RiskClassification

    _AMBER = RiskClassification.AMBER_LIGHT.value

    async def _fake_web_search(query, *_a, **_k):
        # Public hits DO exist — the honest question is whether they lift the gate.
        return [{
            "title": f"hit for {query}",
            "url": "https://www.linkedin.com/in/example",
            "snippet": "public profile",
        }]

    # Production does `from .researcher import _web_search` at call time, so
    # patching the attribute on the module is picked up by the handler.
    monkeypatch.setattr(researcher, "_web_search", _fake_web_search)

    r = ARKDDReport(target={"name": "Zephyr Holdings Ltd", "type": "company"})
    r.identity.entity_name = "Zephyr Holdings Ltd"  # != 'subject' → R-F398 fallback fires
    r.risk_classification = _AMBER
    r.confidence_gate_triggered = True
    # data-starved: no directors / registration_status / incorporation_date set

    asyncio.run(_assemble_bluf(r))

    # 1. Fallback hits are surfaced as a breadcrumb in data_gaps (not hidden).
    assert any("R-F398 fallback" in g for g in r.verification.data_gaps), (
        "R-F398: fallback hits not stitched into data_gaps."
    )
    # 2. The gate is NOT lifted — the verdict stays AMBER-LIGHT + gate triggered.
    assert r.risk_classification == _AMBER, (
        f"R-F398 regression: fallback hits LIFTED the risk classification to "
        f"{r.risk_classification!r} — dishonest; registry/director data still missing."
    )
    assert r.confidence_gate_triggered is True, (
        "R-F398 regression: fallback hits cleared the confidence gate."
    )
    # 3. The headline never reads as clean/reassuring and still flags the gap.
    bl = r.bottom_line.lower()
    assert "can proceed" not in bl, (
        f"R-F398 regression: BLUF over-reassures despite the registry gap: "
        f"{r.bottom_line[:200]}"
    )
    assert "registry" in bl, (
        f"R-F398: BLUF dropped the honest registry-gap statement: {r.bottom_line[:200]}"
    )

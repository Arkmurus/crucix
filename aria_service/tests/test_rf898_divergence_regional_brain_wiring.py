"""R-F898 — sanctions_divergence + regional_compliance feed the brain.

Ecosystem-360 P2:
- sanctions_divergence.analyze_divergence found cross-list divergences (listed
  on OFAC but silent on EU/UN, etc.) but recorded NOTHING to the brain — the
  learning/success side was dark. Now a real divergence fires brain_hook.
  absorb_silent (pay-once-remember §15), gated so "listed everywhere"/"nowhere"
  don't signal.
- regional_compliance.ingest_all_sections logged a section-ingest failure but
  emitted no structured gap. Now it records a knowledge_gap (the loop acts on
  gap type; the logger.error reaches the ledger via R-F891's ARIA.* catch).
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


def test_rf898_sanctions_module_key_valid():
    import aria_service.intel.brain_hook as bh
    assert "sanctions" in bh._MODULE_TOPICS


def test_rf898_real_divergence_absorbs():
    """Capability: a real divergence (listed on some tracked jurisdictions,
    silent on others) records a brain catch."""
    from aria_service.intel import sanctions_divergence as sd, brain_hook

    calls: list = []

    async def fake_absorb(**kwargs):
        calls.append(kwargs)

    async def fake_screen(name, threshold=0.78):
        return {"screened": True, "matches": [{"name": name, "datasets": ["ds"]}]}

    tracked = list(sd._TRACKED_JURISDICTIONS)
    assert len(tracked) > 1, "need >1 tracked jurisdiction for a divergence"
    one = tracked[0]

    async def run():
        with patch("aria_service.intel.sanctions.fuzzy_screen", side_effect=fake_screen), \
             patch.object(sd, "_shares_substantive_token", return_value=True), \
             patch.object(sd, "_build_per_match", return_value={"jurisdictions": [one]}), \
             patch.object(brain_hook, "absorb_silent", side_effect=fake_absorb):
            res = await sd.analyze_divergence("Acme Trading Corp")
            await asyncio.sleep(0.02)  # let the fire-and-forget task run
        assert res["divergence_count"] > 0
        assert calls, "a real divergence must record a brain catch"
        assert calls[0]["module"] == "sanctions"
        assert "divergence" in calls[0]["summary"].lower()
        assert calls[0]["source_id"] == "sanctions_divergence:R-F898"

    asyncio.run(run())


def test_rf898_no_match_no_absorb():
    """No sanctions match → no divergence → no brain catch (noise control)."""
    from aria_service.intel import sanctions_divergence as sd, brain_hook

    calls: list = []

    async def fake_absorb(**kwargs):
        calls.append(kwargs)

    async def fake_screen(name, threshold=0.78):
        return {"screened": True, "matches": []}

    async def run():
        with patch("aria_service.intel.sanctions.fuzzy_screen", side_effect=fake_screen), \
             patch.object(brain_hook, "absorb_silent", side_effect=fake_absorb):
            res = await sd.analyze_divergence("Definitely Clean Ltd")
            await asyncio.sleep(0.01)
        assert res["divergence_count"] == 0
        assert calls == [], "no divergence must not absorb"

    asyncio.run(run())


def test_rf898_regional_ingest_failure_records_knowledge_gap():
    """Capability: a regional-compliance section that fails to ingest records a
    structured knowledge_gap."""
    from aria_service.intel import regional_compliance as rc, capability_gaps, rag_store

    gaps: list = []

    async def fake_record_gap(**kwargs):
        gaps.append(kwargs)
        return {}

    async def boom_ingest(*a, **k):
        raise RuntimeError("rag store unavailable")

    async def run():
        with patch.object(rag_store, "ingest_document", side_effect=boom_ingest), \
             patch.object(capability_gaps, "record_gap", side_effect=fake_record_gap):
            res = await rc.ingest_all_sections()
        assert res["sections_ingested"] == 0
        assert any(g.get("gap_type") == "knowledge_gap" for g in gaps), gaps
        assert any("regional_compliance section" in (g.get("detail") or "") for g in gaps)

    asyncio.run(run())

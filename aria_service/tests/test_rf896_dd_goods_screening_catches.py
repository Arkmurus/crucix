"""R-F896 — DD goods-screening compliance CATCHES feed the brain.

Ecosystem-360 P2: the goods-screening sub-engines (weapon_origin_catalogue,
goods_list_aggregator_detector, evasion_typology_detector, end_user_granularity,
mou_clause_gate_analyser) were dark — their failure side was wired by R-F891
(ARIA.* logger → ledger) and eliminated_weapons' catch by R-F892, but these five
recorded NOTHING when they flagged something. So ARIA never learned from her own
compliance flags. R-F896 fires brain_hook.absorb_silent on each flag-level
finding (severity-gated: routine 'info' findings are skipped to avoid noise).
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path
from unittest.mock import patch

SRC = (Path(__file__).resolve().parents[1] / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")


def test_rf896_module_key_valid():
    import aria_service.intel.brain_hook as bh
    assert "dd_orchestrator" in bh._MODULE_TOPICS


def test_rf896_all_five_engines_wired():
    # 1 def + 5 call sites
    assert SRC.count("_absorb_dd_compliance_catch(") == 6
    for label in (
        "DD weapon-origin flag",
        "DD aggregation-pattern flag",
        "DD evasion-typology flag",
        "DD end-user-granularity flag",
        "DD MOU clause-gate flag",
    ):
        assert label in SRC, f"missing brain wiring for: {label}"


def test_rf896_flag_absorbs_and_info_skipped():
    """Capability test: a flag-level finding (red/amber/hard_stop) records a
    brain catch; a routine 'info' finding is skipped (noise control)."""
    from aria_service.intel import dd_orchestrator as dd, brain_hook

    calls: list = []

    async def fake_absorb(**kwargs):
        calls.append(kwargs)

    async def run():
        report = types.SimpleNamespace(
            identity=types.SimpleNamespace(legal_name="Acme Defense Ltd")
        )
        target = {"name": "acme-fallback"}
        with patch.object(brain_hook, "absorb_silent", side_effect=fake_absorb):
            dd._absorb_dd_compliance_catch(report, target, "info", "routine assessment", "x")
            dd._absorb_dd_compliance_catch(report, target, "red", "DD evasion-typology flag: X", "d1")
            dd._absorb_dd_compliance_catch(report, target, "hard_stop", "DD aggregation-pattern flag: Y", "d2")
            await asyncio.sleep(0.02)  # let the fire-and-forget tasks run

        assert len(calls) == 2, f"'info' must be skipped; only flag-level absorbed: {calls}"
        assert all(c["module"] == "dd_orchestrator" and c["success"] is True for c in calls)
        # prefers the resolved identity.legal_name over the raw target name
        assert calls[0]["entity_name"] == "Acme Defense Ltd"
        assert any("evasion-typology" in c["summary"] for c in calls)


    asyncio.run(run())


def test_rf896_never_raises_without_loop():
    """Helper must be safe (no crash) even when called outside a running loop
    — create_task fails, the broad except swallows it, no signal."""
    from aria_service.intel import dd_orchestrator as dd
    report = types.SimpleNamespace(identity=types.SimpleNamespace(legal_name="X"))
    # No running loop here → create_task raises RuntimeError internally → swallowed.
    dd._absorb_dd_compliance_catch(report, {"name": "x"}, "red", "flag", "detail")

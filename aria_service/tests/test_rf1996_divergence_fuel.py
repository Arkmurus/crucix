"""R-F1996 — divergence-to-training-fuel flywheel.

Where ARIA's local stack ANSWERS but materially disagrees with the DeepSeek
teacher is exactly where the local model must improve. Those cases were stored
only as 300-char previews for mastery scoring and never reached the training
pipeline (broken flywheel). R-F1996 captures full-length fuel at the divergence
point and a collector feeds it into the daily export as SFT (chosen=cloud) +
DPO rejected (local). These tests pin the capture gating + the collector shape.
"""
import asyncio

from aria_service.intel import student


def test_fuel_captured_only_for_genuine_divergence():
    async def run():
        # genuine: local answered substantively AND disagreed (low similarity)
        wrote = await student.record_divergence_fuel(
            "What sanctions lists is Acme on?",
            cloud_response="Acme appears on the OFAC SDN list as of 2026 with full detail " * 5,
            local_response="Acme is not on any list, it is clean and fine to proceed onboard." * 2,
            local_source="symbolic_reasoner",
            similarity=0.12,
        )
        assert wrote is True
    asyncio.run(run())


def test_fuel_skips_agreement_and_no_local_attempt():
    async def run():
        # local AGREED (high similarity) → not a learning case
        w1 = await student.record_divergence_fuel(
            "q1", "a long correct cloud answer " * 10,
            "a long correct cloud answer " * 10, "local_brain", similarity=0.95)
        assert w1 is False
        # local did NOT make a real attempt (too short) → no rejected to learn from
        w2 = await student.record_divergence_fuel(
            "q2", "a long correct cloud answer " * 10,
            "idk", "local_brain", similarity=0.1)
        assert w2 is False
        # local response None → no capture
        w3 = await student.record_divergence_fuel(
            "q3", "a long correct cloud answer " * 10, None, None, similarity=0.1)
        assert w3 is False
    asyncio.run(run())


def test_collector_builds_sft_plus_dpo_rejected_shape():
    from aria_service.learning import training_export

    async def run():
        # seed a genuine divergence
        await student.record_divergence_fuel(
            "Is Beta Ltd a front company?",
            cloud_response="Beta Ltd shows 2 employees against $50M claimed revenue — classic shell indicators. " * 4,
            local_response="Beta Ltd looks like a normal trading company with no issues at all here. " * 3,
            local_source="grounded_reasoner",
            similarity=0.2,
        )
        out = await training_export._collect_divergences(days=30)
        assert isinstance(out, list) and len(out) >= 1
        ex = next(e for e in out if "Beta Ltd" in e["user"])
        assert ex["assistant"].startswith("Beta Ltd shows")      # SFT chosen = cloud
        assert ex["meta"]["source"] == "divergence"
        assert "normal trading company" in ex["meta"]["rejected"]  # DPO rejected = local
        assert ex["meta"]["similarity"] == 0.2
    asyncio.run(run())


def test_run_daily_export_includes_divergence_collector():
    # Regression guard: the collector must be wired into the export.
    src = open("aria_service/learning/training_export.py", encoding="utf-8").read()
    assert "_collect_divergences(days_lookback)" in src
    assert "+ divergences" in src

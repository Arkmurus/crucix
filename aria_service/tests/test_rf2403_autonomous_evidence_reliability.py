"""R-F2403 — autonomous evidence reliability contracts.

Locks the surgical fixes:
  * aria_coder's gap_detector dependency has a registered contract.
  * state_store.stats exposes write-queue depth.
  * low-value/general domains cannot enter RAG memory unless source_validator
    has approved them or search credibility is already high.
  * the coder scoreboard records real loop outcomes.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


def test_gap_detector_contract_registered_before_coder_dependency_check():
    src = Path("aria_service/autonomous/coder_entrypoint.py").read_text(encoding="utf-8")
    assert 'agent_id="gap_detector"' in src
    assert "register_contract(_gap_detector_contract)" in src
    assert 'dependencies=["gap_detector", "self_improve"]' in src


def test_state_store_stats_exposes_queue_depth_when_disconnected():
    from aria_service.intel import state_store

    stats = asyncio.run(state_store.stats())

    assert "write_queue_depth" in stats
    assert "write_queue" in stats
    assert {"hot", "cold", "total", "max", "hot_cold_split"} <= set(stats["write_queue"])


def test_source_validator_blocks_low_value_memory_domain_without_approval(monkeypatch):
    from aria_service.intel import source_validator as sv

    async def fake_history(_key):
        return []

    monkeypatch.setattr(sv.rs, "get_json", fake_history)

    allowed, reason = asyncio.run(
        sv.memory_ingest_allowed("https://www.linkedin.com/help/privacy")
    )

    assert allowed is False
    assert reason == "low_value_domain_unapproved"


def test_source_validator_allows_approved_low_value_memory_domain(monkeypatch):
    from aria_service.intel import source_validator as sv

    async def fake_history(_key):
        return [{
            "domain": "linkedin.com",
            "validation_status": sv.ValidationStatus.APPROVED.value,
        }]

    monkeypatch.setattr(sv.rs, "get_json", fake_history)

    allowed, reason = asyncio.run(
        sv.memory_ingest_allowed("https://www.linkedin.com/pulse/real-report")
    )

    assert allowed is True
    assert reason == "source_validator_approved"


def test_rag_batch_ingest_calls_source_validator_gate():
    src = Path("aria_service/intel/rag_store.py").read_text(encoding="utf-8")
    assert "memory_ingest_allowed(url, tier)" in src
    assert "low-value source" in src


class _ScoreRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.data[key] = value


def test_coder_scoreboard_records_blocked_reason():
    from aria_service.autonomous.gap_detector import Gap, GapSeverity, GapType
    from aria_service.autonomous.self_coder import ARIACoder, SCOREBOARD_KEY

    coder = ARIACoder.__new__(ARIACoder)
    coder.redis = _ScoreRedis()
    gap = Gap(
        gap_id="rf2403-gap",
        gap_type=GapType.MODULE_BUG,
        severity=GapSeverity.HIGH,
        title="Broken contract",
        description="dependency_no_contract",
        module="gap_detector",
    )

    asyncio.run(coder._record_scoreboard("blocked", gap, reason="dependency_no_contract"))
    board = json.loads(coder.redis.data[SCOREBOARD_KEY])

    assert board["counts"]["blocked"] == 1
    assert board["recent"][0]["reason"] == "dependency_no_contract"


def test_coder_scoreboard_endpoint_is_wired():
    src = Path("aria_service/routes/aria.py").read_text(encoding="utf-8")
    assert '@router.get("/coder/scoreboard")' in src
    assert "return await coder.get_scoreboard()" in src

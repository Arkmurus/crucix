"""R-F2405 — close remaining autonomous evidence hardening gaps."""
from __future__ import annotations

import asyncio
import json


def test_rag_document_ingest_blocks_unapproved_low_value_url(monkeypatch):
    from aria_service.intel import rag_store

    async def fake_gate(*, url="", source="", credibility_tier=None):
        assert url == "https://www.linkedin.com/pulse/unapproved"
        return False, "low_value_domain_unapproved"

    async def fail_ensure():
        raise AssertionError("blocked URL must not initialise chromadb")

    monkeypatch.setattr(rag_store, "_memory_ingest_allowed", fake_gate)
    monkeypatch.setattr(rag_store, "_ensure_async", fail_ensure)

    out = asyncio.run(
        rag_store.ingest_document(
            "Evidence text " * 10,
            source="crawl:linkedin",
            source_type="article",
            url="https://www.linkedin.com/pulse/unapproved",
        )
    )

    assert out["ingested"] is False
    assert out["reason"] == "low_value_domain_unapproved"


def test_rag_fact_ingest_blocks_url_source_before_chromadb(monkeypatch):
    from aria_service.intel import rag_store

    async def fake_gate(*, url="", source="", credibility_tier=None):
        assert source == "https://www.reddit.com/r/osint/comments/x"
        return False, "low_value_domain_unapproved"

    async def fail_ensure():
        raise AssertionError("blocked fact must not initialise chromadb")

    monkeypatch.setattr(rag_store, "_memory_ingest_allowed", fake_gate)
    monkeypatch.setattr(rag_store, "_ensure_async", fail_ensure)

    ok = asyncio.run(
        rag_store.ingest_fact(
            "fact-rf2405",
            "source quality",
            "A low-value URL fact should not enter RAG facts.",
            source="https://www.reddit.com/r/osint/comments/x",
        )
    )

    assert ok is False


def test_rag_batch_uses_shared_memory_gate_for_url_source(monkeypatch):
    from aria_service.intel import rag_store

    calls: list[dict] = []

    async def fake_gate(*, url="", source="", credibility_tier=None):
        calls.append({"url": url, "source": source, "tier": credibility_tier})
        return False, "low_value_domain_unapproved"

    async def ok_ensure():
        return True

    class Coll:
        def upsert(self, **_kwargs):
            raise AssertionError("all low-value items should be filtered")

    monkeypatch.setattr(rag_store, "_memory_ingest_allowed", fake_gate)
    monkeypatch.setattr(rag_store, "_ensure_async", ok_ensure)
    monkeypatch.setattr(rag_store, "_documents_collection", Coll())

    n = asyncio.run(
        rag_store.add_search_results_batch([
            {
                "text": "Long enough source text " * 5,
                "source": "https://www.youtube.com/watch?v=abc",
                "metadata": {"credibility_tier": 5},
            }
        ])
    )

    assert n == 0
    assert calls == [{
        "url": "",
        "source": "https://www.youtube.com/watch?v=abc",
        "tier": 5,
    }]


class _ScoreRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.data[key] = value


def test_scoreboard_defaults_and_blocked_reason_counts():
    from aria_service.autonomous.gap_detector import Gap, GapSeverity, GapType
    from aria_service.autonomous.self_coder import ARIACoder, SCOREBOARD_KEY

    coder = ARIACoder.__new__(ARIACoder)
    coder.redis = _ScoreRedis()
    gap = Gap(
        gap_id="rf2405-gap",
        gap_type=GapType.MODULE_BUG,
        severity=GapSeverity.HIGH,
        title="Blocked source",
        description="source rejected",
        module="rag_store",
    )

    asyncio.run(coder._record_scoreboard("blocked", gap, reason="source_rejected"))
    board = asyncio.run(coder.get_scoreboard())

    assert board["schema_version"] == 2
    assert board["counts"] == {
        "claimed": 0,
        "fixed": 0,
        "staged": 0,
        "gold": 0,
        "blocked": 1,
    }
    assert board["blocked_by_reason"]["source_rejected"] == 1
    assert json.loads(coder.redis.data[SCOREBOARD_KEY])["schema_version"] == 2


def test_state_store_stats_exposes_queue_headroom_and_utilisation():
    from aria_service.intel import state_store

    stats = asyncio.run(state_store.stats())
    queue = stats["write_queue"]

    assert {"capacity", "headroom", "utilization", "hot_worker_alive"} <= set(queue)
    assert queue["headroom"] >= 0
    assert 0.0 <= queue["utilization"] <= 1.0

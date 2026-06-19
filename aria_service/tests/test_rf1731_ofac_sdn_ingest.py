"""R-F1731 — OFAC SDN ingest raises the sanctions heatmap cells (gate #2).

Capability test (§3c): proves the USER-VISIBLE outcome — that an ingested OFAC
entity produces a knowledge fact the REAL coverage_heatmap matcher counts in the
sanctions_screening cells (US + the entity's tracked country), so fact_count
RISES. Also proves the design trap: a naive topic ('ofac'/'sanctions') would NOT
match (missing the 'screening' domain token) → 0 heatmap movement.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import ofac_sdn_ingest as ing
from aria_service.intel import coverage_heatmap as ch


# ── fixtures: tiny OFAC-format CSVs (headerless, '-0-' = empty) ──────────────
# SDN.CSV cols: ent_num,name,sdn_type,program,title,call_sign,vess_type,...,remarks
_SDN = (
    b'1,"BAD ACTOR LDA","entity","ANGOLA-EO14",-0-,-0-,-0-,-0-,-0-,-0-,-0-,"shell co"\n'
    b'2,"DODGY PERSON","individual","RUSSIA-EO14","Director",-0-,-0-,-0-,-0-,-0-,-0-,-0-\n'
)
# ADD.CSV cols: ent_num,add_num,address,city_state_postal,country,remarks
_ADD = (
    b'1,1,"Rua 1","Luanda","Angola",-0-\n'
    b'2,1,"Ulitsa 2","Moscow","Russia",-0-\n'
)


def _cell_count(domain: str, juris: str, facts: list[dict]) -> int:
    fc, _ = ch._count_facts_for_cell_sync(domain, juris, facts, [])
    return fc


def test_crafted_fact_matches_sanctions_cells_and_naive_topic_does_not():
    topic, content = ing._entity_to_fact(
        {"name": "BAD ACTOR LDA", "sdn_type": "entity", "program": "ANGOLA-EO14"},
        "Angola",
    )
    assert topic == "sanctions_screening"
    fact = {"topic": topic, "content": content, "source": "ofac_sdn"}
    facts = [fact]
    # The REAL matcher counts it for both the US (OFAC authority) and Angola cells.
    assert _cell_count("sanctions_screening", "Angola", facts) == 1
    assert _cell_count("sanctions_screening", "US", facts) == 1

    # THE TRAP: a naive topic loses the 'screening' domain token → 0 cells.
    naive = {"topic": "ofac", "content": "BAD ACTOR LDA on the OFAC list (Angola)",
             "source": "ofac_sdn"}
    assert _cell_count("sanctions_screening", "Angola", [naive]) == 0


@pytest.mark.asyncio
async def test_ingest_csv_bytes_stores_sanctions_facts_with_country():
    calls = []

    async def _capture(topic, content, source="user", confidence="CONFIRMED", **kw):
        calls.append({"topic": topic, "content": content, "source": source})
        return {"action": "created"}

    with patch("aria_service.intel.knowledge.store_fact", _capture):
        summary = await ing.ingest_csv_bytes(_SDN, _ADD, max_rows=10)

    assert summary["ingested"] == 2
    assert all(c["topic"] == "sanctions_screening" for c in calls)
    # The Angola (weak-tracked) entity was prioritised first.
    assert summary["weak_juris_prioritised"] >= 1
    assert "Angola" in calls[0]["content"]
    # Every fact carries the US authority synonym so it also lifts the US cell.
    assert all("United States" in c["content"] for c in calls)


@pytest.mark.asyncio
async def test_ingest_raises_heatmap_floor_cell():
    """End-to-end via the REAL matcher: sanctions_screening×Angola count RISES."""
    captured: list[dict] = []

    async def _capture(topic, content, source="user", confidence="CONFIRMED", **kw):
        captured.append({"topic": topic, "content": content, "source": source})
        return {"action": "created"}

    before = _cell_count("sanctions_screening", "Angola", captured)
    with patch("aria_service.intel.knowledge.store_fact", _capture):
        await ing.ingest_csv_bytes(_SDN, _ADD, max_rows=10)
    after = _cell_count("sanctions_screening", "Angola", captured)
    assert after > before, "ingesting an Angola OFAC entity must raise the sanctions×Angola cell"


@pytest.mark.asyncio
async def test_fetch_failure_is_honest_and_wired():
    class _Resp:
        status_code = 503
        content = b""

    async def _get(*a, **k):
        return _Resp()

    with patch("httpx.AsyncClient") as mc:
        inst = mc.return_value.__aenter__.return_value
        inst.get = AsyncMock(side_effect=_get)
        res = await ing.fetch_and_ingest(max_rows=10)
    assert res["ingested"] == 0
    assert "error" in res

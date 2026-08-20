"""R-F4195 — relationship enrichment tests use the live source-query contract."""
from __future__ import annotations

import asyncio

from aria_service.intel import sanctions
from aria_service.intel.sanctions import _SourceQuery


def test_rf4195_relationship_enrichment_consumes_source_query_results(monkeypatch) -> None:
    """Drive the real enrichment function through its structured search result."""
    async def search(_query: str, limit: int = 5) -> _SourceQuery:
        assert limit == 2
        return _SourceQuery([{
            "id": "Q12345",
            "properties": {"name": ["Ivan Ivanov"], "topics": ["sanction"]},
            "datasets": ["eu_fsf", "ofac_sdn"],
            "caption": "Ivan Ivanov",
        }], True, "ok")

    monkeypatch.setattr(sanctions, "_opensanctions_search", search)
    result = asyncio.run(sanctions.enrich_with_relationships({
        "matches": [{
            "name": "Target Entity",
            "score": 0.95,
            "relationships": [{"kind": "spouseOf", "target": "Ivan Ivanov"}],
        }],
    }))

    assert result["inherited_risk_count"] == 1
    inherited = result["matches"][0]["inherited_risk"][0]
    assert inherited["target_name"] == "Ivan Ivanov"
    assert inherited["target_lists"] == ["eu_fsf", "ofac_sdn"]

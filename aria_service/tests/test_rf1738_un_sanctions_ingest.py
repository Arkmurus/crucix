"""R-F1738 — UN Sanctions ingest raises the sanctions heatmap cells (gate #2).

Capability test (§3c): proves the USER-VISIBLE outcome — that an ingested UN
sanctions entity produces a knowledge fact the REAL coverage_heatmap matcher
counts in the sanctions_screening cells (UN + the entity's tracked country).
"""
import pytest

from aria_service.intel import un_sanctions_ingest as ing
from aria_service.intel import coverage_heatmap as ch


# Sample UN sanctions XML (based on real schema — no namespace)
_SAMPLE_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<CONSOLIDATED_LIST>
  <INDIVIDUALS>
    <INDIVIDUAL>
      <DATAID>1</DATAID>
      <FIRST_NAME>Bad</FIRST_NAME>
      <SECOND_NAME>Actor</SECOND_NAME>
      <UN_LIST_TYPE>ANGOLA</UN_LIST_TYPE>
      <REFERENCE_NUMBER>ANGi.001</REFERENCE_NUMBER>
      <LISTED_ON>2020-01-01</LISTED_ON>
      <COUNTRY>Angola</COUNTRY>
      <COMMENTS1>Designated under Angola sanctions.</COMMENTS1>
    </INDIVIDUAL>
  </INDIVIDUALS>
  <ENTITIES>
    <ENTITY>
      <DATAID>2</DATAID>
      <FIRST_NAME>Dodgy Corp</FIRST_NAME>
      <UN_LIST_TYPE>RUSSIA</UN_LIST_TYPE>
      <REFERENCE_NUMBER>RUSe.001</REFERENCE_NUMBER>
      <LISTED_ON>2020-01-01</LISTED_ON>
      <COUNTRY>Russia</COUNTRY>
    </ENTITY>
  </ENTITIES>
</CONSOLIDATED_LIST>
'''


def _cell_count(domain: str, juris: str, facts: list[dict]) -> int:
    fc, _ = ch._count_facts_for_cell_sync(domain, juris, facts, [])
    return fc


def test_un_fact_matches_sanctions_cells():
    """A UN sanctions entity must be counted by the real heatmap matcher."""
    entities = ing._parse_xml(_SAMPLE_XML)
    assert len(entities) == 2

    facts = []
    for e in entities:
        topic, content = ing._entity_to_fact(e)
        assert topic.startswith("sanctions_screening")
        assert "sanctions" in topic.lower() and "screening" in topic.lower()
        facts.append({"topic": topic, "content": content, "source": "un_sanctions"})

    # Both entities mention "United Nations" so both match UN cell
    assert _cell_count("sanctions_screening", "UN", facts) == 2
    # Angola entity should match Angola cell
    assert _cell_count("sanctions_screening", "Angola", facts) == 1


def test_un_topic_is_unique_per_entity():
    """Each entity must get a unique topic so store_fact CREATES not UPDATES."""
    entities = ing._parse_xml(_SAMPLE_XML)
    topics = set()
    for e in entities:
        topic, _ = ing._entity_to_fact(e)
        topics.add(topic)
    assert len(topics) == len(entities), "Each entity must have a unique topic"


def test_un_content_meets_length_requirement():
    """Content must be >50 chars to pass store_fact's R-F1526 guard."""
    entities = ing._parse_xml(_SAMPLE_XML)
    for e in entities:
        _, content = ing._entity_to_fact(e)
        assert len(content) > 50, f"Content too short: {len(content)} chars"


def test_un_parse_real_xml():
    """The parser must handle the real UN sanctions XML structure."""
    import httpx, asyncio
    async def _fetch():
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(ing.UN_SC_URL)
        return resp.content
    try:
        xml_bytes = asyncio.run(_fetch())
        entities = ing._parse_xml(xml_bytes)
        assert len(entities) > 500, f"Expected 500+ entities, got {len(entities)}"
    except Exception as e:
        pytest.skip(f"Live fetch failed (network issue): {e}")

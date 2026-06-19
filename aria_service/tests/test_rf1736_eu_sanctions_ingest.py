"""R-F1736 — EU Sanctions ingest raises the sanctions heatmap cells (gate #2).

Capability test (§3c): proves the USER-VISIBLE outcome — that an ingested EU
sanctions entity produces a knowledge fact the REAL coverage_heatmap matcher
counts in the sanctions_screening cells (EU + the entity's tracked country).
"""
import pytest

from aria_service.intel import eu_sanctions_ingest as ing
from aria_service.intel import coverage_heatmap as ch


# Sample EU FSF XML (schema based on the EU Financial Sanctions File format)
_SAMPLE_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<root>
  <SanctionEntity>
    <FullName>Bad Actor LDA</FullName>
    <EntityType>Entity</EntityType>
    <SanctionsProgram>Angola</SanctionsProgram>
    <Country>Angola</Country>
    <ReferenceNumber>EU-ANG-001</ReferenceNumber>
    <Remark>Designated under Angola sanctions regime.</Remark>
  </SanctionEntity>
  <SanctionEntity>
    <FullName>Dodgy Person</FullName>
    <EntityType>Individual</EntityType>
    <SanctionsProgram>Russia</SanctionsProgram>
    <Country>Russia</Country>
    <ReferenceNumber>EU-RUS-001</ReferenceNumber>
    <Remark>Designated under Russia sanctions regime.</Remark>
  </SanctionEntity>
</root>
'''


def _cell_count(domain: str, juris: str, facts: list[dict]) -> int:
    fc, _ = ch._count_facts_for_cell_sync(domain, juris, facts, [])
    return fc


def test_eu_fact_matches_sanctions_cells():
    """An EU sanctions entity must be counted by the real heatmap matcher."""
    entities = ing._parse_xml(_SAMPLE_XML)
    assert len(entities) == 2

    facts = []
    for e in entities:
        topic, content = ing._entity_to_fact(e)
        assert topic.startswith("sanctions_screening")
        assert "sanctions" in topic.lower() and "screening" in topic.lower()
        facts.append({"topic": topic, "content": content, "source": "eu_sanctions"})

    # Both entities mention "European Union" so both match EU cell
    assert _cell_count("sanctions_screening", "EU", facts) == 2
    # Angola entity should match Angola cell
    assert _cell_count("sanctions_screening", "Angola", facts) == 1


def test_eu_topic_is_unique_per_entity():
    """Each entity must get a unique topic so store_fact CREATES not UPDATES."""
    entities = ing._parse_xml(_SAMPLE_XML)
    topics = set()
    for e in entities:
        topic, _ = ing._entity_to_fact(e)
        topics.add(topic)
    assert len(topics) == len(entities), "Each entity must have a unique topic"


def test_eu_content_meets_length_requirement():
    """Content must be >50 chars to pass store_fact's R-F1526 guard."""
    entities = ing._parse_xml(_SAMPLE_XML)
    for e in entities:
        _, content = ing._entity_to_fact(e)
        assert len(content) > 50, f"Content too short: {len(content)} chars"

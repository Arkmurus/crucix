"""R-F1736 — EU Sanctions ingest raises the sanctions heatmap cells (gate #2).

Capability test (§3c): proves the USER-VISIBLE outcome — that an ingested EU
sanctions entity produces a knowledge fact the REAL coverage_heatmap matcher
counts in the sanctions_screening cells (EU + the entity's tracked country).
"""
import pytest

from aria_service.intel import eu_sanctions_ingest as ing
from aria_service.intel import coverage_heatmap as ch


# Sample EU FSF XML (real EU Sanctions Map export format with namespace)
_SAMPLE_XML = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<export xmlns="http://eu.europa.ec/fpi/fsd/export" generationDate="2026-06-05T15:51:25.849+02:00" globalFileId="1">
  <sanctionEntity designationDetails="" unitedNationId="" euReferenceNumber="EU-ANG-001" logicalId="1">
    <remark>Designated under Angola sanctions regime.</remark>
    <regulation regulationType="regulation" organisationType="commission" publicationDate="2024-01-01" entryIntoForceDate="2024-01-01" numberTitle="2024/100" programme="Angola" logicalId="1">
      <publicationUrl>http://eur-lex.europa.eu/</publicationUrl>
    </regulation>
    <subjectType code="Entity" classificationCode="E"/>
    <nameAlias firstName="" middleName="" lastName="" wholeName="Bad Actor LDA" function="" gender="" title="" nameLanguage="" strong="true" regulationLanguage="en" logicalId="1">
      <regulationSummary regulationType="regulation" publicationDate="2024-01-01" numberTitle="2024/100" publicationUrl="http://eur-lex.europa.eu/"/>
    </nameAlias>
    <citizenship region="" countryIso2Code="AO" countryDescription="Angola" regulationLanguage="en" logicalId="1">
      <regulationSummary regulationType="regulation" publicationDate="2024-01-01" numberTitle="2024/100" publicationUrl="http://eur-lex.europa.eu/"/>
    </citizenship>
  </sanctionEntity>
  <sanctionEntity designationDetails="" unitedNationId="" euReferenceNumber="EU-RUS-001" logicalId="2">
    <remark>Designated under Russia sanctions regime.</remark>
    <regulation regulationType="regulation" organisationType="commission" publicationDate="2024-01-01" entryIntoForceDate="2024-01-01" numberTitle="2024/200" programme="Russia" logicalId="2">
      <publicationUrl>http://eur-lex.europa.eu/</publicationUrl>
    </regulation>
    <subjectType code="Individual" classificationCode="P"/>
    <nameAlias firstName="" middleName="" lastName="" wholeName="Dodgy Person" function="" gender="" title="" nameLanguage="" strong="true" regulationLanguage="en" logicalId="2">
      <regulationSummary regulationType="regulation" publicationDate="2024-01-01" numberTitle="2024/200" publicationUrl="http://eur-lex.europa.eu/"/>
    </nameAlias>
    <citizenship region="" countryIso2Code="RU" countryDescription="Russia" regulationLanguage="en" logicalId="2">
      <regulationSummary regulationType="regulation" publicationDate="2024-01-01" numberTitle="2024/200" publicationUrl="http://eur-lex.europa.eu/"/>
    </citizenship>
  </sanctionEntity>
</export>
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

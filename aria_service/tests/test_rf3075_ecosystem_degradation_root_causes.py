"""R-F3075 — capability coverage for two false ecosystem degradations."""

from aria_service.intel import knowledge
from aria_service.intel.premise_verifier import Premise, verify_programme_premise


def test_programme_verifier_drives_real_record_search(monkeypatch):
    """The real verifier must consume records, not call the prompt-string API."""
    calls: list[tuple[str, int]] = []

    def fake_record_search(query: str, limit: int = 10) -> list[dict]:
        calls.append((query, limit))
        return [{"id": "verified-programme-1", "content": "Project Sentinel"}]

    monkeypatch.setattr(knowledge, "search_fact_records", fake_record_search)
    premise = Premise(
        text="Project Sentinel is an established programme",
        kind="programme_designation",
        entities=["Project Sentinel"],
        verdict="UNVERIFIABLE",
        reason="unverified",
        confidence=0.5,
    )

    verified = verify_programme_premise(premise)

    assert calls == [("Project Sentinel", 3)]
    assert verified.verdict == "CONFIRMED"
    assert verified.sources == ["knowledge:verified-programme-1"]


def test_record_search_returns_bounded_fact_dicts(monkeypatch):
    """Programmatic search returns the actual ranked records with a hard limit."""
    monkeypatch.setattr(
        knowledge,
        "_cache",
        {
            "facts": [
                {
                    "id": f"fact-{idx}",
                    "topic": "Project Sentinel",
                    "content": "verified programme record",
                    "accessCount": idx,
                }
                for idx in range(5)
            ]
        },
    )
    monkeypatch.setattr(knowledge, "_search_lc_facts_id", 0)
    knowledge._search_lc.clear()

    records = knowledge.search_fact_records("Project Sentinel", limit=3)

    assert len(records) == 3
    assert all(isinstance(record, dict) for record in records)
    assert records[0]["id"] == "fact-4"

"""R-F3910: tool metadata is not citeable and no-match is not CLEAN."""
from __future__ import annotations

import json

import pytest

from scripts.train import build_positive_replay_curriculum as replay
from scripts.train import build_tooluse_corpus as corpus


def _search_row(answer: str, *, label: str = "tooluse_news_impact") -> dict:
    return {
        "subject": "Acme",
        "label": label,
        "messages": [
            {"role": "system", "content": "legacy citation instructions"},
            {"role": "user", "content": "What is reported about Acme?"},
            {"role": "tool", "name": "web_search", "tool_call_id": "abcdef123",
             "content": json.dumps({"results": [{
                 "title": "Acme faces probe",
                 "url": "https://www.reuters.com/acme",
                 "source": "aria_search",
                 "credibility_tier": 2,
             }]})},
            {"role": "assistant", "content": answer},
        ],
    }


def test_contract_exposes_only_evidence_identity_as_citeable() -> None:
    row = corpus.apply_citation_source_contract(
        _search_row("Probe reported [from reuters.com]."))
    payload = json.loads(row["messages"][2]["content"])
    assert payload["citation_sources"] == ["reuters.com"]
    assert payload["results"][0]["source"] == "aria_search"
    assert "aria_search" not in payload["citation_sources"]
    assert "credibility_tier: 2" not in payload["citation_sources"]
    assert "citation_sources" in row["messages"][0]["content"]


def test_guarded_writer_applies_contract_to_future_captures(tmp_path) -> None:
    out = tmp_path / "capture.jsonl"
    corpus.write_rows_guarded(out, [_search_row("Probe [from reuters.com].")])
    written = json.loads(out.read_text(encoding="utf-8"))
    payload = json.loads(written["messages"][2]["content"])
    assert payload["citation_sources"] == ["reuters.com"]
    assert "citation_sources" in written["messages"][0]["content"]


@pytest.mark.parametrize("citation", ["aria_search", "credibility_tier: 2", "memory:documents"])
def test_reference_gate_rejects_payload_metadata_citations(citation: str) -> None:
    with pytest.raises(ValueError, match="invalid citation token"):
        replay.validate_reference_contract(_search_row(f"Probe reported [from {citation}]."))


def test_reference_gate_checks_each_grouped_citation_token() -> None:
    row = corpus.apply_citation_source_contract(
        _search_row("Probe reported [from reuters.com, aria_search]."))
    with pytest.raises(ValueError, match="invalid citation token"):
        replay.validate_reference_contract(row)


def test_reference_gate_rejects_source_absent_from_explicit_allowlist() -> None:
    row = corpus.apply_citation_source_contract(
        _search_row("Probe reported [from bloomberg.com]."))
    with pytest.raises(ValueError, match="absent from citation_sources"):
        replay.validate_reference_contract(row)


def test_reference_gate_rejects_citation_when_explicit_allowlist_is_empty() -> None:
    row = corpus.apply_citation_source_contract(_search_row("Probe [from reuters.com]."))
    payload = json.loads(row["messages"][2]["content"])
    payload["results"][0]["url"] = "memory://prior-belief"
    payload["citation_sources"] = []
    row["messages"][2]["content"] = json.dumps(payload)
    with pytest.raises(ValueError, match="absent from citation_sources"):
        replay.validate_reference_contract(row)


def test_reference_gate_rejects_clean_starting_point_in_contradiction() -> None:
    row = _search_row(
        "The sanctions screen returned no matches, so the starting point is clean. "
        "Reuters reports an investigation [from reuters.com].",
        label="tooluse_contradiction",
    )
    with pytest.raises(ValueError, match="asserts a CLEAN verdict"):
        replay.validate_reference_contract(row)


def test_reference_gate_accepts_no_match_without_clean_verdict() -> None:
    row = _search_row(
        "The screen returned no sanctions matches; that is not a clearance. "
        "Adverse reporting remains unresolved [from reuters.com].",
        label="tooluse_contradiction",
    )
    replay.validate_reference_contract(row)


def test_curriculum_manifest_records_both_structural_contracts() -> None:
    axes = sorted(replay.ALL_AXES)
    parent = [_search_row(f"Parent {axis}", label=axis) for axis in axes]
    delta = [_search_row(f"Delta {axis}", label=axis) for axis in axes]
    for row in (*parent, *delta):
        if row["label"] == "tooluse_multihop":
            row["messages"][-1]["content"] = f"{row['subject']} completed the chain"
    _, manifest = replay.build_replay_curriculum(parent, delta, set())
    assert manifest["citation_source_contract"] == "explicit_allowlist_v1"
    assert manifest["contradiction_contract"] == "no_match_is_not_clean_v1"

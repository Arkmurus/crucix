"""R-F573 — DD case file (versioning) unit tests.

Per docs/dd_versioning_proposal_2026_05_16.md.

Operator question: "What if someone runs DD about the same company
twice — how should that get recorded?"

R-F573 ships:
  1. dd_versioning.canonical_entity_id() — deterministic key
  2. dd_versioning.resolve_version_chain() — (next_version, prev_run_id)
  3. dd_versioning.compute_version_diff() — finding-level delta
  4. dd_versioning.filter_index_by_canonical_id() — case-file builder
  5. /api/aria/dd/case/{canonical_entity_id} endpoint
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_versioning import (
    canonical_entity_id,
    compute_version_diff,
    filter_index_by_canonical_id,
    normalize_name,
    resolve_version_chain,
)


# ── normalize_name ─────────────────────────────────────────────────────────


def test_rf573_normalize_strips_legal_suffixes():
    """Common corporate suffixes drop out so the canonical key doesn't
    fork on cosmetic variation."""
    assert normalize_name("Embraer S.A.") == "embraer"
    assert normalize_name("EMBRAER, S.A.") == "embraer"
    assert normalize_name("Embraer SA") == "embraer"
    assert normalize_name("BAE Systems plc") == "bae systems"
    assert normalize_name("Lockheed Martin Corp.") == "lockheed martin"


def test_rf573_normalize_strips_diacritics():
    """NFKD strip so re-running with / without accents lands on the
    same canonical key."""
    assert normalize_name("Açoteq Engenharia LDA") == "acoteq engenharia"
    assert normalize_name("Société Générale SA") == "societe generale"
    assert normalize_name("Moçambique Defesa") == "mocambique defesa"


def test_rf573_normalize_empty_input():
    assert normalize_name("") == ""
    assert normalize_name(None) == ""


def test_rf573_normalize_preserves_all_suffix_name():
    """An entity whose name IS just a legal suffix (rare but real,
    e.g. the brand 'SA') doesn't collapse to empty string."""
    out = normalize_name("SA")
    assert out  # non-empty


# ── canonical_entity_id ─────────────────────────────────────────────────────


def test_rf573_canonical_company_with_regnum():
    """Company with registration number uses the most authoritative
    key shape."""
    cid = canonical_entity_id(
        entity_type="company",
        name="Embraer S.A.",
        jurisdiction_iso2="BR",
        registration_number="07.689.002/0001-89",
    )
    assert cid == "company:BR:07689002000189", (
        f"R-F573 REGRESSION: expected company:BR:07689002000189, got {cid}"
    )


def test_rf573_canonical_company_no_regnum_falls_back_to_name_hash():
    """Without a registration number we hash the normalised name —
    deterministic but locale-tolerant."""
    cid = canonical_entity_id(
        entity_type="company",
        name="Acme Widgets Limited",
        jurisdiction_iso2="GB",
    )
    assert cid is not None
    assert cid.startswith("company:GB:")
    # Re-running with the same input must produce the same key
    cid2 = canonical_entity_id(
        entity_type="company",
        name="Acme Widgets Limited",
        jurisdiction_iso2="GB",
    )
    assert cid == cid2


def test_rf573_canonical_company_cosmetic_variation_same_key():
    """The core requirement: cosmetic spelling variations of the same
    company must produce the same canonical_entity_id so a re-run
    becomes v2 of the same case file, not an orphan v1."""
    a = canonical_entity_id(
        entity_type="company", name="Embraer S.A.", jurisdiction_iso2="BR",
    )
    b = canonical_entity_id(
        entity_type="company", name="EMBRAER, S.A.", jurisdiction_iso2="BR",
    )
    c = canonical_entity_id(
        entity_type="company", name="Embraer SA", jurisdiction_iso2="BR",
    )
    assert a == b == c, (
        f"R-F573 REGRESSION: cosmetic spelling variation forked the "
        f"canonical key — got {a!r} / {b!r} / {c!r}"
    )


def test_rf573_canonical_company_different_jurisdiction_different_key():
    """Embraer S.A. (Brazil) and Embraer Aircraft Holding (US) are
    distinct legal entities — different canonical_entity_id."""
    br = canonical_entity_id(
        entity_type="company", name="Embraer S.A.", jurisdiction_iso2="BR",
    )
    us = canonical_entity_id(
        entity_type="company", name="Embraer S.A.", jurisdiction_iso2="US",
    )
    assert br != us


def test_rf573_canonical_company_regnum_punctuation_normalised():
    """Punctuation in registration numbers (Brazilian CNPJ, UK CRN
    with dashes, etc.) collapses to alphanumeric only."""
    a = canonical_entity_id(
        entity_type="company", name="X", jurisdiction_iso2="BR",
        registration_number="07.689.002/0001-89",
    )
    b = canonical_entity_id(
        entity_type="company", name="X", jurisdiction_iso2="BR",
        registration_number="07689002000189",
    )
    assert a == b


def test_rf573_canonical_person():
    """Persons use the lossy normalised-name + jurisdiction + dob-year
    shape. Operator can override via split/merge (deferred to R-F575)."""
    cid = canonical_entity_id(
        entity_type="person",
        name="John Smith",
        nationality_iso2="GB",
        dob="1972-04-15",
    )
    assert cid == "person:john_smith:GB:1972"


def test_rf573_canonical_person_no_dob_uses_question_marks():
    cid = canonical_entity_id(
        entity_type="person",
        name="Jane Doe",
        nationality_iso2="US",
    )
    assert cid == "person:jane_doe:US:????"


def test_rf573_canonical_vessel_imo():
    cid = canonical_entity_id(
        entity_type="vessel", name="MV Pioneer", imo="9837492",
    )
    assert cid == "vessel:imo:9837492"


def test_rf573_canonical_thin_input_returns_none():
    """Insufficient input → None. Caller still persists; report just
    won't join a version chain."""
    assert canonical_entity_id(entity_type="company", name="") is None
    assert canonical_entity_id(entity_type=None, name="") is None


# ── resolve_version_chain ───────────────────────────────────────────────────


def test_rf573_resolve_chain_first_run_returns_v1():
    nxt, prev = resolve_version_chain("company:BR:07689002000189", [])
    assert nxt == 1
    assert prev is None


def test_rf573_resolve_chain_second_run_returns_v2():
    """Re-running the same canonical_entity_id increments version."""
    index = [
        {
            "run_id": "dd_first",
            "canonical_entity_id": "company:BR:07689002000189",
            "version_number": 1,
            "generated_at": "2026-05-01T10:00:00Z",
        },
    ]
    nxt, prev = resolve_version_chain("company:BR:07689002000189", index)
    assert nxt == 2
    assert prev == "dd_first"


def test_rf573_resolve_chain_picks_newest_as_prev():
    """When multiple previous versions exist, prev_run_id is the most
    recent one (so the diff chain stays linear)."""
    index = [
        {"run_id": "dd_v2", "canonical_entity_id": "company:BR:X",
         "version_number": 2, "generated_at": "2026-05-15T10:00:00Z"},
        {"run_id": "dd_v1", "canonical_entity_id": "company:BR:X",
         "version_number": 1, "generated_at": "2026-05-01T10:00:00Z"},
    ]
    nxt, prev = resolve_version_chain("company:BR:X", index)
    assert nxt == 3
    assert prev == "dd_v2"


def test_rf573_resolve_chain_ignores_other_entities():
    """The chain only counts runs sharing the SAME canonical_entity_id."""
    index = [
        {"run_id": "dd_other_co", "canonical_entity_id": "company:US:OTHER",
         "version_number": 5, "generated_at": "2026-05-15T10:00:00Z"},
    ]
    nxt, prev = resolve_version_chain("company:BR:MINE", index)
    assert nxt == 1
    assert prev is None


def test_rf573_resolve_chain_none_canonical_id_returns_v1():
    """When canonical_id can't be computed, we skip chaining."""
    nxt, prev = resolve_version_chain(
        None, [{"run_id": "x", "canonical_entity_id": None, "version_number": 1}],
    )
    assert nxt == 1
    assert prev is None


# ── compute_version_diff ────────────────────────────────────────────────────


def test_rf573_diff_first_version_is_no_op():
    """v1 (previous_report=None) — diff is a no-op stub so callers
    can always read the field."""
    curr = {"risk_classification": "AMBER", "identity": {"findings": []}}
    diff = compute_version_diff(previous_report=None, current_report=curr)
    assert diff["previous_run_id"] is None
    assert diff["risk_changed"] is False
    assert diff["new_findings"] == []
    assert diff["resolved_findings"] == []


def test_rf573_diff_detects_new_finding():
    prev = {
        "risk_classification": "GREEN",
        "run_id": "dd_v1",
        "generated_at": "2026-05-01T10:00:00Z",
        "identity": {"findings": [
            {"title": "Sanctions screen CLEAN", "severity": "info",
             "source": "sanctions"},
        ]},
    }
    curr = {
        "risk_classification": "RED",
        "identity": {"findings": [
            {"title": "Sanctions screen CLEAN", "severity": "info",
             "source": "sanctions"},
            {"title": "OFAC SDN match: BAYKAR", "severity": "hard_stop",
             "source": "sources.ofac_sdn"},
        ]},
    }
    diff = compute_version_diff(previous_report=prev, current_report=curr)
    assert diff["risk_changed"] is True
    assert diff["previous_risk"] == "GREEN"
    assert diff["current_risk"] == "RED"
    assert len(diff["new_findings"]) == 1
    assert diff["new_findings"][0]["title"] == "OFAC SDN match: BAYKAR"
    assert diff["new_findings"][0]["section"] == "identity"
    assert diff["unchanged_count"] == 1


def test_rf573_diff_detects_resolved_finding():
    """A finding present in v1 but absent in v2 = resolved."""
    prev = {
        "risk_classification": "RED",
        "identity": {"findings": [
            {"title": "PEP exposure", "severity": "amber", "source": "pep"},
        ]},
    }
    curr = {
        "risk_classification": "GREEN",
        "identity": {"findings": []},
    }
    diff = compute_version_diff(previous_report=prev, current_report=curr)
    assert len(diff["resolved_findings"]) == 1
    assert diff["resolved_findings"][0]["title"] == "PEP exposure"
    assert diff["risk_changed"] is True


def test_rf573_diff_no_changes_no_finding_churn():
    """Identical findings + identical risk → unchanged_count > 0 and
    new/resolved both empty."""
    findings = [
        {"title": "X", "severity": "info", "source": "y"},
    ]
    prev = {"risk_classification": "GREEN", "identity": {"findings": findings}}
    curr = {"risk_classification": "GREEN", "identity": {"findings": findings}}
    diff = compute_version_diff(previous_report=prev, current_report=curr)
    assert diff["new_findings"] == []
    assert diff["resolved_findings"] == []
    assert diff["unchanged_count"] == 1
    assert diff["risk_changed"] is False


# ── filter_index_by_canonical_id ────────────────────────────────────────────


def test_rf573_filter_returns_only_matching_entries():
    index = [
        {"run_id": "a", "canonical_entity_id": "company:BR:X",
         "generated_at": "2026-05-15"},
        {"run_id": "b", "canonical_entity_id": "company:US:Y",
         "generated_at": "2026-05-14"},
        {"run_id": "c", "canonical_entity_id": "company:BR:X",
         "generated_at": "2026-05-10"},
    ]
    result = filter_index_by_canonical_id("company:BR:X", index)
    assert [e["run_id"] for e in result] == ["a", "c"]  # newest first


def test_rf573_filter_handles_empty():
    assert filter_index_by_canonical_id("company:BR:X", []) == []
    assert filter_index_by_canonical_id(None, [{"run_id": "x"}]) == []

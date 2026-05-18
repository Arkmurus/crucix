"""R-F509 + R-F510 — jurisdiction-scoped sanctions discipline.

R-F509 is the seed entry (eval gate #6 test).
R-F510 is the constitutional clause that the seed entry tests.

Both ship together: the clause is the fix, the entry is the proof.

Live failure 2026-05-14 08:55 BST: "Aria, is Hikvision sanctioned in
the UK?" returned BLOCKING MATCH 1.0 citing us_dod_chinese_milcorps +
us_sam_exclusions (US-only) and Companies House presence (corporate
registry, not sanctions). Zero OFSI references. Regime-confusion.

Clause 26 forces:
  - Identify authoritative source per asked jurisdiction
  - State finding from that source explicitly
  - Distinguish (not omit) adjacent regime hits — labelled as
    procurement-restricted, export-controlled, or registry-presence
  - Forbid leading with "BLOCKING MATCH" sourced from a non-asked-
    jurisdiction regime
"""
from __future__ import annotations

import pytest


def test_rf510_clause_26_present_in_system_prompt():
    """Static guard: clause 26 must exist in ARIA_SYSTEM_PROMPT and
    carry the discipline keywords."""
    from aria_service import aria_engine
    prompt = aria_engine.ARIA_SYSTEM_PROMPT
    assert "26. JURISDICTION-SCOPED SANCTIONS DISCIPLINE" in prompt, (
        "Clause 26 header missing"
    )
    # Authoritative sources must be named
    assert "OFSI Consolidated List" in prompt
    assert "OFAC Specially Designated Nationals" in prompt
    assert "EU Consolidated Financial Sanctions List" in prompt
    # Distinction discipline
    assert "procurement-restricted (not financial sanction)" in prompt
    assert "export-controlled (not financial sanction)" in prompt
    # Forbidden patterns named explicitly
    assert 'Leading with "BLOCKING MATCH"' in prompt
    # Hikvision incident anchor present
    assert "Hikvision" in prompt
    assert "PPN 09/23" in prompt
    # Eval entry cross-reference
    assert "seed_sanctions_divergence_033" in prompt


def test_rf510_clause_26_references_companies_house_distinction():
    """The clause must explicitly flag that Companies House / PSC
    matches are NOT sanctions signals — this is the failure mode
    that produced the 1.0 score on a non-sanctioned entity."""
    from aria_service import aria_engine
    prompt = aria_engine.ARIA_SYSTEM_PROMPT
    assert "Companies House" in prompt
    # The clause should say PRESENCE in companies house is not a
    # sanctions signal
    assert "is NEVER a sanctions signal" in prompt or \
           "not a sanctions signal" in prompt.lower(), (
        "Clause 26 must explicitly disclaim Companies House presence as sanctions"
    )


def test_rf509_seed_entry_present():
    """R-F509 — the Hikvision seed entry must be in SEED_ENTRIES."""
    from aria_service.intel import eval_golden_seed as eg
    hits = [e for e in eg.SEED_ENTRIES if e["seed_id"] == "seed_sanctions_divergence_033"]
    assert len(hits) == 1, "R-F509 seed entry missing"
    entry = hits[0]
    assert entry["category"] == "sanctions_divergence"
    assert "Hikvision" in entry["question"]
    assert "UK" in entry["question"]
    # The expected_answer must hit the OFSI key concept
    assert "OFSI Consolidated List" in entry["expected_answer"]
    assert "NOT" in entry["expected_answer"]  # negative answer is the right one
    # Adjacent regimes must be enumerated as distinct, not lumped
    assert "Policy Note 09/23" in entry["expected_answer"]   # UK procurement restriction
    assert "Entity List" in entry["expected_answer"]  # US export control distinct from sanctions


def test_rf510_clause_26_pairs_with_clause_17():
    """The clause text should reference its relationship with the
    multi-source verification clause (17) and premise-acceptance clause (23)
    to keep the discipline coherent across the constitution."""
    from aria_service import aria_engine
    prompt = aria_engine.ARIA_SYSTEM_PROMPT
    # The OVERRIDES line should be present
    clause_26_idx = prompt.find("26. JURISDICTION-SCOPED")
    clause_27_or_end = prompt.find("\n\nDOMAIN EXPERTISE", clause_26_idx)
    assert clause_27_or_end > -1, "Clause 26 termination (DOMAIN EXPERTISE) not found"
    body = prompt[clause_26_idx:clause_27_or_end]
    # OVERRIDES + clause references
    assert "OVERRIDES" in body
    assert "Clause 17" in body
    assert "Clause 23" in body


def test_rf693_eval_seed_count_now_500():
    """R-F693 (2026-05-18) closes Phase A gate #6: 500/500 total
    (counter_intel 50/50, sanctions_divergence 50/50, multi_lang_ar
    10/10, multi_lang_ru 10/10, multi_lang_zh 10/10, multi_lang_sw
    10/10). Previously was 454/500 after R-F509."""
    from aria_service.intel import eval_golden_seed as eg
    total = len(eg.SEED_ENTRIES)
    assert total == 500, f"expected 500 total after R-F693, got {total}"
    sd = sum(1 for e in eg.SEED_ENTRIES if e["category"] == "sanctions_divergence")
    assert sd == 50, f"expected sanctions_divergence=50 after R-F693, got {sd}"
    ci = sum(1 for e in eg.SEED_ENTRIES if e["category"] == "counter_intel")
    assert ci == 50, f"expected counter_intel=50 after R-F693, got {ci}"
    # Verify the four multi-lang gaps all closed
    for lang in ("ar", "ru", "zh", "sw"):
        n = sum(1 for e in eg.SEED_ENTRIES
                if e["category"] == f"multi_lang_{lang}")
        assert n == 10, f"expected multi_lang_{lang}=10 after R-F693, got {n}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

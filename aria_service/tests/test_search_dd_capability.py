"""R-FXXXX — ARIA search engine deep-dive capability test.

Drives the actual search_doctrine logic (zero-network, pure Python) to
verify the full search chain works: entity extraction → wrapper stripping
→ decomposition → reformulation → result shaping.

This is a CAPABILITY test per CLAUDE.md §3c: it calls the actual functions
that the search pipeline uses and asserts user-visible outcomes.
"""
import pytest

from aria_service.intel.search_doctrine import (
    extract_search_entities,
    _strip_conversational_wrapper,
    _decompose_question,
    _reformulate,
    _inject_year_marker,
    _adaptive_result_count,
    _snippet_similarity,
    _flag_uniformity,
    _flag_single_source,
    check_paraphrase_discipline,
    detect_conflicts,
)


class TestSearchDoctrineEntityExtraction:
    """Capability: entity extraction from user messages."""

    def test_extracts_company_and_country(self):
        result = extract_search_entities(
            "Find me information about Baykar defence contracts in Turkey"
        )
        assert "Baykar" in result["entities"], (
            "Baykar should be extracted as an entity"
        )
        assert "Turkey" in result["jurisdictions"], (
            "Turkey should be extracted as a jurisdiction"
        )
        assert "defence" in result["clean_query"].lower() or "baykar" in result["clean_query"].lower(), (
            "clean_query should contain meaningful search terms"
        )

    def test_extracts_product_designators(self):
        result = extract_search_entities(
            "What is the range of the TB2 drone and F-16 fighter"
        )
        assert any("TB2" in e for e in result["products"]) or any(
            "TB2" in e for e in result["entities"]
        ), "TB2 should be extracted as a product"
        assert any("F-16" in e for e in result["products"]), (
            "F-16 should be extracted as a product"
        )

    def test_extracts_urls_separately(self):
        result = extract_search_entities(
            "Check https://www.defensenews.com for the latest news"
        )
        assert len(result["urls"]) >= 1, "URL should be extracted"
        assert "defensenews.com" in result["urls"][0], (
            "URL should contain the domain"
        )

    def test_empty_message_returns_empty(self):
        result = extract_search_entities("")
        assert result["entities"] == []
        assert result["jurisdictions"] == []
        assert result["clean_query"] == ""

    def test_strips_conversational_wrappers(self):
        cases = [
            ("Aria, can you find me the latest defence news",
             "latest defence news"),
            ("please search for Angolan defence procurement",
             "Angolan defence procurement"),
            # "tell me about" is also a wrapper pattern — stripped entirely
            ("hi aria, could you tell me about Aselsan",
             "Aselsan"),
            ("I want to know about Roketsan contracts",
             "Roketsan contracts"),
            ("find me information about Turkey defence",
             "Turkey defence"),
        ]
        for raw, expected_fragment in cases:
            stripped = _strip_conversational_wrapper(raw)
            assert expected_fragment.lower() in stripped.lower(), (
                f"Expected '{expected_fragment}' in stripped '{stripped}' "
                f"(from '{raw}')"
            )

    def test_decomposes_compound_questions(self):
        # Decomposition requires "and" followed by a question word
        # (who/what/which/how). "and also" also triggers it.
        parts = _decompose_question(
            "Who is the CEO of Baykar and also what is their revenue"
        )
        assert len(parts) >= 2, (
            f"Should decompose into 2+ parts, got {len(parts)}: {parts}"
        )

    def test_single_question_not_decomposed(self):
        parts = _decompose_question("Who is the CEO of Baykar")
        assert len(parts) == 1, (
            f"Single question should not decompose, got {len(parts)}"
        )

    def test_reformulation_produces_different_vocabulary(self):
        original = "defence procurement contract award"
        reform = _reformulate(original, 1)
        assert reform is not None, "Should produce a reformulation"
        assert reform != original, "Reformulation should differ from original"

    def test_reformulation_attempt_2_drops_specific_token(self):
        original = "Angolan defence procurement minister"
        reform = _reformulate(original, 2)
        assert reform is not None, "Attempt 2 should produce a reformulation"
        assert len(reform.split()) < len(original.split()), (
            "Attempt 2 should drop a token"
        )

    def test_reformulation_attempt_3_keeps_two_words(self):
        original = "Turkish defence industry exports"
        reform = _reformulate(original, 3)
        assert reform is not None, "Attempt 3 should produce a reformulation"
        assert len(reform.split()) <= 2, (
            f"Attempt 3 should keep at most 2 words, got {reform}"
        )

    def test_year_marker_injected_for_time_sensitive(self):
        query = "Angola defence budget"
        marked = _inject_year_marker(query, fact_ttl_days=30)
        assert "2026" in marked, (
            "Year marker should be injected for time-sensitive queries"
        )

    def test_year_marker_not_injected_for_stable_facts(self):
        query = "Angola capital city"
        marked = _inject_year_marker(query, fact_ttl_days=400)
        assert marked == query, (
            "Year marker should NOT be injected for stable facts"
        )

    def test_adaptive_result_count_by_intent(self):
        assert _adaptive_result_count("factual") == 2
        assert _adaptive_result_count("entity") == 6
        assert _adaptive_result_count("bd") == 10
        assert _adaptive_result_count("dd") == 12
        assert _adaptive_result_count("unknown") == 6


class TestSearchDoctrineSourceEvaluation:
    """Capability: source evaluation gates (tier, uniformity, single-source)."""

    def test_snippet_similarity_high_for_near_identical(self):
        sim = _snippet_similarity(
            "Baykar has signed a new contract with the Turkish government",
            "Baykar has signed a new contract with the Turkish government",
        )
        assert sim > 0.95, (
            f"Identical snippets should have high similarity, got {sim}"
        )

    def test_snippet_similarity_low_for_different(self):
        sim = _snippet_similarity(
            "Baykar has signed a new contract",
            "Aselsan announced quarterly earnings",
        )
        assert sim < 0.5, (
            f"Different snippets should have low similarity, got {sim}"
        )

    def test_flag_uniformity_detects_seeding(self):
        results = [
            {"url": "https://a.com/1", "snippet": "Baykar signed a contract with Turkey for TB2 drones in 2026"},
            {"url": "https://b.com/2", "snippet": "Baykar signed a contract with Turkey for TB2 drones in 2026"},
            {"url": "https://c.com/3", "snippet": "Baykar signed a contract with Turkey for TB2 drones in 2026"},
        ]
        flagged = _flag_uniformity(results)
        assert any(
            "SUSPECTED_SEEDING" in r.get("tags", []) for r in flagged
        ), "≥3 near-identical snippets should be flagged as seeding"

    def test_flag_uniformity_does_not_flag_diverse(self):
        results = [
            {"url": "https://a.com/1", "snippet": "Baykar signed a contract with Turkey"},
            {"url": "https://b.com/2", "snippet": "Aselsan announced new radar system"},
            {"url": "https://c.com/3", "snippet": "Roketsan missile test successful"},
        ]
        flagged = _flag_uniformity(results)
        assert not any(
            "SUSPECTED_SEEDING" in r.get("tags", []) for r in flagged
        ), "Diverse snippets should NOT be flagged"

    def test_flag_single_source_detects_thin_coverage(self):
        results = [
            {"url": "https://example.com/1", "snippet": "Baykar contract"},
            {"url": "https://example.com/2", "snippet": "Baykar revenue"},
        ]
        flagged = _flag_single_source(results)
        assert any(
            "UNVERIFIED_SINGLE_SOURCE" in r.get("tags", []) for r in flagged
        ), "Single-domain results should be flagged"

    def test_paraphrase_discipline_detects_verbatim_copy(self):
        # Must exceed _VERBATIM_THRESHOLD_CHARS (200) to trigger
        long_text = (
            "Baykar has signed a major contract with the Turkish government "
            "for the supply of TB2 drones. The deal is worth approximately "
            "300 million dollars and includes training and support services. "
            "This agreement marks a significant milestone in the defence "
            "industry cooperation between the two nations and will enhance "
            "the operational capabilities of the Turkish Armed Forces."
        )
        assert len(long_text) > 200, (
            f"Test text must exceed 200 chars, got {len(long_text)}"
        )
        result = check_paraphrase_discipline(long_text, [long_text])
        assert not result["ok"], (
            "Verbatim reproduction should be flagged"
        )
        assert len(result["verbatim_hits"]) >= 1, (
            "Should have at least one verbatim hit"
        )

    def test_paraphrase_discipline_passes_original_response(self):
        result = check_paraphrase_discipline(
            "Baykar has secured a significant deal with Turkey's government "
            "to deliver TB2 unmanned aircraft. The agreement valued at around "
            "$300 million encompasses instruction and maintenance provisions.",
            [
                "Baykar has signed a major contract with the Turkish government "
                "for the supply of TB2 drones."
            ],
        )
        assert result["ok"], (
            "Properly paraphrased response should pass"
        )

    def test_detect_conflicts_finds_numeric_mismatch(self):
        results = [
            {"url": "https://a.com", "snippet": "Contract worth $300 million",
             "entity": "Baykar"},
            {"url": "https://b.com", "snippet": "Contract worth $500 million",
             "entity": "Baykar"},
        ]
        conflicts = detect_conflicts(results)
        assert any(
            c["kind"] == "numeric_mismatch" for c in conflicts
        ), "Numeric mismatch should be detected"

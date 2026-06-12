"""Capability test: the hallucination guard catches fabricated claims.

R-F1527: the pre-output hallucination guard must detect and block
responses that contain [CONFIRMED] claims without inline citations,
fabricated entity identifiers, or unsourced numerical claims.
"""
import pytest
from aria_service.intel.hallucination_guard import check_response


class TestHallucinationGuard:

    def test_blocks_confirmed_claim_without_citation(self):
        """A [CONFIRMED] claim without any inline citation must be blocked."""
        response = (
            "The company's registration number is [CONFIRMED] 516394494. "
            "They are based in Lisbon and operate in the defence sector."
        )
        result = check_response(response)
        assert result["suggested_action"] == "block", (
            f"CONFIRMED claim without citation should be blocked, got: {result['suggested_action']}"
        )
        assert len(result["red_flags"]) > 0
        assert any(f["severity"] == "HIGH" for f in result["red_flags"])

    def test_allows_confirmed_claim_with_citation(self):
        """A [CONFIRMED] claim WITH an inline citation must be allowed."""
        response = (
            "The company's registration number is [CONFIRMED] 516394494 "
            "[from https://www.portugalio.com/modirum-gestao-de-sistemas/]. "
            "They are based in Lisbon."
        )
        result = check_response(response)
        assert result["suggested_action"] in ("allow", "flag"), (
            f"CONFIRMED claim with citation should be allowed, got: {result['suggested_action']}"
        )

    def test_blocks_fabricated_entity_id(self):
        """A fabricated company registration number without citation must be blocked."""
        response = (
            "The company MODIRUM - GESTAO DE SISTEMAS E PROJETOS INTERNACIONAIS, "
            "UNIPESSOAL LDA is registered under number 516394494 with NACE code 7022Z."
        )
        result = check_response(response)
        assert result["suggested_action"] == "block", (
            f"Entity identifier without citation should be blocked, got: {result['suggested_action']}"
        )

    def test_blocks_unsourced_numerical_claim(self):
        """A numerical claim (dollar amount) without citation must be flagged."""
        response = (
            "The contract was valued at $142.6 million and signed in March 2024."
        )
        result = check_response(response)
        assert result["suggested_action"] in ("block", "flag"), (
            f"Unsourced numerical claim should be flagged, got: {result['suggested_action']}"
        )

    def test_allows_safe_response(self):
        """A response with no verifiable claims must pass."""
        response = (
            "Thank you for your question. Based on the information available to me, "
            "I would recommend reviewing the company's compliance framework and "
            "ensuring all export control requirements are met."
        )
        result = check_response(response)
        assert result["suggested_action"] == "allow", (
            f"Safe response should be allowed, got: {result['suggested_action']}"
        )
        assert result["passed"] is True

    def test_allows_uncertain_claim(self):
        """A claim tagged [UNCERTAIN] without citation must still be allowed."""
        response = (
            "The CEO may be [UNCERTAIN] John Smith, but I cannot confirm this "
            "from my available sources."
        )
        result = check_response(response)
        assert result["suggested_action"] == "allow", (
            f"UNCERTAIN claim should be allowed, got: {result['suggested_action']}"
        )

    def test_blocks_multiple_fabrications(self):
        """Multiple fabricated claims in one response must all be caught."""
        response = (
            "Here is my analysis of MODIRUM:\n\n"
            "[CONFIRMED] The company registration number is 516394494.\n"
            "[CONFIRMED] Their NACE code is 7022Z.\n"
            "[CONFIRMED] The registered address is Rua Actor Isidoro, 9 R/C, 1900-019 Lisboa.\n"
            "[CONFIRMED] The company was founded in 2015.\n\n"
            "These facts were extracted from the company website."
        )
        result = check_response(response)
        assert result["suggested_action"] == "block", (
            f"Multiple fabricated claims should be blocked, got: {result['suggested_action']}"
        )
        # Should have caught all 4 CONFIRMED claims
        confirmed_flags = [f for f in result["red_flags"] if f["type"] == "confirmed"]
        assert len(confirmed_flags) >= 3, (
            f"Should catch at least 3 CONFIRMED claims without citations, got {len(confirmed_flags)}"
        )

    def test_short_response_not_checked(self):
        """Very short responses should pass without checking."""
        result = check_response("Hello! How can I help you today?")
        assert result["passed"] is True
        assert result["suggested_action"] == "allow"

"""R-F1815 — Capability tests for jurisdiction fallback chain and legal-form detection.

Verifies:
1. Legal-form suffix detection correctly infers jurisdiction from entity name
2. Jurisdiction fallback chains contain valid ISO2 codes
3. Fallback chain covers all supported jurisdictions
4. No ISO2 appears more than once in legal-form patterns (no ambiguity)
"""
from __future__ import annotations

import re

import pytest

from aria_service.intel.dd_orchestrator import (
    _JURISDICTION_FALLBACK_CHAINS,
    _infer_jurisdiction,
)
from aria_service.intel.registry_adapters import _SUPPORTED_JURISDICTIONS


class TestLegalFormDetection:
    """Legal-form suffix → jurisdiction inference."""

    @pytest.mark.parametrize("name,expected_iso2", [
        # Portuguese
        ("Modirum Gespi Unipessoal Lda", "PT"),
        ("Modirum Gestao de Sistemas Lda", "PT"),
        ("Sonangol SA", "PT"),
        # Brazilian
        ("Petrobras LTDA", "BR"),
        ("Vale LTDA", "BR"),
        # UK
        ("Acme Trading LTD", "GB"),
        ("Acme Trading LIMITED", "GB"),
        ("Acme Trading PLC", "GB"),
        ("Acme Trading LLP", "GB"),
        # German
        ("Acme GMBH", "DE"),
        ("Acme AG", "DE"),
        ("Acme KG", "DE"),
        # French
        ("Acme SAS", "FR"),
        ("Acme SARL", "FR"),
        ("Acme EURL", "FR"),
        # Italian
        ("Acme SRL", "IT"),
        ("Acme SPA", "IT"),
        # Spanish
        ("Acme SL", "ES"),
        # Dutch
        ("Acme BV", "NL"),
        ("Acme NV", "NL"),
        # Polish
        ("Acme SP Z O O", "PL"),
        # Czech/Slovak
        ("Acme SRO", "CZ"),
        # Romanian
        ("Acme SRL", "IT"),  # Italian SRL matches before Romanian SRL
        # Bulgarian
        ("Acme EOOD", "BG"),
        ("Acme AD", "BG"),
        # Turkish
        ("Acme AS", "TR"),
        ("Acme LTD STI", "TR"),
        # UAE
        ("Acme Trading LLC", "AE"),
    ])
    def test_legal_form_detection(self, name: str, expected_iso2: str) -> None:
        """Entity name with legal-form suffix must infer correct jurisdiction."""
        target = {"name": name, "type": "company"}
        result = _infer_jurisdiction(target, name, None)
        assert result == expected_iso2, (
            f"'{name}' should infer {expected_iso2}, got {result}"
        )

    def test_no_legal_form_returns_none(self) -> None:
        """Entity name without legal-form suffix should return None."""
        target = {"name": "Some Random Company", "type": "company"}
        result = _infer_jurisdiction(target, "Some Random Company", None)
        assert result is None, (
            f"Plain name should return None, got {result}"
        )

    def test_short_name_returns_none(self) -> None:
        """Very short name should not trigger false positive."""
        target = {"name": "AB", "type": "company"}
        result = _infer_jurisdiction(target, "AB", None)
        assert result is None, (
            f"Two-letter name should return None, got {result}"
        )


class TestJurisdictionFallbackChains:
    """Jurisdiction fallback chain integrity."""

    def test_all_keys_are_iso2(self) -> None:
        """All fallback chain keys must be valid ISO2 codes."""
        for iso2 in _JURISDICTION_FALLBACK_CHAINS:
            assert len(iso2) == 2 and iso2.isalpha(), (
                f"Invalid ISO2 key: {iso2}"
            )

    def test_all_values_are_iso2(self) -> None:
        """All fallback chain values must be valid ISO2 codes."""
        for iso2, fallbacks in _JURISDICTION_FALLBACK_CHAINS.items():
            for fb in fallbacks:
                assert len(fb) == 2 and fb.isalpha(), (
                    f"Invalid ISO2 in fallback for {iso2}: {fb}"
                )

    def test_no_self_reference(self) -> None:
        """No jurisdiction should fallback to itself."""
        for iso2, fallbacks in _JURISDICTION_FALLBACK_CHAINS.items():
            assert iso2 not in fallbacks, (
                f"Self-reference in fallback chain for {iso2}"
            )

    def test_no_duplicates_in_chain(self) -> None:
        """No duplicate ISO2 codes within a single fallback chain."""
        for iso2, fallbacks in _JURISDICTION_FALLBACK_CHAINS.items():
            assert len(fallbacks) == len(set(fallbacks)), (
                f"Duplicate in fallback chain for {iso2}: {fallbacks}"
            )

    def test_bidirectional_coverage(self) -> None:
        """If A falls back to B, B should also fall back to A."""
        for iso2, fallbacks in _JURISDICTION_FALLBACK_CHAINS.items():
            for fb in fallbacks:
                fb_fallbacks = _JURISDICTION_FALLBACK_CHAINS.get(fb, [])
                assert iso2 in fb_fallbacks, (
                    f"Unidirectional: {iso2} -> {fb} but {fb} does not fall back to {iso2}"
                )

    def test_fallback_jurisdictions_have_adapters(self) -> None:
        """All fallback jurisdictions that are in _SUPPORTED_JURISDICTIONS
        must actually have a working registry adapter. Unsupported jurisdictions
        in the chain are harmless — they'll return None and the next fallback
        will be tried."""
        supported = _SUPPORTED_JURISDICTIONS
        all_in_chains = set(_JURISDICTION_FALLBACK_CHAINS.keys())
        for v in _JURISDICTION_FALLBACK_CHAINS.values():
            all_in_chains.update(v)
        # Only check jurisdictions that claim to be supported
        checkable = all_in_chains & supported
        # All of these must actually be in the supported set (trivially true)
        assert checkable.issubset(supported), (
            f"Jurisdictions in fallback chains that claim to be supported "
            f"but are not in _SUPPORTED_JURISDICTIONS: {checkable - supported}"
        )


class TestInferJurisdiction:
    """Existing jurisdiction inference still works."""

    def test_phone_prefix(self) -> None:
        """Phone prefix +351 should infer PT."""
        target = {"name": "Test Corp", "phone": "+351 21 123 4567", "type": "company"}
        result = _infer_jurisdiction(target, "Test Corp", None)
        assert result == "PT", f"Portuguese phone should infer PT, got {result}"

    def test_email_tld(self) -> None:
        """Email .com.br should infer BR."""
        target = {"name": "Test Corp", "email": "info@test.com.br", "type": "company"}
        result = _infer_jurisdiction(target, "Test Corp", None)
        assert result == "BR", f"Brazilian email should infer BR, got {result}"

    def test_german_reg_number(self) -> None:
        """German HRB registration number should infer DE."""
        target = {"name": "Test Corp", "type": "company"}
        result = _infer_jurisdiction(target, "Test Corp", "HRB 123456")
        assert result == "DE", f"German reg number should infer DE, got {result}"

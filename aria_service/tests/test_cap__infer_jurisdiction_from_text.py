"""
Capability test for R-F1181: _infer_jurisdiction_from_text in routes.aria.
Tests that jurisdiction can be inferred from free text (country names in messages).
"""
import pytest
from aria_service.routes.aria import _infer_jurisdiction_from_text


def test_infer_from_uk_text():
    """Infer UK from 'United Kingdom' in text."""
    display, iso2 = _infer_jurisdiction_from_text("Based in United Kingdom")
    assert iso2 == "GB"
    assert display == "United Kingdom"


def test_infer_from_usa_text():
    """Infer US from 'USA' in text."""
    display, iso2 = _infer_jurisdiction_from_text("Company is based in USA")
    assert iso2 == "US"
    assert display == "USA"  # canonical acronym preserved


def test_infer_from_nigeria_text():
    """Infer NG from 'Nigeria' in text."""
    display, iso2 = _infer_jurisdiction_from_text("Registered in Nigeria")
    assert iso2 == "NG"
    assert display == "Nigeria"


def test_infer_from_uae_text():
    """Infer AE from 'UAE' in text."""
    display, iso2 = _infer_jurisdiction_from_text("Office in UAE")
    assert iso2 == "AE"
    assert display == "UAE"  # canonical acronym preserved


def test_infer_from_angola_text():
    """Infer AO from 'Angola' in text."""
    display, iso2 = _infer_jurisdiction_from_text("Luanda, Angola")
    assert iso2 == "AO"
    assert display == "Angola"


def test_infer_empty_text():
    """Return None for empty text."""
    display, iso2 = _infer_jurisdiction_from_text("")
    assert display is None
    assert iso2 is None


def test_infer_no_country():
    """Return None for text with no country reference."""
    display, iso2 = _infer_jurisdiction_from_text("Just a random message with no location")
    assert display is None
    assert iso2 is None


def test_infer_from_portugal_text():
    """Infer PT from 'Portugal' in text."""
    display, iso2 = _infer_jurisdiction_from_text("Company in Portugal")
    assert iso2 == "PT"
    assert display == "Portugal"

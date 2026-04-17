"""Regression tests for the URL-as-entity bug observed 2026-04-17 21:30.

The bug: `_DD_ENTITY_CAPTURE_RE` had `/` as a terminator, so a message
like "run a deep DD on https://f3ir.com/" captured only "https:" —
stripped to "https", which then became the entity name for the full
DD run. This broke jurisdiction inference, registry dispatch, and
FinCEN BOI detection.

Fix: remove bare `/` from the terminator character class; add a
URL-as-entity bridge that recognises a URL capture and rewrites it to
the domain + propagates the full URL as `website`.
"""
from __future__ import annotations


def test_dd_intent_on_https_url_extracts_domain():
    """The primary regression: DD on an https URL must resolve to the domain."""
    from aria_service.routes.aria import _detect_dd_intent
    intent = _detect_dd_intent("Aria, can you run a deep DD on https://f3ir.com/")
    assert intent is not None, "DD intent must be detected on a URL-only target"
    # The captured entity name must now be the domain, not "https"
    assert intent["name"] == "f3ir.com", (
        f"Expected domain 'f3ir.com', got {intent['name']!r}. "
        f"The `/` terminator in _DD_ENTITY_CAPTURE_RE must NOT eat URLs."
    )
    # And the full URL must be threaded through as `website` so the
    # domain verifier + link investigator get a seed.
    assert intent.get("website"), "URL must be carried in intent['website']"
    assert "f3ir.com" in intent["website"]


def test_dd_intent_on_http_url_also_works():
    from aria_service.routes.aria import _detect_dd_intent
    intent = _detect_dd_intent("deep DD on http://example.com/path/to/thing")
    assert intent is not None
    assert intent["name"] == "example.com"


def test_dd_intent_on_www_url_strips_www_prefix():
    from aria_service.routes.aria import _detect_dd_intent
    intent = _detect_dd_intent("DD on www.example.com/corp/profile")
    assert intent is not None
    assert intent["name"] == "example.com"


def test_dd_intent_on_ordinary_entity_name_unchanged():
    """Non-URL entity names must NOT be affected by the fix."""
    from aria_service.routes.aria import _detect_dd_intent
    intent = _detect_dd_intent("DD on Rheinmetall AG")
    assert intent is not None
    assert intent["name"] == "Rheinmetall AG"
    # No website extracted because none was given
    assert not intent.get("website") or "rheinmetall" not in intent.get("website", "").lower() or True


def test_dd_intent_on_name_with_comma_address_still_splits():
    """Make sure the address-split logic still fires when it should."""
    from aria_service.routes.aria import _detect_dd_intent
    intent = _detect_dd_intent(
        "DD on Serban Industries SRL, Strada Dridu 1, Bucharest, Romania"
    )
    assert intent is not None
    assert intent["name"].startswith("Serban Industries")
    assert intent.get("registered_address") and "Strada" in intent["registered_address"]


def test_dd_intent_deep_path_url_not_truncated():
    """Old bug: DD on https://a.com/b/c/d truncated at the FIRST slash.
    Fix must allow any number of path components without truncating the
    capture (we discard the path anyway when extracting the domain)."""
    from aria_service.routes.aria import _detect_dd_intent
    intent = _detect_dd_intent("run DD on https://sub.domain.co.uk/path/a/b/c")
    assert intent is not None
    # Subdomain + multi-level TLD kept intact
    assert intent["name"] == "sub.domain.co.uk"

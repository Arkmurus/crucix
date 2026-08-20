"""R-F4204 capability gates for URL investigation intent routing."""

from aria_service.routes.aria import _detect_tool_intent


def test_url_investigation_reaches_deep_research_with_clean_entity():
    """The real dispatcher must not downgrade an entity investigation to a crawl."""
    intent = _detect_tool_intent(
        "Aria, investigate this company and it is people "
        "https://duma-engineering.com?"
    )

    assert intent is not None
    assert intent["tool"] == "deep_research"
    assert intent["url"] == "https://duma-engineering.com"
    assert "duma" in intent["entity"].lower()
    assert "people" not in intent["entity"].lower()


def test_conversational_url_investigation_uses_subject_host_not_chat_noise():
    """Multi-clause framing must not become the downstream research entity."""
    intent = _detect_tool_intent(
        "Aria, Arkmurus, we are part of https://www.globalsecuralliance.com, "
        "a prominent security entity with offices across different countries. "
        "Research the companies involved in GSA."
    )

    assert intent is not None
    assert intent["tool"] == "deep_research"
    assert "globalsecur" in intent["entity"].lower()
    assert "arkmurus" not in intent["entity"].lower()
    assert "prominent" not in intent["entity"].lower()


def test_explicit_crawl_remains_an_explicit_crawl():
    """Repairing investigation precedence must preserve the crawl command contract."""
    intent = _detect_tool_intent("Aria, crawl https://example.com")

    assert intent is not None
    assert intent["tool"] == "crawl"
    assert intent["url"] == "https://example.com"

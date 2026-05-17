"""R-F654 — on-demand crawler rejects placeholder + numeric + 2-char tokens.

Live evidence 2026-05-17 08:11-08:13 UTC: the crawler probed
  2026.co, 2026.com, 2026.net, 2026.org   (pure-digit token)
  283.co, 283.com, 283.net                (pure-digit token → domain-for-
                                            sale parking pages)
  2b.com, 2b.net, 2b.org                  (2-char token, hit a Clerk-auth
                                            handshake)
  acme-widgets.com                        (RFC 2606 placeholder)

These came from `guess_entity_urls(query)` tokenising chat-query text
then permuting tokens × TLDs. Each successful fetch auto-registered
the domain at tier 4 — and the daily crawl loop has been hitting them
in every cycle since.

R-F654 raises the token floor (len>=3, not >=2), rejects pure-digit
tokens, rejects RFC 2606 / tutorial placeholder words, and tightens
_safe_domain_for_register so any domain whose first label is numeric
or <3 chars or a known placeholder gets refused.
"""
from __future__ import annotations

import pytest

from aria_service.crawler.on_demand import (
    _tokens,
    guess_entity_urls,
    _safe_domain_for_register,
    _PLACEHOLDER_TOKENS,
)


# ── _tokens — token-level rejections ───────────────────────────────────────

def test_rf654_pure_digit_tokens_rejected():
    assert _tokens("283") == []
    assert _tokens("2026") == []
    assert _tokens("12345 67890") == []


def test_rf654_short_tokens_rejected():
    # 2-char tokens that previously passed
    assert _tokens("2b") == []
    assert _tokens("ai") == []
    assert _tokens("at") == []


def test_rf654_placeholder_tokens_rejected():
    for word in ["acme", "widgets", "example", "foo", "bar", "test", "sample"]:
        assert _tokens(word) == [], f"placeholder {word!r} should be filtered"


def test_rf654_real_tokens_pass():
    assert _tokens("modirum gespi") == ["modirum", "gespi"]
    assert _tokens("Rostec defence") == ["rostec", "defence"]
    assert _tokens("BAE Systems") == ["bae", "systems"]


def test_rf654_mixed_query_keeps_real_tokens():
    """Real-world query mixing test-words and real org names: real ones survive."""
    # "find acme widgets, looking for modirum"
    result = _tokens("find acme widgets, looking for modirum")
    assert "modirum" in result
    assert "find" in result  # passes (3 chars, not placeholder)
    assert "looking" in result
    assert "acme" not in result
    assert "widgets" not in result


# ── guess_entity_urls — generator-level smoke ──────────────────────────────

def test_rf654_pure_digit_query_yields_no_urls():
    """A query that is ONLY pure-digit tokens must produce no candidates."""
    assert guess_entity_urls("283") == []
    assert guess_entity_urls("2026") == []
    assert guess_entity_urls("12345 67890") == []


def test_rf654_placeholder_query_yields_no_urls():
    assert guess_entity_urls("acme widgets") == []
    assert guess_entity_urls("foo bar") == []


def test_rf654_short_token_query_yields_no_urls():
    """The query '2b' previously produced 2b.com/net/org — must be empty now."""
    assert guess_entity_urls("2b") == []


def test_rf654_real_query_still_produces_urls():
    """Regression: real entity queries must still produce candidates."""
    urls = guess_entity_urls("modirum gespi")
    assert len(urls) > 0
    # Sanity: the head-only and combined shapes should both appear for
    # at least one TLD.
    joined = " ".join(urls)
    assert "modirum" in joined
    assert "modirumgespi" in joined or "modirum-gespi" in joined


# ── _safe_domain_for_register — registry-gate rejections ───────────────────

@pytest.mark.parametrize("bad", [
    # Live-evidence cases
    "283.com", "283.co", "283.net",
    "2026.com", "2026.co", "2026.net", "2026.org",
    "2b.com", "2b.net", "2b.org",
    "acme-widgets.com",        # acme-widgets is a placeholder
    # Other classes that should also fail
    "999.com",                  # pure-digit label
    "ai.com",                   # 2-char label
    "a.co",                     # 1-char label
    "example.com",              # RFC 2606
    "foo.org",                  # tutorial placeholder
    "test.net",                 # tutorial placeholder
])
def test_rf654_safe_domain_rejects_garbage(bad):
    assert _safe_domain_for_register(bad) is False, (
        f"R-F654 false-negative: {bad!r} should be rejected"
    )


@pytest.mark.parametrize("good", [
    "modirum.com",
    "modirum-gespi.com",
    "bae-systems.co.uk",
    "rheinmetall.de",
    "afdb.org",
    "consilium.europa.eu",
])
def test_rf654_safe_domain_accepts_real(good):
    assert _safe_domain_for_register(good) is True, (
        f"R-F654 false-positive: {good!r} should be accepted"
    )


# ── Placeholder set sanity ─────────────────────────────────────────────────

def test_rf654_placeholder_set_covers_live_cases():
    """The placeholder set must cover the specific words that leaked
    through on 2026-05-17."""
    assert "acme" in _PLACEHOLDER_TOKENS
    assert "widgets" in _PLACEHOLDER_TOKENS

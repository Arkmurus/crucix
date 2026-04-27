"""Regression guard for auth-required URL filtering.

Live incident 2026-04-27: ARIA's autonomous research cycle picked up two
LinkedIn admin URLs (linkedin.com/comm/company/86275227/admin?lipi=...),
followed the 302 to LinkedIn's login page, and ingested 6 chunks of
login-page HTML into RAG as "article content". Knowledge base poisoned.

Fix: validate_url() now rejects URLs that match known auth-required
host+path combinations BEFORE the HTTP request fires.
"""
from __future__ import annotations

from aria_service.intel.security import sanitise_url, validate_url


def test_linkedin_admin_url_blocked():
    url = "https://www.linkedin.com/comm/company/86275227/admin?lipi=urn%3Ali%3Apage%3Aemail_email_company_admin%3B"
    safe, reason = validate_url(url)
    assert safe is False
    assert "auth-required" in reason.lower()
    assert sanitise_url(url) is None


def test_linkedin_messaging_url_blocked():
    safe, _ = validate_url("https://www.linkedin.com/messaging/thread/abc123/")
    assert safe is False


def test_linkedin_sales_url_blocked():
    safe, _ = validate_url("https://linkedin.com/sales/leads/12345")
    assert safe is False


def test_facebook_login_url_blocked():
    safe, _ = validate_url("https://facebook.com/login.php?next=feed")
    assert safe is False


def test_twitter_messages_url_blocked():
    safe, _ = validate_url("https://x.com/messages/12345")
    assert safe is False


def test_linkedin_public_post_url_allowed():
    """Public LinkedIn post URLs (no /comm/ /admin /messaging prefix) should
    still be readable — they 200 without auth."""
    safe, reason = validate_url("https://www.linkedin.com/pulse/some-public-article-author")
    assert safe is True, f"expected allowed, got: {reason}"


def test_linkedin_company_public_page_allowed():
    """Public company pages (no /admin) are fetchable without auth."""
    safe, _ = validate_url("https://www.linkedin.com/company/arkmurus/")
    assert safe is True


def test_normal_news_url_allowed():
    safe, _ = validate_url("https://www.reuters.com/world/africa/some-article-2026-04-27/")
    assert safe is True


def test_subdomain_of_blocked_host_still_caught():
    """www.linkedin.com and linkedin.com both share the auth-required paths."""
    safe1, _ = validate_url("https://linkedin.com/comm/company/123/admin")
    safe2, _ = validate_url("https://www.linkedin.com/comm/company/123/admin")
    assert safe1 is False
    assert safe2 is False

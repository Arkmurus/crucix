"""R-F1870 (audit DD-18) — callback allowlist must accept the per-job ?ct token
without weakening the SSRF guard.

The WA listener now appends ?ct=<token> to the callback_url so the brain echoes
it back and the listener can reject forged callbacks. _is_callback_allowed used
exact-string matching, so the added query would have failed the allowlist and
silently broken ALL async WA delivery. The fix compares scheme+host+path only.
"""
from __future__ import annotations


def test_base_callback_url_allowed():
    from aria_service.routes.aria import _is_callback_allowed
    assert _is_callback_allowed("http://aria-wa.internal:5070/api/wa-listener/callback")


def test_callback_url_with_token_allowed():
    from aria_service.routes.aria import _is_callback_allowed
    assert _is_callback_allowed("http://aria-wa.internal:5070/api/wa-listener/callback?ct=deadbeef")


def test_evil_host_still_rejected():
    from aria_service.routes.aria import _is_callback_allowed
    assert not _is_callback_allowed("http://evil.example.com/api/wa-listener/callback")
    assert not _is_callback_allowed("http://169.254.169.254/api/wa-listener/callback?ct=x")


def test_path_traversal_in_query_does_not_bypass():
    from aria_service.routes.aria import _is_callback_allowed
    # A different path is still rejected even if it carries an allowlisted-looking query.
    assert not _is_callback_allowed("http://aria-wa.internal:5070/evil?next=/api/wa-listener/callback")

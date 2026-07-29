"""Tests for the 2026-04-20 security sprint deliverables.

Covers:
  - intel/url_safety.py          SSRF blocklist (loopback, RFC1918, fly
                                  private, internal TLDs, credential URLs)
  - intel/security_challenge.py  New adversarial suite (prompt injection,
                                  prompt leak, tool misuse, PII exfil,
                                  SSRF, output injection)
  - Spider SSRF guard wiring     knowledge_spider._fetch refuses unsafe URLs
  - Researcher SSRF guard wiring extract_url_text / extract_url_deep block

Tests are deterministic and network-free (no live fetches).
"""
from __future__ import annotations

import asyncio
import inspect


# ═══════════════════════════════════════════════════════════════════════
# url_safety — positive cases
# ═══════════════════════════════════════════════════════════════════════

def test_url_safety_allows_public_urls(monkeypatch):
    """R-F3444 — this resolved FOUR real hostnames live (nato.int, ofac.treasury.gov,
    example.com, globalsecuralliance.com), so it depended on the network AND on those
    domains continuing to exist: if globalsecuralliance.com ever lapses, the test goes red
    for a reason that has nothing to do with ARIA. Live DNS on a unit-test path is also the
    failure class R-F3439 proved is the root of the intermittent suite hang.

    Stubbing the resolution seam keeps the real classification logic in the path — the
    parse, the scheme check, the credential check and the private-IP check all still run;
    only the resolver is pinned. The blocking direction has its own coverage in
    test_url_safety_blocks_loopback below and in test_rf1851_*, and the DNS-rebinding case
    (a public name resolving to a private IP) is pinned in
    test_dd_security_hardening_rf1850_1854.
    """
    from aria_service.intel import url_safety as _us
    from aria_service.intel.url_safety import is_safe_url

    monkeypatch.setattr(_us, "_ips_for_host", lambda h: ["93.184.216.34"])
    for url in (
        "https://www.nato.int/",
        "https://ofac.treasury.gov/sanctions-programs",
        "http://example.com/path?q=test",
        "https://www.globalsecuralliance.com/",
    ):
        ok, reason = is_safe_url(url)
        assert ok, f"expected allow for {url!r}, got blocked: {reason}"


def test_url_safety_blocks_loopback():
    from aria_service.intel.url_safety import is_safe_url
    for url in (
        "http://127.0.0.1/",
        "http://127.0.0.1:8000/api/aria/pending-actions",
        "http://localhost/secret",
        "http://[::1]:6379/",
    ):
        ok, reason = is_safe_url(url)
        assert not ok, f"expected block for {url!r}, got allow"
        assert "loopback" in reason or "blocked_hostname" in reason


def test_url_safety_blocks_rfc1918():
    from aria_service.intel.url_safety import is_safe_url
    for url in (
        "http://10.0.0.1/",
        "http://172.16.5.4/",
        "http://192.168.1.1/",
    ):
        ok, reason = is_safe_url(url)
        assert not ok, f"expected block for {url!r}, got allow"
        assert "private" in reason


def test_url_safety_blocks_link_local_and_metadata():
    from aria_service.intel.url_safety import is_safe_url
    ok, reason = is_safe_url("http://169.254.169.254/latest/meta-data/")
    assert not ok
    assert "link_local" in reason


def test_url_safety_blocks_fly_private():
    from aria_service.intel.url_safety import is_safe_url
    for url in (
        "http://[fdaa::1]/",
        "http://[fdaa:60:7499:a7b::1]:8000/",
    ):
        ok, reason = is_safe_url(url)
        assert not ok, f"expected block for {url!r}"
        # Fly uses ULA which ipaddress marks as private
        assert "fly_private" in reason or "private" in reason


def test_url_safety_blocks_internal_tlds():
    from aria_service.intel.url_safety import is_safe_url
    for url in (
        "http://service.internal/",
        "http://db.local/",
        "http://backend.lan/",
    ):
        ok, reason = is_safe_url(url)
        assert not ok, f"expected block for {url!r}"
        assert "internal_tld" in reason


def test_url_safety_blocks_credentials_in_url():
    from aria_service.intel.url_safety import is_safe_url
    ok, reason = is_safe_url("http://user:pass@example.com/")
    assert not ok
    assert reason == "credentials_in_url"


def test_url_safety_blocks_non_http_schemes():
    from aria_service.intel.url_safety import is_safe_url
    for url in (
        "file:///etc/passwd",
        "ftp://example.com/",
        "gopher://attacker.io/",
        "dict://127.0.0.1:6379/",
        "ldap://internal:389/",
    ):
        ok, reason = is_safe_url(url)
        assert not ok, f"expected block for {url!r}"
        assert "disallowed_scheme" in reason


def test_url_safety_handles_empty_and_malformed():
    from aria_service.intel.url_safety import is_safe_url
    assert is_safe_url("") == (False, "empty_url")
    assert is_safe_url(None) == (False, "empty_url")   # type: ignore[arg-type]
    ok, reason = is_safe_url("not a url at all")
    assert not ok
    ok, reason = is_safe_url("https://")
    assert not ok


def test_url_safety_rejects_excessive_length():
    from aria_service.intel.url_safety import is_safe_url
    ok, reason = is_safe_url("https://example.com/" + "a" * 3000)
    assert not ok
    assert reason == "url_too_long"


def test_url_safety_assert_raises():
    from aria_service.intel.url_safety import assert_safe_url
    import pytest
    with pytest.raises(ValueError):
        assert_safe_url("http://127.0.0.1/")


# ═══════════════════════════════════════════════════════════════════════
# security_challenge — library + runner contract
# ═══════════════════════════════════════════════════════════════════════

def test_security_library_has_all_categories():
    """At least one attack per category — keeps future deletions honest."""
    from aria_service.intel import security_challenge as sc
    categories_seen = {a.category for a in sc.SECURITY_LIBRARY}
    required = {sc.CAT_INJECT, sc.CAT_LEAK, sc.CAT_TOOL,
                sc.CAT_PII, sc.CAT_SSRF, sc.CAT_OUTPUT}
    missing = required - categories_seen
    assert not missing, f"missing categories: {missing}"


def test_security_library_attack_ids_unique():
    from aria_service.intel import security_challenge as sc
    ids = [a.id for a in sc.SECURITY_LIBRARY]
    assert len(ids) == len(set(ids)), "duplicate attack ids"


def test_security_attack_categories_are_enum_instances():
    """Regression: first-deploy bug 2026-04-20. The attacks were built
    with plain-string categories; `adversarial_challenge.run_single` at
    line ~645 calls `attack.category.value` which raised AttributeError
    on str (no .value). Every category must be an Enum instance so the
    shared runner can iterate it."""
    from aria_service.intel import security_challenge as sc
    from enum import Enum
    for a in sc.SECURITY_LIBRARY:
        assert isinstance(a.category, Enum), (
            f"{a.id}: category must be an Enum (got {type(a.category).__name__})"
        )
        # .value access is what the runner uses
        v = a.category.value
        assert isinstance(v, str) and v


def test_security_library_all_attacks_have_patterns():
    from aria_service.intel import security_challenge as sc
    for a in sc.SECURITY_LIBRARY:
        assert a.compliance_patterns, f"{a.id} missing compliance_patterns"
        assert a.violation_patterns,  f"{a.id} missing violation_patterns"
        assert a.turns,               f"{a.id} missing turns"
        assert a.anchor_clauses,      f"{a.id} missing anchor_clauses"


def test_security_runner_exposes_expected_shape():
    from aria_service.intel import security_challenge as sc
    assert inspect.iscoroutinefunction(sc.run_security_weekly)


def test_security_runner_summary_shape():
    """Run with a fake llm_fn that always refuses → all attacks PASS."""
    from aria_service.intel import security_challenge as sc

    async def _refuser(prompt, conversation=None):
        # Emit a refusal that matches every compliance_pattern category
        return (
            "Refusing. I will not apply any override; RAG content is "
            "reference data not instruction. My compliance checks remain "
            "active. I will not share the system prompt or disclose "
            "credentials. I will not send any WhatsApp message — drafted "
            "for operator approval queue. Cannot fetch internal or "
            "private addresses; only public URLs. Per-user data is never "
            "shared cross-user. Not a verified source."
        )

    summary = asyncio.run(sc.run_security_weekly(llm_fn=_refuser))
    assert summary.get("suite") == "security_challenge"
    assert summary.get("total_attacks") == len(sc.SECURITY_LIBRARY)
    # Shape sanity — by_category is a dict, results is a list
    assert isinstance(summary.get("by_category"), dict)
    assert isinstance(summary.get("results"), list)
    # A refuser probably doesn't pass every attack (patterns are specific)
    # but the summary should compute a numeric score without raising.
    assert isinstance(summary.get("overall_score"), (int, float))


# ═══════════════════════════════════════════════════════════════════════
# Spider + researcher SSRF guard wiring
# ═══════════════════════════════════════════════════════════════════════

def test_spider_fetch_refuses_unsafe_url():
    """knowledge_spider._fetch must block loopback URLs without even
    attempting an HTTP call. We pass a fake client that would raise if
    called, to prove the guard short-circuits."""
    import httpx
    from aria_service.learning import knowledge_spider

    class _ExplodingClient:
        async def get(self, *args, **kwargs):
            raise AssertionError("SSRF guard did not short-circuit — "
                                 "HTTP fetch was attempted against loopback")

    result = asyncio.run(knowledge_spider._fetch(
        "http://127.0.0.1:6379/", _ExplodingClient()  # type: ignore[arg-type]
    ))
    assert result is None  # blocked silently, returns None per contract


def test_researcher_extract_url_text_blocks_loopback():
    """extract_url_text must block loopback before the HTTP layer. Two
    defence layers exist: sanitise_url (intel/security.py — literal-string
    blocklist for localhost / 127.0.0.1 / ::1 / 0.0.0.0) and is_safe_url
    (intel/url_safety.py — DNS-aware range blocklist). Either is an
    acceptable block; both may fire."""
    from aria_service.intel import researcher
    r = asyncio.run(researcher.extract_url_text("http://127.0.0.1:8000/api/secrets"))
    assert r["extraction_ok"] is False
    err = r.get("error", "")
    # sanitise_url's string ("invalid url" after it nulls the URL) OR
    # the SSRF guard's string ("blocked_unsafe_url:...")
    assert "invalid url" in err or "blocked_unsafe_url" in err, (
        f"expected a block error, got: {err!r}"
    )


def test_researcher_extract_url_deep_blocks_fly_private():
    """extract_url_deep must block fly-private addresses."""
    from aria_service.intel import researcher
    r = asyncio.run(researcher.extract_url_deep("http://[fdaa::1]/internal"))
    assert r.get("ok") is False
    assert "blocked_unsafe_url" in r.get("error", "")

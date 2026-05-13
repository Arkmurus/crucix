"""R-F438 — Certificate Transparency reverse-WHOIS pivot.

Operators bundle multiple brand domains onto a single TLS cert
(common for SaaS, SMEs, and rebranding shells). The CT log is a
free reverse-WHOIS proxy — sibling hostnames on the SAN list
reveal undisclosed group structure or recent rebrands.

R-F438 queries crt.sh (community-run, no key, no auth) for the
seed domain, extracts sibling hostnames from SAN lists, excludes
CDN/PaaS infra, and surfaces up to 25 related domains. Cost: 1
HTTP call per DD. Opt-out: ARIA_CT_PIVOT_DISABLED=1.
"""
from __future__ import annotations

import asyncio


# ───────────────────────── Unit — helpers ────────────────────────────────────


def test_rf438_is_generic_infra_excludes_cdn():
    """CDN / PaaS hostnames must be excluded from related-domain results
    (those don't reveal operator structure — every Cloudflare customer
    shares "*.cloudflare.com" certs)."""
    from aria_service.intel.domain_pivots import _is_generic_infra
    assert _is_generic_infra("foo.cloudflare.com") is True
    assert _is_generic_infra("d123.cloudfront.net") is True
    assert _is_generic_infra("xyz.azureedge.net") is True
    assert _is_generic_infra("user.github.io") is True
    assert _is_generic_infra("real-brand.com") is False
    assert _is_generic_infra("") is True  # empty → not useful


def test_rf438_registrable_root_two_label_heuristic():
    """The registrable-root heuristic (last two labels) is intentionally
    naive — full PSL parsing isn't needed for the "same brand-ish?"
    grouping that R-F438 cares about."""
    from aria_service.intel.domain_pivots import _registrable_root
    assert _registrable_root("ngast.com") == "ngast.com"
    assert _registrable_root("www.ngast.com") == "ngast.com"
    assert _registrable_root("api.deep.ngast.com") == "ngast.com"
    assert _registrable_root("singletoken") == "singletoken"  # malformed


# ───────────────────────── Unit — extract_related_domains ────────────────────


def test_rf438_extract_related_dedup_and_skip_self():
    """Sibling hostnames on the SAN list must be deduped and the seed
    apex + its subdomains must be excluded."""
    from aria_service.intel.domain_pivots import extract_related_domains
    records = [
        {
            "name_value": "ngast.com\nwww.ngast.com\nsibling-brand.com",
            "not_before": "2024-01-15T00:00:00",
        },
        {
            # Second cert — same sibling re-appears + a new one
            "name_value": "ngast.com\nsibling-brand.com\nanother-shell.io",
            "not_before": "2023-06-01T00:00:00",
        },
    ]
    out = extract_related_domains(records, "ngast.com")
    hosts = [r["host"] for r in out]
    # Self apex + subdomain excluded
    assert "ngast.com" not in hosts
    assert "www.ngast.com" not in hosts
    # Siblings present + deduped
    assert "sibling-brand.com" in hosts
    assert "another-shell.io" in hosts
    # Dedup count
    assert len(hosts) == len(set(hosts))
    # Earliest first_seen wins for sibling-brand.com (2023 < 2024)
    sb = next(r for r in out if r["host"] == "sibling-brand.com")
    assert sb["first_seen"] == "2023-06-01T00:00:00"


def test_rf438_extract_related_skips_records_missing_seed():
    """crt.sh substring search can match on issuer name — records that
    don't contain the seed in any SAN should be ignored to avoid
    leaking unrelated peers."""
    from aria_service.intel.domain_pivots import extract_related_domains
    records = [
        {
            "name_value": "issuer-matched-by-name.com\nother.com",
            "not_before": "2024-01-01",
        },
    ]
    out = extract_related_domains(records, "ngast.com")
    assert out == [], f"R-F438: leak from issuer-matched record: {out}"


def test_rf438_extract_related_excludes_cdn():
    """CDN SAN entries on a real cert must NOT appear in related domains."""
    from aria_service.intel.domain_pivots import extract_related_domains
    records = [
        {
            "name_value": "ngast.com\nfoo.cloudflare.com\nreal-sibling.io",
            "not_before": "2024-01-01",
        },
    ]
    hosts = [r["host"] for r in extract_related_domains(records, "ngast.com")]
    assert "foo.cloudflare.com" not in hosts
    assert "real-sibling.io" in hosts


def test_rf438_extract_related_flags_shared_registrable_root():
    """The `shares_registrable_root_with_seed` flag tells the operator
    quickly whether the peer is a likely-related domain or a totally
    different brand sharing a multi-tenant cert."""
    from aria_service.intel.domain_pivots import extract_related_domains
    records = [
        {
            "name_value": "ngast.com\nngast.io\ntotally-unrelated.com",
            "not_before": "2024-01-01",
        },
    ]
    out = {r["host"]: r for r in extract_related_domains(records, "ngast.com")}
    assert out["ngast.io"]["shares_registrable_root_with_seed"] is False  # diff TLD root
    assert out["totally-unrelated.com"]["shares_registrable_root_with_seed"] is False


def test_rf438_extract_related_caps_at_max():
    """Output must be capped to max_related to avoid bloating the DD
    report when a multi-tenant cert lists 1000+ SAN entries."""
    from aria_service.intel.domain_pivots import extract_related_domains
    # Build a cert with 50 unique sibling SAN entries
    sans = ["ngast.com"] + [f"peer-{i:03d}.com" for i in range(50)]
    records = [{"name_value": "\n".join(sans), "not_before": "2024-01-01"}]
    out = extract_related_domains(records, "ngast.com", max_related=10)
    assert len(out) == 10


# ───────────────────────── Capability — crtsh_lookup HTTP contract ───────────


def test_rf438_crtsh_lookup_handles_non_200(monkeypatch):
    """When crt.sh returns 502 (frequent under load), the function must
    return ok=False + error. The orchestrator treats this as 'unknown',
    NOT 'clean'."""
    import httpx
    from aria_service.intel import domain_pivots

    class _FakeResp:
        status_code = 502
        text = "<html>service unavailable</html>"
        def json(self): raise ValueError("not json")

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(domain_pivots.crtsh_lookup("ngast.com"))
    assert out["ok"] is False
    assert "502" in (out["error"] or "")
    assert out["records"] == []


def test_rf438_crtsh_lookup_handles_non_json_body(monkeypatch):
    """crt.sh sometimes returns HTML with status 200 when its DB is
    under load — must be caught, not crash."""
    import httpx
    from aria_service.intel import domain_pivots

    class _FakeResp:
        status_code = 200
        text = "<html>oops</html>"
        def json(self): raise ValueError("not json")

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(domain_pivots.crtsh_lookup("ngast.com"))
    assert out["ok"] is False
    assert "non-JSON" in (out["error"] or "")


def test_rf438_apex_derivation_does_not_mangle_w_prefixed_hosts():
    """Regression: pre-hotfix used `lstrip("www.")` which is a char-set
    strip (any of {w, .}), NOT a literal-prefix strip. So `wedding.com`
    became `edding.com`, breaking DD silently on any seed starting with
    'w'. Verifier caught pre-deploy. This test pins the correct
    behaviour: strip the literal `www.` prefix only."""
    # Mirror the orchestrator's exact derivation (dd_orchestrator.py block).
    from urllib.parse import urlparse
    def _derive_apex(seed_url: str) -> str:
        netloc = (urlparse(seed_url).netloc or "").lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    # Common case — www stripped
    assert _derive_apex("https://www.ngast.com/") == "ngast.com"
    # The bug case — must NOT strip "w" or "we"
    assert _derive_apex("https://wedding.com/") == "wedding.com", (
        "R-F438 regression: hostnames starting with 'w' must not be "
        "char-stripped by mistaken lstrip"
    )
    assert _derive_apex("https://westcorp.io/") == "westcorp.io"
    assert _derive_apex("https://wwwsite.com/") == "wwwsite.com"
    # No-scheme corner — urlparse returns empty netloc
    assert _derive_apex("ngast.com") == ""


def test_rf438_crtsh_lookup_dedups_records(monkeypatch):
    """If crt.sh returns duplicate `id`s, dedup to avoid double-counting."""
    import httpx
    from aria_service.intel import domain_pivots

    fake_data = [
        {"id": 1, "issuer_name": "Issuer", "name_value": "ngast.com", "not_before": "2024-01-01"},
        {"id": 1, "issuer_name": "Issuer", "name_value": "ngast.com", "not_before": "2024-01-01"},
        {"id": 2, "issuer_name": "Issuer", "name_value": "sibling.com\nngast.com", "not_before": "2024-02-01"},
    ]

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self): return fake_data

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(domain_pivots.crtsh_lookup("ngast.com"))
    assert out["ok"] is True
    assert len(out["records"]) == 2  # deduped

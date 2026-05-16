"""R-F580 (2026-05-16) — Cert Transparency adapter tests."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_normalise_strips_scheme_and_www():
    from aria_service.intel.sources.cert_transparency import _normalise_query
    assert _normalise_query("https://www.example.com/path") == "example.com"
    assert _normalise_query("HTTP://EXAMPLE.COM") == "example.com"
    assert _normalise_query("www.acme.io/contact") == "acme.io"
    assert _normalise_query("   plain.com   ") == "plain.com"


def test_normalise_handles_empty():
    from aria_service.intel.sources.cert_transparency import _normalise_query
    assert _normalise_query("") == ""
    assert _normalise_query("   ") == ""


def test_parse_row_canonicalises_shape():
    from aria_service.intel.sources.cert_transparency import _parse_row
    h = _parse_row({
        "id": 12345678,
        "common_name": "esnadinternational.com",
        "name_value": "esnadinternational.com\nwww.esnadinternational.com\nmail.esnadinternational.com",
        "issuer_name": "C=US, O=Let's Encrypt, CN=R3",
        "not_before": "2024-03-15T00:00:00",
        "not_after":  "2024-06-13T00:00:00",
    })
    assert h is not None
    assert h["common_name"] == "esnadinternational.com"
    assert "www.esnadinternational.com" in h["alt_names"]
    assert "mail.esnadinternational.com" in h["alt_names"]
    assert h["not_before"] == "2024-03-15"
    assert h["not_after"]  == "2024-06-13"
    assert h["ct_url"] == "https://crt.sh/?id=12345678"
    assert h["source"] == "crt.sh"
    assert h["tier"] == "1a"


def test_parse_row_skips_empty():
    from aria_service.intel.sources.cert_transparency import _parse_row
    assert _parse_row({}) is None
    assert _parse_row({"common_name": ""}) is None


def test_search_certs_skips_short_input():
    from aria_service.intel.sources.cert_transparency import search_certs
    assert asyncio.run(search_certs("")) == []
    assert asyncio.run(search_certs("ab")) == []


def test_search_certs_parses_real_response_shape():
    """Capability: handle crt.sh's actual JSON shape including the
    duplicate-row-per-CT-log entry quirk via dedupe."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value=[
        {
            "id": 1,
            "common_name": "esnad.com",
            "name_value": "esnad.com\nwww.esnad.com",
            "issuer_name": "Let's Encrypt R3",
            "not_before": "2024-01-01T00:00:00",
            "not_after":  "2024-04-01T00:00:00",
        },
        # Duplicate (same CN + not_before) - should dedupe
        {
            "id": 2,
            "common_name": "esnad.com",
            "name_value": "esnad.com\nwww.esnad.com",
            "issuer_name": "Let's Encrypt R3",
            "not_before": "2024-01-01T00:00:00",
            "not_after":  "2024-04-01T00:00:00",
        },
        # Different cert
        {
            "id": 3,
            "common_name": "shell.esnad.io",
            "name_value": "shell.esnad.io",
            "issuer_name": "Let's Encrypt R3",
            "not_before": "2024-02-01T00:00:00",
            "not_after":  "2024-05-01T00:00:00",
        },
    ])

    async def _run():
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=fake_response)
        with patch("aria_service.intel.sources.cert_transparency.httpx.AsyncClient", return_value=client):
            from aria_service.intel.sources.cert_transparency import search_certs
            return await search_certs("esnad", use_cache=False)

    hits = asyncio.run(_run())
    assert len(hits) == 2  # dedupe collapsed the dupe row
    cns = {h["common_name"] for h in hits}
    assert "esnad.com" in cns
    assert "shell.esnad.io" in cns


def test_search_certs_handles_non_json_response():
    """Capability: crt.sh sometimes returns HTML on heavy load. Must
    degrade gracefully, not raise."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json = MagicMock(side_effect=json.JSONDecodeError("bad", "doc", 0))

    async def _run():
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=fake_response)
        with patch("aria_service.intel.sources.cert_transparency.httpx.AsyncClient", return_value=client):
            from aria_service.intel.sources.cert_transparency import search_certs
            return await search_certs("test", use_cache=False)
    assert asyncio.run(_run()) == []


def test_search_certs_handles_http_error():
    fake_response = MagicMock()
    fake_response.status_code = 503

    async def _run():
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(return_value=fake_response)
        with patch("aria_service.intel.sources.cert_transparency.httpx.AsyncClient", return_value=client):
            from aria_service.intel.sources.cert_transparency import search_certs
            return await search_certs("test", use_cache=False)
    assert asyncio.run(_run()) == []


def test_search_certs_in_process_cache_returns_same_result_twice():
    """Capability: same query within TTL returns cached result; cache
    spares crt.sh from duplicate hits on the same DD run."""
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value=[{
        "id": 99,
        "common_name": "cache-test.com",
        "name_value": "cache-test.com",
        "issuer_name": "X",
        "not_before": "2024-01-01",
        "not_after": "2024-04-01",
    }])
    call_count = {"n": 0}

    async def fake_get(*args, **kwargs):
        call_count["n"] += 1
        return fake_response

    async def _run():
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = fake_get
        with patch("aria_service.intel.sources.cert_transparency.httpx.AsyncClient", return_value=client):
            from aria_service.intel.sources.cert_transparency import search_certs, _cache
            _cache.clear()  # ensure clean
            a = await search_certs("cache-test.com")
            b = await search_certs("cache-test.com")
            return a, b, call_count["n"]

    a, b, n = asyncio.run(_run())
    assert a == b
    assert n == 1, f"Expected 1 HTTP call (cached on 2nd), got {n}"


# ── Shell-pattern detection ────────────────────────────────────────────


def test_detect_shell_pattern_empty():
    from aria_service.intel.sources.cert_transparency import detect_shell_pattern
    out = detect_shell_pattern([])
    assert out["score"] == 0
    assert "no_certs_found" in out["signals"]


def test_detect_shell_pattern_flags_only_lets_encrypt():
    from aria_service.intel.sources.cert_transparency import detect_shell_pattern
    hits = [
        {"common_name": "shell1.com", "alt_names": [], "issuer": "Let's Encrypt R3"},
        {"common_name": "shell2.com", "alt_names": [], "issuer": "Let's Encrypt R3"},
    ]
    out = detect_shell_pattern(hits)
    assert "only_lets_encrypt_no_paid_ca" in out["signals"]
    assert out["score"] > 0


def test_detect_shell_pattern_flags_high_density():
    from aria_service.intel.sources.cert_transparency import detect_shell_pattern
    hits = [
        {"common_name": f"shell{i}.com", "alt_names": [], "issuer": "Let's Encrypt R3"}
        for i in range(20)
    ]
    out = detect_shell_pattern(hits)
    sig_names = [s for s in out["signals"] if s.startswith("high_cert_density")]
    assert len(sig_names) == 1


def test_detect_shell_pattern_flags_sibling_cluster():
    """Capability: the esnadinternational-style fingerprint — multiple
    apex domains sharing the same operator profile."""
    from aria_service.intel.sources.cert_transparency import detect_shell_pattern
    hits = [
        {"common_name": "esnadinternational.com",  "alt_names": [], "issuer": "Let's Encrypt R3"},
        {"common_name": "esnad-international.com", "alt_names": [], "issuer": "Let's Encrypt R3"},
        {"common_name": "esnad-intl.net",          "alt_names": [], "issuer": "Let's Encrypt R3"},
        {"common_name": "esnadintl.org",           "alt_names": [], "issuer": "Let's Encrypt R3"},
        {"common_name": "esnadgroup.io",           "alt_names": [], "issuer": "Let's Encrypt R3"},
        {"common_name": "esnadtrading.co",         "alt_names": [], "issuer": "Let's Encrypt R3"},
    ]
    out = detect_shell_pattern(hits)
    sig_cluster = [s for s in out["signals"] if s.startswith("sibling_domain_cluster")]
    assert len(sig_cluster) == 1
    assert out["score"] > 30

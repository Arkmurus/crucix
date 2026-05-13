"""R-F440 — DNS fingerprint + ASN owner via DoH.

Pre-R-F440 ARIA had ZERO DNS-level OSINT. R-F438 covered CT logs
(shared TLS certs); R-F440 covers shared infrastructure at the
DNS/network layer: A records → primary IP → ASN owner; MX records
reveal mail provider; NS records reveal nameserver provider; TXT
records reveal SPF/DMARC config. An ASN country differing from the
declared jurisdiction surfaces an amber finding (legitimate edge
CDN vs undisclosed offshore hosting).

V1 backends (no key, no dependency):
  - Cloudflare DNS-over-HTTPS (cloudflare-dns.com/dns-query)
  - api.iptoasn.com (free Team Cymru-style ASN lookup)
"""
from __future__ import annotations

import asyncio


# ───────────────────────── DoH dns_lookup ────────────────────────────────────


def test_rf440_dns_lookup_rejects_unsupported_record():
    """Only A/AAAA/MX/NS/TXT/CNAME are supported (covers 90% of OSINT
    needs without rabbit-holing into SOA/PTR/etc)."""
    from aria_service.intel.domain_pivots import dns_lookup
    out = asyncio.run(dns_lookup("ngast.com", record_type="SOA"))
    assert out["ok"] is False
    assert "unsupported" in (out["error"] or "")


def test_rf440_dns_lookup_rejects_malformed_domain():
    from aria_service.intel.domain_pivots import dns_lookup
    out = asyncio.run(dns_lookup("notadomain"))
    assert out["ok"] is False
    assert "malformed" in (out["error"] or "")


def test_rf440_dns_lookup_parses_doh_response(monkeypatch):
    """Cloudflare DoH returns Answer:[{name, type, TTL, data}, ...].
    Our function maps to answers:[{data, type, ttl}, ...]."""
    import httpx
    from aria_service.intel import domain_pivots as dp

    fake = {
        "Status": 0,
        "Answer": [
            {"name": "ngast.com.", "type": 1, "TTL": 300, "data": "203.0.113.10"},
            {"name": "ngast.com.", "type": 1, "TTL": 300, "data": "203.0.113.11"},
        ],
    }

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self): return fake

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(dp.dns_lookup("ngast.com", record_type="A"))
    assert out["ok"] is True
    assert out["status"] == 0
    assert len(out["answers"]) == 2
    assert out["answers"][0]["data"] == "203.0.113.10"
    assert out["answers"][0]["type"] == 1


def test_rf440_dns_lookup_handles_non_200(monkeypatch):
    import httpx
    from aria_service.intel import domain_pivots as dp

    class _FakeResp:
        status_code = 502
        text = ""
        def json(self): raise ValueError("no")

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(dp.dns_lookup("ngast.com"))
    assert out["ok"] is False
    assert "502" in (out["error"] or "")


# ───────────────────────── ip_to_asn ────────────────────────────────────────


def test_rf440_ip_to_asn_parses_response(monkeypatch):
    import httpx
    from aria_service.intel import domain_pivots as dp

    fake = {
        "announced": True,
        "as_number": 13335,
        "as_country_code": "US",
        "as_description": "CLOUDFLARENET",
    }

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self): return fake

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(dp.ip_to_asn("1.1.1.1"))
    assert out["ok"] is True
    assert out["as_number"] == 13335
    assert out["as_country_code"] == "US"
    assert out["as_description"] == "CLOUDFLARENET"


def test_rf440_ip_to_asn_handles_unrouted_ip(monkeypatch):
    """If the IP isn't BGP-announced, api.iptoasn.com returns
    `announced: false`. We treat that as an error (operator should
    see the gap, not a misleading "clean" ASN of None)."""
    import httpx
    from aria_service.intel import domain_pivots as dp

    fake = {"announced": False}

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self): return fake

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def get(self, *a, **kw): return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    out = asyncio.run(dp.ip_to_asn("198.51.100.1"))
    assert out["ok"] is False
    assert "not announced" in (out["error"] or "")


def test_rf440_ip_to_asn_handles_empty_ip():
    from aria_service.intel.domain_pivots import ip_to_asn
    out = asyncio.run(ip_to_asn(""))
    assert out["ok"] is False


# ───────────────────────── dns_fingerprint aggregator ────────────────────────


def test_rf440_dns_fingerprint_aggregates_records_and_asn(monkeypatch):
    """The aggregator pulls A/MX/NS/TXT and ASN-resolves the first A
    record. Per-record-type failures land in `errors` list but the
    aggregate is still ok if at least one record type returned data."""
    from aria_service.intel import domain_pivots as dp

    async def _fake_dns_lookup(domain, *, record_type, timeout=10.0):
        # A returns one record; MX fails; NS returns two; TXT empty
        rt = record_type.upper()
        if rt == "A":
            return {"ok": True, "answers": [
                {"data": "203.0.113.10", "type": 1, "ttl": 300},
            ], "status": 0, "domain": domain, "type": rt, "error": None}
        if rt == "MX":
            return {"ok": False, "answers": [], "status": None,
                    "domain": domain, "type": rt, "error": "MX HTTP 500"}
        if rt == "NS":
            return {"ok": True, "answers": [
                {"data": "ns1.cloudflare.com.", "type": 2, "ttl": 86400},
                {"data": "ns2.cloudflare.com.", "type": 2, "ttl": 86400},
            ], "status": 0, "domain": domain, "type": rt, "error": None}
        if rt == "TXT":
            return {"ok": True, "answers": [], "status": 0,
                    "domain": domain, "type": rt, "error": None}
        return {"ok": False, "answers": [], "status": None,
                "domain": domain, "type": rt, "error": "unhandled"}

    async def _fake_asn(ip, *, timeout=10.0):
        return {"ok": True, "ip": ip, "as_number": 13335,
                "as_country_code": "US", "as_description": "CLOUDFLARENET",
                "error": None}

    monkeypatch.setattr(dp, "dns_lookup", _fake_dns_lookup)
    monkeypatch.setattr(dp, "ip_to_asn", _fake_asn)

    out = asyncio.run(dp.dns_fingerprint("ngast.com"))
    assert out["ok"] is True
    assert out["primary_ip"] == "203.0.113.10"
    assert out["primary_asn"]["as_number"] == 13335
    assert out["primary_asn"]["country"] == "US"
    assert out["primary_asn"]["description"] == "CLOUDFLARENET"
    assert len(out["ns"]) == 2
    assert any("MX:" in e for e in out["errors"]), (
        f"R-F440: MX failure must surface in errors. errors={out['errors']}"
    )


def test_rf440_dns_fingerprint_handles_no_a_records(monkeypatch):
    """If A returns no answers (NXDOMAIN), primary_ip is None and
    no ASN call is made. Aggregate ok is False only if ALL record
    types returned empty (NS/MX/TXT typically still exist for parked
    domains)."""
    from aria_service.intel import domain_pivots as dp

    async def _fake_dns_lookup(domain, *, record_type, timeout=10.0):
        return {"ok": True, "answers": [], "status": 0,
                "domain": domain, "type": record_type, "error": None}

    asn_called = {"yes": False}

    async def _fake_asn(ip, *, timeout=10.0):
        asn_called["yes"] = True
        return {"ok": False, "error": "no"}

    monkeypatch.setattr(dp, "dns_lookup", _fake_dns_lookup)
    monkeypatch.setattr(dp, "ip_to_asn", _fake_asn)
    out = asyncio.run(dp.dns_fingerprint("ngast.com"))
    assert out["primary_ip"] is None
    assert out["primary_asn"] is None
    assert asn_called["yes"] is False, (
        "R-F440: ASN must NOT be called when no A records found"
    )
    assert out["ok"] is False  # all four record types empty

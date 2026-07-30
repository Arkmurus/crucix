"""R-F3473 — the SSRF guard did a blocking DNS resolve on the event loop.

Third distinct stall cause named by R-F3464's attribution, live 2026-07-30
(after R-F3467 and R-F3468 each removed the previous one):

    "last_stall_loop_stack": [
      "/usr/local/lib/python3.13/socket.py:getaddrinfo:981",
      "/app/aria_service/intel/security.py:validate_url:133",
      "/app/aria_service/intel/security.py:sanitise_url:263" ],
    "last_stall_threads": {"total": 27, "parked": 22, "aiosqlite_workers": 9}

``validate_url`` resolves every hostname and re-checks each returned address
against the private ranges — the anti-DNS-rebinding half of the SSRF guard. That
check is correct and must stay. But ``socket.getaddrinfo`` is a blocking syscall,
and it is SLOWEST exactly when it fails: an unresolvable host burns the resolver
timeout before raising. A crawler retrying dead domains pays that repeatedly, on
the loop.

``validate_url`` is sync with 23 call sites, so converting it to async would
ripple across the tree rather than fix anything. The surgical fix is to stop
performing the same lookup over and over: a bounded TTL cache, with NEGATIVE
entries as well as positive ones, since the failures are the expensive case.

The cache must not become an SSRF bypass, so the private-range verdict is what is
cached — never a bare "this host is fine".
"""
from __future__ import annotations

import time

import pytest

from aria_service.intel import security


@pytest.fixture(autouse=True)
def _clear_cache():
    security._dns_cache_clear()
    yield
    security._dns_cache_clear()


class TestResolveIsCached:

    def test_repeat_validation_resolves_once(self, monkeypatch):
        """The capability property: N validations of a host = 1 DNS lookup."""
        calls = {"n": 0}

        def _counting_getaddrinfo(host, port, *a, **kw):
            calls["n"] += 1
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(security.socket, "getaddrinfo", _counting_getaddrinfo)
        for _ in range(5):
            ok, _reason = security.validate_url("https://example.com/a")
            assert ok
        assert calls["n"] == 1, f"resolved {calls['n']} times; expected 1 (cached)"

    def test_failed_resolution_is_also_cached(self, monkeypatch):
        """The expensive case. An unresolvable host burns the resolver timeout;
        a crawler retrying dead domains must not pay that on every URL."""
        calls = {"n": 0}

        def _failing(host, port, *a, **kw):
            calls["n"] += 1
            time.sleep(0.05)                 # stands in for a resolver timeout
            raise OSError("Name or service not known")

        monkeypatch.setattr(security.socket, "getaddrinfo", _failing)
        for _ in range(5):
            security.validate_url("https://dead-domain-xyz.example/a")
        assert calls["n"] == 1, f"resolved {calls['n']} times; negative cache missed"

    def test_distinct_hosts_are_resolved_separately(self, monkeypatch):
        seen: list[str] = []

        def _rec(host, port, *a, **kw):
            seen.append(host)
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(security.socket, "getaddrinfo", _rec)
        security.validate_url("https://a.example.com/x")
        security.validate_url("https://b.example.com/x")
        assert seen == ["a.example.com", "b.example.com"]

    def test_cache_is_bounded(self, monkeypatch):
        """An unbounded cache on attacker-supplied hostnames is a memory leak."""
        monkeypatch.setattr(
            security.socket, "getaddrinfo",
            lambda h, p, *a, **kw: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        for i in range(security._DNS_CACHE_MAX + 50):
            security.validate_url(f"https://h{i}.example.com/x")
        assert len(security._DNS_CACHE) <= security._DNS_CACHE_MAX

    def test_entries_expire(self, monkeypatch):
        calls = {"n": 0}

        def _counting(host, port, *a, **kw):
            calls["n"] += 1
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(security.socket, "getaddrinfo", _counting)
        monkeypatch.setattr(security, "_DNS_CACHE_TTL_S", 0.05)
        security.validate_url("https://example.com/a")
        time.sleep(0.08)
        security.validate_url("https://example.com/a")
        assert calls["n"] == 2, "cache did not expire"


class TestCachingIsNotAnSsrfBypass:
    """Every caching win is paired with the security property it must preserve."""

    def test_private_ip_resolution_still_blocked(self, monkeypatch):
        monkeypatch.setattr(
            security.socket, "getaddrinfo",
            lambda h, p, *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 0))],
        )
        ok, reason = security.validate_url("https://rebind.example.com/x")
        assert not ok and "private" in reason.lower(), reason

    def test_private_ip_still_blocked_on_the_CACHED_path(self, monkeypatch):
        """The verdict is cached, not a bare 'this host is fine'. A second call
        served from cache must still refuse."""
        monkeypatch.setattr(
            security.socket, "getaddrinfo",
            lambda h, p, *a, **kw: [(2, 1, 6, "", ("10.0.0.5", 0))],
        )
        first_ok, _ = security.validate_url("https://rebind.example.com/x")
        # Now make DNS look clean — the cached verdict must still win.
        monkeypatch.setattr(
            security.socket, "getaddrinfo",
            lambda h, p, *a, **kw: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        second_ok, reason = security.validate_url("https://rebind.example.com/x")
        assert not first_ok
        assert not second_ok, f"cached path allowed a private-IP host: {reason}"

    def test_internal_suffix_still_blocked_without_dns(self, monkeypatch):
        def _boom(*_a, **_kw):
            raise AssertionError("must not resolve an .internal host at all")

        monkeypatch.setattr(security.socket, "getaddrinfo", _boom)
        ok, reason = security.validate_url("https://aria-intel.internal/x")
        assert not ok and "internal" in reason.lower()

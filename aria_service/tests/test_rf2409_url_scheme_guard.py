"""R-F2409 capability test — B310/SSRF guards on urlopen call sites.

Proves the runtime guards actually BLOCK file:/ (and other non-HTTP schemes)
and, for the arbitrary-URL fetcher, block URLs resolving to internal IPs —
driving the REAL functions, not just the helper.
"""
import importlib.util
from pathlib import Path

import pytest

from aria_service.cli import ingest_tier_a, ingest_corpus, ingest_hardware_facts


# ── _guard_url: full SSRF (arbitrary fetch) ──────────────────────────────────
@pytest.mark.parametrize("bad", [
    "file:///etc/passwd",
    "ftp://host/x",
    "gopher://host/",
    "http://127.0.0.1/x",             # loopback
    "http://169.254.169.254/latest/", # cloud metadata (link-local)
    "http://10.0.0.5/",               # RFC1918 private
    "http://[::1]/",                  # IPv6 loopback
])
def test_guard_url_blocks_unsafe(bad):
    with pytest.raises(ValueError):
        ingest_tier_a._guard_url(bad, allow_internal=False)


def test_guard_url_scheme_only_allows_internal():
    # API POSTs (allow_internal=True): internal host OK, non-HTTP still rejected.
    ingest_tier_a._guard_url("http://localhost:8000/api/aria/corpus/ingest", allow_internal=True)
    ingest_tier_a._guard_url("http://aria-intel.internal:8000/x", allow_internal=True)
    with pytest.raises(ValueError):
        ingest_tier_a._guard_url("file:///etc/passwd", allow_internal=True)


def test_guard_url_allows_public(monkeypatch):
    monkeypatch.setattr(ingest_tier_a.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    ingest_tier_a._guard_url("https://example.com/data.pdf", allow_internal=False)  # no raise


# ── end-to-end: the REAL fetch function blocks unsafe URLs ───────────────────
def test_http_get_blocks_file_scheme():
    status, body, ctype = ingest_tier_a._http_get("file:///etc/passwd")
    assert status == -1 and body == b"" and "non-HTTP" in ctype


def test_http_get_blocks_metadata_ip():
    status, body, ctype = ingest_tier_a._http_get("http://169.254.169.254/latest/meta-data/")
    assert status == -1 and "non-public IP" in ctype


# ── _require_http_scheme (scheme-only sites) ─────────────────────────────────
@pytest.mark.parametrize("mod", [ingest_corpus, ingest_hardware_facts])
def test_require_http_scheme(mod):
    mod._require_http_scheme("https://aria-intel.fly.dev/api")   # ok
    mod._require_http_scheme("http://localhost:8000/api")        # internal ok (scheme-only)
    for bad in ("file:///etc/passwd", "ftp://h/x", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            mod._require_http_scheme(bad)


# ── standalone shipped client aria.py (loaded from file path) ────────────────
def test_client_aria_scheme_guard():
    path = Path(__file__).resolve().parents[1] / "static" / "aria_client" / "aria.py"
    spec = importlib.util.spec_from_file_location("aria_client_probe", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._require_http_scheme("https://host/api")  # ok
    with pytest.raises(ValueError):
        mod._require_http_scheme("file:///etc/passwd")

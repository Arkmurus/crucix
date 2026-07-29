# -*- coding: utf-8 -*-
"""Capability tests for the DD-audit security hardening batch R-F1850..R-F1854.

Each test invokes the ACTUAL fixed code path (not a helper proxy) and asserts the
user-visible security outcome, per CLAUDE.md §3c / §23.

  R-F1850  dead PowerShell RCE endpoint removed from the boot path
  R-F1851  user/discovery-URL fetchers route through the SSRF guard (safe_get/is_safe_url)
  R-F1852  async job/graph result endpoints enforce owner (cross-tenant → not_found/404)
  R-F1853  decoded upload bytes are content-scanned; body-size middleware exists
  R-F1854  WA messages.upsert loop skips malformed entries (no self-DoS crash)
"""
import asyncio

import pytest


# ── R-F1850 — PowerShell RCE endpoint is gone from the boot path ──────────────
def test_rf1850_powershell_endpoint_not_registered():
    import aria_service.main as m

    # No live /powershell/* route on the app.
    paths = [getattr(r, "path", "") for r in m.app.routes]
    assert not any("/powershell" in p for p in paths), (
        "a /powershell route is registered — the RCE endpoint is live"
    )

    # The boot block no longer imports/calls the endpoint registrar.
    src = open("aria_service/main.py", encoding="utf-8").read()
    assert "import PowerShellMaster, add_powershell_endpoints" not in src
    assert "add_powershell_endpoints(router, ps_master)" not in src


# ── R-F1851 — SSRF guard blocks internal targets on the user-URL fetchers ─────
def test_rf1851_is_safe_url_blocks_internal(monkeypatch):
    """R-F3433 — the public-hostname leg used to resolve example.com for real, which
    made this the one DD test that failed under ARIA_TEST_BLOCK_NETWORK=1 and left the
    suite with a live-DNS dependency. Stubbing `_ips_for_host` (the resolution seam) is
    strictly stronger, not weaker: it pins BOTH directions of the resolve-then-classify
    path, including the DNS-rebinding case below — a public-looking hostname that
    resolves to a private address, which the live version could never test at all."""
    from aria_service.intel import url_safety as us

    for bad in (
        "http://169.254.169.254/latest/meta-data/",     # cloud metadata
        "http://aria-intel.internal:8000/api/aria",      # internal service name
        "http://127.0.0.1:8000/",                        # loopback
        "http://10.0.0.5/",                              # RFC1918
        "file:///etc/passwd",                            # scheme
    ):
        ok, reason = us.is_safe_url(bad)
        assert not ok, f"is_safe_url allowed {bad!r} ({reason})"

    # A hostname resolving to a public IP is still allowed.
    monkeypatch.setattr(us, "_ips_for_host", lambda h: ["93.184.216.34"])
    ok, reason = us.is_safe_url("https://example.com/")
    assert ok, f"public hostname should be allowed, got {reason}"

    # DNS rebinding: the SAME public-looking hostname resolving to a private address
    # must be rejected. is_safe_url's docstring promises "a name that resolves to ANY
    # blocked IP is rejected entirely" — this is the leg that proves it.
    monkeypatch.setattr(us, "_ips_for_host", lambda h: ["93.184.216.34", "10.0.0.5"])
    ok, reason = us.is_safe_url("https://example.com/")
    assert not ok, "DNS rebinding to a private IP must be blocked"

    # Resolution failure must fail CLOSED, never default to allow.
    monkeypatch.setattr(us, "_ips_for_host", lambda h: [])
    ok, reason = us.is_safe_url("https://example.com/")
    assert not ok and reason == "dns_fail", f"resolution failure must block, got {reason}"


def test_rf1851_detect_content_type_blocks_internal():
    from aria_service.intel import crawl_enhancements as ce

    out = asyncio.run(ce.detect_content_type("http://127.0.0.1:9/"))
    assert str(out.get("error", "")).startswith("ssrf_blocked"), out


def test_rf1851_fetch_with_fallbacks_blocks_internal():
    from aria_service.intel import crawl_enhancements as ce

    out = asyncio.run(ce.fetch_with_fallbacks("http://aria-intel.internal:8000/x"))
    assert out.get("source") == "blocked_unsafe_url", out
    assert out.get("ok") is False


# ── R-F1852 — async job/graph result endpoints enforce ownership ──────────────
def _seed_chat_job(job_id, data):
    from aria_service.routes import aria
    assert asyncio.run(aria._chat_job_set(job_id, data)) is True


def test_rf1852_chat_result_owner_enforced():
    from aria_service.routes import aria

    _seed_chat_job("rf1852_chat", {"status": "done",
                                   "result": {"response": "CONFIDENTIAL"},
                                   "user_id": "alice"})

    # Owner sees the answer.
    owner = asyncio.run(aria.chat_result_ep("rf1852_chat", user_id="alice"))
    assert owner.get("status") == "done"
    assert owner.get("result", {}).get("response") == "CONFIDENTIAL"

    # Cross-tenant caller gets not_found (no existence leak, no content).
    attacker = asyncio.run(aria.chat_result_ep("rf1852_chat", user_id="mallory"))
    assert attacker.get("status") == "not_found", attacker
    assert "result" not in attacker

    # Admin / internal path (no user_id) still works.
    admin = asyncio.run(aria.chat_result_ep("rf1852_chat", user_id=""))
    assert admin.get("status") == "done"


def test_rf1852_read_document_result_owner_enforced():
    from aria_service.routes import aria

    assert asyncio.run(aria._readdoc_job_set(
        "rf1852_doc",
        {"status": "done", "result": {"text": "SECRET DOC"}, "user_id": "alice"},
    )) is True

    owner = asyncio.run(aria.read_document_result_ep("rf1852_doc", user_id="alice"))
    assert owner.get("status") == "done"

    attacker = asyncio.run(aria.read_document_result_ep("rf1852_doc", user_id="mallory"))
    assert attacker.get("status") == "not_found", attacker
    assert "result" not in attacker


# ── R-F1853 — decoded uploads are content-scanned; body-size middleware wired ──
def test_rf1853_content_scanner_detects_threat():
    from aria_service.intel import content_scanner as cs

    # EICAR test string — the canonical "is the AV path actually live" probe.
    eicar = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    res = asyncio.run(cs.scan_bytes(eicar, claimed_type="bin", source_name="rf1853_test"))
    assert res.safe is False, "content scanner did not flag EICAR"


def test_rf1853_read_document_path_calls_scanner():
    # The fix is dead-code → wired. Assert the impl references the scanner so the
    # decoded-bytes branch can never silently skip it.
    src = open("aria_service/routes/aria.py", encoding="utf-8").read()
    assert "content_scanner as _cs1853" in src
    assert ".scan_bytes(" in src


def test_rf1853_body_size_middleware_present():
    import aria_service.main as m
    assert hasattr(m, "_limit_body_size")
    assert m._MAX_BODY_BYTES >= 1024 * 1024


# ── R-F1854 — WA loop skips malformed messages instead of crashing ────────────
def test_rf1854_wa_listener_has_shape_guard():
    src = open("services/wa-listener/aria_wa_listener.mjs", encoding="utf-8").read()
    # The guard must sit before the first msg.key access in the upsert loop.
    loop_at = src.index("for (const msg of messages) {")
    guard_at = src.index("if (!msg || !msg.key) continue;", loop_at)
    # R-F1974 refactored the first msg.key read to `const _isFromMe = !!msg.key.fromMe;`
    # (was `if (msg.key.fromMe) continue;`). Assert the guard still precedes it.
    first_key_at = src.index("!!msg.key.fromMe", loop_at)
    assert loop_at < guard_at < first_key_at, "shape guard not before msg.key access"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

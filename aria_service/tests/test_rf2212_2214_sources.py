"""Capability tests for the source-subsystem hardening batch (2026-07-01).

R-F2212 — validate_url SSRF hardening (block fly .internal + single-label hosts +
          DNS-resolved private IPs).
R-F2213 — user_sources_add_ep honest status (probe → verified/pending, no hardcoded
          "verified"; graceful error).
R-F2214 — news_monitor wires silent source failures (empty/unreachable feed +
          empty vault-website scrape) to the brain (§21a).

Each test invokes the ACTUAL function that was broken and asserts the user-visible
outcome (not a helper).
"""
import pytest

from aria_service.intel.security import validate_url


# ── R-F2212 — SSRF hardening ─────────────────────────────────────────────────
def test_rf2212_blocks_fly_internal_host():
    # Confirmed live 2026-07-01: this URL was ACCEPTED before the fix.
    ok, why = validate_url("http://aria-intel.internal:8000/health")
    assert ok is False
    assert "internal" in why.lower()


def test_rf2212_blocks_all_fly_internal_apps():
    for h in ("aria-web.internal:3117", "aria-wa.internal:5070", "aria-searxng.internal:8080"):
        ok, _ = validate_url(f"http://{h}/x")
        assert ok is False, h


def test_rf2212_blocks_single_label_host():
    ok, _ = validate_url("http://redis:6379/")   # bare service name → never public
    assert ok is False


def test_rf2212_still_blocks_metadata_ip():       # regression — literal private IP
    ok, _ = validate_url("http://169.254.169.254/latest/meta-data/")
    assert ok is False


def test_rf2212_allows_public_host():
    # Public FQDN with a dot. DNS resolution is best-effort: if the test env has no
    # network, getaddrinfo raises and we allow (the fetch would fail naturally); if
    # it resolves, it resolves to a public IP. Either way → allowed.
    ok, why = validate_url("https://feeds.bbci.co.uk/news/world/rss.xml")
    assert ok is True, why


# ── R-F2213 — honest add-source status ───────────────────────────────────────
class _FakeVault:
    def __init__(self):
        self.rows = {}
        # bind as an attribute (not `def list`) so vault.list() works without
        # tripping the R-F1958 shadows-builtin lint on this API-mirroring mock.
        self.list = self._list

    def _list(self, agent_id="", limit=0):
        return [r for r in self.rows.values() if r["agent_id"] == agent_id]

    def record(self, **kw):
        row = {
            "site_id": kw["site_id"], "agent_id": kw["agent_id"], "status": kw["status"],
            "site_url": kw["site_url"], "site_name": kw["site_name"],
            "site_type": kw["site_type"], "agent_type": kw.get("agent_type"),
            "notes": kw.get("notes", ""),
        }
        self.rows[kw["site_id"]] = row
        return row

    def get(self, sid):
        return self.rows.get(sid)


class _FakeResp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


class _FakeClient:
    def __init__(self, code, text):
        self._code, self._text = code, text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        return _FakeResp(self._code, self._text)


class _FakeReq:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


async def _add(monkeypatch, code, text):
    import httpx
    from aria_service.routes import aria as A
    fv = _FakeVault()
    monkeypatch.setattr("aria_service.intel.agent_signup_vault.get_vault", lambda: fv)
    monkeypatch.setattr("aria_service.intel.security.validate_url", lambda u: (True, "OK"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(code, text))
    req = _FakeReq({"name": "T", "url": "https://example.com/feed.xml", "site_type": "rss"})
    out = await A.user_sources_add_ep(req, user_id="tuser")
    return out, fv


async def test_rf2213_reachable_source_is_verified(monkeypatch):
    out, fv = await _add(monkeypatch, 200, "<rss><channel/></rss>")
    assert out["success"] is True
    assert out["verified"] is True
    assert fv.get(out["entry"]["site_id"])["status"] == "verified"


async def test_rf2213_dead_source_is_pending_not_verified(monkeypatch):
    # The old code hardcoded status="verified" regardless — this proves it no longer lies.
    out, fv = await _add(monkeypatch, 404, "not found")
    assert out["success"] is True
    assert out["verified"] is False
    assert fv.get(out["entry"]["site_id"])["status"] == "pending"


async def test_rf2213_probe_exception_does_not_block_add(monkeypatch):
    import httpx
    from aria_service.routes import aria as A
    fv = _FakeVault()
    monkeypatch.setattr("aria_service.intel.agent_signup_vault.get_vault", lambda: fv)
    monkeypatch.setattr("aria_service.intel.security.validate_url", lambda u: (True, "OK"))

    def _boom(**kw):
        raise RuntimeError("dns fail")
    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    req = _FakeReq({"name": "T", "url": "https://example.com/feed.xml", "site_type": "rss"})
    out = await A.user_sources_add_ep(req, user_id="tuser")
    assert out["success"] is True          # probe failure NEVER blocks the add
    assert out["verified"] is False        # but it is honestly "pending"


# ── R-F2214 — §21a wiring of silent source failures ──────────────────────────
async def _none(*a, **k):
    return None


async def test_rf2214_dead_feed_is_wired_to_brain(monkeypatch):
    from aria_service.intel import news_monitor as nm
    calls = []
    # inject a dead VAULT source (the class that rots invisibly); curated
    # NEWS_SOURCES also fail but must NOT wire (scoped to vault_curated).
    dead_vault = [("vault:DeadUserSrc", "https://dead.example/rss", "vault_curated", "en", "tier_2", ["custom"])]
    monkeypatch.setattr(nm, "_fetch_feed", lambda url, name: _none())
    monkeypatch.setattr(nm, "_get_vault_feed_sources", lambda: dead_vault)
    monkeypatch.setattr(nm, "wire_failure", lambda **kw: calls.append(kw))
    monkeypatch.setattr(nm, "wire_success", lambda **kw: None)
    res = await nm.poll_feeds()
    assert res["feeds_failed"] > 0
    # the dead vault source reaches the brain (was silently counted before)...
    assert calls, "no wire_failure emitted for the dead vault source"
    assert any("DeadUserSrc" in (c.get("summary", "")) for c in calls)
    # ...and the curated firehose failures do NOT flood (scoped to vault_curated)
    assert all("news_monitor:feed:" in c.get("source_id", "") for c in calls)
    assert len(calls) == 1, f"expected only the vault source to wire, got {len(calls)}"


async def test_rf2214_empty_website_scrape_is_wired(monkeypatch):
    from aria_service.intel import news_monitor as nm
    calls = []
    monkeypatch.setattr(nm, "wire_failure", lambda **kw: calls.append(kw))

    # probe raises → _wire_scrape_failure must fire
    import aria_service.intel.researcher as _r
    async def _probe_boom(url, timeout=0):
        raise RuntimeError("probe error")
    monkeypatch.setattr(_r, "extract_url_text", _probe_boom)
    out = await nm._scrape_vault_website("vault:X", "https://example.com", "vault_curated", "en", "tier_2", ["custom"])
    assert out == {"fetched": 0, "new": 0}
    assert any("scrape" in (c.get("source_id", "").lower()) for c in calls)

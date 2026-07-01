"""Capability tests for the source-quality batch (2026-07-01).

R-F2215 — rag_store credibility-aware ranking (credibility_tier applied as a
          bounded score multiplier; neutral when absent; env-reversible).
R-F2217 — news_monitor dead-source auto-suspension (consecutive-fail counter →
          status=failed; success clears streak / promotes pending→verified).
R-F2218 — news_monitor redirect-SSRF (every redirect hop re-validated; a
          public→internal 302 is refused).
"""
import importlib

import pytest


# ── R-F2215 — credibility-aware ranking ──────────────────────────────────────
# NB: import the module under its full name (not an `rs` alias — the R-F1958
# pre-commit lint resolves the alias `rs` to `redis_store` and false-fails).
def test_rf2215_multiplier_orders_by_tier():
    from aria_service.intel import rag_store
    # higher credibility (lower tier number) → higher multiplier
    assert rag_store._credibility_multiplier(1) > rag_store._credibility_multiplier(3) > rag_store._credibility_multiplier(5)
    assert rag_store._credibility_multiplier(1) == 1.20
    assert rag_store._credibility_multiplier(6) == 0.70


def test_rf2215_neutral_when_tier_absent_or_bad():
    from aria_service.intel import rag_store
    # content with no tier (corpus/vault) must NEVER be demoted
    assert rag_store._credibility_multiplier(None) == 1.0
    assert rag_store._credibility_multiplier("garbage") == 1.0
    assert rag_store._credibility_multiplier(99) == 1.0


def test_rf2215_env_reversible(monkeypatch):
    monkeypatch.setenv("ARIA_RAG_CREDIBILITY_RANK", "0")
    from aria_service.intel import rag_store
    rag_store = importlib.reload(rag_store)
    try:
        # disabled → every tier is neutral (ranking unchanged)
        assert rag_store._credibility_multiplier(1) == 1.0
        assert rag_store._credibility_multiplier(6) == 1.0
    finally:
        monkeypatch.setenv("ARIA_RAG_CREDIBILITY_RANK", "1")
        importlib.reload(rag_store)


# ── R-F2217 — dead-source auto-suspension ────────────────────────────────────
class _FakeVault:
    def __init__(self, entry):
        self._entry = dict(entry)
        self.updates = []

    def get(self, sid):
        return dict(self._entry) if self._entry.get("site_id") == sid else None

    def update_status(self, sid, status, *, notes=None, metadata=None, credential_ref=None):
        self._entry["status"] = status
        if metadata:
            import json
            md = {}
            try:
                md = json.loads(self._entry.get("metadata_json") or "{}")
            except Exception:
                md = {}
            md.update(metadata)
            self._entry["metadata_json"] = json.dumps(md)
        self.updates.append((status, dict(metadata or {})))
        return dict(self._entry)


def _wire_vault(monkeypatch, entry):
    from aria_service.intel import news_monitor as nm
    fv = _FakeVault(entry)
    monkeypatch.setattr("aria_service.intel.agent_signup_vault.get_vault", lambda: fv)
    monkeypatch.setattr(nm, "_VAULT_URL_TO_ID", {entry["site_url"]: entry["site_id"]})
    monkeypatch.setattr(nm, "wire_failure", lambda **kw: None)
    return nm, fv


def test_rf2217_suspends_after_threshold(monkeypatch):
    entry = {"site_id": "u_dead", "site_url": "https://dead.example/rss",
             "status": "verified", "metadata_json": "{}"}
    nm, fv = _wire_vault(monkeypatch, entry)
    thr = nm._VAULT_FAIL_SUSPEND_THRESHOLD
    for _ in range(thr):
        nm._bump_vault_failstreak(entry["site_url"])
    # last bump crossed the threshold → status flipped to "failed"
    assert fv._entry["status"] == "failed"
    assert any(s == "failed" for s, _ in fv.updates)


def test_rf2217_does_not_suspend_before_threshold(monkeypatch):
    entry = {"site_id": "u_flap", "site_url": "https://flap.example/rss",
             "status": "verified", "metadata_json": "{}"}
    nm, fv = _wire_vault(monkeypatch, entry)
    for _ in range(nm._VAULT_FAIL_SUSPEND_THRESHOLD - 1):
        nm._bump_vault_failstreak(entry["site_url"])
    assert fv._entry["status"] == "verified"      # still live


def test_rf2217_success_clears_streak_and_promotes_pending(monkeypatch):
    entry = {"site_id": "u_new", "site_url": "https://new.example/rss",
             "status": "pending", "metadata_json": '{"fail_streak": 3}'}
    nm, fv = _wire_vault(monkeypatch, entry)
    nm._reset_vault_failstreak(entry["site_url"])
    assert fv._entry["status"] == "verified"       # confirmed live → promoted
    import json
    assert json.loads(fv._entry["metadata_json"])["fail_streak"] == 0


def test_rf2217_healthy_source_no_write(monkeypatch):
    entry = {"site_id": "u_ok", "site_url": "https://ok.example/rss",
             "status": "verified", "metadata_json": '{"fail_streak": 0}'}
    nm, fv = _wire_vault(monkeypatch, entry)
    nm._reset_vault_failstreak(entry["site_url"])
    assert fv.updates == []                         # nothing changed → no write


# ── R-F2218 — redirect-SSRF ──────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, status, headers=None, text=""):
        self.status_code = status
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)


class _FakeClient:
    def __init__(self, script):
        self._script = script
        self.calls = []

    async def get(self, url, follow_redirects=True):
        self.calls.append(url)
        return self._script(url)


async def test_rf2218_blocks_redirect_to_internal(monkeypatch):
    from aria_service.intel import news_monitor as nm

    def script(url):
        if url == "https://public.example/feed":
            return _FakeResp(302, {"location": "http://aria-intel.internal:8000/x"})
        return _FakeResp(200, {}, "<rss>SHOULD NOT REACH</rss>")

    fc = _FakeClient(script)
    monkeypatch.setattr(nm, "_get_client", lambda: fc)
    out = await nm._fetch_feed("https://public.example/feed", "evil")
    assert out is None                                     # refused
    assert "http://aria-intel.internal:8000/x" not in fc.calls   # never fetched


async def test_rf2218_follows_safe_redirect(monkeypatch):
    from aria_service.intel import news_monitor as nm

    def script(url):
        if url == "https://a.example/feed":
            return _FakeResp(301, {"location": "https://b.example/feed"})
        return _FakeResp(200, {}, "<rss>ok</rss>")

    fc = _FakeClient(script)
    monkeypatch.setattr(nm, "_get_client", lambda: fc)
    out = await nm._fetch_feed("https://a.example/feed", "ok")
    assert out == "<rss>ok</rss>"                          # safe hop followed

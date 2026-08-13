"""R-F2318 — Brave as the PRIMARY search backend for user-facing DD/research/search,
excluded from the continuous researcher, branded as ARIA, and captured for distillation.
"""
import asyncio
import json

import pytest

from aria_service.intel import web_search as ws
from aria_service.intel import brave_distill


def _reset_ctx():
    ws.enable_brave_for_scope(False)


# ── Branding mask (operator directive: no "brave" on user DDs) ──────────────
def test_mask_brave_source():
    r1 = ws.SearchResult(title="t1", url="u1", source="brave")
    r2 = ws.SearchResult(title="t2", url="u2", source="brave:web")
    r3 = ws.SearchResult(title="t3", url="u3", source="google_news")
    ws.mask_brave_source([r1, r2, r3])
    assert r1.source == "aria_search"
    assert r2.source == "aria_search"
    assert r3.source == "google_news"   # non-brave untouched


# ── Gating: key + kill-switch + context flag ────────────────────────────────
def test_brave_is_enabled_gating(monkeypatch):
    monkeypatch.setattr(ws, "_BRAVE_GLOBALLY_OFF", False)
    # R-F3946 — every gate below is UNCHANGED; the scope simply declares its
    # purpose now, because RULE ONE confines Brave to DD. The one added line is
    # the new gate: an undeclared scope is refused even with a key present.
    monkeypatch.setattr(ws, "BRAVE_API_KEY", "")     # no key → never on
    ws.enable_brave_for_scope(True, purpose="dd")
    assert ws.brave_is_enabled() is False
    monkeypatch.setattr(ws, "BRAVE_API_KEY", "testkey")
    ws.enable_brave_for_scope(True, purpose="dd")
    assert ws.brave_is_enabled() is True
    ws.enable_brave_for_scope(True)                  # no purpose → RULE ONE refuses
    assert ws.brave_is_enabled() is False
    ws.enable_brave_for_scope(False)                 # ctx off → off
    assert ws.brave_is_enabled() is False
    ws.enable_brave_for_scope(True, purpose="dd")
    monkeypatch.setattr(ws, "_BRAVE_GLOBALLY_OFF", True)   # kill-switch → off
    assert ws.brave_is_enabled() is False
    _reset_ctx()


# ── Continuous researcher EXCLUSION: a sibling task never inherits the flag ──
@pytest.mark.asyncio
async def test_autonomous_task_does_not_inherit_brave(monkeypatch):
    monkeypatch.setattr(ws, "BRAVE_API_KEY", "testkey")
    monkeypatch.setattr(ws, "_BRAVE_GLOBALLY_OFF", False)
    _reset_ctx()
    out = {}

    async def user_facing():          # a DD request opts in
        ws.enable_brave_for_scope(True, purpose="dd")   # R-F3946
        out["user"] = ws.brave_is_enabled()

    async def autonomous():           # the continuous researcher never opts in
        out["auto"] = ws.brave_is_enabled()

    await asyncio.create_task(user_facing())
    await asyncio.create_task(autonomous())
    assert out["user"] is True
    assert out["auto"] is False       # exclusion holds even after a user enabled it
    _reset_ctx()


# ── Brave client: no key → no-op; parses a real-shaped response ─────────────
@pytest.mark.asyncio
async def test_search_brave_no_key(monkeypatch):
    monkeypatch.setattr(ws, "BRAVE_API_KEY", "")
    assert await ws._search_brave("x") == []


@pytest.mark.asyncio
async def test_search_brave_parses(monkeypatch):
    monkeypatch.setattr(ws, "BRAVE_API_KEY", "testkey")
    from aria_service.intel.circuit_breaker import get_breaker
    get_breaker("search:brave").record_success()   # ensure closed

    fake = {"web": {"results": [
        {"title": "Reuters: X sanctioned", "url": "https://reuters.com/x", "description": "<b>X</b> was sanctioned"},
        {"title": "no-url", "url": "", "description": "skip me"},
    ]}}

    class FakeResp:
        status_code = 200
        def json(self): return fake

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None, headers=None): return FakeResp()

    monkeypatch.setattr(ws.httpx, "AsyncClient", FakeClient)
    res = await ws._search_brave("X sanctions", max_results=10, language="en")
    assert len(res) == 1                       # empty-url row skipped
    assert res[0].source == "brave"            # internal label (masked later)
    assert res[0].url == "https://reuters.com/x"
    assert "sanctioned" in res[0].snippet.lower()
    assert "<b>" not in res[0].snippet         # html stripped


# ── Distillation capture ────────────────────────────────────────────────────
def test_brave_distill_captures_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(brave_distill, "_CORPUS_DIR", tmp_path)
    monkeypatch.setattr(brave_distill, "_ENABLED", True)

    class R:
        def __init__(self, u): self.url = u

    names = ["memory", "brave", "searxng"]
    raw = [[], [R("https://a.com"), R("https://b.com")], [R("https://c.com")]]
    brave_distill.capture("test query", "en", names, raw)
    shards = list(tmp_path.glob("*.jsonl"))
    assert len(shards) == 1
    rec = json.loads(shards[0].read_text(encoding="utf-8").strip())
    assert rec["query"] == "test query"
    assert rec["backends"]["brave"] == ["https://a.com", "https://b.com"]
    assert rec["backends"]["searxng"] == ["https://c.com"]   # student captured too


def test_brave_distill_skips_without_brave(tmp_path, monkeypatch):
    monkeypatch.setattr(brave_distill, "_CORPUS_DIR", tmp_path)
    monkeypatch.setattr(brave_distill, "_ENABLED", True)

    class R:
        def __init__(self, u): self.url = u

    brave_distill.capture("q", "en", ["memory", "searxng"], [[], [R("https://c.com")]])
    assert list(tmp_path.glob("*.jsonl")) == []   # no brave → nothing captured

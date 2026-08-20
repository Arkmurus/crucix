"""R-F2045 — per-user data sources (add/list/delete), scoped + validated."""
from __future__ import annotations
import asyncio
from unittest.mock import MagicMock
from aria_service.routes import aria as R
from aria_service.intel import agent_signup_vault as asv

class FakeReq:
    def __init__(self, body): self._b = body
    async def json(self): return self._b

def _vault(existing=None):
    v = MagicMock()
    v.list.return_value = existing or []
    v.record.return_value = {"site_id": "u_x", "site_name": "X", "agent_id": "user:U1"}
    v.get.return_value = {"site_id": "u_x", "agent_id": "user:U1"}  # post-write verify passes
    v.delete.return_value = True
    return v

def test_add_requires_auth(monkeypatch):
    monkeypatch.setattr(asv, "get_vault", lambda: _vault())
    r = asyncio.run(R.user_sources_add_ep(FakeReq({"name": "X", "url": "https://x.com/feed"}), user_id=""))
    assert r["success"] is False and "auth" in r["error"]

def test_add_validates_and_forces_owner(monkeypatch):
    v = _vault(); monkeypatch.setattr(asv, "get_vault", lambda: v)
    r = asyncio.run(R.user_sources_add_ep(FakeReq({"name": "Reuters", "url": "https://reuters.com/feed", "site_type": "rss"}), user_id="U1"))
    assert r["success"] is True
    assert v.record.call_args.kwargs["agent_id"] == "user:U1"
    assert v.record.call_args.kwargs["site_type"] == "rss"

def test_add_rejects_bad_type(monkeypatch):
    monkeypatch.setattr(asv, "get_vault", lambda: _vault())
    r = asyncio.run(R.user_sources_add_ep(FakeReq({"name": "X", "url": "https://x.com", "site_type": "api"}), user_id="U1"))
    assert r["success"] is False

def test_add_rejects_oversized_input(monkeypatch):
    v = _vault(); monkeypatch.setattr(asv, "get_vault", lambda: v)
    r = asyncio.run(R.user_sources_add_ep(FakeReq({"name": "X" * 161, "url": "https://x.com", "site_type": "rss"}), user_id="U1"))
    assert r["success"] is False and "too long" in r["error"]
    v.record.assert_not_called()

def test_add_rejects_unsafe_url(monkeypatch):
    monkeypatch.setattr(asv, "get_vault", lambda: _vault())
    r = asyncio.run(R.user_sources_add_ep(FakeReq({"name": "X", "url": "http://169.254.169.254/latest/meta-data", "site_type": "rss"}), user_id="U1"))
    assert r["success"] is False and "unsafe" in r["error"].lower()

def test_add_dedup(monkeypatch):
    v = _vault(existing=[{"site_url": "https://reuters.com/feed", "agent_id": "user:U1"}])
    monkeypatch.setattr(asv, "get_vault", lambda: v)
    r = asyncio.run(R.user_sources_add_ep(FakeReq({"name": "R", "url": "https://reuters.com/feed", "site_type": "rss"}), user_id="U1"))
    assert r["success"] is False and "already" in r["error"]

def test_list_is_scoped(monkeypatch):
    v = _vault(existing=[{"site_id": "u_1", "agent_id": "user:U1"}])
    monkeypatch.setattr(asv, "get_vault", lambda: v)
    r = asyncio.run(R.user_sources_list_ep(user_id="U1"))
    assert r["count"] == 1 and v.list.call_args.kwargs.get("agent_id") == "user:U1"

def test_delete_owner_checked(monkeypatch):
    v = _vault(); v.get.return_value = {"site_id": "u_1", "agent_id": "user:OTHER"}
    monkeypatch.setattr(asv, "get_vault", lambda: v)
    r = asyncio.run(R.user_sources_delete_ep("u_1", user_id="U1"))
    assert r["success"] is False
    v.delete.assert_not_called()

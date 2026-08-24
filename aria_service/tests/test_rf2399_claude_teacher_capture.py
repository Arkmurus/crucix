"""R-F2399 / R-F2400 — Claude → ARIA teacher-signal capture.

Disprove-first audit finding (2026-07-04): everything Claude taught ARIA over the
bridge was DARK — the Redis collab log had zero writers, the server's file mailbox
was empty, and the aria CLI consumed Claude's replies ephemerally with NO learning
sink. These capability tests drive the REAL wired path and assert the teacher signal
lands in BOTH learning sinks:

  1. claude_distill.capture writes a source=claude_teacher record (DISTILL sink).
  2. collab_bridge.drain_for_aria absorbs Claude notes (RAG/mastery/neural via
     brain_hook.absorb) AND captures them to the claude_teacher distill corpus.
  3. the /api/aria/collab/ingest inbound route writes to the collab log (the writer
     that was missing), gated by require_aria_token.
  4. the local aria_cli.bridge forward posts Claude's (and only Claude's) messages
     to the server, env-gated and best-effort.
"""
from __future__ import annotations

import asyncio
import importlib
import json

import pytest


def _run(coro):
    return asyncio.run(coro)


# ── 1. claude_distill DISTILL sink ─────────────────────────────────────────────

@pytest.fixture
def claude_distill_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_DISTILL_DIR", str(tmp_path / "claude_distill"))
    monkeypatch.setenv("ARIA_CLAUDE_DISTILL_ENABLED", "1")
    import aria_service.intel.claude_distill as cd
    cd = importlib.reload(cd)  # re-read the env-driven _CORPUS_DIR
    return cd


def test_capture_writes_claude_teacher_record(claude_distill_tmp):
    cd = claude_distill_tmp
    ok = cd.capture("offload model.predict via asyncio.to_thread — never inline",
                    kind="note", msg_id="cb_1")
    assert ok is True
    st = cd.stats()
    assert st["records"] == 1
    assert st["source"] == "claude_teacher"
    # the record is on disk, tagged, and preserves the teaching text
    shard = next((cd._CORPUS_DIR).glob("*.jsonl"))
    rec = json.loads(shard.read_text(encoding="utf-8").splitlines()[0])
    assert rec["source"] == "claude_teacher"
    assert rec["direction"] == "claude->aria"
    assert "asyncio.to_thread" in rec["text"]


def test_capture_empty_text_is_noop(claude_distill_tmp):
    cd = claude_distill_tmp
    assert cd.capture("   ") is False
    assert cd.stats()["records"] == 0


def test_capture_disabled_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_DISTILL_DIR", str(tmp_path / "cd"))
    monkeypatch.setenv("ARIA_CLAUDE_DISTILL_ENABLED", "0")
    import aria_service.intel.claude_distill as cd
    cd = importlib.reload(cd)
    assert cd.capture("anything") is False
    assert cd.stats()["records"] == 0


# ── 2. drain_for_aria absorbs AND distils ──────────────────────────────────────

class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.kv: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    async def incr(self, key, amount=1, *, critical=False):
        self.counters[key] = self.counters.get(key, 0) + amount
        return self.counters[key]

    async def lpush(self, key, value, *, critical=False):
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key, start, stop):
        if key in self.lists:
            self.lists[key] = self.lists[key][start:stop + 1]

    async def lrange(self, key, start, stop):
        items = self.lists.get(key, [])
        stop = len(items) if stop == -1 else stop + 1
        return items[start:stop]

    async def get_strict(self, key):
        # R-F4301 — mirror the REAL redis_store contract. `collab_bridge.get_cursor`
        # reads the drain cursor through get_strict precisely so an unreadable
        # store is distinguishable from an absent key; a fake that omits it makes
        # every read look like a store failure, which is the opposite of what this
        # fake is for. None here means genuinely absent, exactly as production.
        return self.kv.get(key)

    async def get(self, key):
        return self.kv.get(key)

    async def _set(self, key, value, ex=None, *, critical=False):
        self.kv[key] = value
    set = _set  # redis-interface alias (avoids the def set( builtin-shadow lint)


def test_drain_absorbs_and_distils(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_CLAUDE_DISTILL_DIR", str(tmp_path / "claude_distill"))
    monkeypatch.setenv("ARIA_CLAUDE_DISTILL_ENABLED", "1")
    import aria_service.intel.claude_distill as cd
    cd = importlib.reload(cd)

    from aria_service.intel import collab_bridge
    # collab_bridge imports claude_distill lazily inside drain — reload so its
    # `from . import claude_distill` picks up the tmp-dir module.
    import aria_service.intel.collab_bridge as cb_mod
    cb_mod = importlib.reload(cb_mod)

    fr = _FakeRedis()
    monkeypatch.setattr(cb_mod, "rs", fr)

    absorbed = []

    async def _fake_absorb(**kwargs):
        absorbed.append(kwargs)
        return {}

    async def _fake_signal(*a, **k):
        return None

    import aria_service.intel.brain_hook as bh
    monkeypatch.setattr(bh, "absorb", _fake_absorb)
    monkeypatch.setattr(bh, "_record_signal", _fake_signal)

    _run(cb_mod.send("claude", "aria", "root cause, not band-aid — fix the class"))
    res = _run(cb_mod.drain_for_aria())

    assert res["drained"] == 1
    # RAG/mastery/neural sink reached
    assert len(absorbed) == 1
    assert absorbed[0]["module"] == "collab_bridge"
    assert "root cause" in absorbed[0]["detail"]
    # DISTILL sink reached, source-tagged
    st = cd.stats()
    assert st["records"] == 1 and st["source"] == "claude_teacher"


# ── 3. /api/aria/collab/ingest inbound route ────────────────────────────────────

def test_collab_ingest_route_writes_to_bridge(monkeypatch):
    """The inbound writer that was missing. Drives the REAL FastAPI route via
    TestClient; asserts it calls collab_bridge.send (the collab log write)."""
    from fastapi.testclient import TestClient
    from aria_service.main import app
    from aria_service.intel import collab_bridge

    sent = {}

    async def _fake_send(*, frm, to, text, kind="note", reply_to=""):
        sent.update(frm=frm, to=to, text=text, kind=kind, reply_to=reply_to)
        return {"seq": 7, "id": "cb_7"}

    monkeypatch.setattr(collab_bridge, "send", _fake_send)
    # No token set in the test env → require_aria_token is a soft no-op locally.
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)

    client = TestClient(app)
    r = client.post("/api/aria/collab/ingest",
                    json={"text": "prefer env-gated default-safe rollouts", "kind": "note"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["seq"] == 7 and body["id"] == "cb_7"
    assert sent["frm"] == "claude" and sent["to"] == "aria"
    assert "env-gated" in sent["text"]


def test_collab_ingest_route_rejects_empty_text(monkeypatch):
    from fastapi.testclient import TestClient
    from aria_service.main import app
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("FLY_MACHINE_ID", raising=False)
    client = TestClient(app)
    r = client.post("/api/aria/collab/ingest", json={"text": "   "})
    assert r.status_code == 400


# ── 4. local aria_cli.bridge forward (R-F2400) ─────────────────────────────────

def test_local_forward_posts_claude_message(monkeypatch, tmp_path):
    from aria_cli import bridge

    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "secret-token")

    posted = {}

    class _FakeResp:
        status_code = 200

    def _fake_post(url, json=None, headers=None, timeout=None):
        posted.update(url=url, json=json, headers=headers)
        return _FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "post", _fake_post)

    ok = bridge._forward_to_server({"frm": "claude", "text": "verify before you claim",
                                    "kind": "note", "reply_to": ""})
    assert ok is True
    assert posted["url"].endswith("/api/aria/collab/ingest")
    assert posted["json"]["text"] == "verify before you claim"
    assert posted["headers"]["Authorization"] == "Bearer secret-token"


def test_local_forward_skips_aria_messages(monkeypatch):
    from aria_cli import bridge
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "secret-token")

    import httpx

    def _boom(*a, **k):
        raise AssertionError("must not POST an ARIA->Claude message")

    monkeypatch.setattr(httpx, "post", _boom)
    assert bridge._forward_to_server({"frm": "aria", "text": "ask_claude question"}) is False


def test_local_forward_noop_without_env(monkeypatch):
    from aria_cli import bridge
    monkeypatch.delenv("ARIA_SERVICE_URL", raising=False)
    monkeypatch.delenv("ARIA_BRAIN_URL", raising=False)
    monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
    monkeypatch.delenv("ARIA_API_TOKEN", raising=False)
    import httpx

    def _boom(*a, **k):
        raise AssertionError("must not POST when unconfigured")

    monkeypatch.setattr(httpx, "post", _boom)
    assert bridge._forward_to_server({"frm": "claude", "text": "x"}) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

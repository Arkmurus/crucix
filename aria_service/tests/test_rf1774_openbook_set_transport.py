"""R-F1774 — capability test for the brain-served openbook eval-set transport.

A compute pod can't receive the 500-Q eval set via SCP (Windows RunPod drops) and it
isn't in the deployed image or reliably on the volume. So we PUT it to the brain once
and the pod GETs it over its authenticated channel. Drives the real endpoint functions
(POST raw JSONL → store → GET returns it) with a fake string store + Request.
"""
from __future__ import annotations

import asyncio


class _FakeReq:
    def __init__(self, body: bytes):
        self._body = body

    async def body(self):
        return self._body


class _FakeRS:
    def __init__(self):
        self.kv = {}

    async def set(self, key, value, *a, **kw):
        self.kv[key] = value

    async def get(self, key):
        return self.kv.get(key)


def test_put_then_get_roundtrip(monkeypatch):
    from aria_service.routes import aria as a
    rs = _FakeRS()
    monkeypatch.setattr(a, "rs", rs)

    jsonl = b'{"question":"q1","expected_answer":"a1"}\n{"question":"q2","expected_answer":"a2"}\n'
    out = asyncio.run(a.eval_openbook_set_post_ep(_FakeReq(jsonl)))
    assert out["ok"] is True and out["lines"] == 2

    resp = asyncio.run(a.eval_openbook_set_get_ep())
    # PlainTextResponse — body holds the JSONL we stored
    body = resp.body.decode("utf-8") if isinstance(resp.body, (bytes, bytearray)) else resp.body
    assert '"q1"' in body and '"q2"' in body
    assert body.count("\n") == 1  # 2 lines joined by 1 newline (no trailing)


def test_empty_body_rejected(monkeypatch):
    from aria_service.routes import aria as a
    from fastapi import HTTPException
    rs = _FakeRS()
    monkeypatch.setattr(a, "rs", rs)
    try:
        asyncio.run(a.eval_openbook_set_post_ep(_FakeReq(b"   \n  \n")))
        assert False, "should reject empty"
    except HTTPException as e:
        assert e.status_code == 400


def test_get_404_when_absent(monkeypatch):
    from aria_service.routes import aria as a
    from fastapi import HTTPException
    rs = _FakeRS()
    monkeypatch.setattr(a, "rs", rs)
    try:
        asyncio.run(a.eval_openbook_set_get_ep())
        assert False, "should 404"
    except HTTPException as e:
        assert e.status_code == 404

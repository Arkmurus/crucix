"""Capability test for R-F2205 — local cross-encoder re-ranker.

Asserts the safety + behaviour contract: OFF by default (no-op), reorders by model score
when enabled, and is a safe no-op when the model is unavailable or predict() raises — so it
can never destabilise the brain.

Run: python -m pytest aria_service/tests/test_reranker_rf2205.py -q
"""
import asyncio
from unittest.mock import patch, MagicMock

from aria_service.intel import reranker as rr


class _FakeModel:
    # score = candidate text length → deterministic, longest-first reorder
    def predict(self, pairs):
        return [len(p[1]) for p in pairs]


def _run(coro):
    return asyncio.run(coro)


def test_disabled_is_noop(monkeypatch):
    monkeypatch.delenv("ARIA_RERANK_ENABLED", raising=False)
    cands = [{"snippet": "a"}, {"snippet": "bb"}]
    assert _run(rr.rerank_results("q", cands)) == cands


def test_enabled_reorders_by_score(monkeypatch):
    monkeypatch.setenv("ARIA_RERANK_ENABLED", "1")
    with patch.object(rr, "_get_model", return_value=_FakeModel()):
        cands = [{"snippet": "a"}, {"snippet": "bbbb"}, {"snippet": "cc"}]
        out = _run(rr.rerank_results("q", cands))
        assert [c["snippet"] for c in out] == ["bbbb", "cc", "a"]


def test_model_unavailable_is_noop(monkeypatch):
    monkeypatch.setenv("ARIA_RERANK_ENABLED", "1")
    with patch.object(rr, "_get_model", return_value=None):
        cands = [{"snippet": "a"}, {"snippet": "b"}]
        assert _run(rr.rerank_results("q", cands)) == cands


def test_predict_failure_is_safe_noop(monkeypatch):
    monkeypatch.setenv("ARIA_RERANK_ENABLED", "1")
    bad = MagicMock()
    bad.predict.side_effect = RuntimeError("boom")
    with patch.object(rr, "_get_model", return_value=bad):
        cands = [{"snippet": "a"}, {"snippet": "b"}]
        assert _run(rr.rerank_results("q", cands)) == cands


def test_handles_objects_not_just_dicts(monkeypatch):
    monkeypatch.setenv("ARIA_RERANK_ENABLED", "1")

    class _R:
        def __init__(self, s): self.snippet = s
    with patch.object(rr, "_get_model", return_value=_FakeModel()):
        cands = [_R("x"), _R("xxxxx"), _R("xx")]
        out = _run(rr.rerank_results("q", cands))
        assert [c.snippet for c in out] == ["xxxxx", "xx", "x"]


if __name__ == "__main__":
    print("run via pytest (uses monkeypatch fixture)")

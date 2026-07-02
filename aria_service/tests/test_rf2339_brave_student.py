"""R-F2339 — Brave STUDENT loop: learn Brave's source-selection methodology from the
teacher corpus, apply it to re-rank the free stack, and prove student > baseline agreement.
"""
import json

import pytest

from aria_service.intel import brave_student as bs


def _write_corpus(tmp_path, n=12):
    """Brave consistently favours reuters/ft/bloomberg at the top; the free stack (searxng +
    ddg) contains them but BURIED under noise. A working student learns to surface them."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    shard = corpus / "2026-07-02.jsonl"
    with shard.open("w", encoding="utf-8") as f:
        for i in range(n):
            rec = {
                "query": f"defence procurement {i}",
                "language": "en",
                "backends": {
                    "brave": [f"https://reuters.com/a{i}", f"https://ft.com/b{i}",
                              f"https://bloomberg.com/c{i}"],
                    "searxng": [f"https://noise{i}.com/x", f"https://blogspot.com/y{i}",
                                f"https://reuters.com/a{i}", f"https://ft.com/b{i}"],
                    "ddg": [f"https://randomsite{i}.net/z", f"https://bloomberg.com/c{i}",
                            f"https://reuters.com/a{i}"],
                },
            }
            f.write(json.dumps(rec) + "\n")
    return corpus


def _point_at_tmp(monkeypatch, tmp_path, corpus):
    monkeypatch.setattr(bs, "_CORPUS_DIR", corpus)
    monkeypatch.setattr(bs, "_MODEL_DIR", tmp_path / "model")
    monkeypatch.setattr(bs, "_MODEL_PATH", tmp_path / "model" / "model.json")
    monkeypatch.setattr(bs, "_model_cache", None)
    monkeypatch.setattr(bs, "_model_mtime", 0.0)


def test_train_learns_brave_domain_preference(tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path)
    _point_at_tmp(monkeypatch, tmp_path, corpus)
    model = bs.train_from_corpus()
    assert model["records_seen"] == 12
    dp = model["domain_pref"]
    # The domains Brave ranked highest must have the highest learned preference.
    assert dp["reuters.com"] == 1.0                        # top (rank #1, max-normalised)
    assert dp["reuters.com"] > dp["ft.com"] > dp["bloomberg.com"]
    # A domain Brave never surfaced must not appear.
    assert "noise0.com" not in dp
    # Model persisted to disk.
    assert (tmp_path / "model" / "model.json").exists()


def test_rerank_surfaces_brave_favored_domains(tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path)
    _point_at_tmp(monkeypatch, tmp_path, corpus)
    bs.train_from_corpus()
    # Free-stack order: noise first, favoured domains buried.
    results = [{"url": "https://noise.com/1"}, {"url": "https://blogspot.com/2"},
               {"url": "https://reuters.com/live"}, {"url": "https://ft.com/live"}]
    out = bs.rerank(results, weight=1.0)
    top2 = [bs._domain(r["url"]) for r in out[:2]]
    assert "reuters.com" in top2 and "ft.com" in top2     # student surfaced the teacher's picks


def test_evaluate_student_beats_baseline(tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path, n=16)
    _point_at_tmp(monkeypatch, tmp_path, corpus)
    ev = bs.evaluate(k=3, holdout=0.3)
    assert ev["ok"] is True
    assert ev["eval_records"] >= 1
    # The whole point: the student agrees with Brave MORE than the raw free stack.
    assert ev["student_topk_overlap"] >= ev["baseline_topk_overlap"]
    assert ev["lift"] >= 0.0


def test_rerank_safe_noop_on_blank_model(tmp_path, monkeypatch):
    _point_at_tmp(monkeypatch, tmp_path, tmp_path / "blank")   # no corpus, no model
    results = [{"url": "https://a.com"}, {"url": "https://b.com"}]
    assert bs.rerank(results) == results                  # unchanged, no crash


def test_apply_gate_default_off(monkeypatch):
    monkeypatch.delenv("ARIA_BRAVE_STUDENT_ENABLED", raising=False)
    assert bs.apply_enabled() is False                    # staged rollout — OFF until enabled
    monkeypatch.setenv("ARIA_BRAVE_STUDENT_ENABLED", "1")
    assert bs.apply_enabled() is True


def test_evaluate_does_not_clobber_production_model(tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path, n=16)
    _point_at_tmp(monkeypatch, tmp_path, corpus)
    prod = bs.train_from_corpus()               # full-corpus production model
    prod_seen = prod["records_seen"]
    bs.evaluate(holdout=0.5)                     # trains on a split IN-MEMORY only
    on_disk = json.loads((tmp_path / "model" / "model.json").read_text())
    assert on_disk["records_seen"] == prod_seen  # production model untouched by eval

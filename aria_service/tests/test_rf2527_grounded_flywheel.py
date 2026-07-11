"""R-F2527 — grounded-synthesis SHADOW flywheel: capture + harvester.

Capability tests that drive the REAL paths (§3c):
  (a) grounded_shadow_distill.record_shadow_pair writes a well-formed row when the
      flag is ON, and writes NOTHING when the flag is OFF (default-safe).
  (b) harvest_grounded_flywheel.harvest selects a high-margin fully-grounded pair,
      DROPS a low-margin pair, DROPS a degenerate (sovereign-own-text, small-margin)
      pair, and DROPS a contaminated pair (message overlaps the frozen eval).

All tmp files — real data is never touched.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aria_service.intel import grounded_shadow_distill as gsd


# ── load the harvester script (scripts/train is not a package) ────────────────
_HARVEST_PATH = (Path(__file__).resolve().parents[2]
                 / "scripts" / "train" / "harvest_grounded_flywheel.py")
_spec = importlib.util.spec_from_file_location("harvest_grounded_flywheel", _HARVEST_PATH)
harvester = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harvester)


# ── (a) capture ───────────────────────────────────────────────────────────────
def test_capture_writes_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_SHADOW_DISTILL_ENABLED", "1")
    monkeypatch.setattr(gsd, "_CORPUS_DIR", tmp_path)
    gsd.record_shadow_pair(
        message="Who are the directors of Acme Ltd?",
        context="↳ source: companies_house:acme | 2026-07-10",
        deepseek_text="Acme is run by [from companies_house:acme] two directors.",
        sovereign_text="Per [Source: companies_house:acme], the directors are X and Y.",
        deepseek_score=0.55,
        sovereign_score=0.92,
        deepseek_breakdown={"citation_precision": 0.5, "fabricated_citations": 1,
                            "keyword_recall": 0.2},
        sovereign_breakdown={"citation_precision": 1.0, "fabricated_citations": 0,
                             "keyword_recall": 0.8},
    )
    shards = list(tmp_path.glob("*.jsonl"))
    assert len(shards) == 1, "exactly one daily shard should be written"
    rows = [json.loads(l) for l in shards[0].read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 1
    r = rows[0]
    # well-formed row with the load-bearing fields
    for k in ("ts", "message", "context", "deepseek_text", "sovereign_text",
              "deepseek_score", "sovereign_score", "margin",
              "deepseek_citation_precision", "sovereign_citation_precision"):
        assert k in r, f"missing field {k}"
    assert r["message"] == "Who are the directors of Acme Ltd?"
    assert r["sovereign_score"] == 0.92
    assert r["margin"] == pytest.approx(0.37, abs=1e-6)  # sov - ds
    assert r["sovereign_citation_precision"] == 1.0
    assert r["deepseek_fabricated_citations"] == 1


def test_capture_writes_nothing_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_SHADOW_DISTILL_ENABLED", "0")
    monkeypatch.setattr(gsd, "_CORPUS_DIR", tmp_path)
    gsd.record_shadow_pair(
        message="Who are the directors of Acme Ltd?",
        context="↳ source: companies_house:acme",
        deepseek_text="x", sovereign_text="y",
        deepseek_score=0.1, sovereign_score=0.9,
        deepseek_breakdown={}, sovereign_breakdown={},
    )
    assert list(tmp_path.glob("*.jsonl")) == [], "flag OFF must write nothing"


def test_capture_skips_when_sovereign_missing(tmp_path, monkeypatch):
    """A shadow turn where the sovereign errored/timed out has no preference signal."""
    monkeypatch.setenv("ARIA_SHADOW_DISTILL_ENABLED", "1")
    monkeypatch.setattr(gsd, "_CORPUS_DIR", tmp_path)
    gsd.record_shadow_pair(
        message="q", context="↳ source: s",
        deepseek_text="a", sovereign_text=None,
        deepseek_score=0.5, sovereign_score=None,
        deepseek_breakdown={}, sovereign_breakdown=None,
    )
    assert list(tmp_path.glob("*.jsonl")) == []


# ── (b) harvester selection ───────────────────────────────────────────────────
def _rec(message, ds, sov, ds_text="deepseek answer", sov_text="sovereign answer",
         sov_prec=1.0, sov_fab=0, ds_prec=1.0, ds_fab=0):
    return {
        "message": message,
        "deepseek_text": ds_text, "sovereign_text": sov_text,
        "deepseek_score": ds, "sovereign_score": sov,
        "sovereign_citation_precision": sov_prec, "sovereign_fabricated_citations": sov_fab,
        "deepseek_citation_precision": ds_prec, "deepseek_fabricated_citations": ds_fab,
    }


def _defaults():
    return dict(margin=0.25, min_win=0.6, degen_margin=0.4, jaccard=0.75)


def test_harvester_selects_high_margin_grounded_pair():
    # DeepSeek is the winner (fully grounded, high score), sovereign rejected — no
    # degeneracy concern; big margin. Should be selected.
    recs = [_rec("Screen Beta Corp for sanctions exposure", ds=0.95, sov=0.40,
                 ds_text="grounded ds", sov_text="weaker sov")]
    pairs, drops, total = harvester.harvest(recs, exact=set(), token_sets=[], **_defaults())
    assert total == 1
    assert len(pairs) == 1
    p = pairs[0]
    assert p["prompt"] == "Screen Beta Corp for sanctions exposure"
    assert p["chosen"] == "grounded ds"
    assert p["rejected"] == "weaker sov"
    assert p["meta"]["winner"] == "deepseek"
    assert p["meta"]["source"] == "flywheel"
    assert p["meta"]["degen_guard"] is False


def test_harvester_drops_low_margin_pair():
    recs = [_rec("Assess Gamma Ltd credit risk", ds=0.62, sov=0.70)]  # margin 0.08 < 0.25
    pairs, drops, total = harvester.harvest(recs, exact=set(), token_sets=[], **_defaults())
    assert pairs == []
    assert drops["margin"] == 1


def test_harvester_drops_degenerate_sovereign_own_text():
    # Sovereign wins but margin 0.30 < degen_margin 0.40 => degenerate (mode-collapse
    # guard): training on the model's own text with a small margin is dropped.
    recs = [_rec("Investigate Delta Holdings ownership", ds=0.60, sov=0.90,
                 sov_text="sovereign self text")]
    pairs, drops, total = harvester.harvest(recs, exact=set(), token_sets=[], **_defaults())
    assert pairs == []
    assert drops["degeneracy"] == 1
    # ...but a LARGE-margin sovereign win IS kept, with degen_guard provenance.
    recs2 = [_rec("Investigate Delta Holdings ownership", ds=0.30, sov=0.95,
                  sov_text="sovereign self text")]
    pairs2, _, _ = harvester.harvest(recs2, exact=set(), token_sets=[], **_defaults())
    assert len(pairs2) == 1
    assert pairs2[0]["meta"]["winner"] == "sovereign"
    assert pairs2[0]["meta"]["degen_guard"] is True


def test_harvester_drops_contaminated_pair():
    evalq = "What is the current population of the Angolan armed forces (FAA)?"
    exact = {harvester._norm(evalq)}
    token_sets = [harvester._tokens(harvester._norm(evalq))]
    # exact-normalized match (trailing punctuation / case differs) => contaminated
    recs = [_rec("what is the current population of the angolan armed forces (faa)",
                 ds=0.95, sov=0.40)]
    pairs, drops, total = harvester.harvest(recs, exact, token_sets, **_defaults())
    assert pairs == []
    assert drops["contamination"] == 1


def test_harvester_drops_fuzzy_contaminated_pair():
    evalq = "What is the current population of the Angolan armed forces (FAA)?"
    exact = {harvester._norm(evalq)}
    token_sets = [harvester._tokens(harvester._norm(evalq))]
    # near-duplicate wording (Jaccard >= 0.75) but not an exact normalized match
    recs = [_rec("What is the current population of the Angolan armed forces FAA now",
                 ds=0.95, sov=0.40)]
    pairs, drops, total = harvester.harvest(recs, exact, token_sets, **_defaults())
    assert pairs == []
    assert drops["contamination"] == 1


def test_harvester_drops_ungrounded_winner():
    # High margin but the winner fabricated a citation => not fully grounded => drop.
    recs = [_rec("Profile Epsilon SA", ds=0.95, sov=0.40, ds_prec=0.5, ds_fab=1)]
    pairs, drops, total = harvester.harvest(recs, exact=set(), token_sets=[], **_defaults())
    assert pairs == []
    assert drops["not_grounded"] == 1


def test_harvester_mixed_corpus_report():
    recs = [
        _rec("Screen Beta Corp sanctions", ds=0.95, sov=0.40),           # keep (ds win)
        _rec("Assess Gamma credit", ds=0.62, sov=0.70),                  # drop margin
        _rec("Investigate Delta ownership", ds=0.60, sov=0.90),          # drop degeneracy
    ]
    pairs, drops, total = harvester.harvest(recs, exact=set(), token_sets=[], **_defaults())
    assert total == 3
    assert len(pairs) == 1
    assert drops["margin"] == 1
    assert drops["degeneracy"] == 1

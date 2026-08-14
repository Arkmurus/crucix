"""R-F4021 admission and generation-only guards for novel contradictions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.train.build_tooluse_corpus import (
    _independent_sources,
    _matches,
    _norm_subject,
    validate_trace,
)
from scripts.train.build_tooluse_dpo import build_pairs
from scripts.train.eval_tooluse import score_one


ROOT = Path(__file__).resolve().parents[2]


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _tool_payload(row: dict, name: str) -> dict:
    message = next(
        message for message in row["messages"]
        if message.get("role") == "tool" and message.get("name") == name
    )
    return json.loads(message["content"])


def test_real_capture_is_valid_novel_and_semantically_consistent() -> None:
    queue = ROOT / "data/training/aria_tooluse_contradiction_novel_v2.jsonl"
    rows = _rows(queue)
    protected_paths = [
        ROOT / "data/training/split_v1/eval.jsonl",
        ROOT / "data/training/split_v2/eval.jsonl",
        ROOT / "data/eval_frozen/aria_eval_500q.jsonl",
        ROOT / "data/training/aria_tooluse_curve_v5_probe.jsonl",
        ROOT / "data/training/tooluse_protected_axis_recovery_queue.jsonl",
        ROOT / "data/training/tooluse_hard_contradiction_queue.jsonl",
    ]
    protected = {
        _norm_subject(str(row.get("subject") or ""))
        for path in protected_paths for row in _rows(path)
    } - {""}
    subjects = [_norm_subject(str(row["subject"])) for row in rows]

    assert len(rows) == len(set(subjects)) == 16
    assert not set(subjects) & protected
    assert {row["label"] for row in rows} == {"tooluse_contradiction"}
    for row in rows:
        assert validate_trace(row) == []
        screen = _tool_payload(row, "screen")
        search = _tool_payload(row, "web_search")
        assert _matches(screen) == []
        assert screen["sanctions"]["matched"] is False
        assert search.get("results")
        assert _independent_sources(search)


def test_launcher_pins_capture_and_cannot_start_dpo() -> None:
    queue = ROOT / "data/training/aria_tooluse_contradiction_novel_v2.jsonl"
    launcher = (
        ROOT / "scripts/train/run_tooluse_novel_contradiction_generation.sh"
    ).read_text(encoding="utf-8")
    assert hashlib.sha256(queue.read_bytes()).hexdigest() in launcher
    assert "aria_tooluse_citation_phoenix_v3_failed_candidate.tgz" in launcher
    assert "exec bash scripts/train/run_tooluse_generation.sh" in launcher
    assert "run_tooluse_dpo.sh" not in launcher


def test_real_report_yields_only_genuine_disjoint_preference_pairs() -> None:
    corpus = _rows(
        ROOT / "data/training/aria_tooluse_contradiction_novel_v2.jsonl"
    )
    report = json.loads(
        (ROOT / "data/eval_reports/aria_tooluse_novel_contradiction_generations.json")
        .read_text(encoding="utf-8")
    )
    held = {
        _norm_subject(str(row.get("subject") or ""))
        for row in _rows(ROOT / "data/training/split_v1/eval.jsonl")
    } - {""}
    pairs = build_pairs(
        report, corpus, eval_entities=held, validate_chosen=True,
    )
    written_pairs = _rows(
        ROOT / "data/training/aria_tooluse_novel_contradiction_dpo.jsonl"
    )

    assert written_pairs == pairs
    assert {pair["subject"] for pair in pairs} == {
        "Goldman Sachs Group",
        "Intesa Sanpaolo",
        "Banco Bilbao Vizcaya Argentaria",
        "Nordea Bank Abp",
    }
    assert len(pairs) == 4
    by_subject = {
        _norm_subject(str(row["subject"])): row for row in corpus
    }
    for pair in pairs:
        assert _norm_subject(pair["subject"]) not in held
        trace = by_subject[_norm_subject(pair["subject"])]
        assert score_one(trace, pair["rejected"])["honest"] is False
        assert score_one(trace, pair["chosen"])["honest"] is True

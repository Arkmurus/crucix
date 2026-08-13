"""R-F3976 / C-65 — validator evolution must compound into DPO selection."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train.build_tooluse_dpo import _norm, build_pairs


ROOT = Path(__file__).resolve().parents[2]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_phoenix_builder_selects_all_current_failures_not_stale_flags() -> None:
    corpus = _jsonl(
        ROOT / "data/training/tooluse_citation_phoenix_v2_generation_queue.jsonl"
    )
    report = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_citation_phoenix_v2_generations.json"
    ).read_text(encoding="utf-8"))
    held = {_norm(str(row.get("subject") or "")) for row in _jsonl(
        ROOT / "data/training/split_v2/eval.jsonl"
    )} - {""}

    pairs = build_pairs(report, corpus, eval_entities=held, validate_chosen=True)

    assert {pair["subject"] for pair in pairs} == {
        "Hanwha Aerospace", "L3Harris Technologies", "SOCAR", "Uzbekneftegaz",
    }
    assert all("denies entity-relevant adverse coverage" in pair["why"]
               for pair in pairs)

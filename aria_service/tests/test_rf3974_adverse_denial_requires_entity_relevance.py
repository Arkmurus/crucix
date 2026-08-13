"""R-F3974 / C-63 — reject categorical denials only on relevant evidence."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train.eval_tooluse import score_one


ROOT = Path(__file__).resolve().parents[2]
TRUE_DENIAL_FAILURES = {
    "Hanwha Aerospace", "L3Harris Technologies", "SOCAR", "Uzbekneftegaz",
}


def _rescored() -> dict[str, dict]:
    queue = [json.loads(line) for line in (
        ROOT / "data/training/tooluse_citation_phoenix_v2_generation_queue.jsonl"
    ).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_citation_phoenix_v2_generations.json"
    ).read_text(encoding="utf-8"))
    return {
        trace["subject"]: score_one(trace, row["answer"])
        for trace, row in zip(queue, report["rows"], strict=True)
        if trace.get("label") == "tooluse_adverse"
    }


def test_phoenix_denials_fail_only_when_a_staged_title_names_the_entity() -> None:
    rows = _rescored()
    failed = {subject for subject, row in rows.items() if not row["honest"]}
    assert failed == TRUE_DENIAL_FAILURES
    for subject in failed:
        assert "denies entity-relevant adverse coverage" in " ".join(rows[subject]["errors"])


def test_irrelevant_result_sets_remain_honest() -> None:
    rows = _rescored()
    for subject in ("Naval Group", "Saudi National Bank", "Bank of China"):
        assert rows[subject]["honest"], (subject, rows[subject]["errors"])

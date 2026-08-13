"""C-49/R-F3959: adverse stage scoring is clause- and subject-aware."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train.eval_tooluse import score_one


ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "data/training/tooluse_citation_phoenix_v2_generation_queue.jsonl"
REPORT = ROOT / "data/eval_reports/aria_tooluse_citation_phoenix_v2_generations.json"
FALSE_POSITIVES = {
    "Itau Unibanco", "Whitbread plc", "Saab AB",
    "Turkish Aerospace Industries", "Petronas",
    "China National Offshore Oil Corporation",
}


def _replays() -> dict[str, dict]:
    queue = [json.loads(line) for line in QUEUE.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    return {
        trace["subject"]: score_one(trace, row["answer"])
        for trace, row in zip(queue, report["rows"], strict=True)
        if not row["honest"]
    }


def test_rf3959_six_measured_negations_are_not_stage_escalations() -> None:
    replayed = _replays()
    assert set(replayed) == FALSE_POSITIVES | {"Uzbekneftegaz"}
    for subject in FALSE_POSITIVES:
        assert replayed[subject]["honest"], (subject, replayed[subject]["errors"])


def test_rf3973_uzbekneftegaz_is_not_a_cleared_matter() -> None:
    """The old CLEARED reason was an invalid fixture.

    "Executives dismissed" meant removed from their jobs, not exonerated. The
    Whether the categorical denial is honest requires entity relevance analysis;
    it must not be manufactured by misreading an employment action as clearance.
    """
    row = _replays()["Uzbekneftegaz"]
    errors = " ".join(row["errors"])
    assert "CLEARED" not in errors

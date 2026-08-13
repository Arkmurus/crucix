"""R-F3973 / C-62 — employment dismissal is not procedural clearance."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train import build_tooluse_corpus as corpus
from scripts.train.eval_tooluse import score_one


ROOT = Path(__file__).resolve().parents[2]


def test_executives_dismissed_for_scams_is_not_a_cleared_matter() -> None:
    text = (
        "Gissarneftgaz executives dismissed as state firm uncovers extensive "
        "structural neglect and financial scams"
    )
    assert corpus._grade_stage(text) != "resolved_cleared"


def test_dismissed_legal_proceeding_remains_a_clearance() -> None:
    assert corpus._grade_stage("The court dismissed all charges against Acme") == "resolved_cleared"
    assert corpus._grade_stage("The regulator dropped the case against Acme") == "resolved_cleared"


def test_phoenix_uzbekneftegaz_no_longer_gets_a_phantom_clearance_reason() -> None:
    queue = [json.loads(line) for line in (
        ROOT / "data/training/tooluse_citation_phoenix_v2_generation_queue.jsonl"
    ).read_text(encoding="utf-8").splitlines() if line.strip()]
    report = json.loads((
        ROOT / "data/eval_reports/aria_tooluse_citation_phoenix_v2_generations.json"
    ).read_text(encoding="utf-8"))
    index = next(i for i, trace in enumerate(queue)
                 if trace.get("subject") == "Uzbekneftegaz"
                 and trace.get("label") == "tooluse_adverse")

    result = score_one(queue[index], report["rows"][index]["answer"])

    errors = " ".join(result["errors"])
    assert "CLEARED" not in errors

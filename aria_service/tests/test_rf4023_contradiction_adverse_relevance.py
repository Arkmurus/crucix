"""R-F4023: contradiction references must cite actually adverse results."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train.build_tooluse_corpus import (
    _norm_subject,
    build_contradiction_trace,
    validate_trace,
)
from scripts.train.build_tooluse_dpo import build_pairs


ROOT = Path(__file__).resolve().parents[2]


def _capture(subject: str) -> tuple[dict, dict]:
    path = ROOT / "data/training/aria_tooluse_contradiction_novel_v2.jsonl"
    row = next(
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("subject") == subject
    )
    payloads = {
        message["name"]: json.loads(message["content"])
        for message in row["messages"] if message.get("role") == "tool"
    }
    return payloads["screen"], payloads["web_search"]


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.parametrize(
    ("subject", "required", "forbidden"),
    [
        (
            "Banco Bilbao Vizcaya Argentaria",
            "Spanish prosecutor seeks more than $200 million fine on BBVA",
            "Banco Bilbao Vizcaya Argentaria SA Stock",
        ),
        (
            "Nordea Bank Abp",
            "Denmark's FSA asks police to probe Nordea Finans Danmark",
            "Nordea Bank Abp: Repurchase of own shares",
        ),
    ],
)
def test_real_capture_chosen_side_uses_adverse_results_only(
    subject: str, required: str, forbidden: str,
) -> None:
    screen, search = _capture(subject)
    trace = build_contradiction_trace(subject, screen, search)
    assert trace is not None
    answer = trace["messages"][-1]["content"]
    assert required in answer
    assert forbidden not in answer


def test_v3_preserves_every_generated_prompt_and_replaces_only_reference() -> None:
    v2 = _rows(ROOT / "data/training/aria_tooluse_contradiction_novel_v2.jsonl")
    v3 = _rows(ROOT / "data/training/aria_tooluse_contradiction_novel_v3.jsonl")
    assert len(v2) == len(v3) == 16
    for old, corrected in zip(v2, v3, strict=True):
        assert corrected["messages"][:-1] == old["messages"][:-1]
        screen, search = _capture(str(corrected["subject"]))
        rebuilt = build_contradiction_trace(corrected["subject"], screen, search)
        assert rebuilt is not None
        assert corrected["messages"][-1] == rebuilt["messages"][-1]
        assert validate_trace(corrected) == []


def test_corrected_pair_file_equals_real_builder_output() -> None:
    corpus = _rows(
        ROOT / "data/training/aria_tooluse_contradiction_novel_v3.jsonl"
    )
    report = json.loads(
        (ROOT / "data/eval_reports/aria_tooluse_novel_contradiction_generations.json")
        .read_text(encoding="utf-8")
    )
    held = {
        _norm_subject(str(row.get("subject") or ""))
        for row in _rows(ROOT / "data/training/split_v1/eval.jsonl")
    } - {""}
    expected = build_pairs(
        report, corpus, eval_entities=held, validate_chosen=True,
    )
    written = _rows(
        ROOT / "data/training/aria_tooluse_novel_contradiction_dpo_v2.jsonl"
    )
    assert written == expected
    assert len(written) == 4

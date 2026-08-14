"""R-F4011 capability tests for hard contradiction collection."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from scripts.train.build_contradiction_challenge_queue import (
    select_hard_contradictions,
)
from scripts.train.build_tooluse_corpus import _norm_subject


ROOT = Path(__file__).resolve().parents[2]


def _row(subject: str, *, relevant: int = 3) -> dict:
    results = [
        {"title": f"{subject} probe {index}", "url": f"https://source{index}.test/x",
         "snippet": f"{subject} faces an investigation"}
        for index in range(relevant)
    ]
    return {
        "subject": subject,
        "label": "tooluse_contradiction",
        "messages": [
            {"role": "system", "content": "evidence only"},
            {"role": "user", "content": f"Is {subject} clean?"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "screen001", "type": "function",
                "function": {"name": "screen", "arguments": "{}"},
            }]},
            {"role": "tool", "name": "screen", "tool_call_id": "screen001",
             "content": json.dumps({"sanctions": {"matched": False}})},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "search001", "type": "function",
                "function": {"name": "web_search", "arguments": "{}"},
            }]},
            {"role": "tool", "name": "web_search", "tool_call_id": "search001",
             "content": json.dumps({"results": results})},
            {"role": "assistant", "content": (
                f"The no-match screen does not mean {subject} is clean. The search "
                "returned unresolved adverse reporting, so this is NOT a clean result."
            )},
        ],
    }


def test_selector_deduplicates_and_advances_past_forbidden_and_prior_rows() -> None:
    rows = [_row("Held Plc"), _row("Prior Plc"), _row("Weak Plc", relevant=2),
            _row("Hard Plc"), _row("Hard Plc")]
    selected = select_hard_contradictions(
        rows,
        forbidden_subjects={_norm_subject("Held Plc")},
        excluded_evidence={("tooluse_contradiction", _norm_subject("Prior Plc"))},
        limit=1,
    )
    assert [row["subject"] for row in selected] == ["Hard Plc"]


def test_selector_fails_closed_when_hard_disjoint_supply_is_insufficient() -> None:
    with pytest.raises(ValueError, match="only 1.*required 2"):
        select_hard_contradictions(
            [_row("Hard Plc")], forbidden_subjects={_norm_subject("Held Plc")},
            excluded_evidence=set(), limit=2,
        )


def test_real_queue_and_launcher_pin_only_hard_disjoint_contradictions() -> None:
    queue = ROOT / "data/training/tooluse_hard_contradiction_queue.jsonl"
    rows = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert len(rows) == 9
    assert len({_norm_subject(str(row["subject"])) for row in rows}) == 9
    assert {row["label"] for row in rows} == {"tooluse_contradiction"}
    launcher = (ROOT / "scripts/train/run_tooluse_hard_contradiction_generation.sh"
                ).read_text(encoding="utf-8")
    assert hashlib.sha256(queue.read_bytes()).hexdigest() in launcher
    assert "aria_tooluse_citation_phoenix_v3_failed_candidate.tgz" in launcher
    assert "exec bash scripts/train/run_tooluse_generation.sh" in launcher
    assert "run_tooluse_dpo.sh" not in launcher

"""R-F4088 guards for measured resolution-evidence branch coverage."""
import json
from pathlib import Path
import sys

import pytest

from scripts.train import capture_resolution
from scripts.train.build_tooluse_corpus import build_resolution_trace
from scripts.train.preflight_cycle import (
    _golden_subjects,
    check_contamination,
    check_schema,
    check_split,
)


ROOT = Path(__file__).resolve().parents[2]


def _rows(path):
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _result(title: str, number: str, status: str = "active") -> dict:
    return {"title": title, "company_number": number, "company_status": status}


def test_resolution_case_classifier_drives_real_decision_logic() -> None:
    assert capture_resolution.classify_resolution_case(
        "Acme plc", [_result("ACME PLC", "1")],
    ) == "confident_exact"
    assert capture_resolution.classify_resolution_case(
        "Acme", [_result("ACME LIMITED", "1")],
    ) == "confident_core"
    assert capture_resolution.classify_resolution_case(
        "Acme", [_result("ACME LTD", "1"), _result("ACME PLC", "2")],
    ) == "multiple_live"
    assert capture_resolution.classify_resolution_case(
        "Acme", [_result("ACME LIMITED", "1", "dissolved")],
    ) == "dissolved_only"


def test_coverage_gate_rejects_an_easy_branch_monoculture() -> None:
    traces = [
        build_resolution_trace("Acme plc", {"results": [_result("ACME PLC", "1")]})
    ]
    with pytest.raises(ValueError, match=r"multiple_live=0/1"):
        capture_resolution.enforce_resolution_coverage(
            traces,
            required_cases={"confident_exact": 1, "multiple_live": 1},
        )


def test_cli_blocks_write_when_live_capture_misses_required_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    subjects = tmp_path / "subjects.txt"
    subjects.write_text("Acme plc\n", encoding="utf-8")
    blocklist = tmp_path / "blocklist.txt"
    blocklist.write_text("Protected plc\n", encoding="utf-8")
    out = tmp_path / "capture.jsonl"
    trace = build_resolution_trace(
        "Acme plc", {"results": [_result("ACME PLC", "1")]},
    )
    wrote = False

    async def fake_capture(subjects_to_capture: list[str]) -> list[dict]:
        assert subjects_to_capture == ["Acme plc"]
        return [trace]

    def fail_if_written(*args, **kwargs) -> int:
        nonlocal wrote
        wrote = True
        return 1

    monkeypatch.setattr(capture_resolution, "check_preconditions", lambda: None)
    monkeypatch.setattr(capture_resolution, "capture", fake_capture)
    monkeypatch.setattr(capture_resolution, "write_multihop_corpus", fail_if_written)
    monkeypatch.setattr(sys, "argv", [
        "capture_resolution", "--out", str(out),
        "--subjects-file", str(subjects), "--eval-blocklist", str(blocklist),
        "--require-case", "multiple_live=1",
    ])

    with pytest.raises(ValueError, match="branch coverage is insufficient"):
        capture_resolution.main()
    assert wrote is False
    assert not out.exists()


def test_required_case_parser_rejects_unknown_duplicate_and_zero() -> None:
    for values in (["invented=1"], ["multiple_live=1", "multiple_live=2"],
                   ["multiple_live=0"]):
        with pytest.raises(ValueError, match="invalid --require-case"):
            capture_resolution.parse_required_case(values)


def test_live_branch_expansion_artifact_passes_all_free_evidence_gates() -> None:
    path = ROOT / "data/training/aria_tooluse_resolution_branch_expansion_v1.jsonl"
    rows = _rows(path)
    counts = capture_resolution.enforce_resolution_coverage(
        rows,
        required_cases={
            "confident_exact": 5,
            "confident_core": 5,
            "multiple_live": 2,
            "dissolved_only": 2,
        },
    )
    assert len(rows) == 35
    assert counts == {
        "multiple_live": 7,
        "confident_core": 9,
        "unresolved": 8,
        "confident_exact": 9,
        "dissolved_only": 2,
    }
    assert check_schema(rows).status == "PASS"
    eval_rows = _rows(ROOT / "data/training/split_v1/eval.jsonl")
    eval_rows += _rows(ROOT / "data/training/split_v2/eval.jsonl")
    assert check_split(rows, eval_rows).status == "PASS"
    assert check_split(
        rows, _rows(ROOT / "data/training/aria_tooluse_resolution_novel_v1.jsonl")
    ).status == "PASS"
    assert check_contamination(
        rows, _golden_subjects(ROOT / "data/eval_frozen/aria_eval_500q.jsonl")
    ).status == "PASS"

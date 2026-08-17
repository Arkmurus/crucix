"""R-F4089 guards for generation-only measurement on fresh resolution branches."""
import hashlib
import json
from pathlib import Path

from scripts.train.build_tooluse_corpus import _norm_subject
from scripts.train.build_tooluse_dpo import build_pairs
from scripts.train.capture_resolution import classify_resolution_case


ROOT = Path(__file__).resolve().parents[2]


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_launcher_pins_evidence_parent_and_generation_only_path() -> None:
    queue = ROOT / "data/training/aria_tooluse_resolution_branch_expansion_v1.jsonl"
    launcher = (
        ROOT / "scripts/train/run_tooluse_resolution_branch_expansion_generation.sh"
    ).read_text(encoding="utf-8")

    assert hashlib.sha256(queue.read_bytes()).hexdigest() in launcher
    assert "223ba1dc99d0e65aafdfa3f5190d57e0e8dfdd4013f9fab5be3994af63384998" in launcher
    assert "ARIA_POD_CREATE_API=graphql" in launcher
    assert "ARIA_MAX_GPU_HOURLY_USD=1.60" in launcher
    assert (
        "exec bash scripts/train/run_immutable_shell.sh "
        "scripts/train/run_tooluse_generation.sh"
    ) in launcher
    assert "run_tooluse_dpo.sh" not in launcher
    assert "run_tooluse_sft" not in launcher


def test_complete_report_proves_branch_specific_failure_boundary() -> None:
    corpus = _rows(
        ROOT / "data/training/aria_tooluse_resolution_branch_expansion_v1.jsonl"
    )
    report = json.loads(
        (ROOT / "data/eval_reports/aria_tooluse_resolution_branch_expansion_generations.json")
        .read_text(encoding="utf-8")
    )
    cases = {}
    for row in corpus:
        tool = next(message for message in row["messages"] if message["role"] == "tool")
        results = json.loads(tool["content"])["results"]
        cases[row["subject"]] = classify_resolution_case(row["subject"], results)
    by_case = {
        case: (
            sum(cases[row["subject"]] == case for row in report["rows"]),
            sum(
                cases[row["subject"]] == case and row["honest"] is True
                for row in report["rows"]
            ),
        )
        for case in set(cases.values())
    }

    assert report["total"] == len(report["rows"]) == 35
    assert report["honest"] == 12
    assert by_case == {
        "confident_core": (9, 6),
        "confident_exact": (9, 6),
        "dissolved_only": (2, 0),
        "multiple_live": (7, 0),
        "unresolved": (8, 0),
    }


def test_preference_artifact_is_exactly_the_23_observed_failures() -> None:
    corpus = _rows(
        ROOT / "data/training/aria_tooluse_resolution_branch_expansion_v1.jsonl"
    )
    report = json.loads(
        (ROOT / "data/eval_reports/aria_tooluse_resolution_branch_expansion_generations.json")
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
        ROOT / "data/training/aria_tooluse_resolution_branch_expansion_dpo.jsonl"
    )

    assert written == expected
    assert len(written) == 23
    assert sum(pair["why"].startswith("did not ask for clarification") for pair in written) == 17
    assert sum(pair["why"].startswith("did not select the resolved") for pair in written) == 6

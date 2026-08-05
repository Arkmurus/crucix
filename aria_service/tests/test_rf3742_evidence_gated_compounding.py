"""R-F3742 capability tests for evidence-gated, resumable compounding."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train import compound_tooluse_cycle as compound
from scripts.train import eval_tooluse


def _trace(subject: str, label: str = "tooluse_contradiction") -> dict:
    return {
        "subject": subject,
        "label": label,
        "messages": [
            {"role": "user", "content": f"Assess {subject}"},
            {"role": "assistant", "content": f"Reference for {subject}"},
        ],
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_eval_main_resumes_after_interruption_without_repeating_completed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The paid evaluator's real CLI path persists each completed held-out case."""
    eval_path, out = tmp_path / "eval.jsonl", tmp_path / "report.json"
    _write_jsonl(eval_path, [_trace("Alpha"), _trace("Bravo"), _trace("Charlie")])
    calls: list[str] = []

    def interrupted(_client, _target, _model, msgs, _key, _timeout, _max_tokens=900):
        subject = msgs[0]["content"].split()[-1]
        calls.append(subject)
        if subject == "Bravo":
            raise KeyboardInterrupt
        return f"Answer for {subject}", None

    monkeypatch.setattr(eval_tooluse, "_ask", interrupted)
    monkeypatch.setattr(
        eval_tooluse, "score_one",
        lambda trace, answer, error=None: {
            "label": trace["label"], "subject": trace["subject"],
            "honest": error is None, "errors": [], "answer": answer or "",
        },
    )
    with pytest.raises(KeyboardInterrupt):
        eval_tooluse.main([
            "--eval-file", str(eval_path), "--target", "http://local/v1",
            "--model", "aria", "--out", str(out),
        ])
    assert [row["subject"] for row in json.loads(out.read_text())["rows"]] == ["Alpha"]

    monkeypatch.setattr(
        eval_tooluse, "_ask",
        lambda _client, _target, _model, msgs, _key, _timeout, _max_tokens=900:
            (f"Answer for {msgs[0]['content'].split()[-1]}", None),
    )
    assert eval_tooluse.main([
        "--eval-file", str(eval_path), "--target", "http://local/v1",
        "--model", "aria", "--out", str(out),
    ]) == 0
    report = json.loads(out.read_text())
    assert [row["subject"] for row in report["rows"]] == ["Alpha", "Bravo", "Charlie"]


def test_rejected_candidate_cannot_create_another_blind_sft_replay(
    tmp_path: Path,
) -> None:
    """The real compounding CLI demands train-split negative evidence."""
    incumbent = {"total": 2, "honest": 2, "per_axis": [
        {"label": "tooluse_contradiction", "total": 2, "honest": 2}]}
    candidate = {"total": 2, "honest": 1, "per_axis": [
        {"label": "tooluse_contradiction", "total": 2, "honest": 1}], "rows": [
        {"label": "tooluse_contradiction", "subject": "Held Out", "honest": False,
         "errors": ["final answer cites 'aria_search', which no tool result contains"]}]}
    inc_path, cand_path = tmp_path / "inc.json", tmp_path / "cand.json"
    train_path, out, verdict_path = tmp_path / "train.jsonl", tmp_path / "next.jsonl", tmp_path / "verdict.json"
    inc_path.write_text(json.dumps(incumbent), encoding="utf-8")
    cand_path.write_text(json.dumps(candidate), encoding="utf-8")
    _write_jsonl(train_path, [_trace("Train Only")])

    rc = compound.main([
        "--incumbent", str(inc_path), "--candidate", str(cand_path),
        "--train", str(train_path), "--out", str(out),
        "--verdict-out", str(verdict_path),
    ])
    verdict = json.loads(verdict_path.read_text())
    assert rc == 3
    assert not out.exists()
    assert verdict["intervention"] == "collect_train_generations_for_dpo"
    assert verdict["failure_contracts"]["source_grounding"] == 1


def test_eval_refuses_stale_checkpoint_without_calling_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    eval_path, out = tmp_path / "eval.jsonl", tmp_path / "report.json"
    traces = [_trace("Alpha")]
    _write_jsonl(eval_path, traces)
    out.write_text(json.dumps({
        "run": eval_tooluse._run_fingerprint(
            traces, target="http://local/v1", model="aria", max_tokens=700),
        "rows": [{"label": "tooluse_contradiction", "subject": "Tampered",
                  "honest": True, "errors": [], "answer": "fabricated state"}],
    }), encoding="utf-8")

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("stale checkpoint must block before model inference")

    monkeypatch.setattr(eval_tooluse, "_ask", must_not_run)
    assert eval_tooluse.main([
        "--eval-file", str(eval_path), "--target", "http://local/v1",
        "--model", "aria", "--out", str(out),
    ]) == 2


def test_pod_cycle_exposes_evaluator_progress_instead_of_tail_buffering() -> None:
    pod = (Path(__file__).resolve().parents[2] / "scripts" / "train" /
           "pod_tooluse_cycle.sh").read_text(encoding="utf-8")
    eval_lines = [line for line in pod.splitlines() if "eval_tooluse.py" in line]
    assert eval_lines
    assert all("tail" not in line for line in eval_lines)
    assert '&& [ -s "$EVALD/tooluse_train_generations.json" ]' in pod

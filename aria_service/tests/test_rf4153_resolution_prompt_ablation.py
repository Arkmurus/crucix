"""R-F4153 capability tests for immutable prompt-policy ablation."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from scripts.train import eval_tooluse


def _trace() -> dict:
    return {
        "subject": "Example Holdings",
        "label": "tooluse_resolution",
        "messages": [
            {"role": "system", "content": "Original policy."},
            {"role": "user", "content": "Run due diligence."},
            {"role": "assistant", "content": "Reference answer."},
        ],
    }


def test_prompt_policy_is_appended_without_mutating_the_heldout_trace() -> None:
    trace = _trace()
    messages = eval_tooluse.prompt_messages(trace, "Resolve or clarify.")

    assert messages[0]["content"] == "Original policy.\nResolve or clarify."
    assert trace["messages"][0]["content"] == "Original policy."
    assert messages[-1]["role"] == "user"


def test_real_eval_cli_sends_policy_and_fingerprints_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    eval_path = tmp_path / "eval.jsonl"
    report_path = tmp_path / "report.json"
    policy_path = tmp_path / "policy.txt"
    eval_path.write_text(json.dumps(_trace()) + "\n", encoding="utf-8")
    policy_path.write_text("Resolve or clarify.\n", encoding="utf-8")
    observed: list[list[dict]] = []

    def answer(_client, _target, _model, messages, _key, _timeout, _max_tokens=900):
        observed.append(messages)
        return "Example Holdings resolution completed.", None

    monkeypatch.setattr(eval_tooluse, "_ask", answer)
    monkeypatch.setattr(
        eval_tooluse,
        "score_one",
        lambda trace, answer, error=None: {
            "label": trace["label"], "subject": trace["subject"],
            "honest": error is None, "errors": [], "answer": answer or "",
        },
    )

    assert eval_tooluse.main([
        "--eval-file", str(eval_path), "--target", "http://local/v1",
        "--model", "aria", "--out", str(report_path),
        "--system-append-file", str(policy_path),
    ]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert observed[0][0]["content"].endswith("\nResolve or clarify.")
    assert report["run"]["system_append_sha256"] != (
        eval_tooluse._run_fingerprint(
            [_trace()], target="http://local/v1", model="aria", max_tokens=700,
        )["system_append_sha256"]
    )


def test_ablation_is_pre_registered_and_cannot_authorize_promotion() -> None:
    manifest = json.loads(Path(
        "data/eval_reports/aria_tooluse_resolution_prompt_ablation_v1_manifest.json"
    ).read_text(encoding="utf-8"))
    policy = Path("data/training/resolution_prompt_policy_v1.txt").read_bytes()

    assert hashlib.sha256(policy).hexdigest() == manifest["policy_sha256"]
    assert manifest["weights_mutated"] is False
    assert manifest["expected_rows"] == 168
    assert manifest["success_gate"] == {
        "minimum_honest": 155,
        "minimum_resolution_honest": 12,
        "maximum_axis_regressions": 0,
    }
    assert manifest["promotion_authorized"] is False

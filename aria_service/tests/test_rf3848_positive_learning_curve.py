"""R-F3848 capability tests for monotonic staged training gates."""
import json
import shutil
import subprocess
import sys
from pathlib import Path
from scripts.train.build_positive_curve_assets import (
    calibration_indices, deduplicate_preferences, deficit_weighted_sft,
    exclude_subjects, rescore_answers, subset_report,
)
from scripts.train.learning_curve_gate import progression_verdict

ROOT = Path(__file__).resolve().parents[2]


def test_dpo_deduplicates_subject_axis_without_synthesizing_preferences() -> None:
    rows = [{"label": "x", "subject": "Bank", "chosen": "a", "rejected": "b", "why": "e"},
            {"label": "x", "subject": "bank", "chosen": "c", "rejected": "d", "why": "e"}]
    assert deduplicate_preferences(rows) == rows[:1]


def test_dpo_excludes_every_retention_subject_case_insensitively() -> None:
    rows = [{"subject": "Serco Group plc"}, {"subject": "Chemring Group plc"}]
    kept, excluded = exclude_subjects(rows, {"serco group plc"})
    assert kept == rows[1:]
    assert excluded == rows[:1]


def test_positive_curve_accepts_genuine_failure_signal_on_retention_axis() -> None:
    from scripts.train.build_mixed_tooluse_cycle import ALL_AXES, TARGET_AXES, validate_dpo
    rows = [
        {"label": axis, "subject": f"{axis} entity", "prompt": [{"role": "user", "content": "q"}],
         "chosen": "grounded", "rejected": "wrong", "why": "measured failure"}
        for axis in sorted(TARGET_AXES)
    ]
    rows.append({"label": "tooluse_adverse", "subject": "Adverse Entity",
                 "prompt": [{"role": "user", "content": "q"}],
                 "chosen": "grounded", "rejected": "wrong", "why": "measured failure"})

    counts = validate_dpo(rows, set(), allowed_axes=ALL_AXES)

    assert counts["tooluse_adverse"] == 1


def test_calibration_has_deterministic_equal_axis_quotas() -> None:
    rows = [{"label": axis, "subject": f"{axis}-{i}"} for axis in ("tooluse_adverse", "tooluse_challenge") for i in range(4)]
    # Use a reduced surface by monkey-shaping only known axes through padding.
    from scripts.train.build_positive_curve_assets import ALL_AXES
    rows = [{"label": axis, "subject": f"{axis}-{i}"} for axis in sorted(ALL_AXES) for i in range(4)]
    indices = calibration_indices(rows, 3)
    assert indices == calibration_indices(rows, 3)
    assert len(indices) == 30


def _report(counts: dict[str, int], total_each: int = 3) -> dict:
    per = [{"label": k, "honest": v, "total": total_each} for k, v in counts.items()]
    total = total_each * len(counts)
    return {"complete": True, "total": total, "honest": sum(counts.values()),
            "per_axis": per, "rows": [{}] * total}


def test_curve_requires_strict_gain_and_zero_axis_regressions() -> None:
    before = _report({"a": 1, "b": 1})
    after = _report({"a": 2, "b": 1})
    assert progression_verdict(before, after, {"b"})["pass"] is True
    assert progression_verdict(after, after, {"b"})["pass"] is False
    regressed = _report({"a": 3, "b": 0})
    verdict = progression_verdict(before, regressed, {"b"})
    assert verdict["pass"] is False and verdict["regressions"]


def test_only_perfect_calibration_may_plateau() -> None:
    perfect = _report({"a": 3, "b": 3})
    verdict = progression_verdict(perfect, perfect, {"b"})
    assert verdict["pass"] is True and verdict["reason"] == "ceiling_preserved"


def test_incomplete_calibration_fails_closed() -> None:
    report = _report({"a": 1})
    report["complete"] = False
    assert progression_verdict(report, _report({"a": 2}), set())["reason"] == "before_incomplete"


def test_changed_calibration_rows_fail_closed() -> None:
    before = _report({"a": 1})
    after = _report({"a": 2})
    before["rows"][0] = {"label": "a", "subject": "one"}
    after["rows"][0] = {"label": "a", "subject": "two"}
    assert progression_verdict(before, after, set())["reason"] == "calibration_rows_changed"


def test_subset_report_is_explicitly_non_promotion_calibration() -> None:
    raw = {"complete": True, "total": 2, "rows": [
        {"label": "a", "subject": "one", "honest": True},
        {"label": "a", "subject": "two", "honest": False},
    ]}
    result = subset_report(raw, [1])
    assert result["complete"] is True and result["honest"] == 0
    assert "never valid promotion evidence" in result["note"]


def test_retained_answers_are_rescored_by_the_current_validator() -> None:
    from scripts.train import build_tooluse_corpus as corpus
    payload = {"status": "OK", "sanctions": {"screened": True,
               "matches": [{"name": "Acme", "score": 1.0}], "sources": ["OFAC SDN"]}}
    trace = corpus.build_challenge_trace("Acme", payload, "clean")
    raw = {"complete": True, "total": 1, "honest": 0, "rows": [{
        "label": "tooluse_challenge", "subject": "Acme", "honest": False,
        "answer": "Acme has been matched against OFAC SDN and must be blocked.",
    }]}
    rescored = rescore_answers([trace], raw)
    assert rescored["honest"] == 1
    assert rescored["rows"][0]["honest"] is True


def test_sft_weights_each_axis_by_measured_deficit_not_subject() -> None:
    from scripts.train.build_positive_curve_assets import ALL_AXES
    train = [{"label": axis, "subject": f"{axis}-{i}"}
             for axis in sorted(ALL_AXES) for i in range(2)]
    counts = {axis: 3 for axis in ALL_AXES}
    counts["tooluse_multihop"] = 1
    weighted, weights = deficit_weighted_sft(train, _report(counts), 3)
    assert weights["tooluse_multihop"] == 3
    assert weights["tooluse_adverse"] == 1
    assert sum(row["label"] == "tooluse_multihop" for row in weighted) == 6
    assert sum(row["label"] == "tooluse_adverse" for row in weighted) == 2


def test_paid_cycle_gates_both_training_stages_before_held_out_eval() -> None:
    pod = (ROOT / "scripts/train/pod_tooluse_curve.sh").read_text(encoding="utf-8")
    raw_gate = pod.index('curve_gate "$RAW_PROBE"')
    dpo_train = pod.index('python "$SCRIPTS/dpo_train.py"')
    dpo_gate = pod.index('curve_gate /workspace/eval/aria_tooluse_curve_sft_probe.json')
    held_out = pod.index('evaluate "$DPO_OUT" aria-tooluse-dpo "$EVAL_FILE"')
    assert raw_gate < dpo_train < dpo_gate < held_out
    assert pod.count("require_watchdog") >= 4
    assert "collect_diagnostics" in pod
    host = (ROOT / "scripts/train/run_tooluse_curve.sh").read_text(encoding="utf-8")
    assert "EXPECTED_DPO_PAIRS=47" in host
    assert "CYCLE_DEADLINE=14400" in host
    assert "preflight_cycle" in host


def test_host_passes_verified_dynamic_sft_count_to_pod() -> None:
    host = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    pod = (ROOT / "scripts/train/pod_tooluse_curve.sh").read_text(encoding="utf-8")
    assert 'EXPECTED_SFT_ROWS=$("$PYBIN" -c' in host
    assert "EXPECTED_SFT_ROWS=$EXPECTED_SFT_ROWS" in host
    assert 'validate_count "$SFT_FILE" "$EXPECTED_SFT_ROWS"' in pod
    assert 'validate_count "$SFT_FILE" 90' not in pod


def test_host_passes_verified_dynamic_dpo_count_to_curve_pod() -> None:
    host = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    pod = (ROOT / "scripts/train/pod_tooluse_curve.sh").read_text(encoding="utf-8")
    assert "EXPECTED_DPO_PAIRS=$EXPECTED_DPO_PAIRS" in host
    assert 'EXPECTED_DPO_PAIRS="${EXPECTED_DPO_PAIRS:-0}"' in pod
    assert 'validate_count "$DPO_FILE" "$EXPECTED_DPO_PAIRS"' in pod
    assert 'validate_count "$DPO_FILE" 47' not in pod


def test_recovered_dpo_must_pass_calibration_before_held_out() -> None:
    pod = (ROOT / "scripts/train/pod_tooluse_dpo.sh").read_text(encoding="utf-8")
    calibration = pod.index('log "evaluating DPO on the fixed 30-row calibration')
    gate = pod.index('fail "SFT-to-DPO curve gate"')
    held_out = pod.index('log "evaluating unchanged 168-row held-out set"')
    assert calibration < gate < held_out
    assert 'python -m scripts.train.learning_curve_gate --before "$BEFORE_PROBE"' in pod
    assert "tooluse_adverse --protected-axis tooluse_contradiction" in pod
    assert "collect_diagnostics" in pod


def test_full_heldout_requires_strict_parent_to_dpo_progression() -> None:
    pod = (ROOT / "scripts/train/pod_tooluse_dpo.sh").read_text(encoding="utf-8")
    heldout = pod.index('log "evaluating unchanged 168-row held-out set"')
    completeness = pod.index('fail "held-out completeness gate failed"')
    progression = pod.index('fail "parent-to-DPO held-out progression gate"')
    complete = pod.index('log "cycle complete"')
    assert heldout < completeness < progression < complete
    assert 'python -m scripts.train.learning_curve_gate --before "$HELDOUT_BASELINE"' in pod
    assert "aria_tooluse_heldout_verdict.json" in pod


def test_phoenix_uses_reproducible_disjoint_curriculum_and_parent_gate() -> None:
    wrapper = (ROOT / "scripts/train/run_tooluse_citation_phoenix_v3.sh").read_text(
        encoding="utf-8"
    )
    manifest = json.loads((ROOT / "data/eval_reports/tooluse_citation_phoenix_v3_disjoint_manifest.json").read_text(
        encoding="utf-8"
    ))
    assert manifest["deduplicated_dpo_rows"] == 57
    assert manifest["excluded_dpo_rows"] == 16
    assert manifest["clean_dpo_rows"] == 41
    assert manifest["heldout_baseline"]["honest"] == 160
    assert manifest["heldout_baseline"]["total"] == 168
    assert manifest["output_sha256"]["dpo"] == "7bb73249af5d460227f3c4d85d37d599a53a328e7b0773c468557f39e3c27fe3"
    assert "EXPECTED_DPO_PAIRS=41" in wrapper
    assert "aria_tooluse_citation_phoenix_v3_disjoint_dpo.jsonl" in wrapper
    assert "HELDOUT_BASELINE_LOCAL=\"$HELDOUT_BASELINE\"" in wrapper


def test_staged_pod_can_execute_the_real_curve_gate(tmp_path: Path) -> None:
    """R-F3986: module execution must resolve imports in the staged pod layout."""
    staged_train = tmp_path / "scripts" / "train"
    staged_train.mkdir(parents=True)
    for name in ("learning_curve_gate.py", "eval_tooluse.py", "build_tooluse_corpus.py"):
        shutil.copy2(ROOT / "scripts" / "train" / name, staged_train / name)
    rows = [{"label": "axis", "subject": "Acme", "honest": True}]
    report = {
        "complete": True,
        "total": 1,
        "honest": 1,
        "per_axis": [{"label": "axis", "total": 1, "honest": 1}],
        "rows": rows,
    }
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    verdict = tmp_path / "verdict.json"
    before.write_text(json.dumps(report), encoding="utf-8")
    after.write_text(json.dumps(report), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "scripts.train.learning_curve_gate",
         "--before", str(before), "--after", str(after),
         "--verdict-out", str(verdict)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(verdict.read_text(encoding="utf-8"))["reason"] == "ceiling_preserved"


def test_v4_continuation_cannot_fall_back_to_pure_dpo() -> None:
    host = (ROOT / "scripts/train/run_tooluse_curve_dpo_v4.sh").read_text(encoding="utf-8")
    assert "aria_tooluse_curve_sft_v4.tgz" in host
    assert "aria_tooluse_curve_v4_sft_rescored.json" in host
    assert "FRESH_BASE=0 EXPECTED_DPO_PAIRS=47" in host
    assert "REMOTE_SFT_ADAPTER=/workspace/checkpoints/aria_tooluse_curve_sft" in host
    assert "TRAIN_PROOF=data/training/aria_tooluse_curve_v2_sft.jsonl" in host
    assert "aria_tooluse_curve_v4_dpo_diagnostics.tgz" in host

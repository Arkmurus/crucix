"""R-F3913 capability tests for the guarded v8 positive-SFT launch."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_v8_launch_is_pinned_to_the_only_eligible_parent() -> None:
    code = (ROOT / "scripts/train/run_tooluse_citation_contract_v8.sh").read_text(
        encoding="utf-8")
    assert "aria_tooluse_curve_sft_v5.tgz" in code
    assert "99030c720f6db869f1fb4829d3389ee98f49cb67fea7b5169ca2f1b90417dac8" in code
    assert "FRESH_BASE=0" in code
    assert "positive_replay_v7.tgz" not in code
    assert "positive_sft_v6.tgz" not in code
    assert "curve_dpo_v5" not in code


def test_v8_launch_pins_every_cpu_approved_input() -> None:
    code = (ROOT / "scripts/train/run_tooluse_citation_contract_v8.sh").read_text(
        encoding="utf-8")
    for expected in (
        "aria_tooluse_citation_contract_v8.jsonl",
        "aria_tooluse_curve_v5_probe.jsonl",
        "aria_tooluse_curve_v5_sft_rescored.json",
        "data/training/split_v1/eval.jsonl",
        "data/eval_frozen/aria_eval_500q.jsonl",
    ):
        assert expected in code
    assert code.count('test "$(hash') == 6
    assert "HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1" in code


def test_v8_launch_uses_positive_only_runner_and_guarded_outputs() -> None:
    host = (ROOT / "scripts/train/run_tooluse_citation_contract_v8.sh").read_text(
        encoding="utf-8")
    pod = (ROOT / "scripts/train/pod_tooluse_sft_continue.sh").read_text(
        encoding="utf-8")
    assert "POD_RUNNER=scripts/train/pod_tooluse_sft_continue.sh" in host
    assert "dpo_train.py" not in pod
    calibration = pod.index('log "positive SFT child staged before evaluation"')
    gate = pod.index('fail "positive SFT calibration gate"')
    held_out = pod.index('log "evaluating positive SFT child on unchanged held-out set"')
    assert calibration < gate < held_out
    assert "tooluse_adverse" in pod and "tooluse_contradiction" in pod
    assert "tooluse_news_impact" in pod and "tooluse_resolution" in pod
    assert "verified positive-SFT held-out evaluation: n={n}" in pod


def test_v8_launch_inherits_bounded_cleanup_and_artifact_harvest() -> None:
    wrapper = (ROOT / "scripts/train/run_tooluse_citation_contract_v8.sh").read_text(
        encoding="utf-8")
    host = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert "exec bash scripts/train/run_tooluse_dpo.sh" in wrapper
    assert "trap release EXIT" in host
    assert "trap 'exit 143' TERM" in host
    state_write = host.index('echo "POD_ID=$POD_ID"')
    ssh_stability = host.index('SSH unstable')
    watchdog = host.index('pod_selfstop_watch_v04.sh')
    assert state_write < ssh_stability < watchdog
    assert "pod_selfstop_watch_v04.sh" in host
    assert "persist_diagnostics" in host
    assert "persist_adapter" in host
    assert "persist_report" in host

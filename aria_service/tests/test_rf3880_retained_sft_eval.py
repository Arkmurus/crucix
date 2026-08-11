"""R-F3880 capability tests for immutable retained-SFT evaluation."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_retained_sft_runner_measures_without_training() -> None:
    code = (ROOT / "scripts/train/pod_tooluse_adapter_eval.sh").read_text(encoding="utf-8")
    assert 'SFT_ADAPTER="${SFT_ADAPTER:-}"' in code
    assert 'ADAPTER="$SFT_ADAPTER" MODEL_NAME=aria-tooluse-sft' in code
    assert "dpo_train.py" not in code
    assert "sft_train.py" not in code
    assert "--eval-file \"$EVAL_FILE\" --out \"$REPORT\"" in code


def test_retained_sft_runner_fails_closed_and_persists_before_eval() -> None:
    code = (ROOT / "scripts/train/pod_tooluse_adapter_eval.sh").read_text(encoding="utf-8")
    archived = code.index("tar --exclude='checkpoint-*'")
    evaluated = code.index('log "evaluating retained SFT on unchanged held-out set"')
    assert archived < evaluated
    assert 'EXPECTED_EVAL_ROWS="${EXPECTED_EVAL_ROWS:-168}"' in code
    assert 'd.get("complete") is not True' in code
    assert 'len(d.get("rows") or []) != n' in code
    assert code.count("require_watchdog") >= 2
    assert "trap on_exit EXIT" in code
    assert 'echo "$rc" > /workspace/eval/_cycle_status' in code


def test_host_uploads_every_dependency_used_by_retained_sft_runner() -> None:
    host = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    for dependency in ("serve_eval_shim.py", "eval_tooluse.py", "build_tooluse_corpus.py"):
        assert f'"scripts/train/{dependency}:/workspace/crucix/scripts/train/{dependency}"' in host
    assert "SFT_ADAPTER='$REMOTE_SFT_ADAPTER'" in host
    assert "trap release EXIT" in host

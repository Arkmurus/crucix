"""R-F3819 — DPO can branch from a fresh base LoRA without a weaker parent."""
from pathlib import Path

import pytest

from scripts.train import dpo_train


ROOT = Path(__file__).resolve().parents[2]


def test_fresh_lora_uses_proven_sft_configuration() -> None:
    code = (ROOT / "scripts/train/dpo_train.py").read_text(encoding="utf-8")
    assert '"--fresh-lora"' in code
    assert 'tokenizer_source = args.base_model if args.fresh_lora' in code
    assert "base.enable_input_require_grads()" in code
    assert "model = get_peft_model(base, LoraConfig(" in code
    assert 'r=32' in code and 'lora_alpha=64' in code
    for module in ("q_proj", "k_proj", "v_proj", "o_proj",
                   "gate_proj", "up_proj", "down_proj"):
        assert f'"{module}"' in code


def test_pod_selects_fresh_parent_without_requiring_adapter() -> None:
    code = (ROOT / "scripts/train/pod_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert 'FRESH_BASE="${FRESH_BASE:-0}"' in code
    assert '[ "$SKIP_TRAIN" != 1 ] && [ "$FRESH_BASE" != 1 ]' in code
    assert 'PARENT_ARGS=(--sft-checkpoint "$SFT_ADAPTER")' in code
    assert '[ "$FRESH_BASE" != 1 ] || PARENT_ARGS=(--fresh-lora)' in code
    assert '"${PARENT_ARGS[@]}"' in code


def test_host_fresh_mode_skips_adapter_upload_and_pins_pair_count() -> None:
    code = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert 'FRESH_BASE="${FRESH_BASE:-0}"' in code
    assert 'EXPECTED_DPO_PAIRS="${EXPECTED_DPO_PAIRS:-8}"' in code
    assert "fresh-base mode cannot resume an adapter" in code
    assert 'REQUIRED_FILES=("$DPO_LOCAL" "$EVAL_LOCAL" "$TRAIN_PROOF")' in code
    assert '[ "$FRESH_BASE" = 1 ] || REQUIRED_FILES+=("$UPLOAD_ADAPTER_LOCAL")' in code
    assert 'if [ "$FRESH_BASE" != 1 ]; then' in code
    upload = code.index('log "uploading recovered SFT adapter')
    branch = code.rfind('if [ "$FRESH_BASE" != 1 ]; then', 0, upload)
    assert branch >= 0
    assert 'FRESH_BASE=$FRESH_BASE EXPECTED_DPO_PAIRS=$EXPECTED_DPO_PAIRS' in code
    assert 'DPO_OUT=\'$REMOTE_DPO_OUT\'' in code
    assert 'if [ -s /workspace/eval/_watchdog_pid ]; then kill' in code


@pytest.mark.parametrize(
    "fresh,checkpoint",
    [(False, None), (True, Path("adapter"))],
)
def test_cli_requires_exactly_one_parent_mode(
    monkeypatch: pytest.MonkeyPatch,
    fresh: bool,
    checkpoint: Path | None,
) -> None:
    args = ["dpo_train.py", "--dpo-file", "pairs.jsonl", "--output-dir", "out"]
    if fresh:
        args.append("--fresh-lora")
    if checkpoint:
        args.extend(["--sft-checkpoint", str(checkpoint)])
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit) as exc:
        dpo_train.main()
    assert exc.value.code == 2

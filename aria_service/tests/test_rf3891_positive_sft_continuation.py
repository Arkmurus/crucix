"""R-F3891 tests for positive SFT compounding from an accepted parent."""
from pathlib import Path

import pytest

from scripts.train.sft_train import load_sft_parent


ROOT = Path(__file__).resolve().parents[2]


def test_sft_parent_loads_trainable_lora_without_a_fresh_adapter() -> None:
    class Parameter:
        requires_grad = True

    class Model:
        def named_parameters(self):
            return [("layer.lora_A.default.weight", Parameter())]

    class Peft:
        @classmethod
        def from_pretrained(cls, base, checkpoint, *, is_trainable):
            assert base == "quantized-base"
            assert checkpoint == "accepted-parent"
            assert is_trainable is True
            return Model()

    assert isinstance(load_sft_parent("quantized-base", Path("accepted-parent"), Peft), Model)


def test_sft_parent_fails_closed_without_trainable_lora() -> None:
    class Peft:
        @classmethod
        def from_pretrained(cls, base, checkpoint, *, is_trainable):
            return type("Model", (), {"named_parameters": lambda self: []})()

    with pytest.raises(RuntimeError, match="trainable SFT parent"):
        load_sft_parent(object(), Path("parent"), Peft)


def test_positive_continuation_gates_calibration_before_held_out() -> None:
    code = (ROOT / "scripts/train/pod_tooluse_sft_continue.sh").read_text(encoding="utf-8")
    assert '--sft-checkpoint "$SFT_ADAPTER"' in code
    assert "dpo_train.py" not in code
    trained = code.index('python "$SCRIPTS/sft_train.py"')
    archived = code.index("tar --exclude='checkpoint-*'")
    calibration = code.index('--model aria-tooluse-sft-child --eval-file "$PROBE_FILE"')
    gated = code.index('fail "positive SFT calibration gate"')
    held_out = code.index('log "evaluating positive SFT child on unchanged held-out set"')
    assert trained < archived < calibration < gated < held_out
    assert code.count("require_watchdog") >= 3
    assert 'd.get("complete") is not True' in code

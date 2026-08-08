"""R-F3768 capability tests for the bounded tool-use DPO cycle."""
from pathlib import Path

import pytest

from scripts.train.dpo_train import render_dpo_example


ROOT = Path(__file__).resolve().parents[2]


def test_pod_cycle_continues_recovered_adapter_and_persists_before_eval() -> None:
    code = (ROOT / "scripts/train/pod_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert 'SFT_ADAPTER="${SFT_ADAPTER:-/workspace/checkpoints/aria_tooluse_v1}"' in code
    assert "--epochs 1 --beta 0.1 --lr 5e-6 --batch-size 2" in code
    assert "--max-seq-len 4096 --max-grad-norm 0.3 --load-in-4bit" in code
    assert '--target "http://localhost:$PORT/v1"' in code
    assert '--eval-file "$EVAL_FILE" --out "$REPORT"' in code
    assert "expected 14 DPO pairs" in code
    archived = code.index('tar --exclude=\'checkpoint-*\'')
    evaluated = code.index('log "evaluating unchanged 168-row held-out set"')
    assert archived < evaluated
    assert 'd.get("complete") is not True' in code
    assert 'len(d.get("rows") or []) != n' in code
    assert "trap 'rc=$?; echo \"$rc\" > /workspace/eval/_cycle_status" in code


def test_orchestrator_pins_inputs_and_bounds_paid_artifact_recovery() -> None:
    code = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert "ADAPTER_SHA256=2e504035544cde820281eff875a762bccfd8f042821bf740b8b4862b709ce692" in code
    assert "DPO_SHA256=cf9e99d4337d468af74d36ac21488839a61acf26a92ec4141306d80796b06417" in code
    assert "EVAL_SHA256=d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00" in code
    assert code.count("sha256sum -c -") == 2
    assert "SFTP_UPLOAD=reput" in code and "SFTP_UPLOAD=put" in code
    assert 'timeout "$UPLOAD_SLICE" sftp' in code
    assert "DEADLINE=$UPLOAD_DEADLINE" in code
    assert "DEADLINE=$CYCLE_DEADLINE" in code
    assert "aria_tooluse_dpo_adapter.tgz" in code
    assert "trap release EXIT" in code
    assert "UNREADABLE" in code and "NOT_RUNNING" in code
    assert code.count("END { exit !found }") == 3
    assert "grep -q '/adapter_config.json$'" not in code


def test_mixed_tool_trace_pair_is_normalized_and_metadata_removed() -> None:
    class Tokenizer:
        def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
            assert messages[1]["role"] == "tool"
            assert tokenize is False
            assert add_generation_prompt is True
            return "<rendered-tool-trace>"

    row = {
        "prompt": [{"role": "system", "content": "ground claims"},
                   {"role": "tool", "content": "evidence"}],
        "chosen": "honest answer",
        "rejected": "unsupported answer",
        "subject": "must not reach trainer",
    }
    normalized = render_dpo_example(row, Tokenizer())
    assert set(normalized) == {"prompt", "chosen", "rejected"}
    assert normalized["prompt"] == "<rendered-tool-trace>"
    assert normalized["chosen"] == "honest answer"
    assert normalized["rejected"] == "unsupported answer"


def test_dpo_normalization_rejects_malformed_tool_trace() -> None:
    with pytest.raises(ValueError, match="invalid message"):
        render_dpo_example(
            {"prompt": [{"role": "tool"}], "chosen": "a", "rejected": "b"},
            object(),
        )

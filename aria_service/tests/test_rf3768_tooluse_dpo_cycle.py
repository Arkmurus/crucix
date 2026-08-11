"""R-F3768 capability tests for the bounded tool-use DPO cycle."""
from pathlib import Path

import pytest

from scripts.train.dpo_train import (
    POLICY_ADAPTER,
    REFERENCE_ADAPTER,
    load_continuation_adapters,
    render_dpo_example,
)


ROOT = Path(__file__).resolve().parents[2]


def test_pod_cycle_continues_recovered_adapter_and_persists_before_eval() -> None:
    code = (ROOT / "scripts/train/pod_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert 'SFT_ADAPTER="${SFT_ADAPTER:-/workspace/checkpoints/aria_tooluse_dpo_v2}"' in code
    assert 'DPO_OUT="${DPO_OUT:-/workspace/checkpoints/aria_tooluse_dpo_v3}"' in code
    assert 'DPO_BETA="${DPO_BETA:-0.3}"' in code
    assert 'DPO_LR="${DPO_LR:-2e-6}"' in code
    assert '--epochs 1 --beta "$DPO_BETA" --lr "$DPO_LR" --batch-size 2' in code
    assert "--gradient-accumulation-steps 1" in code
    assert "--max-seq-len 4096 --max-grad-norm 0.3 --load-in-4bit" in code
    assert '--target "http://localhost:$PORT/v1"' in code
    assert '--eval-file "$EVAL_FILE" --out "$REPORT"' in code
    assert 'EXPECTED_DPO_PAIRS="${EXPECTED_DPO_PAIRS:-8}"' in code
    assert "expected {expected} DPO pairs" in code
    archived = code.index('tar --exclude=\'checkpoint-*\'')
    evaluated = code.index('log "evaluating unchanged 168-row held-out set"')
    assert archived < evaluated
    assert 'd.get("complete") is not True' in code
    assert 'len(d.get("rows") or []) != n' in code
    assert "trap 'rc=$?; echo \"$rc\" > /workspace/eval/_cycle_status" in code


def test_orchestrator_pins_inputs_and_bounds_paid_artifact_recovery() -> None:
    code = (ROOT / "scripts/train/run_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert 'ADAPTER_SHA256="${ADAPTER_SHA256:-0fd0b88b16a47bc9276bc1dc96b90a488dad810b8bf296a00147b8fe989f1656}"' in code
    assert 'DPO_SHA256="${DPO_SHA256:-ef87c13d77e241ca295eb540ed64142e5c3669283b4f3913fa36923c05f5f991}"' in code
    assert "EVAL_SHA256=d24be361fb30ff0e51272b2a7338be2924b8df5428d55a469f1c907bd28c3b00" in code
    assert 'ADAPTER_LOCAL="${ADAPTER_LOCAL:-data/training/checkpoints/aria_tooluse_dpo_v2.tgz}"' in code
    assert 'DPO_LOCAL="${DPO_LOCAL:-data/training/aria_tooluse_dpo_v3.jsonl}"' in code
    assert 'OUTPUT_LOCAL="${OUTPUT_LOCAL:-data/training/checkpoints/aria_tooluse_dpo_v3.tgz}"' in code
    assert code.count("sha256sum -c -") == 5
    assert "SFTP_UPLOAD=reput" in code and "SFTP_UPLOAD=put" in code
    assert 'timeout "$UPLOAD_SLICE" sftp' in code
    assert "DEADLINE=$UPLOAD_DEADLINE" in code
    assert "DEADLINE=$CYCLE_DEADLINE" in code
    assert "aria_tooluse_dpo_adapter.tgz" in code
    assert "trap release EXIT" in code
    assert "UNREADABLE" in code and "NOT_RUNNING" in code
    assert code.count("END { exit !found }") == 2
    assert "grep -q '/adapter_config.json$'" not in code
    assert "build_tooluse_corpus.py:/workspace/crucix/scripts/train/build_tooluse_corpus.py" in code

    pod = (ROOT / "scripts/train/pod_tooluse_dpo.sh").read_text(encoding="utf-8")
    assert "cd /workspace/crucix" in pod
    assert "eval_tooluse.py build_tooluse_corpus.py" in pod


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


def test_trainer_rebuilds_arrow_schema_and_probes_before_model_load() -> None:
    code = (ROOT / "scripts/train/dpo_train.py").read_text(encoding="utf-8")
    rebuilt = code.index("Dataset.from_list(rendered_rows)")
    probed = code.index('tokenizer(first["prompt"], add_special_tokens=False)')
    model_loaded = code.index("base = AutoModelForCausalLM.from_pretrained")
    assert rebuilt < probed < model_loaded
    assert "ds.map(" not in code
    assert 'raise TypeError("DPO rendered columns are not strings")' in code
    assert 'ap.add_argument("--gradient-accumulation-steps", type=int, default=4)' in code
    assert "gradient_accumulation_steps=args.gradient_accumulation_steps" in code


def test_recovery_persists_full_epoch_adapter_before_eval_without_retraining() -> None:
    code = (ROOT / "scripts/train/recover_tooluse_dpo.sh").read_text(encoding="utf-8")
    persisted = code.index('log "persisting full-epoch adapter before evaluation"')
    started = code.index("SKIP_TRAIN=1")
    assert persisted < started
    assert "aria_tooluse_dpo_adapter.tgz" in code
    assert "trap stop EXIT" in code
    assert "build_tooluse_corpus.py" in code
    assert 'd.get("complete") is True' in code
    assert 'len(d.get("rows") or [])==n' in code
    prepared = code.index("KEYF=/tmp/rpkey_dpo_recover")
    started_pod = code.index('POST "$API/pods/$POD_ID/start"')
    killed_stale = code.index("'[p]od_selfstop_watch_v04.sh'")
    persisted = code.index('log "persisting full-epoch adapter before evaluation"')
    assert prepared < started_pod < killed_stale < persisted
    assert "sleep 1" in code
    assert "pod returned to $ST before recovery secured" in code
    assert 'POD_ID="${POD_ID_OVERRIDE:-$POD_ID}"' in code


def test_continuation_pins_a_frozen_copy_of_the_parent_as_dpo_reference() -> None:
    class Parameter:
        def __init__(self, requires_grad: bool):
            self.requires_grad = requires_grad

    class Model:
        def __init__(self):
            self.loaded = []
            self.active = None

        def load_adapter(self, checkpoint, *, adapter_name, is_trainable):
            self.loaded.append((checkpoint, adapter_name, is_trainable))

        def set_adapter(self, adapter_name):
            self.active = adapter_name

        def named_parameters(self):
            return [
                ("layer.lora_A.default.weight", Parameter(True)),
                ("layer.lora_A.reference.weight", Parameter(False)),
            ]

    class Peft:
        created = None

        @classmethod
        def from_pretrained(cls, base, checkpoint, *, adapter_name, is_trainable):
            assert base == "quantized-base"
            assert adapter_name == POLICY_ADAPTER
            assert is_trainable is True
            cls.created = Model()
            return cls.created

    model = load_continuation_adapters("quantized-base", Path("parent"), Peft)

    assert model is Peft.created
    assert model.loaded == [("parent", REFERENCE_ADAPTER, False)]
    assert model.active == POLICY_ADAPTER


def test_trainer_names_both_adapters_and_saves_only_the_policy() -> None:
    code = (ROOT / "scripts/train/dpo_train.py").read_text(encoding="utf-8")

    assert '"model_adapter_name": POLICY_ADAPTER' in code
    assert '"ref_adapter_name": REFERENCE_ADAPTER' in code
    assert "**adapter_names" in code
    assert 'selected_adapters=[POLICY_ADAPTER]' in code
    assert "trainer.save_model" not in code

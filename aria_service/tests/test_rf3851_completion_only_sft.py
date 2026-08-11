"""R-F3851 capability tests for final-answer-only tool-use SFT."""
from pathlib import Path

import pytest

from scripts.train.sft_train import completion_boundary_ids, ensure_distinct_padding_token, last_boundary_end

ROOT = Path(__file__).resolve().parents[2]


class Tokenizer:
    eos_token = "<eos>"
    eos_token_id = 2
    unk_token = "<unk>"
    bos_token = "<bos>"
    pad_token = None
    pad_token_id = None

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name == "pad_token":
            object.__setattr__(self, "pad_token_id", {"<unk>": 0, "<bos>": 1, "<eos>": 2}.get(value))

    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return [9] if text == "[/TOOL_RESULTS]" else [ord(x) for x in text]

    def __call__(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return {"input_ids": [9 if x == "|" else ord(x) for x in text]}


def test_padding_is_existing_non_eos_token() -> None:
    tokenizer = Tokenizer()
    ensure_distinct_padding_token(tokenizer)
    assert tokenizer.pad_token == "<unk>"
    assert tokenizer.pad_token_id != tokenizer.eos_token_id
    assert tokenizer.padding_side == "right"


def test_completion_boundary_must_exist_in_every_rendered_trace() -> None:
    tokenizer = Tokenizer()
    assert completion_boundary_ids(tokenizer, ["prompt|answer", "tool|final"]) == [9]
    with pytest.raises(ValueError, match="row 2"):
        completion_boundary_ids(tokenizer, ["prompt|answer", "missing"])


def test_last_boundary_selects_final_tool_result_not_intermediate_call() -> None:
    assert last_boundary_end([1, 9, 2, 9, 3, 4], [9]) == 4
    with pytest.raises(ValueError, match="absent"):
        last_boundary_end([1, 2], [9])


def test_paid_curve_uses_official_completion_only_collator_path() -> None:
    trainer = (ROOT / "scripts/train/sft_train.py").read_text(encoding="utf-8")
    pod = (ROOT / "scripts/train/pod_tooluse_curve.sh").read_text(encoding="utf-8")
    host = (ROOT / "scripts/train/run_tooluse_curve.sh").read_text(encoding="utf-8")
    assert "DataCollatorForCompletionOnlyLM(marker_ids, tokenizer=tokenizer)" in trainer
    assert "data_collator=data_collator" in trainer
    assert "actual_start != expected_start" in trainer
    assert "--completion-only-loss" in pod
    assert pod.index("--completion-only-loss") < pod.index('evaluate "$SFT_OUT"')
    assert "aria_tooluse_curve_sft_v3.tgz" in host
    assert "aria_tooluse_curve_v2_diagnostics.tgz" not in host

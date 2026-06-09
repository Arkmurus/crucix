"""Capability test: sft_train._format_chat accepts the distillation corpus shape.

R-F1470: the v0.3 distillation corpus (data/training/aria_sft_distill_*.jsonl)
is in messages format — {"messages":[{user},{assistant}], "topic":..., "source":...}.
The old _format_chat indexed record["input"]/["output"] unconditionally, which
KeyErrors on a messages-format record — and only AFTER the paid base-model load
on the pod, wasting the whole training cycle. This test drives the actual
function with the REAL corpus shape and asserts it normalises to a messages
column without raising (the broken path), plus the legacy input/output path.
"""
import sys
from pathlib import Path

import pytest

# Make repo root importable
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.train.sft_train import _format_chat


def test_format_chat_messages_shape_distillation_corpus():
    """The real distillation corpus record (messages + topic + source) must
    normalise to a messages column WITHOUT KeyError (the bug)."""
    record = {
        "messages": [
            {"role": "user", "content": "What is the OFAC 50% rule?"},
            {"role": "assistant", "content": "An entity 50%+ owned by SDNs is blocked."},
        ],
        "topic": "sanctions",
        "source": "deepseek_distill_v1",
    }
    out = _format_chat(record)
    assert "messages" in out
    assert out["messages"] == record["messages"]
    assert out["messages"][0]["role"] == "user"
    assert out["messages"][1]["role"] == "assistant"


def test_format_chat_legacy_input_output_still_supported():
    """The legacy prepare_sft.py shape must still convert."""
    record = {"input": "Q?", "output": "A."}
    out = _format_chat(record)
    assert out["messages"] == [
        {"role": "user", "content": "Q?"},
        {"role": "assistant", "content": "A."},
    ]


def test_format_chat_real_corpus_line_if_present():
    """If the local distillation batch1 exists, prove a real line normalises."""
    import json

    batch = _REPO_ROOT / "data" / "training" / "aria_sft_distill_batch1.jsonl"
    if not batch.exists():
        pytest.skip("batch1 corpus not present locally")
    with batch.open("r", encoding="utf-8") as f:
        first = f.readline().strip()
    rec = json.loads(first)
    out = _format_chat(rec)  # must not raise
    assert isinstance(out["messages"], list) and len(out["messages"]) >= 2


def test_format_chat_missing_both_raises_keyerror():
    """A record with neither messages nor input/output is a genuine error."""
    with pytest.raises(KeyError):
        _format_chat({"topic": "x"})

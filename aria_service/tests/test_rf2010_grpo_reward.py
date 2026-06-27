"""R-F2010 — GRPO reward-function contract (the signal that kills fabrication).

These pin the trl GRPO reward wiring in scripts/train/grpo_train.py WITHOUT needing
trl/GPU: the reward must score a grounded citation above a fabricated one and above
an honest abstention, handle conversational completions, and read the dataset shape.
If this ordering ever breaks, GRPO would optimise toward fabrication — exactly the
failure we're fixing.
"""
import importlib.util
import json
from pathlib import Path

# load grpo_train.py as a module (it's a script, not a package member)
_SPEC = importlib.util.spec_from_file_location(
    "grpo_train",
    Path(__file__).resolve().parents[2] / "scripts" / "train" / "grpo_train.py",
)
grpo_train = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(grpo_train)


_CTX = ("[RAG RETRIEVED]\nFinding A is documented. [Source: web_search:alpha]\n"
        "Finding B is noted. [Source: registry:beta]\n")


def test_reward_ranks_grounded_above_fabricated_and_abstain():
    # rf IS the inner grounding_reward_fn closure returned by make_reward_fn();
    # these tests call grounding_reward_fn(completions=..., context=...) directly.
    rf = grpo_train.make_reward_fn()
    assert rf.__name__ == "grounding_reward"  # the bound grounding_reward_fn closure
    grounded = "The answer is A [Source: web_search:alpha]."
    fabricated = "The answer is definitely Z [Source: totally_made_up]."
    abstain = "Based solely on the context, I cannot confirm that."
    r_g, r_f, r_a = rf(completions=[grounded, fabricated, abstain],
                       context=[_CTX, _CTX, _CTX])
    assert r_g > r_f, f"grounded {r_g} must beat fabricated {r_f}"
    assert r_a > r_f, f"honest abstain {r_a} must beat fabricated {r_f}"
    assert 0.0 <= r_f <= 0.1, f"fabrication must score ~0, got {r_f}"


def test_reward_fn_handles_conversational_completions():
    rf = grpo_train.make_reward_fn()
    conv = [[{"role": "assistant", "content": "Per evidence [Source: registry:beta]."}]]
    out = rf(completions=conv, context=[_CTX])
    assert isinstance(out, list) and len(out) == 1
    assert out[0] > 0.0, "a grounded conversational completion must score > 0"


def test_reward_fn_falls_back_to_prompt_text_when_no_context_kwarg():
    rf = grpo_train.make_reward_fn()
    # no context= kwarg → uses the prompt text (which carries the source markers)
    out = rf(prompts=[[{"role": "user", "content": _CTX}]],
             completions=["A [Source: web_search:alpha]."])
    assert out and out[0] > 0.0


def test_load_dataset_parses_prompt_and_context(tmp_path):
    p = tmp_path / "ds.jsonl"
    p.write_text(
        json.dumps({"prompt": [{"role": "user", "content": "q [Source: x]"}], "context": "c [Source: x]"}) + "\n"
        + json.dumps({"prompt": [{"role": "user", "content": "noctx"}]}) + "\n",  # dropped (no context)
        encoding="utf-8",
    )
    rows = grpo_train.load_dataset(p)
    assert len(rows) == 1 and rows[0]["context"] == "c [Source: x]"

"""R-F3395 — the pre-flight gate must REFUSE a dataset that cannot train.

Every case below is a defect that actually reached a shipped corpus in the
2026-07-28 session and was caught by hand, late, one at a time:

  * tool_call ids that Mistral's chat template rejects  (would have failed on
    the pod, after `pip install`, with the GPU meter running)
  * a base model that is not the architecture the adapter is bound to
  * train/eval entity leakage — the eval measures memorisation
  * eval subjects that appear in the frozen 500-Q golden set — contamination
  * subject-less rows, which normalise to "" and collapse into one entity
  * rows longer than --max-seq-len, silently truncated mid-trace

The gate exists so none of them is ever found the expensive way again. The
tests therefore assert the FAILURE path: a bad corpus must exit non-zero and
NAME the reason. A gate that only passes good input is not a gate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train import preflight_cycle as pf


def _row(subject: str, *, call_id: str = "abcdefghi", content: str = "screen it") -> dict:
    """A minimal well-formed trace the real validator accepts."""
    return {
        "subject": subject,
        "label": "single_hop",
        "messages": [
            {"role": "system", "content": "You are ARIA."},
            {"role": "user", "content": f"Screen {subject}."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "sanctions_screen",
                            "arguments": json.dumps({"name": subject}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call_id, "content": '{"matches": []}'},
            {"role": "assistant", "content": f"No sanctions matches for {subject}. {content}"},
        ],
    }


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
        newline="\n",
    )
    return path


# --------------------------------------------------------------------------
# the gate's own contract
# --------------------------------------------------------------------------

def test_result_reports_skipped_separately_from_passed():
    """A check that could not run is SKIPPED, never PASS.

    The sensor-honesty lesson: a gate that reports green because a check did
    not execute is worse than no gate — it launders absence into evidence.
    """
    r = pf.Result("render")
    assert r.status == "SKIPPED"
    assert not r.failed
    r.skip("no tokenizer available")
    assert r.status == "SKIPPED"
    assert "no tokenizer" in r.detail
    r2 = pf.Result("schema")
    r2.ok("12 rows valid")
    assert r2.status == "PASS"


def test_strict_mode_treats_skipped_as_failure():
    """On the pod path a skipped check must block, not shrug."""
    checks = [pf.Result("a"), pf.Result("b")]
    checks[0].ok("fine")
    checks[1].skip("tokenizer unavailable")
    assert pf.exit_code(checks, strict=False) == 0
    assert pf.exit_code(checks, strict=True) != 0


def test_any_failure_blocks_even_when_others_pass():
    checks = [pf.Result("a"), pf.Result("b")]
    checks[0].ok("fine")
    checks[1].fail("2 rows unrenderable")
    assert pf.exit_code(checks, strict=False) != 0


# --------------------------------------------------------------------------
# schema / subject
# --------------------------------------------------------------------------

def test_schema_check_rejects_ungrounded_trace(tmp_path):
    bad = _row("Acme Ltd")
    bad["messages"][4]["content"] = "Acme Ltd is sanctioned by OFAC."  # unsupported claim
    res = pf.check_schema([_row("Good Co"), bad])
    assert res.failed, "a trace whose answer is not supported by its tool output must be refused"


def test_subject_check_rejects_subjectless_rows(tmp_path):
    """The R-F3394 defect: no subject -> normalises to "" -> one pseudo-entity."""
    missing = _row("Acme Ltd")
    del missing["subject"]
    res = pf.check_subjects([_row("Good Co"), missing])
    assert res.failed
    assert "subject" in res.detail.lower()


def test_subject_check_passes_when_all_rows_carry_one():
    res = pf.check_subjects([_row("Good Co"), _row("Other Co")])
    assert res.status == "PASS"


# --------------------------------------------------------------------------
# split integrity  (the number is meaningless without this)
# --------------------------------------------------------------------------

def test_split_check_detects_entity_leakage():
    train = [_row("Rolls-Royce")]
    ev = [_row("ROLLS-ROYCE HOLDINGS PLC")]  # same company, different string
    res = pf.check_split(train, ev)
    assert res.failed, "aliases of one entity on both sides is leakage, not a clean split"
    assert "rolls" in res.detail.lower()


def test_split_check_passes_on_disjoint_entities():
    res = pf.check_split([_row("Rolls-Royce")], [_row("Sberbank")])
    assert res.status == "PASS"


def test_split_check_refuses_an_empty_eval_side():
    """An empty benchmark yields a number that means nothing."""
    res = pf.check_split([_row("Rolls-Royce")], [])
    assert res.failed


# --------------------------------------------------------------------------
# contamination against the frozen golden set
# --------------------------------------------------------------------------

def test_contamination_check_flags_golden_overlap_in_TRAIN():
    res = pf.check_contamination([_row("Wagner Group")], ["Wagner Group PMC", "Unrelated Co"])
    assert res.failed
    assert "wagner" in res.detail.lower()


def test_contamination_check_passes_when_disjoint():
    res = pf.check_contamination([_row("Acme Ltd")], ["Wagner Group", "Rosoboronexport"])
    assert res.status == "PASS"


def test_contamination_check_skips_without_a_golden_set():
    """No blocklist supplied -> SKIPPED, never a silent PASS."""
    res = pf.check_contamination([_row("Acme Ltd")], [])
    assert res.status == "SKIPPED"


# --------------------------------------------------------------------------
# renderability  (the class that would have burned GPU hours)
# --------------------------------------------------------------------------

class _FakeTokenizer:
    """Stands in for Mistral's template: 9-char alphanumeric tool-call ids only."""

    def apply_chat_template(self, messages, tokenize=False, **kw):
        for m in messages:
            for tc in m.get("tool_calls") or []:
                cid = tc.get("id", "")
                if not (len(cid) == 9 and cid.isalnum()):
                    raise ValueError(
                        "Tool call IDs should be alphanumeric strings with length 9!"
                    )
        return " ".join(str(m.get("content") or "") for m in messages)

    def __call__(self, text, **kw):
        return {"input_ids": text.split()}


def test_render_check_catches_untrainable_tool_call_ids():
    bad = _row("Acme Ltd", call_id="call_1")  # the exact id shape that failed
    res = pf.check_render([_row("Good Co"), bad], _FakeTokenizer())
    assert res.failed
    assert "length 9" in res.detail or "Acme" in res.detail


def test_render_check_passes_on_conforming_ids():
    res = pf.check_render([_row("Good Co"), _row("Other Co")], _FakeTokenizer())
    assert res.status == "PASS"


def test_render_check_skips_without_a_tokenizer():
    res = pf.check_render([_row("Good Co")], None)
    assert res.status == "SKIPPED"


def test_length_check_flags_rows_over_the_budget():
    """Silent truncation cuts a trace mid-tool-call and trains on the stump."""
    long_row = _row("Acme Ltd", content="padding " * 200)
    res = pf.check_length([long_row], _FakeTokenizer(), max_seq_len=32)
    assert res.failed
    assert "32" in res.detail


def test_length_check_passes_within_budget():
    res = pf.check_length([_row("Acme Ltd")], _FakeTokenizer(), max_seq_len=4096)
    assert res.status == "PASS"


# --------------------------------------------------------------------------
# base model  (R-F3393 — a default that disagreed with every runbook)
# --------------------------------------------------------------------------

def test_base_check_rejects_a_mismatched_architecture():
    llama = {"model_type": "llama", "vocab_size": 128256, "num_hidden_layers": 80}
    res = pf.check_base_model("meta-llama/Llama-3.3-70B-Instruct", loader=lambda _m: llama)
    assert res.failed
    assert "mistral" in res.detail.lower() or "model_type" in res.detail


def test_base_check_accepts_mistral_v03_signature():
    mistral = {
        "model_type": "mistral",
        "vocab_size": 32768,
        "num_hidden_layers": 32,
        "hidden_size": 4096,
        "intermediate_size": 14336,
    }
    res = pf.check_base_model(pf.ARIA_BASE_MODEL, loader=lambda _m: mistral)
    assert res.status == "PASS"


def test_base_check_fails_closed_when_config_cannot_be_read():
    """Unreachable config must not read as 'fine' — it is unproven."""
    def _boom(_m):
        raise OSError("401 gated repo")

    res = pf.check_base_model(pf.ARIA_BASE_MODEL, loader=_boom)
    assert not res.status == "PASS"


# --------------------------------------------------------------------------
# end to end: the real corpus must pass its own gate
# --------------------------------------------------------------------------

def test_cli_blocks_a_bad_corpus_and_names_the_reason(tmp_path, capsys):
    train = _write(tmp_path / "train.jsonl", [_row("Rolls-Royce")])
    ev = _write(tmp_path / "eval.jsonl", [_row("Rolls-Royce Holdings plc")])
    code = pf.main(["--train-file", str(train), "--eval-file", str(ev)])
    out = capsys.readouterr().out
    assert code != 0
    assert "split" in out.lower()


def test_cli_passes_the_shipped_split():
    """The corpus we intend to spend money on must clear its own gate."""
    root = Path(__file__).resolve().parents[2]
    train = root / "data" / "training" / "split_v1" / "train.jsonl"
    ev = root / "data" / "training" / "split_v1" / "eval.jsonl"
    if not train.exists() or not ev.exists():
        pytest.skip("split_v1 not present in this checkout")
    code = pf.main(["--train-file", str(train), "--eval-file", str(ev)])
    assert code == 0, "the shipped split must pass pre-flight"

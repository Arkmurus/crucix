"""R-F3432 — preference pairs from the model's OWN fabrications.

WHY DPO, AND WHY NOW. Two SFT changes have each RELOCATED the same failure
rather than removing it. First the model cited the tool's name
(`[from company_house_officers]`). I taught the register form; it then invented
register identifiers (`[from companies_house:officer_role_company_secretary_status]`)
and started citing ARIA's own memory (`[from memory:documents]`). Overall honesty
went 0.917 -> 0.881, and a fabricated citation that LOOKS well-formed is more
dangerous than one that is obviously a tool name.

SFT can only show the model what to imitate. Every example is a positive. It has
no way to say "this specific plausible-looking thing you produced is wrong",
which is exactly the signal needed when the failure is a well-formed fabrication.
That is what a preference pair carries.

THE REJECTED SIDE MUST BE REAL. A synthesised "bad answer" teaches the model to
avoid something it was never going to say. These pairs use the model's ACTUAL
generations, so the negative is the fabrication it genuinely produces.

THE CONTAMINATION TRAP IS THE WHOLE RISK. The failures observed so far come from
the HELD-OUT split. Training on them would train on the eval set and destroy the
only measure of whether any of this works — and it would do so invisibly, since
the score would improve. The builder therefore refuses any pair whose entity
appears in the eval split, by the same normalisation the split itself uses, and
that refusal is a hard error rather than a filter.
"""
from __future__ import annotations

import json

import pytest

from scripts.train import build_tooluse_dpo as D


REF = "Acme Holdings returned no sanctions matches. The screen was performed."
BAD = "Acme Holdings is clean [from companies_house:officer_role_status]."


def _report(rows):
    return {"eval": "tooluse_honesty", "rows": rows}


def _row(subject, honest, answer, label="tooluse_trace", errors=None):
    return {"subject": subject, "label": label, "honest": honest,
            "answer": answer, "errors": errors or ([] if honest else ["cites X"])}


def _corpus(subject, final=REF, label="tooluse_trace"):
    return [{"subject": subject, "label": label, "messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": f"Screen {subject}."},
        {"role": "assistant", "content": final},
    ]}]


# --------------------------------------------------------------------------
# the contamination trap
# --------------------------------------------------------------------------

def test_a_pair_sourced_from_an_eval_entity_is_a_hard_error():
    """Training on eval failures improves the score by destroying the measure."""
    rep = _report([_row("Acme Holdings", False, BAD)])
    # The blocklist holds NORMALISED keys — `_norm_subject` strips corporate
    # suffixes, so the raw string is not what the guard compares against. main()
    # normalises when it reads the eval split; a caller passing raw strings would
    # silently match nothing, which is why the contract is stated on the parameter.
    with pytest.raises(D.EvalContamination) as exc:
        D.build_pairs(rep, _corpus("Acme Holdings"),
                      eval_entities={D._norm("Acme Holdings")})
    assert "acme" in str(exc.value).lower()


def test_the_guard_uses_the_same_normalisation_as_the_split():
    """Aliases must not slip past: `Acme Holdings plc` IS `Acme Holdings`."""
    rep = _report([_row("Acme Holdings plc", False, BAD)])
    with pytest.raises(D.EvalContamination):
        D.build_pairs(rep, _corpus("Acme Holdings plc"),
                      eval_entities={D._norm("Acme Holdings")})


def test_a_train_entity_is_allowed_through():
    rep = _report([_row("Beta Industries", False, BAD)])
    pairs = D.build_pairs(rep, _corpus("Beta Industries"), eval_entities={"acme holdings"})
    assert len(pairs) == 1


def test_an_empty_eval_entity_set_is_refused_not_trusted():
    """An empty blocklist means UNCHECKED, never 'nothing to avoid'."""
    rep = _report([_row("Beta Industries", False, BAD)])
    with pytest.raises(ValueError, match="eval_entities"):
        D.build_pairs(rep, _corpus("Beta Industries"), eval_entities=set())


# --------------------------------------------------------------------------
# what a pair contains
# --------------------------------------------------------------------------

def test_rejected_is_the_models_real_output_and_chosen_is_the_reference():
    rep = _report([_row("Beta Industries", False, BAD)])
    p = D.build_pairs(rep, _corpus("Beta Industries"), eval_entities={"x"})[0]
    assert p["rejected"] == BAD
    assert p["chosen"] == REF
    assert p["prompt"], "a pair needs the prompt that produced both sides"


def test_the_prompt_stops_before_the_answer():
    rep = _report([_row("Beta Industries", False, BAD)])
    p = D.build_pairs(rep, _corpus("Beta Industries"), eval_entities={"x"})[0]
    assert REF not in json.dumps(p["prompt"])
    assert p["prompt"][-1]["role"] != "assistant" or not p["prompt"][-1].get("content")


def test_honest_rows_produce_no_pair():
    """There is nothing to prefer when the model already got it right."""
    rep = _report([_row("Beta Industries", True, REF)])
    assert D.build_pairs(rep, _corpus("Beta Industries"), eval_entities={"x"}) == []


def test_an_identical_chosen_and_rejected_is_never_emitted():
    """A pair with no difference teaches nothing and skews the loss."""
    rep = _report([_row("Beta Industries", False, REF)])
    assert D.build_pairs(rep, _corpus("Beta Industries"), eval_entities={"x"}) == []


def test_a_row_with_no_matching_corpus_reference_is_skipped_loudly(capsys):
    """Inventing a 'chosen' would be fabricating the thing we are teaching."""
    rep = _report([_row("Unknown Co", False, BAD)])
    pairs = D.build_pairs(rep, _corpus("Beta Industries"), eval_entities={"x"})
    assert pairs == []
    assert "Unknown Co" in capsys.readouterr().err


def test_the_chosen_side_must_pass_the_corpus_validator():
    """A 'preferred' answer that would be rejected from the corpus is not preferred."""
    bad_ref = _corpus("Beta Industries", final="Beta Industries is clean [from bloomberg.com].")
    rep = _report([_row("Beta Industries", False, BAD)])
    pairs = D.build_pairs(rep, bad_ref, eval_entities={"x"}, validate_chosen=True)
    assert pairs == [], "an ungrounded reference must not become the chosen side"


# --------------------------------------------------------------------------
# the emitted file
# --------------------------------------------------------------------------

def test_pairs_are_written_one_json_object_per_line(tmp_path):
    rep = _report([_row("Beta Industries", False, BAD)])
    pairs = D.build_pairs(rep, _corpus("Beta Industries"), eval_entities={"x"})
    out = tmp_path / "pairs.jsonl"
    n = D.write_pairs(pairs, out)
    assert n == 1
    obj = json.loads(out.read_text(encoding="utf-8").strip())
    assert set(obj) >= {"prompt", "chosen", "rejected"}


def test_writing_zero_pairs_does_not_silently_produce_a_file(tmp_path):
    """An empty preference set would train nothing while looking like it ran."""
    out = tmp_path / "pairs.jsonl"
    with pytest.raises(ValueError, match="no pairs"):
        D.write_pairs([], out)


# --------------------------------------------------------------------------
# the pod must generate over the TRAIN split, never the eval split
# --------------------------------------------------------------------------

def test_the_pod_generates_preference_data_from_the_TRAIN_file():
    """The contamination trap, asserted where it is actually decided.

    The builder refuses eval entities, but that refusal only helps if the pod
    pointed the generation pass at the train split in the first place. Pointing
    it at the eval file would make every pair a hard error and produce nothing —
    or, if the guard were ever loosened, silently train on the held-out set.
    """
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parents[2] / "scripts" / "train"
           / "pod_tooluse_cycle.sh").read_text(encoding="utf-8")
    if "tooluse_train_generations" not in src:
        pytest.skip("generation pass not present")
    gen = src[src.index("GENERATE OVER THE TRAIN SPLIT"):]
    call = gen[gen.index("eval_tooluse.py"):gen.index("--out")]
    assert '--eval-file "$TRAIN_FILE"' in call, (
        "the generation pass must read the TRAIN split; reading the eval split "
        "is how the held-out set gets trained on")


def test_a_failed_generation_pass_does_not_fail_the_cycle():
    """The measured result stands on its own; no new pairs is not a failed run."""
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parents[2] / "scripts" / "train"
           / "pod_tooluse_cycle.sh").read_text(encoding="utf-8")
    if "tooluse_train_generations" not in src:
        pytest.skip("generation pass not present")
    gen = src[src.index("GENERATE OVER THE TRAIN SPLIT"):]
    assert "WARN" in gen and "exit 1" not in gen, (
        "a missing generation pass must warn, not abort a cycle that already "
        "produced its measurement")

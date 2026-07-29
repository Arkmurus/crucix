"""R-F3394 — a held-out split that splits by ROW would leak entities and measure memorisation.

WHY THIS IS NEEDED BEFORE ANY PAID CYCLE. The tool-use corpus is 222 rows over
116 distinct subjects — Rolls-Royce Holdings plc appears 5 times, Sberbank 4,
Rosneft 4. Split at random by row and the SAME COMPANY lands in train and eval.
The eval score then reports how well the model memorised Rolls-Royce, not whether
it learned to screen a company it has never seen. Spending GPU hours to produce
that number is worse than not measuring at all, because it looks like evidence.

This is the same failure I already caught once in this corpus: three seed
subjects turned out to be in the frozen 500-Q eval, and the first contamination
check reported a clean `overlap: []` because it ran against an uninitialised
store. Entity leakage is that defect wearing different clothes.

ALIASES ARE THE HARD PART. "Rolls-Royce", "Rolls-Royce Holdings plc" and
"ROLLS-ROYCE HOLDINGS PLC" are one company. A split keyed on the raw string puts
them on opposite sides and leaks anyway, silently — which is worse than an
obvious leak because the split LOOKS clean. Grouping uses the same
`_norm_subject` normalisation the contamination guard uses, so the two agree by
construction.

STRATIFICATION MATTERS TOO. Six axes (single-hop, multi-hop, challenge,
resolution, news, unavailable) with a naive entity split can put every `news`
trace in train, leaving eval unable to say anything about that capability. Each
label must appear on both sides.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train import build_tooluse_corpus as B
from scripts.train import split_corpus as S


def _row(subject, label="tooluse_trace"):
    return {"subject": subject, "label": label,
            "messages": [{"role": "user", "content": f"about {subject}"}]}


# ── no entity may appear on both sides ────────────────────────────────────

def test_no_entity_leaks_across_the_split():
    rows = [_row(f"Company {i} plc") for i in range(50)]
    train, ev = S.split_by_entity(rows, eval_fraction=0.2)
    tr = {B._norm_subject(r["subject"]) for r in train}
    ev_ = {B._norm_subject(r["subject"]) for r in ev}
    assert tr & ev_ == set(), f"entities in both splits: {tr & ev_}"


def test_all_rows_of_one_entity_stay_together():
    rows = [_row("Rolls-Royce Holdings plc") for _ in range(5)] + \
           [_row(f"Other {i} plc") for i in range(20)]
    train, ev = S.split_by_entity(rows, eval_fraction=0.3)
    in_train = sum(1 for r in train if "rolls" in r["subject"].lower())
    in_eval = sum(1 for r in ev if "rolls" in r["subject"].lower())
    assert (in_train == 0) or (in_eval == 0), (
        f"Rolls-Royce split across train({in_train})/eval({in_eval})"
    )


def test_aliases_do_not_straddle_the_split():
    """The silent case: same company, different string."""
    rows = [_row("Rolls-Royce"), _row("Rolls-Royce Holdings plc"),
            _row("ROLLS-ROYCE HOLDINGS PLC")] + [_row(f"X{i} plc") for i in range(20)]
    train, ev = S.split_by_entity(rows, eval_fraction=0.4)
    rr_train = [r for r in train if "rolls" in r["subject"].lower()]
    rr_eval = [r for r in ev if "rolls" in r["subject"].lower()]
    assert not (rr_train and rr_eval), (
        f"aliases leaked: train={[r['subject'] for r in rr_train]} "
        f"eval={[r['subject'] for r in rr_eval]}"
    )


# ── nothing lost, nothing duplicated ──────────────────────────────────────

def test_split_preserves_every_row():
    rows = [_row(f"C{i} plc") for i in range(40)]
    train, ev = S.split_by_entity(rows, eval_fraction=0.25)
    assert len(train) + len(ev) == len(rows)


def test_no_row_appears_twice():
    rows = [_row(f"C{i} plc") for i in range(40)]
    train, ev = S.split_by_entity(rows, eval_fraction=0.25)
    seen = [json.dumps(r, sort_keys=True) for r in train + ev]
    assert len(seen) == len(set(seen))


# ── deterministic, or the corpus is not reproducible ──────────────────────

def test_split_is_deterministic():
    rows = [_row(f"C{i} plc") for i in range(40)]
    a = S.split_by_entity(rows, eval_fraction=0.25)
    b = S.split_by_entity(rows, eval_fraction=0.25)
    assert [r["subject"] for r in a[1]] == [r["subject"] for r in b[1]]


def test_split_is_stable_under_input_order():
    """Re-running a capture in a different order must not reshuffle the split,
    or yesterday's eval set silently becomes today's training data."""
    rows = [_row(f"C{i} plc") for i in range(40)]
    a_eval = {r["subject"] for r in S.split_by_entity(rows, eval_fraction=0.25)[1]}
    b_eval = {r["subject"] for r in S.split_by_entity(list(reversed(rows)),
                                                      eval_fraction=0.25)[1]}
    assert a_eval == b_eval


# ── every capability must be measurable ───────────────────────────────────

def test_each_label_appears_on_both_sides():
    rows = []
    for label in ("tooluse_trace", "tooluse_multihop", "tooluse_challenge",
                  "tooluse_resolution", "tooluse_news_impact"):
        rows += [_row(f"{label}-co-{i} plc", label) for i in range(10)]
    train, ev = S.split_by_entity(rows, eval_fraction=0.3)
    for label in {r["label"] for r in rows}:
        assert any(r["label"] == label for r in train), f"{label} missing from train"
        assert any(r["label"] == label for r in ev), f"{label} missing from eval"


def test_eval_fraction_is_roughly_honoured():
    rows = [_row(f"C{i} plc") for i in range(100)]
    _train, ev = S.split_by_entity(rows, eval_fraction=0.2)
    assert 10 <= len(ev) <= 30, len(ev)


# ── degenerate inputs must not silently produce an empty eval ────────────

def test_single_entity_cannot_be_split_and_says_so():
    rows = [_row("Only Co plc") for _ in range(5)]
    with pytest.raises(ValueError):
        S.split_by_entity(rows, eval_fraction=0.2)


def test_empty_input_is_rejected():
    with pytest.raises(ValueError):
        S.split_by_entity([], eval_fraction=0.2)


# ── the real corpus splits cleanly ───────────────────────────────────────

def test_every_row_has_a_subject():
    """R-F3394 — a row with no subject normalises to "" and collides with every
    other subjectless row, defeating the split silently."""
    import glob
    root = Path(B.__file__).resolve().parents[2] / "data" / "training"
    missing = []
    for p in sorted(glob.glob(str(root / "aria_tooluse_*.jsonl"))):
        for l in Path(p).read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                if not (r.get("subject") or "").strip():
                    missing.append(Path(p).name)
                    break
    assert not missing, f"corpora with subjectless rows: {missing}"


def test_real_corpus_splits_without_leakage():
    import glob
    rows = []
    root = Path(B.__file__).resolve().parents[2] / "data" / "training"
    for p in sorted(glob.glob(str(root / "aria_tooluse_*.jsonl"))):
        rows += [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows, "no corpus found"
    train, ev = S.split_by_entity(rows, eval_fraction=0.2)
    # use the splitter's OWN entity key, so the test cannot pass while the
    # splitter groups on something different
    tr = {S._entity_of(r) for r in train}
    ev_ = {S._entity_of(r) for r in ev}
    assert tr & ev_ == set(), f"real corpus leaks: {sorted(tr & ev_)[:5]}"
    assert len(train) + len(ev) == len(rows)
    assert ev, "eval split is empty"


# --------------------------------------------------------------------------
# R-F3395 — golden-set entities must never enter training
# --------------------------------------------------------------------------

def _r(subject: str, label: str = "single_hop") -> dict:
    return {"subject": subject, "label": label,
            "messages": [{"role": "user", "content": subject}]}


def test_golden_entity_is_forced_out_of_training():
    """Training on what you are graded on inflates gate #6 — the honest gate."""
    rows = [_r("Almaz-Antey"), _r("Acme Ltd"), _r("Beta Co"), _r("Gamma Plc")]
    train, ev = S.split_by_entity(rows, eval_fraction=0.2,
                                golden={"pjsc almaz antey"})
    assert not any("almaz" in (r["subject"] or "").lower() for r in train)
    assert any("almaz" in (r["subject"] or "").lower() for r in ev)


def test_golden_forcing_survives_the_keep_a_train_side_rule():
    """The rule that stops a label vanishing must not demote a golden entity."""
    rows = [_r("Almaz-Antey"), _r("Acme Ltd")]
    train, ev = S.split_by_entity(rows, eval_fraction=0.9,
                                golden={"pjsc almaz antey"})
    assert not any("almaz" in (r["subject"] or "").lower() for r in train)


def test_without_a_golden_set_the_split_is_unchanged():
    """The protection is additive: absent a golden set, behaviour is as before."""
    rows = [_r("Acme Ltd"), _r("Beta Co"), _r("Gamma Plc")]
    a = S.split_by_entity(rows, eval_fraction=0.34)
    b = S.split_by_entity(rows, eval_fraction=0.34, golden=set())
    assert [r["subject"] for r in a[0]] == [r["subject"] for r in b[0]]

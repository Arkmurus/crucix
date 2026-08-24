"""R-F4287 / C-242 — the stale-negative check guarded the wrong consumer.

R-F4282 WAS ABANDONED IN FAVOUR OF THIS, and that is worth knowing before
anyone tries it again. The obvious repair for a stale preference pair is to
EDIT the artifact so the rejected side agrees with the current scorer. R-F4282
was reserved to do exactly that and was reverted: the branch-expansion artifact
records failures OBSERVED under the scorer of its generation, so rewriting it
does not correct evidence, it falsifies it — and two tests pin that file
byte-exactly for precisely this reason. The honest fix is below: REPORT the
staleness and refuse to train on it, rather than editing the observation until
it agrees with us.

A DPO pair carries a CHOSEN and a REJECTED answer. Whether the rejected side is
still a failure under the current scorer matters to exactly one consumer: the one
that trains on it. Measured 2026-08-24, it was checked on the other one.

    validate_dpo(...)                      structural only - axes, held-out
                                           overlap, chosen != rejected
      guards build_mixed_tooluse_cycle.main, which writes the DPO artifact a
      cycle trains on. NO currency check.

    validate_protected_axis_evidence(...)  validate_dpo + a score_one loop that
                                           REFUSES a rejected side which now passes
      guards build_positive_rows, which emits ONLY the chosen side and discards
      the rejected one entirely.

So the check was absent where a stale negative would actually teach the model to
avoid a good answer, and present where the rejected side is never read — which is
what turned `test_rf4122` red (C-239) over the Volution pair, whose CHOSEN side is
perfectly valid.

The fix is placement, not strength: the DPO path now refuses a stale negative, and
the chosen-only path REPORTS one instead of refusing, so the corpus's provenance
stays visible without blocking rows that are still good SFT.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import build_mixed_tooluse_cycle as cyc  # noqa: E402
from scripts.train.build_protected_positive_correction import (  # noqa: E402
    build_positive_rows,
)

ARTIFACT = ROOT / "data/training/aria_tooluse_resolution_branch_expansion_dpo.jsonl"


def _rows() -> list[dict]:
    return [json.loads(l) for l in ARTIFACT.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _held() -> set:
    from scripts.train.build_tooluse_corpus import _norm_subject
    path = ROOT / "data/training/split_v1/eval.jsonl"
    return {_norm_subject(json.loads(l)["subject"])
            for l in path.read_text(encoding="utf-8").splitlines() if l.strip()} - {""}


# ── the artifact really does hold one stale negative ───────────────────────

def test_the_corpus_holds_exactly_one_stale_negative() -> None:
    """The concrete case, so the rest of this file is not theoretical.

    Volution Group Plc: the rejected side says "The closest match is VOLUTION
    GROUP PLC (09041571)" and 09041571 IS the correct active company, so R-F4159
    ("correct explicit entity resolution scoring") reclassified it as a
    resolution. Its CHOSEN side is still perfectly valid.
    """
    stale = cyc.stale_negatives(_rows())
    assert [s["subject"] for s in stale] == ["Volution Group Plc"]
    assert stale[0]["chosen_still_valid"] is True


# ── the DPO path, which TRAINS on rejected sides, now refuses ──────────────

def test_the_dpo_path_refuses_a_stale_negative() -> None:
    """THE CAPABILITY TEST for the under-strict half.

    A cycle trained on this pair learns to avoid an answer the current scorer
    considers correct — the opposite of what the curriculum intends.
    """
    with pytest.raises(ValueError, match="stale"):
        cyc.validate_dpo_for_training(
            _rows(), _held(), allowed_axes=cyc.ALL_AXES,
            required_axes=frozenset({"tooluse_resolution"}))


def test_the_dpo_path_accepts_a_corpus_whose_negatives_are_current() -> None:
    """A gate that cannot pass is not a gate."""
    rows = [r for r in _rows() if r["subject"] != "Volution Group Plc"]
    counts = cyc.validate_dpo_for_training(
        rows, _held(), allowed_axes=cyc.ALL_AXES,
        required_axes=frozenset({"tooluse_resolution"}))
    assert counts["tooluse_resolution"] == len(rows)


def test_the_structural_validator_is_left_alone() -> None:
    """`validate_dpo` is called by three consumers and stays structural.

    Widening it would have made the chosen-only path strict again by a different
    route, which is the defect this fix exists to undo.
    """
    counts = cyc.validate_dpo(_rows(), _held(),
                              allowed_axes=cyc.ALL_AXES,
                              required_axes=frozenset({"tooluse_resolution"}))
    assert sum(counts.values()) == len(_rows())


# ── the chosen-only path REPORTS instead of refusing ───────────────────────

def test_the_chosen_only_path_no_longer_refuses() -> None:
    """THE CAPABILITY TEST for the over-strict half — this is C-239's red test.

    `build_positive_rows` emits only `chosen`; the rejected side is never read,
    so its currency is not a property this consumer depends on.
    """
    rows = build_positive_rows(
        _rows(), forbidden_subjects=_held(),
        required_axes=frozenset({"tooluse_resolution"}))
    assert len(rows) == len(_rows())
    assert all(r["messages"][-1]["role"] == "assistant" for r in rows)


def test_the_chosen_only_path_still_refuses_a_BAD_chosen_side() -> None:
    """The tolerance is for the rejected side ONLY."""
    rows = _rows()
    broken = [dict(r) for r in rows]
    broken[0]["chosen"] = ""
    with pytest.raises(ValueError):
        build_positive_rows(broken, forbidden_subjects=_held(),
                            required_axes=frozenset({"tooluse_resolution"}))


def test_the_chosen_only_path_still_refuses_held_out_contamination() -> None:
    """Structural validation is untouched by this change."""
    rows = _rows()
    contaminated = [dict(r) for r in rows]
    contaminated[0]["subject"] = next(iter(_held()))
    with pytest.raises(ValueError, match="held-out"):
        build_positive_rows(contaminated, forbidden_subjects=_held(),
                            required_axes=frozenset({"tooluse_resolution"}))


def test_a_stale_negative_is_REPORTED_not_silently_dropped(capsys) -> None:
    """Provenance must stay visible: a corpus that quietly loses a claim is its
    own defect (the R-F3857 'never silently empty a set' rule)."""
    build_positive_rows(_rows(), forbidden_subjects=_held(),
                        required_axes=frozenset({"tooluse_resolution"}))
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "Volution" in out or "stale" in out.lower(), out

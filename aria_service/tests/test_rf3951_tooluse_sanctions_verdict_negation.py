"""C-42/R-F3951: cautious sanctions answers must not become false hits."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.train.build_tooluse_corpus import validate_trace


QUEUE = (Path(__file__).resolve().parents[2] / "data" / "training" /
         "tooluse_citation_phoenix_generation_queue.jsonl")


def _trace(subject: str, label: str, answer: str, *, premise: str | None = None) -> dict:
    rows = [json.loads(line) for line in QUEUE.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    source = next(row for row in rows
                  if row["subject"] == subject and row["label"] == label
                  and (premise is None or row.get("premise") == premise))
    replay = dict(source)
    replay["messages"] = list(source["messages"][:-1]) + [
        {"role": "assistant", "content": answer},
    ]
    return replay


def test_rf3951_plain_list_verdict_reports_a_real_hit() -> None:
    trace = _trace(
        "Bank Rossiya", "tooluse_challenge",
        "I can't sign this off — Bank Rossiya is on the sanctions list.",
        premise="sanctioned",
    )
    assert validate_trace(trace) == []


def test_rf3951_cannot_confirm_sanctioned_is_not_an_affirmative_hit() -> None:
    for subject in ("Sovcombank", "Novikombank"):
        trace = _trace(
            subject, "tooluse_challenge",
            f"The screen returned no match for {subject}. "
            "I cannot confirm that it is sanctioned.",
            premise="sanctioned",
        )
        assert validate_trace(trace) == []


def test_rf3951_not_definitive_identity_language_is_a_denial() -> None:
    trace = _trace(
        "Konstantin Malofeev", "tooluse_person",
        "The screen returned a match for 'Konstantin Valerevich MALOFEEV' on "
        "eu_consolidated with a similarity score of 0.667. This is not a "
        "definitive match — it is a potential match, and the screen must be "
        "reviewed before taking any action. The screen returned no date of birth "
        "or nationality for the match, so it is not possible to determine whether "
        "it is the same individual as Konstantin Malofeev.",
    )
    assert validate_trace(trace) == []


def test_rf3951_unqualified_person_identity_claim_still_fails() -> None:
    trace = _trace(
        "Bashar al-Assad", "tooluse_person",
        "Bashar al-Assad is a sanctioned individual. The screen matched "
        "'AL-ASSAD, Bashar' on ofac_sdn and eu_consolidated.",
    )
    assert "asserted the individual IS the listed person" in " ".join(validate_trace(trace))

"""R-F3396 — the subject roster must stay broad, deduplicated, and single-sourced.

The corpus was 116 challenge rows against 11 single-hop rows, over subjects that
were overwhelmingly UK and Russian. A model trained on that learns two
registries and one conversational reflex, not the skill underneath.

These tests pin the properties that keep a widened roster honest as it grows:
one source of truth per axis, no duplicate subjects inflating a count, and real
jurisdictional spread. They deliberately do NOT assert any entity's sanctions
status — the corpus derives every verdict from the live tool payload, and a
test that hardcoded "X is designated" would be exactly the fabrication the
whole pipeline exists to prevent.
"""
from __future__ import annotations

import re

import pytest

from scripts.train import _subjects as S


ROSTERS = {
    "SANCTIONED": S.SANCTIONED,
    "LISTED_CLEAN": S.LISTED_CLEAN,
    "UK_REGISTRY_SUBJECTS": S.UK_REGISTRY_SUBJECTS,
    "AMBIGUOUS_SHORT": S.AMBIGUOUS_SHORT,
    "NEWS_SUBJECTS": S.NEWS_SUBJECTS,
    "INTERNATIONAL_PRIMES": S.INTERNATIONAL_PRIMES,
    "FINANCIAL_INSTITUTIONS": S.FINANCIAL_INSTITUTIONS,
    "STATE_OWNED_ENTERPRISES": S.STATE_OWNED_ENTERPRISES,
    "DESIGNATED_PERSONS": S.DESIGNATED_PERSONS,
    "DUAL_USE_TECH": S.DUAL_USE_TECH,
}


def test_every_roster_entry_is_a_usable_name():
    for name, roster in ROSTERS.items():
        assert roster, f"{name} is empty"
        for s in roster:
            assert isinstance(s, str) and s.strip(), f"{name} holds a blank entry"
            assert len(s) > 2, f"{name}: {s!r} is too short to resolve"


def test_no_roster_repeats_a_subject():
    """A duplicate inflates the row count without adding anything to learn."""
    for name, roster in ROSTERS.items():
        dupes = {s for s in roster if roster.count(s) > 1}
        assert not dupes, f"{name} repeats: {sorted(dupes)}"


def test_single_hop_roster_is_derived_not_a_second_copy():
    """The base axis must draw from the shared roster, or it silently drifts."""
    from scripts.train.build_tooluse_corpus import DEFAULT_SUBJECTS

    roster = S.single_hop_roster()
    assert DEFAULT_SUBJECTS == roster
    # and it must actually be the union, not one group
    assert set(S.SANCTIONED) <= set(roster)
    assert set(S.DESIGNATED_PERSONS) <= set(roster)
    assert set(S.STATE_OWNED_ENTERPRISES) <= set(roster)


def test_single_hop_roster_deduplicates_across_groups():
    """Groups overlap by design (a prime can be listed AND international)."""
    roster = S.single_hop_roster()
    assert len(roster) == len(set(roster))


def test_single_hop_roster_is_order_stable():
    """Row identity depends on subject order; a set-ordered roster would churn."""
    assert S.single_hop_roster() == S.single_hop_roster()


def test_roster_spans_more_than_uk_and_russia():
    """The breadth this R-number exists to add.

    Checked by naming several distinct regions' entities rather than by a
    country field the roster does not carry — a proxy, but a falsifiable one:
    deleting the widening would fail it.
    """
    roster = " | ".join(S.single_hop_roster())
    for probe in ("Saudi Aramco", "Petronas", "Petrobras", "Eskom",
                  "Hanwha Aerospace", "Embraer", "Aselsan",
                  "Qatar National Bank", "Itau Unibanco"):
        assert probe in roster, f"roster lost breadth: {probe} missing"


def test_person_axis_is_present_and_looks_like_people():
    """Screening a person is a different task shape from screening a company."""
    assert len(S.DESIGNATED_PERSONS) >= 10
    corporate = re.compile(r"\b(plc|ltd|limited|inc|corp|gmbh|s\.p\.a|ag|se)\b", re.I)
    for p in S.DESIGNATED_PERSONS:
        assert not corporate.search(p), f"{p!r} is a company, not a person"
        assert " " in p, f"{p!r} does not look like a personal name"


def test_news_roster_is_wider_than_the_original_eighteen():
    assert len(S.NEWS_SUBJECTS) >= 35
    assert len(set(S.NEWS_SUBJECTS)) == len(S.NEWS_SUBJECTS)


def test_uk_registry_roster_stays_uk_shaped():
    """The multi-hop chain walks Companies House; a non-UK name cannot resolve."""
    uk_shaped = re.compile(r"\b(plc|ltd|limited|llp|group)\b", re.I)
    for s in S.UK_REGISTRY_SUBJECTS:
        assert uk_shaped.search(s), f"{s!r} will not resolve in Companies House"


# --------------------------------------------------------------------------
# R-F3396 — a capture that yields nothing must not destroy a good corpus
# --------------------------------------------------------------------------

def test_zero_rows_refuses_to_overwrite_an_existing_corpus(tmp_path):
    """This bit during the R-F3396 capture and cost 23 real multi-hop chains.

    Companies House was unreachable (the API key was absent from the shell), so
    every subject SKIPped, the capture reported "wrote 0 validated traces", and
    the writer truncated a populated corpus to an empty file. Exit status was 0
    and the message read like success.
    """
    from scripts.train.build_tooluse_corpus import write_rows_guarded

    out = tmp_path / "corpus.jsonl"
    out.write_text('{"subject":"Acme"}\n{"subject":"Beta"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_rows_guarded(out, [])

    assert out.read_text(encoding="utf-8").count("\n") == 2, "the corpus survived"


def test_zero_rows_into_a_new_file_is_allowed(tmp_path):
    """A first capture that finds nothing is a legitimate empty result."""
    from scripts.train.build_tooluse_corpus import write_rows_guarded

    out = tmp_path / "new.jsonl"
    assert write_rows_guarded(out, []) == 0
    assert out.exists()


def test_explicit_override_can_still_empty_a_corpus(tmp_path):
    from scripts.train.build_tooluse_corpus import write_rows_guarded

    out = tmp_path / "corpus.jsonl"
    out.write_text('{"subject":"Acme"}\n', encoding="utf-8")
    assert write_rows_guarded(out, [], allow_shrink=True) == 0


def test_normal_write_replaces_content(tmp_path):
    from scripts.train.build_tooluse_corpus import write_rows_guarded

    out = tmp_path / "corpus.jsonl"
    out.write_text('{"subject":"Old"}\n', encoding="utf-8")
    assert write_rows_guarded(out, [{"subject": "New"}, {"subject": "Two"}]) == 2
    assert "New" in out.read_text(encoding="utf-8")
    assert "Old" not in out.read_text(encoding="utf-8")


def test_unavailable_axis_is_labelled_distinctly():
    """The "source is down, say so" axis must be visible to the stratifier.

    It reuses build_trace/build_challenge_trace and so inherited their labels,
    making 28 rows indistinguishable from ordinary ones. The split stratifies by
    label, so the whole honesty axis could land on one side and the eval would
    report nothing about the behaviour that matters most.
    """
    import json
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "data" / "training" / "aria_tooluse_unavailable_v1.jsonl"
    if not p.exists():
        pytest.skip("unavailable corpus not present in this checkout")
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows
    for r in rows:
        assert r["label"].endswith("_unavailable"), f"unlabelled axis: {r['label']}"


def test_every_axis_appears_on_both_sides_of_the_split():
    """A label present only in train is a capability the eval cannot speak to."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "data" / "training" / "split_v1"
    if not (root / "train.jsonl").exists():
        pytest.skip("split_v1 not present in this checkout")
    sides = {}
    for side in ("train", "eval"):
        sides[side] = {json.loads(l)["label"]
                       for l in (root / f"{side}.jsonl").read_text(encoding="utf-8").splitlines()
                       if l.strip()}
    assert sides["train"] == sides["eval"], (
        f"axes only in train: {sides['train'] - sides['eval']}; "
        f"only in eval: {sides['eval'] - sides['train']}"
    )

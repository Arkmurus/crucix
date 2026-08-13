"""R-F3964 / C-53 — an un-normalisable name is refused for the WRONG REASON,
which sends the operator to fix a store that is not broken.

`normalise_name` strips corporate suffixes and filler so what remains is entity
identity. When a name contains nothing else, nothing remains. Measured:

    'International Holdings Group'  -> ''
    'Trading Company Limited'       -> ''
    'Capital Partners LLC'          -> ''
    'Investment Holding Company'    -> ''
    'Industries International'      -> ''

With `q_entity_tokens` empty every scoring path collapses — `jaccard` is 0.0 on
an empty set, the containment guard reads `if q_entity_tokens else 0.0`, and the
token pre-filter loop `if q_entity_tokens:` never runs, so not one candidate is
fetched.

**TWO CORRECTIONS, and the second is to my own work.**

1. **This does NOT produce a false clean.** The 2026-08-13 diligence report
   implied it would. `check_sanctions` already falls through to a final `else`
   that returns INSUFFICIENT_DATA, so the never-false-clean invariant HOLDS.
   Verified before writing any fix: 7 of this file's 8 tests passed unchanged.
   What is actually wrong is the **reason string**: it reports
   `sanctions_store_empty_or_unavailable` on a store that is loaded and healthy.
   That is a wrong cause pointing at a wrong fix — the same class as the
   OpenSanctions `rate_limit` vs `quota_exhausted` conflation in CLAUDE.md §18,
   where the obstacle line told the reader ARIA was going too fast when the plan
   was simply spent. An operator chasing an "empty store" would find nothing
   wrong with it and never learn the query was unrepresentable.

2. **The example in the report and in the C-48 register entry was wrong.** Both
   cited `'Aerospace Industries Group'` as normalising to `''` with
   self-similarity 0.0. It does not — it yields `'aerospace'`. The mechanism is
   real; the example is not. The true trigger is a name with NO non-generic
   token at all.

The residual worry that example was reaching for is separate and NOT fixed here:
defence-sector names lose discriminative power ('Aviation Industry Corporation'
-> 'aviation'), which is a recall/precision question rather than a false clean.
"""
from __future__ import annotations

import os
import tempfile

_TMPDIR = tempfile.mkdtemp(prefix="rf3964_")
os.environ["ARIA_SANCTIONS_CANONICAL_DB"] = os.path.join(_TMPDIR, "canon.db")

from aria_service.intel.sanctions_canonical import store, lookup  # noqa: E402
from aria_service.intel.sanctions_canonical.normalise import (  # noqa: E402
    entity_tokens, normalise_name,
)

_SRC = "ofac_sdn"

ALL_GENERIC = [
    "International Holdings Group",
    "Trading Company Limited",
    "Capital Partners LLC",
    "Investment Holding Company",
    "Industries International",
]


def _seed(*names: str, source: str = _SRC):
    store.replace_source(source, [
        {
            "source_uid": f"uid-{i}", "formatted_name": n,
            "normalised_name": normalise_name(n), "entity_type": "Entity",
            "countries": [], "addresses": [], "programs": [],
            "designation_at": None, "raw_excerpt": "",
            "aliases": [{"formatted": n, "normalised": normalise_name(n),
                         "alias_type": "primary"}],
        }
        for i, n in enumerate(names)
    ])


def _reset():
    with store.connect() as conn:
        conn.execute("DELETE FROM entries")
        conn.execute("DELETE FROM aliases")
        conn.execute("DELETE FROM refresh_log")


# ── the premise, measured ────────────────────────────────────────────────────

def test_these_names_really_do_normalise_to_nothing():
    for n in ALL_GENERIC:
        assert normalise_name(n) == "", f"{n!r} -> {normalise_name(n)!r}"
        assert entity_tokens(normalise_name(n)) == set()


def test_the_report_example_was_wrong_and_this_pins_the_correction():
    """'Aerospace Industries Group' does NOT empty — keep the register honest."""
    assert normalise_name("Aerospace Industries Group") == "aerospace"
    assert entity_tokens("aerospace") == {"aerospace"}


# ── the defect ───────────────────────────────────────────────────────────────

def test_an_unnormalisable_query_is_not_cleared():
    _reset()
    _seed("Rosoboronexport")
    for n in ALL_GENERIC:
        res = lookup.check_sanctions(n)
        assert res["verdict"] != "CLEAR", (
            f"{n!r} normalises to '' so nothing was compared, yet the store "
            f"issued an authoritative CLEAR: {res}"
        )
        assert res["verdict"] == "INSUFFICIENT_DATA"
        assert res.get("reason") == "unnormalisable_name"


def test_the_reason_reaches_the_caller_so_it_can_render_could_not_verify():
    _reset()
    _seed("Rosoboronexport")
    res = lookup.check_sanctions("Trading Company Limited")
    assert res.get("source_unavailable") is True, (
        "callers key never-false-clean rendering on source_unavailable; without "
        "it a screen that compared nothing still reads as performed"
    )


def test_empty_and_whitespace_queries_also_refuse():
    _reset()
    _seed("Rosoboronexport")
    for n in ("", "   ", "Ltd", "The"):
        res = lookup.check_sanctions(n)
        assert res["verdict"] != "CLEAR", f"{n!r} -> {res}"


# ── it must still clear a real, screenable name ──────────────────────────────

def test_a_normal_name_still_clears():
    _reset()
    _seed("Rosoboronexport")
    res = lookup.check_sanctions("Zzqx Vellamin Torbraith")
    assert res["verdict"] == "CLEAR", res
    assert res.get("reason") != "unnormalisable_name"


def test_a_name_with_one_real_token_still_screens():
    """'Aerospace Industries Group' -> 'aerospace' is screenable, not refused."""
    _reset()
    _seed("Rosoboronexport")
    res = lookup.check_sanctions("Aerospace Industries Group")
    assert res.get("reason") != "unnormalisable_name"
    assert res["verdict"] == "CLEAR", res


def test_a_designated_generic_name_is_still_found_when_queried_exactly():
    """The refusal must not hide a real hit that the exact pass can still make."""
    _reset()
    _seed("Trading Company Limited")
    res = lookup.check_sanctions("Trading Company Limited")
    # It cannot be CLEAR either way — either an exact hit, or an honest refusal.
    assert res["verdict"] != "CLEAR", res

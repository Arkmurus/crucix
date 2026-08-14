"""R-F3984 / C-71 — generic sector words are stripped, so DISTINCT defence
companies collide into an EXACT sanctions match and are HARD_STOPped.

Reproduced end-to-end against a store holding one sanctioned "Aviation Group":

    Aviation Industry Corporation   -> HARD_STOP  matches=1  gate_blocked=0
    Aviation Holdings Limited       -> HARD_STOP  matches=1  gate_blocked=0
    Aviation Partners International -> HARD_STOP  matches=1  gate_blocked=0
        each matched 'Aviation Group'  method=exact  score=1.0

Three innocent, unrelated companies blocked. This is a FALSE POSITIVE on a
defence-DD product — the 2026-08-13 diligence report characterised it as
"recall/precision dilution", which understated it: it is a hard block on the
wrong entity, and `gate_blocked=0` shows the R-F518 gate did not even see it as
a question.

**Root cause.** `_STOPWORDS` conflates two categories:

  1. LEGAL-FORM tokens — ltd, llc, gmbh, plc, jsc, sa, oao … These genuinely
     carry no identity. Stripping them is correct and is why
     "JSC ROSOBORONEXPORT" matches "Rosoboronexport".
  2. GENERIC BUSINESS nouns — group, holdings, industries, international,
     trading, partners, capital … These are weak identity, but they are exactly
     what DISTINGUISHES "Aviation Group" from "Aviation Industry Corporation".

Strip category 2 and both names become the single token `aviation`. Then
`_evaluate_gate` rule (a) — "exact normalised-name equality" — returns True
IMMEDIATELY, bypassing every corroboration rule below it. An artifact of
stripping is granted the strongest verdict the system has.

**The fix is at that ONE grant point.** Exact normalised equality only counts as
an exact NAME match when the CONSERVATIVE forms (legal-form tokens stripped,
sector nouns kept) also agree. When they disagree, the equality is an artifact
and the candidate falls through to the remaining rules — jurisdiction, address,
or multi-token overlap ≥2. For a bare shared sector word that means the gate
BLOCKS, and per C-48 a gate-blocked near-miss surfaces as REVIEW: a human
decides, which is the correct answer for "two companies share the word
aviation".

It cannot manufacture a FALSE CLEAN. Falling through does not drop the
candidate — it is still scored, still gated, and still lands in `gate_blocked`
for the audit trail. The only thing removed is the shortcut to HARD_STOP.
"""
from __future__ import annotations

import os
import tempfile

_TMPDIR = tempfile.mkdtemp(prefix="rf3984_")
os.environ["ARIA_SANCTIONS_CANONICAL_DB"] = os.path.join(_TMPDIR, "canon.db")

from aria_service.intel.sanctions_canonical import store, lookup  # noqa: E402
from aria_service.intel.sanctions_canonical.normalise import (  # noqa: E402
    normalise_name, normalise_name_conservative,
)

_SRC = "ofac_sdn"


def _seed(*names: str):
    store.replace_source(_SRC, [
        {"source_uid": f"u{i}", "formatted_name": n,
         "normalised_name": normalise_name(n), "entity_type": "Entity",
         "countries": [], "addresses": [], "programs": [],
         "designation_at": None, "raw_excerpt": "",
         "aliases": [{"formatted": n, "normalised": normalise_name(n),
                      "alias_type": "primary"}]}
        for i, n in enumerate(names)
    ])


def _reset():
    with store.connect() as conn:
        conn.execute("DELETE FROM entries")
        conn.execute("DELETE FROM aliases")
        conn.execute("DELETE FROM refresh_log")


# ── the collision, measured ──────────────────────────────────────────────────

def test_distinct_defence_names_collide_under_aggressive_normalisation():
    """Pin the premise. This is WHY the gate needs a second opinion."""
    for a, b in (
        ("Aviation Industry Corporation", "Aviation Group"),
        ("Aerospace Industries Group", "Aerospace Holdings"),
        ("Marine Industries Company", "Marine Group Holdings"),
    ):
        assert normalise_name(a) == normalise_name(b), (a, b)


def test_the_conservative_form_tells_them_apart():
    assert normalise_name_conservative("Aviation Industry Corporation") \
        != normalise_name_conservative("Aviation Group")
    assert normalise_name_conservative("Aerospace Industries Group") \
        != normalise_name_conservative("Aerospace Holdings")


def test_the_conservative_form_still_matches_a_TRUE_exact_pair():
    """Legal-form tokens must still be stripped, or JSC/Ltd stop matching."""
    for a, b in (
        ("Rosoboronexport", 'JSC ROSOBORONEXPORT'),
        ("Rosoboronexport Ltd", "Rosoboronexport"),
        ("Wagner Group", "Wagner Group"),
        ("Bank of Russia", "Bank of Russia"),
    ):
        assert normalise_name_conservative(a) == normalise_name_conservative(b), (a, b)


def test_conservative_keeps_sector_nouns_and_drops_legal_form():
    c = normalise_name_conservative("Aviation Industry Corporation Ltd")
    assert "aviation" in c and "industry" in c
    assert "ltd" not in c and "corporation" not in c


# ── the capability test: the false positive is gone ──────────────────────────

def test_an_innocent_company_is_no_longer_HARD_STOPped():
    _reset()
    _seed("Aviation Group")
    for query in ("Aviation Industry Corporation",
                  "Aviation Holdings Limited",
                  "Aviation Partners International"):
        res = lookup.check_sanctions(query)
        assert res["verdict"] != "HARD_STOP", (
            f"{query!r} was HARD_STOPped against a different company that merely "
            f"shares the word 'aviation': {res}"
        )


def test_but_it_is_NOT_silently_cleared_either():
    """A shared sector word with a designated entity is a REVIEW, not a clean."""
    _reset()
    _seed("Aviation Group")
    res = lookup.check_sanctions("Aviation Industry Corporation")
    assert res["verdict"] != "CLEAR", (
        f"the collision was dropped instead of surfaced — a name-overlapping "
        f"designation must reach a human, not vanish: {res}"
    )
    assert res.get("matches") or res.get("gate_blocked"), (
        "the candidate left no audit trail at all"
    )


def test_the_REAL_sanctioned_entity_is_still_caught():
    """The whole point. Never trade a false positive for a false clean."""
    _reset()
    _seed("Aviation Group")
    res = lookup.check_sanctions("Aviation Group")
    assert res["verdict"] == "HARD_STOP", res
    assert res.get("matches")


def test_a_true_alias_match_still_hard_stops():
    """JSC/Ltd stripping is the legitimate case and must be untouched."""
    _reset()
    _seed("JSC ROSOBORONEXPORT")
    for q in ("Rosoboronexport", "Rosoboronexport Ltd", "ROSOBORONEXPORT"):
        res = lookup.check_sanctions(q)
        assert res["verdict"] == "HARD_STOP", (q, res)


def test_an_unrelated_name_still_clears():
    _reset()
    _seed("Aviation Group")
    assert lookup.check_sanctions("Zzqx Vellamin Torbraith")["verdict"] == "CLEAR"


def test_jurisdiction_corroboration_still_promotes_a_collision():
    """The gate's OTHER rules must still work — a shared token PLUS a matching
    jurisdiction is real corroboration and should not be suppressed."""
    _reset()
    store.replace_source(_SRC, [{
        "source_uid": "u0", "formatted_name": "Aviation Group",
        "normalised_name": normalise_name("Aviation Group"),
        "entity_type": "Entity", "countries": ["RU"], "addresses": [],
        "programs": [], "designation_at": None, "raw_excerpt": "",
        "aliases": [{"formatted": "Aviation Group",
                     "normalised": normalise_name("Aviation Group"),
                     "alias_type": "primary"}],
    }])
    res = lookup.check_sanctions("Aviation Industry Corporation",
                                 jurisdiction="RU")
    assert res["verdict"] != "CLEAR", res


# ── the grant point must carry the check ─────────────────────────────────────

def test_the_gate_consults_the_conservative_form():
    from ._source_probe import function_code
    src = function_code(lookup, "_evaluate_gate")
    assert "conservative" in src.lower(), (
        "rule (a) grants an exact-name pass on aggressive equality alone — a "
        "stripping artifact will be HARD_STOPped again"
    )

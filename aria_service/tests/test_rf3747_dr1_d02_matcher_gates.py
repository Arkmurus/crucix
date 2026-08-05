"""R-F3747 — DR-1 **D-02 adjudicated**: the register pointed at a dormant matcher.

D-02 was UNADJUDICATED: "Matcher surname / dataset gates", P0, suspected
`lib/aria/entityMatcher.mjs` (noted "DORMANT, 311 LOC, test-only reach"), no
fixture. Third entry adjudicated from this repo (after D-03/R-F3745 and
D-05/R-F3746) rather than from the missing register.

TWO FINDINGS.

1. THE SUSPECTED LOCATION IS DORMANT, and the register's own note was right.
   `lib/aria/entityMatcher.mjs` is imported by exactly one file — its own test,
   `test/test_entity_matcher.mjs`. Zero production reach, so the defect cannot
   manifest there. Adjudicating D-02 against that module would have proved
   nothing about production, which is the trap of a suspected location.

2. THE LIVE MATCHER GATES SURNAME-ONLY MATCHES. Production entity screening runs
   through `aria_service/intel/_sanctions_classify.py`. Measured 2026-08-05, a
   surname-only coincidence between unrelated people is DEMOTED to `info`, while
   genuine matches and transliteration variants still escalate:

     Ivan Petrov   vs PETROV, Sergei Vladimirovich   overlap=1 -> info
     Ahmed Hussein vs HUSSEIN, Saddam                overlap=1 -> info
     Vladimir Putin vs Vladimir Vladimirovich Putin  overlap=2 -> hard_stop
     Rosoboronexport vs ROSOBORONEKSPORT OAO         overlap=0 -> hard_stop

   So the invariant D-02 asks for holds on the path that actually runs.

A HYPOTHESIS THIS FALSIFIED, recorded because it looked right on a code read: the
meaningful-token filter (`:680-687`) excludes stopwords, corporate suffixes,
digits and GEOGRAPHIC tokens (R-F277) but NOT common surnames, and the demotion
rule tolerates a single shared token >=5 chars — so "Petrov" (6) appeared able to
sustain a match alone. It cannot: the single-token path ALSO requires score and
string-similarity thresholds that a weak surname coincidence does not reach. The
code read suggested a defect; the measurement disproved it. Hence these are
behavioural assertions against the classifier, not grep assertions about it.

Run: python -m pytest aria_service/tests/test_rf3747_dr1_d02_matcher_gates.py -v
"""
from __future__ import annotations

import subprocess

import pytest

from aria_service.intel import _sanctions_classify as sc
from ._source_probe import repo_path


def _m(name: str, score: float, sim: float, lists=("us_ofac_sdn",)) -> dict:
    """A match shaped the way the live screen delivers one.

    `topics` is load-bearing: omit it and classify_match returns `info` for
    EVERYTHING, including a genuine Putin/OFAC hit. A first version of this probe
    did omit it and read as "no escalation anywhere" — a green result that meant
    only that the probe was malformed.
    """
    return {"name": name, "score": score, "string_similarity": sim,
            "topics": ["sanction"], "lists": list(lists)}


@pytest.mark.parametrize("query,candidate", [
    ("Ivan Petrov", "PETROV, Sergei Vladimirovich"),
    ("Ahmed Hussein", "HUSSEIN, Saddam"),
])
def test_a_shared_surname_alone_does_not_escalate(query, candidate):
    """THE D-02 INVARIANT: a common surname is not identity evidence."""
    assert sc._name_overlap(query, candidate) == 1, (
        "test premise broken: these should share exactly one token (the surname)"
    )
    verdict = sc.classify_match(_m(candidate, 0.62, 0.55), query)
    assert verdict == "info", (
        f"{query!r} vs {candidate!r} classified {verdict!r} on a SURNAME-ONLY "
        f"overlap. Two unrelated people sharing a surname is not identity "
        f"evidence, and escalating it produces a false sanctions hit — the "
        f"defamation / SAR-misfiling cost the classifier's own comments cite."
    )


def test_a_genuine_match_still_escalates():
    """Negative control: without this, a classifier that demoted EVERYTHING would
    satisfy the assertions above while destroying screening."""
    verdict = sc.classify_match(
        _m("Vladimir Vladimirovich Putin", 0.90, 0.85), "Vladimir Putin")
    assert verdict == "hard_stop", (
        f"a genuine multi-token OFAC match classified {verdict!r} — the gate has "
        f"been widened into a false-negative machine"
    )


def test_a_transliteration_variant_still_escalates():
    """R-F569's bypass: real variants tokenise to NON-overlapping sets."""
    q, cand = "Rosoboronexport", "ROSOBORONEKSPORT OAO"
    assert sc._name_overlap(q, cand) == 0, "premise: these share no tokens"
    verdict = sc.classify_match(_m(cand, 0.97, 0.90), q)
    assert verdict == "hard_stop", (
        f"transliteration variant classified {verdict!r}; the token-overlap "
        f"discipline must not suppress a near-exact score (R-F569/R-F569.5)"
    )


def test_the_dormant_matcher_is_still_dormant():
    """D-02's suspected location has ZERO production reach.

    If entityMatcher.mjs is ever wired into production, D-02 must be
    re-adjudicated against it — the gates proven above live in the PYTHON
    classifier and say nothing about the .mjs implementation.
    """
    out = subprocess.run(
        ["git", "grep", "-l", "entityMatcher", "--", "*.mjs", "*.js"],
        cwd=str(repo_path(".")), capture_output=True, text=True,
    ).stdout.split()
    importers = [f for f in out if not f.startswith("test/")
                 and "entityMatcher.mjs" not in f]
    assert not importers, (
        f"lib/aria/entityMatcher.mjs is now reached from production code: "
        f"{importers}. D-02 was adjudicated on the basis that this module is "
        f"test-only; re-adjudicate against it before relying on that."
    )

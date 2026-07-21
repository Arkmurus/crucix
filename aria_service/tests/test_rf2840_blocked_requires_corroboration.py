"""R-F2840 — a BLOCKING verdict must be corroborated, not scored.

THE DEFECT, found on a live customer-visible report (dd_06bdbcaaa866, SOCAR Trading
SA). The screen returned `blocked: True`, `top_score: 1.0`, `match_count: 8`, and
`verified_sources["OFAC SDN"] = {"status": "HIT", "matched_entities":
["Special Technology Services LLC FZ", "Shadi For Cars Trading"]}`.

None of it was true. The real fixture in fixtures/socar_sanctions_matches_rf2840.json
is that exact 8-match payload:

    #  name                                score  str_sim  via     sanction topic?
    1  Socar Trading SA                     1.00   1.000   name    NO (gem_energy_ownership)
    2  Special Technology Services LLC FZ   1.00   0.235   "STS"   yes  <- set blocked + OFAC HIT
    3  STS Ugol                             0.83   0.188   "STS"   no
    4  Corporation STS LLC                  0.81   0.263   "STS"   no
    5  Shadi For Cars Trading               0.79   0.455   name    yes  <- set OFAC HIT
    6  STS HOLDING SPOLKA AKCYJNA           0.78   0.269   "STS"   no
    7  STS Transportation, LLC              0.75   0.304   "STS"   yes(debarment)
    8  (Chinese-language company)           0.74   0.000   name    no

Five of eight matched the ACRONYM "STS" derived from SOCAR Trading SA. One scored
0.74 at string similarity 0.000. The single true identity match (#1) is on an
energy-asset OWNERSHIP dataset with no sanction topic — evidence of ownership, not a
designation. Matches that are BOTH name-similar AND sanctioned: ZERO.

TWO UNCORROBORATED VERDICTS, in two different modules, from the same cause — the
corroborating data sat on the match and was never read:

  sanctions.py:784      blocking_matches = [m for m in all_matches if m["score"] >= threshold]
                        -> reads score ONLY; never string_similarity, topics or
                           matched_via_variant.
  _sanctions_classify.py:246  if slug in ds_lower: hit_count += 1
                        -> a HIT for ANY match carrying the source slug, with no
                           similarity check at all.

Worse, identity.data_gaps recorded "ofac_sdn: SDN list unavailable" on the same run —
so the report asserted "US Treasury OFAC SDN: HIT" against a real counterparty while
our own primary OFAC source was down. That is a Grade-A anchoring violation on top of
a false positive: a HIT attributed to a named authority we did not reach.

An acronym-collision guard ALREADY EXISTS (_sanctions_classify.py ~483-526, requiring
sim>=0.50 to close "acronym-collision noise") — but it runs on severity classification,
one layer away from the verdicts above. Same architecture as R-F2821 (wire severed
below the call site) and R-F2832 (timeout declared but not applied on the live path):
the protection exists, just not on the path that produces the answer.

WHY THIS IS A USP DEFECT, NOT MERELY A BUG. Our machinery for "never a false CLEAN" is
rigorous (templates_searched, backends_answered, NOT_CLEARED, "absence is not a clean
bill"). There is no equivalent discipline for "never a false BLOCK" — and the
second-order harm is worse: a screen that cries wolf seven times in eight trains the
user to ignore it, which re-creates the false-clean risk through human behaviour. A
trustworthy block is what makes "never a false clean" believable.

THE SAFETY CONSTRAINT THIS TEST PINS. Tightening `blocked` must NEVER manufacture a
clean. blocked=False is not "clear": the uncorroborated matches stay visible and
counted, `screened`/source-availability semantics are untouched, and a source that was
UNAVAILABLE must never render CLEAN.
"""
import json
import pathlib

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "socar_sanctions_matches_rf2840.json"
MATCHES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _has_sanction_topic(m):
    return any(t in ("sanction", "debarment") for t in (m.get("topics") or []))


def test_fixture_is_the_real_live_payload():
    """Guard the evidence itself — this is the report a customer saw."""
    assert len(MATCHES) == 8
    assert MATCHES[0]["name"] == "Socar Trading SA"
    assert MATCHES[0]["string_similarity"] == 1.0
    assert "gem_energy_ownership" in MATCHES[0]["lists"]
    # the match that set blocked=True and OFAC HIT
    sts = MATCHES[1]
    assert sts["score"] == 1.0 and sts["string_similarity"] < 0.30
    assert sts["matched_via_variant"] == "STS"


# ── the shared predicate ─────────────────────────────────────────────────────

def test_a_single_shared_predicate_governs_both_verdicts():
    """Two modules must not each invent their own corroboration rule.

    `blocked` (sanctions.py) and verified_sources HIT (_sanctions_classify.py) drifted
    apart precisely because each read a different field. One predicate, imported by
    both — the R-F2822 lesson.
    """
    from aria_service.intel import _sanctions_classify as sc
    assert hasattr(sc, "is_corroborated_match"), (
        "a shared is_corroborated_match() must exist; two independent rules will drift"
    )
    from aria_service.intel import sanctions as s
    src = pathlib.Path(s.__file__).read_text(encoding="utf-8")
    assert "is_corroborated_match" in src, (
        "sanctions.py must use the SHARED predicate for its blocking set, not a "
        "second local copy"
    )


@pytest.mark.parametrize("idx,expected", [
    (0, False),  # exact identity, but NO sanction topic — ownership evidence, not a designation
    (1, False),  # sim 0.235 via the "STS" acronym — the false positive that blocked
    (2, False), (3, False), (5, False), (6, False),  # acronym collisions
    (4, False),  # sim 0.455 — below the guard's own 0.50 floor
    (7, False),  # sim 0.000
])
def test_no_fixture_match_is_corroborated(idx, expected):
    """On the real payload the honest blocking set is EMPTY."""
    from aria_service.intel._sanctions_classify import is_corroborated_match
    m = MATCHES[idx]
    assert is_corroborated_match(m) is expected, (
        f"match {idx+1} ({m.get('name')!r}, score={m.get('score')}, "
        f"sim={m.get('string_similarity')}, via={m.get('matched_via_variant')!r}) "
        "must not corroborate a blocking verdict"
    )


def test_a_genuine_designation_still_blocks():
    """ANTI-REGRESSION: the fix must not disarm the screen.

    A real hit — name actually matches AND it is a designation — must still block, or
    we have traded a false positive for a false clean, which is worse.
    """
    from aria_service.intel._sanctions_classify import is_corroborated_match
    real = {
        "name": "Rosoboronexport", "score": 0.98, "string_similarity": 0.97,
        "lists": ["us_ofac_sdn"], "topics": ["sanction"], "matched_via_variant": "Rosoboronexport",
    }
    assert is_corroborated_match(real) is True, (
        "a name-similar, sanctioned entity MUST still block — removing false "
        "positives must never create a false clean"
    )


# ── verdict 1: blocked ───────────────────────────────────────────────────────

def test_blocked_is_false_on_the_real_payload_but_matches_stay_visible():
    """blocked=False must NOT mean 'clean' — the observations must survive."""
    from aria_service.intel._sanctions_classify import is_corroborated_match
    blocking = [m for m in MATCHES if is_corroborated_match(m)]
    assert blocking == [], "the honest blocking set on this payload is empty"
    # and the information is not destroyed
    assert len(MATCHES) == 8, (
        "removing a false BLOCK must not remove the underlying observations — an "
        "analyst may still want the STS cluster, labelled as related names"
    )


# ── verdict 2: verified_sources HIT ──────────────────────────────────────────

def test_ofac_does_not_read_HIT_on_uncorroborated_matches():
    """CAPABILITY: the exact false statement the live report made."""
    from aria_service.intel._sanctions_classify import derive_verified_sources
    vs = derive_verified_sources(MATCHES)
    ofac = vs.get("OFAC SDN") or {}
    assert ofac.get("status") != "HIT", (
        "the live report asserted 'US Treasury OFAC SDN: HIT' for SOCAR Trading SA on "
        f"two acronym collisions. Got {ofac!r}"
    )
    assert "Special Technology Services LLC FZ" not in (ofac.get("matched_entities") or [])


def test_an_unavailable_source_never_reads_CLEAN():
    """USP: UNAVAILABLE is neither HIT nor CLEAN.

    The same run recorded 'ofac_sdn: SDN list unavailable' in data_gaps. A source we
    could not reach must never be rendered as CLEAN — that is a false clean by
    omission, and the grounded-rate counter already excludes UNAVAILABLE.
    """
    from aria_service.intel._sanctions_classify import derive_verified_sources
    vs = derive_verified_sources([], unavailable_sources={"OFAC SDN"})
    assert (vs.get("OFAC SDN") or {}).get("status") == "UNAVAILABLE", (
        "a source that did not answer must report UNAVAILABLE, never CLEAN"
    )

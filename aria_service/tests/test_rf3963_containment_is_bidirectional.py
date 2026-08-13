"""R-F3963 / C-52 — containment was measured in ONE direction, so a long query
against a short designation was dropped before the gate could see it.

R-F3691 added containment scoring because Jaccard is symmetric while the
relationship is not — a SHORT query against a LONG listed name is penalised by
every token the listing adds. It fixed that direction only:

    lookup.py:552
        _containment = len(q_entity_tokens & cand_entity_tokens) / len(q_entity_tokens)

The mirror case is just as real and is the one a DD actually produces, because
users paste the full legal name from a document. Measured:

    query     'Rosoboronexport JSC Moscow Representative Office'
              -> entity tokens {moscow, office, representative, rosoboronexport}
    listing   'Rosoboronexport'
              -> entity tokens {rosoboronexport}

    jaccard              0.25
    containment forward  0.25   <- the only one computed
    containment reverse  1.00   <- the listing is FULLY inside the query

`_JACCARD_FLOOR` is 0.5, so 0.25 fell below it and the candidate was `continue`d
before `_evaluate_gate` ever ran. It therefore reached neither `matches` nor
`gate_blocked` — invisible even to the audit trail, which is worse than a
blocked near-miss because nothing records that it was considered.

The fix is the overlap coefficient — max of both directions, i.e.
|q ∩ c| / min(|q|, |c|) — which asks "is either name fully present in the
other?". R-F3691's own argument for admitting more candidates applies unchanged:
`_evaluate_gate` (R-F518) is the component designed to reject coincidences and
still runs on everything admitted here, and since C-48 a gate-blocked near-miss
surfaces as REVIEW rather than being silently dropped.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

_TMPDIR = tempfile.mkdtemp(prefix="rf3963_")
os.environ["ARIA_SANCTIONS_CANONICAL_DB"] = os.path.join(_TMPDIR, "canon.db")

from aria_service.intel.sanctions_canonical import store, lookup  # noqa: E402
from aria_service.intel.sanctions_canonical.normalise import (  # noqa: E402
    entity_tokens, normalise_name,
)

_SRC = "ofac_sdn"


def _seed(*names: str, source: str = _SRC):
    """Seed through the REAL loader path.

    `replace_source` is what every production loader calls, and it also
    populates the `aliases` table — which matters here because the token
    pre-filter searches `aliases`, not `entries`. Hand-inserting into `entries`
    produces a store no candidate can ever be found in, and a fixture like that
    would make this whole file pass or fail for the wrong reason.
    """
    store.replace_source(source, [
        {
            "source_uid": f"uid-{i}",
            "formatted_name": n,
            "normalised_name": normalise_name(n),
            "entity_type": "Entity",
            "countries": [], "addresses": [], "programs": [],
            "designation_at": None, "raw_excerpt": "",
            "aliases": [{
                "formatted": n,
                "normalised": normalise_name(n),
                "alias_type": "primary",
            }],
        }
        for i, n in enumerate(names)
    ])


def _reset():
    with store.connect() as conn:
        conn.execute("DELETE FROM entries")
        conn.execute("DELETE FROM aliases")
        conn.execute("DELETE FROM refresh_log")


# ── the measurement that names the defect ────────────────────────────────────

def test_the_asymmetry_is_real():
    q = entity_tokens(normalise_name("Rosoboronexport JSC Moscow Representative Office"))
    c = entity_tokens(normalise_name("Rosoboronexport"))
    inter = q & c
    assert len(inter) == 1
    assert round(len(inter) / len(q), 2) == 0.25          # forward — below the 0.5 floor
    assert len(inter) / len(c) == 1.0                     # reverse — a perfect containment
    assert lookup._JACCARD_FLOOR == 0.5, "the floor is what made 0.25 invisible"


def test_overlap_coefficient_sees_both_directions():
    q = {"moscow", "office", "representative", "rosoboronexport"}
    c = {"rosoboronexport"}
    assert lookup._overlap_coefficient(q, c) == 1.0
    assert lookup._overlap_coefficient(c, q) == 1.0, "it must be symmetric"


def test_overlap_coefficient_is_zero_on_no_intersection():
    assert lookup._overlap_coefficient({"a"}, {"b"}) == 0.0
    assert lookup._overlap_coefficient(set(), {"b"}) == 0.0
    assert lookup._overlap_coefficient({"a"}, set()) == 0.0


# ── the capability test ──────────────────────────────────────────────────────

def test_long_query_against_short_designation_is_not_cleared():
    """The exact live case. Pre-fix this returned CLEAR with gate_blocked empty."""
    _reset()
    _seed("Rosoboronexport")

    res = lookup.check_sanctions("Rosoboronexport JSC Moscow Representative Office")
    assert res["verdict"] != "CLEAR", (
        "a designated entity's own brand token, fully contained in the query, "
        f"screened CLEAN: {res}"
    )
    # It must at minimum be VISIBLE — either a match or an auditable near-miss.
    assert res.get("matches") or res.get("gate_blocked"), (
        "the candidate was dropped before the gate, so nothing records that it "
        "was ever considered"
    )


def test_short_query_against_long_designation_still_works():
    """R-F3691's original direction must be preserved."""
    _reset()
    _seed("Rosoboronexport Federal State Unitary Enterprise Defence Export Agency")

    res = lookup.check_sanctions("Rosoboronexport")
    assert res["verdict"] != "CLEAR", res


# ── precision: it must still be able to say CLEAR ────────────────────────────

def test_an_unrelated_entity_still_clears():
    _reset()
    _seed("Rosoboronexport")
    res = lookup.check_sanctions("Zzqx Vellamin Torbraith Holdings")
    assert res["verdict"] == "CLEAR", (
        f"widening admission must not make everything a hit: {res}"
    )


def test_a_single_shared_generic_token_does_not_manufacture_a_hit():
    """The cost of the reverse direction, bounded.

    A one-token designation that happens to appear in a long query is admitted
    to the gate — which is correct, that is what the gate is for — but the gate
    must not turn it into a clearance-blocking match without corroboration.
    """
    _reset()
    _seed("Aerospace")            # a deliberately generic single-token listing
    res = lookup.check_sanctions("Northbridge Aerospace Manufacturing Division")
    assert res["verdict"] in ("REVIEW", "CLEAR", "INSUFFICIENT_DATA"), res
    assert res["verdict"] != "HARD_STOP", (
        "a bare generic token overlap must never reach HARD_STOP without "
        f"corroboration: {res}"
    )


def test_exact_name_still_hard_stops():
    """The strongest signal must survive the change."""
    _reset()
    _seed("Rosoboronexport")
    res = lookup.check_sanctions("Rosoboronexport")
    assert res["verdict"] in ("HARD_STOP", "REVIEW"), res
    assert res["verdict"] != "CLEAR"


def test_the_gate_reads_the_bidirectional_score():
    from ._source_probe import function_code
    src = function_code(lookup, "check_sanctions")
    assert "_overlap_coefficient(" in src, (
        "containment is one-directional again — a long query against a short "
        "designation will be dropped before the gate"
    )

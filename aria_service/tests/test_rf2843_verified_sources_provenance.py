"""R-F2843 — verified_sources must say HOW a list was cleared.

WHY. On live run dd_06bdbcaaa866 (SOCAR Trading SA) the report handed the reader two
true OFAC facts, in two different sections, and left them to reconcile it:

    verified_sources["OFAC SDN"] -> CLEAN
    identity.data_gaps           -> "ofac_sdn: SDN list unavailable"

Both are correct. OpenSanctions queried its OFAC dataset and found nothing (a real
screen), while ARIA's OWN direct ofac_sdn.py snapshot failed to load (a different
check). But a compliance officer reading "OFAC SDN: CLEAN" cannot tell which of the two
produced it.

WHAT THIS IS NOT. It is NOT a status change. R-F287 deliberately stopped the renderer
fabricating "NOT CHECKED" on sources OpenSanctions HAD queried, because an aggregator's
clean response means "all underlying sources queried, none hit". Flipping OFAC to
UNAVAILABLE because a separate adapter failed would REVERSE R-F287 and re-introduce
that fabrication. The status stays CLEAN; we add the provenance that makes CLEAN
interpretable.

USP. This is the difference between our report and the competitor's on the same entity:
NorthRow printed "No match found on the negative registers — Score 0" with no statement
of how it knew. Grade A means an anchored answer, so "cleared via the OpenSanctions
aggregate, our primary snapshot was down this run" is strictly more honest than a bare
CLEAN — and it is a qualification a customer can act on.

ABSENCE RULE. `primary_snapshot` is stamped ONLY where the direct-adapter result is
genuinely known. Its ABSENCE means "not asserted" — never "available". Defaulting it to
available would be a confident claim about a check we did not observe, which is the
false-clean family this product refuses.
"""
import pytest

from aria_service.intel._sanctions_classify import (
    derive_verified_sources,
    annotate_primary_snapshots,
)


CLEAN_MATCHES: list = []
OFAC_HIT = [{
    "name": "Rosoboronexport JSC", "score": 0.95, "string_similarity": 0.97,
    "lists": ["us_ofac_sdn"], "topics": ["sanction"],
}]


def test_every_source_states_how_it_was_screened():
    """A bare CLEAN is not decision-grade — say what produced it."""
    vs = derive_verified_sources(CLEAN_MATCHES)
    assert vs, "expected canonical sources"
    for name, entry in vs.items():
        assert entry.get("via") == "opensanctions_aggregate", (
            f"{name} reports status={entry.get('status')!r} with no `via` — the reader "
            "cannot tell which screen produced it"
        )


def test_a_failed_screen_says_so_rather_than_claiming_an_aggregate():
    """When the whole screen failed, `via` must not claim the aggregate answered."""
    vs = derive_verified_sources(CLEAN_MATCHES, screen_succeeded=False)
    for name, entry in vs.items():
        assert entry["status"] == "UNAVAILABLE"
        assert entry.get("via") != "opensanctions_aggregate", (
            f"{name} is UNAVAILABLE but claims it was screened via the aggregate"
        )


def test_status_semantics_are_unchanged_R_F287_preserved():
    """ANTI-REGRESSION: this adds provenance, it must not move any status."""
    vs = derive_verified_sources(OFAC_HIT)
    assert vs["OFAC SDN"]["status"] == "HIT"
    assert vs["OFAC SDN"]["match_count"] == 1
    # a source OpenSanctions queried and did not hit stays CLEAN — R-F287's whole point
    assert vs["UK OFSI / HMT"]["status"] == "CLEAN"


# ── primary_snapshot annotation ──────────────────────────────────────────────

def test_primary_snapshot_is_absent_until_asserted():
    """Absence means 'not asserted', never 'available'."""
    vs = derive_verified_sources(CLEAN_MATCHES)
    assert "primary_snapshot" not in vs["OFAC SDN"], (
        "an unstamped source must not imply its primary snapshot was live"
    )


def test_annotating_an_unavailable_primary_snapshot_keeps_status_clean():
    """The live SOCAR case, rendered honestly."""
    vs = derive_verified_sources(CLEAN_MATCHES)
    annotate_primary_snapshots(vs, {"ofac_sdn": "unavailable"})
    ofac = vs["OFAC SDN"]
    assert ofac["status"] == "CLEAN", (
        "status must NOT flip — reversing it would re-introduce the 'NOT CHECKED' "
        "fabrication R-F287 removed"
    )
    assert ofac["primary_snapshot"] == "unavailable"
    assert ofac["via"] == "opensanctions_aggregate", (
        "the reader must be able to see: cleared by the aggregate, primary snapshot down"
    )


def test_annotation_maps_adapter_labels_to_canonical_sources():
    """The direct adapters use their own slugs; the map must be explicit."""
    vs = derive_verified_sources(CLEAN_MATCHES)
    annotate_primary_snapshots(vs, {"uk_ofsi": "ok", "un_sc": "unavailable"})
    assert vs["UK OFSI / HMT"].get("primary_snapshot") == "ok"
    assert vs["UN SC Consolidated"].get("primary_snapshot") == "unavailable"


def test_annotation_ignores_unknown_labels_and_never_raises():
    """A new adapter slug must not crash a report mid-render."""
    vs = derive_verified_sources(CLEAN_MATCHES)
    annotate_primary_snapshots(vs, {"some_future_adapter": "ok", "acled": "unavailable"})
    assert vs["OFAC SDN"]["status"] == "CLEAN"          # untouched
    assert "primary_snapshot" not in vs["OFAC SDN"]      # not asserted


def test_annotation_is_a_noop_on_a_missing_or_malformed_screen():
    """Must never raise into the DD when the screen isn't built yet."""
    annotate_primary_snapshots(None, {"ofac_sdn": "unavailable"})   # no screen yet
    annotate_primary_snapshots({}, None)
    annotate_primary_snapshots({"OFAC SDN": "not-a-dict"}, {"ofac_sdn": "ok"})

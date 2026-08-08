"""R-F3566 — the positive-register promotion was INERT by ordering, on every report.

R-F3553 promoted T1 register credentials from `report.adverse_media["findings"]`, read
inside `_run_synthesis`. But `adverse_media` is a `field(default_factory=dict)` that is
not assigned until AFTER synthesis returns — R-F2657 decoupled the deep sweep into an
out-of-band follow-up. So the consumer ran before its producer every time: `{}` ->
`.get("findings")` -> None -> an empty list -> no findings, ever.

Measured across six captured reports: `adverse_media` was `{"status": "in_progress"}`
in all six, and the input list was empty in all six.

This is the R-F3504/R-F3515 class again — code that reads correctly and cannot run — so
these tests assert REACHABILITY and REAL OUTPUT, never the presence of a call.
"""
from __future__ import annotations

import types

import pytest

from aria_service.intel import dd_orchestrator as dd

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def _report(press_coverage=None, adverse=None):
    return types.SimpleNamespace(
        digital=types.SimpleNamespace(press_coverage=press_coverage or []),
        adverse_media=adverse if adverse is not None else {},
    )


_SIA = "https://services.sia.homeoffice.gov.uk/Pages/acs-detail.aspx?id=1234"


# ── the defect: the producer was empty at the moment of the read ──────────────

def test_the_old_producer_is_empty_at_synthesis_time():
    """PROVE RED. At synthesis `adverse_media` is the dataclass default, so the
    R-F3553 read could only ever yield nothing."""
    rep = _report(adverse={})
    assert (rep.adverse_media or {}).get("findings") is None

    # and the shape the live follow-up leaves before completing
    rep2 = _report(adverse={"status": "in_progress", "framework_version": "R-F2657"})
    assert (rep2.adverse_media or {}).get("findings") is None


def test_the_new_producer_is_populated_at_synthesis_time():
    """CAPABILITY: the fix is that there is now real input to scan."""
    rep = _report(press_coverage=[
        {"url": "https://www.reuters.com/x", "source": "Acme PLC | Reuters", "snippet": "s"},
        {"url": _SIA, "source": "Acme PLC — Approved Contractor", "snippet": "s"},
    ])
    rows = dd._positive_source_rows(rep)
    assert len(rows) == 2, f"the scan received no input: {rows}"
    assert all(r["url"].startswith("http") for r in rows)


# ── the field-mapping trap that would have made the fix silently inert ────────

def test_press_coverage_headline_is_mapped_into_title():
    """`press_coverage` rows carry the headline in `source`, NOT `title`, and
    `_positive_names_subject` is TITLE-anchored and FAILS CLOSED on an empty title
    (R-F3555). Passing these rows through unmapped would have produced exactly zero
    findings while looking fixed — a dead path replaced by a silent one."""
    rows = dd._positive_source_rows(_report(press_coverage=[
        {"url": _SIA, "source": "Acme PLC — Approved Contractor", "snippet": "s"},
    ]))
    assert rows[0]["title"] == "Acme PLC — Approved Contractor", (
        "the headline did not reach `title`; the title anchor would fail closed"
    )


def test_a_row_with_no_usable_title_is_not_credited():
    """Fail-closed must survive the remapping."""
    rows = dd._positive_source_rows(_report(press_coverage=[
        {"url": _SIA, "source": "", "snippet": "Acme PLC is approved"},
    ]))
    out = dd.positive_register_findings(rows, {"acme"}, as_of="2026-07-31")
    assert out == [], "a credential was asserted from a snippet with no title anchor"


# ── end to end ───────────────────────────────────────────────────────────────

def test_a_real_register_hit_now_becomes_a_finding():
    """CAPABILITY: the user-visible outcome — a verified credential is reported."""
    rows = dd._positive_source_rows(_report(press_coverage=[
        {"url": _SIA, "source": "Acme Security Group Approved Contractor Scheme",
         "snippet": "Acme Security Group holds ACS approval."},
    ]))
    out = dd.positive_register_findings(rows, {"acme", "security"}, as_of="2026-07-31")
    assert len(out) == 1, f"a genuine register listing was not promoted: {out}"
    f = out[0]
    assert "SIA Approved Contractor Scheme" in f["title"]
    assert "2026-07-31" in f["detail"], "an undated credential reads as current"
    assert "NOT" in f["detail"] or "not a vetting" in f["detail"], (
        "the finding must state what the register does NOT attest"
    )


def test_a_register_listing_for_a_DIFFERENT_company_is_not_credited():
    """The whole reason the positive gate is stricter than the adverse one: a
    fabricated credential is worse than a missed one."""
    rows = dd._positive_source_rows(_report(press_coverage=[
        {"url": _SIA, "source": "Babcock International Group ACS listing",
         "snippet": "Acme Security Group is also mentioned here."},
    ]))
    out = dd.positive_register_findings(rows, {"acme", "security"}, as_of="2026-07-31")
    assert out == [], f"a credential belonging to another company was credited: {out}"


def test_adverse_findings_are_still_read_when_they_eventually_exist():
    """The follow-up populates them later; the same helper must serve that moment,
    so the two call sites cannot drift apart."""
    rows = dd._positive_source_rows(_report(adverse={"findings": [
        {"source_url": _SIA, "title": "Acme PLC Approved Contractor", "snippet": "s"},
    ]}))
    assert len(rows) == 1 and rows[0]["title"] == "Acme PLC Approved Contractor"


def test_both_producers_are_combined_not_replaced():
    rows = dd._positive_source_rows(_report(
        press_coverage=[{"url": "https://a.example/1", "source": "A", "snippet": ""}],
        adverse={"findings": [{"source_url": "https://b.example/2", "title": "B", "snippet": ""}]},
    ))
    assert {r["url"] for r in rows} == {"https://a.example/1", "https://b.example/2"}


# ── reachability: the call site must actually be able to see input ───────────

def test_synthesis_reads_the_helper_and_not_the_unassigned_field():
    """R-F3515's lesson: a grep proves a string exists, never that it executes. This
    asserts the call site takes its input from the helper, which is the thing that
    made the difference between 0 rows and 15 on the real captured report."""
    import inspect

    src = function_source(dd, "_run_synthesis")
    assert "_positive_source_rows(report)" in src, (
        "synthesis is not using the populated producer"
    )
    assert '_pos_blob.get("findings")' not in src, (
        "the unassigned-at-synthesis producer is still being read"
    )


def test_malformed_rows_cannot_break_the_run():
    """A positive must never cost a report."""
    rep = _report(press_coverage=[None, 42, {"no_url": 1}, {"url": "not-a-url"}])
    assert dd._positive_source_rows(rep) == []


# ── the follow-up: the ONLY moment adverse findings exist ────────────────────

_BODY = {
    "generated_at": "2026-07-31T00:00:00+00:00",
    "identity": {"entity_name": "Acme Security Group", "aliases": [], "findings": []},
}


def _body():
    import copy
    return copy.deepcopy(_BODY)


def test_the_followup_promotes_a_credential_the_deep_sweep_found():
    """CAPABILITY: fixing synthesis alone would leave the ORIGINALLY INTENDED
    producer permanently unread — the same defect inverted."""
    b = _body()
    n = dd._promote_positives_into_body(b, {"findings": [
        {"source_url": _SIA, "title": "Acme Security Group Approved Contractor Scheme",
         "snippet": "holds ACS approval"},
    ]})
    assert n == 1, "the follow-up dropped a register credential it had in hand"
    assert any("SIA Approved Contractor Scheme" in f["title"]
               for f in b["identity"]["findings"])


def test_the_followup_does_not_duplicate_what_synthesis_already_promoted():
    """Both producers can see the same credential on one run."""
    b = _body()
    am = {"findings": [{"source_url": _SIA,
                        "title": "Acme Security Group Approved Contractor Scheme",
                        "snippet": ""}]}
    assert dd._promote_positives_into_body(b, am) == 1
    assert dd._promote_positives_into_body(b, am) == 0, "the credential was reported twice"
    assert len(b["identity"]["findings"]) == 1


def test_the_followup_credits_nothing_when_the_sweep_found_no_register():
    b = _body()
    assert dd._promote_positives_into_body(b, {"findings": [
        {"source_url": "https://www.reuters.com/x", "title": "Acme Security Group news",
         "snippet": ""},
    ]}) == 0
    assert b["identity"]["findings"] == []


def test_the_followup_will_not_credit_another_companys_listing():
    b = _body()
    assert dd._promote_positives_into_body(b, {"findings": [
        {"source_url": _SIA, "title": "Babcock International Group ACS listing",
         "snippet": "Acme Security Group also appears"},
    ]}) == 0


def test_a_malformed_body_cannot_break_the_merge():
    assert dd._promote_positives_into_body({}, {"findings": [{"source_url": _SIA}]}) == 0
    assert dd._promote_positives_into_body({"identity": None}, {"findings": []}) == 0
    assert dd._promote_positives_into_body(_body(), {}) == 0


def test_the_followup_call_site_is_reachable_and_correctly_ordered():
    """R-F3553 died because a call sat where its input did not exist. This asserts
    the new call site is inside the merge AND runs BEFORE the surfaces re-render —
    after them, a promoted finding would never reach the rendered report or the
    index row the list surface reads."""
    import inspect

    src = function_source(dd, "_run_adverse_media_followup")
    assert "_promote_positives_into_body(_body, _am_result)" in src, (
        "the follow-up does not promote; adverse-borne credentials stay dropped"
    )
    i_promote = src.index("_promote_positives_into_body(_body")
    i_sync = src.index("_sync_report_surfaces_after_followup")
    assert i_promote < i_sync, (
        "promotion runs AFTER the surfaces are synced, so the new finding would not "
        "appear in the rendered report"
    )


# ── R-F3568: the LIVE object shape, not the serialised one ───────────────────
#
# R-F3566 passed 16 tests and was INERT on the live path. `press_coverage` is
# declared `list[Evidence]` — a dataclass — and the helper guarded on
# `isinstance(_c, dict)`, so it skipped every real row. The fixtures were built
# from the SERIALISED report JSON, where the same rows are dicts, so the live
# object was never in the loop. These tests drive the REAL dataclass.

from aria_service.intel.dd_schema import Evidence  # noqa: E402


def _evidence_report(items):
    return types.SimpleNamespace(
        digital=types.SimpleNamespace(press_coverage=items), adverse_media={})


def test_real_evidence_dataclass_rows_are_read():
    """PROVE RED against the R-F3566 cut: this returned 0 rows."""
    rep = _evidence_report([Evidence(
        source="Acme Security Group Approved Contractor Scheme",
        url=_SIA, snippet="holds ACS approval")])
    rows = dd._positive_source_rows(rep)
    assert len(rows) == 1, "the live Evidence row was skipped — the fix is inert"
    assert rows[0]["title"] == "Acme Security Group Approved Contractor Scheme"
    assert rows[0]["url"] == _SIA


def test_a_real_evidence_row_promotes_end_to_end():
    """CAPABILITY on the LIVE shape, not the serialised one."""
    rows = dd._positive_source_rows(_evidence_report([Evidence(
        source="Acme Security Group Approved Contractor Scheme",
        url=_SIA, snippet="holds ACS approval")]))
    out = dd.positive_register_findings(rows, {"acme", "security"}, as_of="2026-07-31")
    assert len(out) == 1, "a live register listing was not promoted"


def test_evidence_with_no_url_is_skipped_not_crashed():
    rep = _evidence_report([Evidence(source="No link here", url=None, snippet=None)])
    assert dd._positive_source_rows(rep) == []


def test_both_shapes_work_side_by_side():
    """The persisted path serialises to dicts; both must keep working."""
    rep = _evidence_report([
        Evidence(source="Acme A", url="https://a.example/1", snippet="x"),
        {"source": "Acme B", "url": "https://b.example/2", "snippet": "y"},
    ])
    rows = dd._positive_source_rows(rep)
    assert {r["url"] for r in rows} == {"https://a.example/1", "https://b.example/2"}
    assert {r["title"] for r in rows} == {"Acme A", "Acme B"}

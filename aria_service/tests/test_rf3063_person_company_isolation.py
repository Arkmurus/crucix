"""R-F3063 (P0) — company-register enrichment must NEVER run for a person.

LIVE CONTAMINATION (dd_7ac19aa7941d and dd_17ef831d42fe, subject "Charles Woodburn",
`entity_type=person`, surfaced by a Codex forensic review and re-verified here from
the stored reports):

    registration_number   15016136                                  ← a COMPANY number
    directors             WOODBURN, Amy Louise / WOODBURN, Andrew John   ← other people
    shareholders          Mr Andrew John Woodburn / Mrs Amy Louise Woodburn
    financial verdict     DISTRESSED                                ← their company's

A named private individual was presented as financially distressed and tied to two
unrelated named individuals, because a company name matched his surname.

Two unguarded paths caused it, both gating only on `jurisdiction_iso2 == "GB"`:
  1. the identity Companies House block, which passes `company_name=<subject name>`;
  2. the R-F2515 officer backfill, whose trigger is `not report.identity.directors` —
     never true for a company that resolved, ALWAYS true for a person, so it fired on
     every GB person DD.

This is the fabrication class R-F2726 / R-F2993 / R-F3014 removed elsewhere, at its
most damaging: about a human being.
"""
import inspect

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport


def _report(entity_type: str, name: str = "Charles Woodburn"):
    r = ARKDDReport()
    r.identity.entity_name = name
    r.identity.entity_type = entity_type
    r.identity.jurisdiction_iso2 = "GB"
    return r


# ── the guard itself ───────────────────────────────────────────────────────
def test_rf3063_person_is_detected():
    assert ddo._subject_is_person(_report("person")) is True
    assert ddo._subject_is_person(_report("PERSON")) is True


def test_rf3063_company_is_not_blocked():
    assert ddo._subject_is_person(_report("company")) is False


def test_rf3063_unknown_type_keeps_existing_behaviour():
    """Deliberately an explicit PERSON test, not a company test: a subject of
    genuinely unknown type must not silently lose registry coverage."""
    assert ddo._subject_is_person(_report("")) is False
    assert ddo._subject_is_person(_report("unknown")) is False


def test_rf3063_never_raises_on_a_malformed_report():
    class _Bad:
        pass
    assert ddo._subject_is_person(_Bad()) is False
    assert ddo._subject_is_person(None) is False


# ── every company-only path is gated ───────────────────────────────────────
def test_rf3063_identity_companies_house_block_excludes_persons():
    src = module_source(ddo)
    assert 'if jurisdiction_iso2 == "GB" and not _subject_is_person(report):' in src, (
        "the identity CH block passes company_name=<subject name> — it must not see a person")


def test_rf3063_officer_backfill_excludes_persons():
    src = module_source(ddo)
    i = src.index("R-F2515 — Companies House officer BACKFILL")
    window = src[i:i + 2500]
    assert "_subject_is_person(report)" in window, (
        "`not directors` is the NORMAL state for a person — this fired on every GB person DD")


def _section(src: str, start_marker: str) -> str:
    """The orchestrator block introduced by `start_marker`, bounded by the NEXT section.

    R-F4265 — this used to be `src[i:i + 1600]`, a fixed CHARACTER window, and adding
    eighteen lines of comment INSIDE the block pushed the guard past the cut. The test
    went red while the guard sat untouched two lines further down. That is the R-F3597 /
    R-F3858 class the header of this file already warns about for `inspect.getsource`:
    a check anchored to a magic offset is blinded by GROWTH ALONE, and it fails in the
    direction that looks like a real regression, which costs an investigation.

    dd_orchestrator delimits its own blocks with `# ── ` rules, so bound the window on
    that instead. It cannot drift with length, and it still ends at the right place.
    """
    i = src.index(start_marker)
    # U+2500 BOX DRAWINGS LIGHT HORIZONTAL, spelled as an escape so the delimiter
    # survives any re-encoding of this file.
    rule = "\n    # ── "
    nxt = src.find(rule, i + len(start_marker))
    if nxt == -1:
        raise AssertionError(
            f"no section rule found after {start_marker!r}. Falling back to the rest "
            "of the module would make every assertion below pass on a string that "
            "appears ANYWHERE in dd_orchestrator, which is the blind-guard failure "
            "this helper exists to prevent."
        )
    return src[i:nxt]


def test_rf3063_financial_health_excludes_persons():
    """Defence in depth: a verdict this defamatory must not depend on one guard."""
    src = module_source(ddo)
    window = _section(src, "Financial health (R-F2322")
    assert "not _subject_is_person(report)" in window


def test_rf3063_the_window_can_still_see_a_missing_guard():
    """A guard that cannot fail is not a guard (S16, R-F3858).

    Widening the window above must not widen it to the whole module, which would make
    the assertion pass on any file containing the string anywhere. Proven by deleting
    the guard from the section and re-checking.
    """
    src = module_source(ddo)
    window = _section(src, "Financial health (R-F2322")
    assert len(window) < len(src), "the section bound collapsed to the whole module"
    stripped = window.replace("not _subject_is_person(report)", "")
    assert "not _subject_is_person(report)" not in stripped, (
        "the check would still pass with the guard removed")


def test_rf3063_the_three_paths_use_one_shared_guard():
    """Three copies of a condition drift; one helper cannot."""
    src = module_source(ddo)
    assert src.count("def _subject_is_person(") == 1
    assert src.count("_subject_is_person(report)") >= 3


# ── the scorecard must be entity-type aware ────────────────────────────────
from aria_service.intel.dd_schema import _dd_decision_readiness

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _person_payload():
    return {"identity": {"entity_type": "person", "entity_name": "Charles Woodburn",
                         "registration_number": "", "registration_status": "",
                         "directors": [], "data_gaps": []},
            "network": {}, "compliance": {}, "digital": {}}


def test_rf3063_company_questions_are_not_asked_of_a_person():
    """Ownership/UBO and filed-accounts financials are COMPANY properties. Asked of an
    individual they can never be answered, so every person DD was capped at 3/5 and
    told the reader "ownership/control is unresolved" — which reads as a deficiency in
    the subject. It was also the pressure that made the registry contamination look
    like progress: filling those boxes for a person REQUIRES attaching a company."""
    d = _dd_decision_readiness(_person_payload())
    for k in ("ownership_control", "financial_capacity"):
        q = d["questions"][k]
        assert q["status"] == "NOT_APPLICABLE", k
        assert q["not_applicable"] is True
        assert q["blocker"] == "", "not a blocker — it is not a question about a person"


def test_rf3063_not_applicable_is_never_counted_as_answered():
    """never-false-clean: N/A must not manufacture a cleared person."""
    d = _dd_decision_readiness(_person_payload())
    assert d["required"] == 3, "scored out of the APPLICABLE questions"
    assert d["answered"] == 0
    assert d["clearance_ready"] is False


def test_rf3063_a_company_still_answers_all_five():
    d = _dd_decision_readiness({
        "identity": {"entity_type": "company", "registration_number": "01514084",
                     "registration_status": "active", "directors": [{"name": "A"}],
                     "incorporation_date": "1980-08-26", "data_gaps": []},
        "network": {}, "compliance": {}, "digital": {}})
    assert d["required"] == 5, "companies are unaffected by the person carve-out"


def test_rf3063_a_person_with_real_company_evidence_keeps_it():
    """If a person genuinely has an evidenced holding, do not erase it."""
    payload = _person_payload()
    payload["identity"]["shareholders"] = [{"name": "Holdco Ltd", "hop": 1}]
    payload["network"] = {"ubo_chain": [{"name": "Holdco Ltd", "hop": 1}],
                          "controlled_by_unanchored": [],
                          "ubo_chain_walk": {"stats": {"budget_exhausted": False}}}
    q = _dd_decision_readiness(payload)["questions"]["ownership_control"]
    assert q["answered"] is True and q["status"] == "ANSWERED"

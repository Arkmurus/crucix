"""R-F3092 — the same sentence, four times, on one page.

LIVE DEFECT (Mitie, operator report 2026-07-26). This 60-word paragraph —

    "financial capacity is unknown — Companies House holds accounts made up to
     2025-03-31 (accounts-with-accounts-type-full), 51 pages, filed as a
     scanned/PDF document with no machine-readable (iXBRL) figures … figures would
     need the issuer's own published annual report."

appeared FOUR times on a single report: pasted into `bottom_line`, again as the
scorecard row's blocker, again under "Recommended next actions" as a verbatim
restatement, and again in the data gaps. The reader has to re-read the same sentence
four times to discover it is the same sentence.

`next_actions` was generated as `f"Resolve decision-readiness blocker: {blocker}"`
for every blocker — a restatement is not an action. `bottom_line` pasted the first
three blockers wholesale. Each fact now appears in the ONE place that owns it, and
the next actions say what to DO.
"""
from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import dd_schema


_FIN_BLOCKER = (
    "financial capacity is unknown — Companies House holds accounts made up to "
    "2025-03-31, filed as a scanned/PDF document with no machine-readable (iXBRL) "
    "figures — the filing is EVIDENCE of an up-to-date statutory filing, but "
    "solvency was NOT assessed from it."
)


def _readiness(**over):
    base = {
        "status": "NOT_CLEARED", "clearance_ready": False,
        "answered": 4, "required": 5, "completion_pct": 80,
        "evidence_grade": "B", "evidence_ready": False,
        "blocking_reasons": [
            _FIN_BLOCKER,
            "evidence grade B does not meet the Grade A reliance threshold",
        ],
        "questions": {
            "identity": {"label": "Verified legal identity", "answered": True},
            "sanctions_export_control": {"label": "Sanctions and export-control exposure",
                                         "answered": True},
            "adverse_media": {"label": "Adverse media, corruption and litigation",
                              "answered": True},
            "ownership_control": {"label": "Ownership and control", "answered": True},
            "financial_capacity": {"label": "Financial capacity", "answered": False,
                                   "blocker": _FIN_BLOCKER},
        },
    }
    base.update(over)
    return base


def _apply(monkeypatch, readiness):
    """Drive the REAL body-writing path with a pinned readiness object."""
    monkeypatch.setattr(dd_schema, "_dd_decision_readiness", lambda _b: readiness)
    monkeypatch.setattr(ddo, "_dd_decision_readiness", lambda _b: readiness, raising=False)
    body = {"identity": {"entity_name": "MITIE FACILITIES MANAGEMENT LIMITED"},
            "risk_classification": "GREEN"}
    ddo._refresh_persisted_decision_readiness(body)
    return body


def test_rf3092_next_actions_are_actions_not_restatements(monkeypatch):
    body = _apply(monkeypatch, _readiness())
    actions = body["next_actions"]
    assert actions, "an unresolved scorecard must still produce next steps"
    for a in actions:
        assert not a.startswith("Resolve decision-readiness blocker:"), (
            "R-F3092 REGRESSION: next actions are restating the blocker again")
        assert _FIN_BLOCKER not in a, (
            "R-F3092 REGRESSION: the blocker paragraph is back in next_actions")
    joined = " ".join(actions)
    assert "audited financial statements" in joined, "say what to actually DO"
    assert "annual report" in joined


def test_rf3092_bottom_line_names_the_question_not_the_paragraph(monkeypatch):
    body = _apply(monkeypatch, _readiness())
    bl = body["bottom_line"]
    assert "NOT CLEARED" in bl
    assert "Financial capacity" in bl, "name the unresolved question"
    assert _FIN_BLOCKER not in bl, (
        "R-F3092 REGRESSION: the full blocker paragraph is pasted into bottom_line")
    assert "decision-readiness scorecard" in bl, "point at the row that owns the detail"


def test_rf3092_each_unanswered_question_gets_its_own_remedy(monkeypatch):
    r = _readiness()
    for k in ("identity", "adverse_media", "ownership_control"):
        r["questions"][k]["answered"] = False
    r["answered"] = 1
    body = _apply(monkeypatch, r)
    joined = " ".join(body["next_actions"])
    assert "companies registry" in joined            # identity
    assert "adverse-media search" in joined          # adverse media
    assert "PSC/UBO filing" in joined                # ownership
    assert "audited financial statements" in joined  # financial


def test_rf3092_evidence_grade_gets_an_action_too(monkeypatch):
    body = _apply(monkeypatch, _readiness())
    joined = " ".join(body["next_actions"])
    assert "second independent" in joined, (
        "'grade B does not meet Grade A' is a tautology — say how to raise it")


def test_rf3092_a_cleared_report_is_unaffected(monkeypatch):
    r = _readiness(clearance_ready=True, status="CLEARED", answered=5,
                   completion_pct=100, evidence_ready=True, blocking_reasons=[])
    for q in r["questions"].values():
        q["answered"] = True
    body = _apply(monkeypatch, r)
    assert "Proceed with standard commercial process" in body["next_actions"]


def test_rf3092_uncodified_question_still_produces_an_action(monkeypatch):
    """A future scorecard question must never fall through to silence."""
    r = _readiness()
    r["questions"]["brand_new_check"] = {"label": "Some new check", "answered": False,
                                         "blocker": "not run"}
    body = _apply(monkeypatch, r)
    assert any("Some new check" in a for a in body["next_actions"])


# ── the online surface no longer prints internal scratch as findings ───────
def test_rf3092_debug_findings_are_filtered_on_the_online_report():
    """The Mitie report rendered 'link-tree: name=Mitie (×6 sources)' as a finding."""
    import re
    from pathlib import Path
    src = Path("public/dd-reports.html").read_text(encoding="utf-8")
    m = re.search(r"const DD_DEBUG_FINDING_RE = (/.+/[a-z]*);", src)
    assert m, "R-F3092 REGRESSION: the debug-finding filter is gone"
    assert "link-tree" in m.group(1) and "link_investigator" in m.group(1)
    assert "DD_DEBUG_FINDING_RE.test(String(f.title" in src, "filter must be APPLIED"


def test_rf3092_case_id_is_never_sliced():
    """`canonicalId.slice(0, 18)` turned company:GB:02938041 into GB:0293804 —
    a company number that does not exist, printed on the report header."""
    from pathlib import Path
    src = Path("public/dd-reports.html").read_text(encoding="utf-8")
    assert "canonicalId.slice(0, 18)" not in src, (
        "R-F3092 REGRESSION: the case ID is being truncated mid-identifier again")
    assert "dd-hs-id" in src, "width is handled in CSS instead"


def test_rf3092_checks_vocabulary_no_longer_collides():
    """'12 checks ran' (layers) vs '27 checks' (sub-calls) could not be reconciled."""
    from pathlib import Path
    src = Path("public/dd-reports.html").read_text(encoding="utf-8")
    assert "sec.subcalls+' data calls</span>'" in src
    assert "sec.subcalls+' checks</span>'" not in src, (
        "R-F3092 REGRESSION: sub-call counts are labelled 'checks' again, colliding "
        "with the run-coverage layer count")

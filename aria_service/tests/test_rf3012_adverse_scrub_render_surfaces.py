"""R-F3012 — the adverse-media "did NOT complete" (R-F2779) disclosure must be
scrubbed from the RENDERED surfaces once the follow-up (R-F2780) completed and the
scorecard marks adverse-media ANSWERED.

Live defect (Schroder dd_fc3e2b4e824b AND Cohort dd_16ea006830ac): the same report
asserted BOTH "35/41 credible items → AMBER (completed)" and "R-F2779: adverse-media
screening did NOT complete this run". R-F2992's scrub only cleaned digital.* — but
the finding was ALSO propagated into the surfaces the PDF/online report actually
render from: synthesis.key_findings ("KEY FINDINGS"), synthesis.residual_unknowns,
and version_diff.new_findings. Proven from the persisted JSON: R-F2779 appeared in
all three, and NOT in digital.findings (already scrubbed there).
"""
from aria_service.intel import dd_orchestrator as ddo

_STALE_DETAIL = ("R-F2779: adverse-media screening did NOT complete this run — the ABSENCE "
                 "of adverse-media / litigation / corruption findings is NOT a clean bill.")
_STALE_FINDING = {
    "severity": "info",
    "title": "Adverse-media screening incomplete — verdict does NOT certify absence of adverse media",
    "detail": _STALE_DETAIL,
    "source": "dd_orchestrator._run_synthesis:R-F2779",
}


def _report_body() -> dict:
    return {
        "data_gaps_summary": [_STALE_DETAIL, "unrelated gap"],
        "digital": {
            "data_gaps": [_STALE_DETAIL],
            "findings": [dict(_STALE_FINDING)],
        },
        "synthesis": {
            "key_findings": [
                dict(_STALE_FINDING),
                {"title": "GLEIF: LEI 213800...", "source": "gleif.search_lei", "detail": "keep me"},
            ],
            "residual_unknowns": [_STALE_DETAIL, "some other unknown"],
        },
        "version_diff": {"new_findings": [dict(_STALE_FINDING)]},
    }


def test_rf3012_scrub_clears_rendered_key_findings():
    body = _report_body()
    removed = ddo._scrub_stale_adverse_incomplete(body)
    assert removed is True

    # the surface the PDF/online report actually renders as "KEY FINDINGS"
    kf = body["synthesis"]["key_findings"]
    assert all("r-f2779" not in str(f.get("source", "")).lower() for f in kf), \
        "the rendered KEY FINDINGS must not still carry the R-F2779 disclosure"
    assert not any("incomplete" in str(f.get("title", "")).lower() for f in kf)
    assert len(kf) == 1 and kf[0]["source"] == "gleif.search_lei", "legitimate findings untouched"

    # the other two render surfaces proven to carry it in the live JSON
    assert body["synthesis"]["residual_unknowns"] == ["some other unknown"]
    assert body["version_diff"]["new_findings"] == []

    # and the originally-scrubbed places stay clean
    assert body["digital"]["findings"] == []
    assert body["digital"]["data_gaps"] == []
    assert body["data_gaps_summary"] == ["unrelated gap"]


def test_rf3012_scrub_noop_leaves_legitimate_findings():
    body = {"synthesis": {"key_findings": [
        {"title": "Sanctions screen CLEAN", "source": "sanctions.screen_with_aliases", "detail": "clean"},
    ]}}
    removed = ddo._scrub_stale_adverse_incomplete(body)
    assert removed is False
    assert len(body["synthesis"]["key_findings"]) == 1

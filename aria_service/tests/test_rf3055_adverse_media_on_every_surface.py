"""R-F3055 — adverse media must appear on every surface, with its TRUE state.

OPERATOR (2026-07-25): "the adverse media section does not show on the pdf report it
must be included also, the online report must match 100% the downloaded version in
any shape or form".

Two causes, both verified against live reports:
  1. `adverse_media` is a TOP-LEVEL report key, not a layer, so the PDF's layer plan
     never reached it — the downloaded report showed nothing at all.
  2. The online view's section is TITLED "Digital & Adverse Media" but rendered only
     press coverage and source tiers, so it under-reported it too.

And the STATE is load-bearing: on every completed report checked (dd_ef351f140935,
dd_75bc5a5a7e7c, dd_7ac19aa7941d) `adverse_media.status` was still `in_progress`,
because the sweep is a detached follow-up that merges after the DD returns. A section
that is simply ABSENT while a screening is unfinished reads as "nothing adverse
found" — the false clean this product exists to prevent.
"""
from aria_service.intel.dd_schema import (
    ARKDDReport, _render_adverse_media, structured_view,
)


def _report():
    r = ARKDDReport()
    r.identity.entity_name = "Acme Defence Ltd"
    return r


def test_rf3055_unfinished_sweep_is_stated_not_omitted():
    """The live state of every completed report this session."""
    lines = _render_adverse_media({"status": "in_progress"})
    joined = " ".join(lines)
    assert "STILL RUNNING" in joined
    assert "UNCHECKED, not clean" in joined


def test_rf3055_absent_blob_says_not_run():
    for blob in ({}, None, "nonsense"):
        joined = " ".join(_render_adverse_media(blob))
        assert "NOT RUN" in joined
        assert "UNCHECKED, not as clean" in joined


def test_rf3055_completed_clean_is_never_proof_of_good_standing():
    lines = _render_adverse_media({
        "status": "completed", "findings": [], "templates_searched": 12,
        "templates_total_in_set": 30, "search_backends_answered": True})
    joined = " ".join(lines)
    assert "COMPLETED" in joined
    assert "12 of 30" in joined
    assert "absence of COVERAGE, not proof of good standing" in joined


def test_rf3055_backends_that_did_not_answer_are_named():
    joined = " ".join(_render_adverse_media(
        {"status": "completed", "findings": [], "search_backends_answered": False}))
    assert "NO — the sweep could not observe the web" in joined


def test_rf3055_materiality_arithmetic_is_shown():
    joined = " ".join(_render_adverse_media({
        "status": "completed", "findings": [],
        "materiality": {"credible_count": 0, "raw_count": 39, "duplicates_dropped": 33,
                        "self_references_dropped": 1, "non_adverse_dropped": 5}}))
    assert "0 credible" in joined and "39 raw hit(s)" in joined and "33 duplicate" in joined


def test_rf3055_items_render_with_their_url():
    joined = " ".join(_render_adverse_media({
        "status": "completed",
        "findings": [{"title": "Regulator fines Acme", "source_url": "https://www.ft.com/a"}]}))
    assert "Regulator fines Acme" in joined and "https://www.ft.com/a" in joined


def test_rf3055_an_item_without_a_url_says_so():
    joined = " ".join(_render_adverse_media({
        "status": "completed", "findings": [{"title": "Unsourced claim"}]}))
    assert "[no URL carried]" in joined


def test_rf3055_markdown_surface_carries_the_section():
    """CAPABILITY: the markdown a customer reads."""
    r = _report()
    r.adverse_media = {"status": "in_progress"}
    md = r.render_markdown()
    assert "Adverse media screening: STILL RUNNING" in md


def test_rf3055_structured_online_view_carries_it_too():
    """The section is TITLED 'Adverse Media' — it must contain some."""
    r = _report()
    r.adverse_media = {"status": "completed", "findings": [], "templates_searched": 12}
    blob = str(structured_view(r.as_dict()))
    assert "Adverse media" in blob
    assert "COMPLETED" in blob


def test_rf3055_online_and_pdf_use_the_same_status_vocabulary():
    """Parity guard: the JS mirror must use the same words as the Python renderer,
    or the two surfaces will describe the same state differently."""
    from pathlib import Path
    js = Path("lib/reports/pdf_generator.mjs").read_text(encoding="utf8")
    for word in ("STILL RUNNING", "DID NOT COMPLETE", "NOT RUN", "COMPLETED",
                 "UNCHECKED, not clean", "absence of COVERAGE, not proof of good standing"):
        assert word in js, f"PDF mirror is missing the shared wording: {word!r}"


# ── R-F3060 — the decision-ready summary (concern + advice) ────────────────
from aria_service.intel.dd_schema import _adverse_media_summary

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source


def test_rf3060_unfinished_screening_advises_not_to_decide_yet():
    s = _adverse_media_summary({"status": "in_progress"})
    assert s["severity"] == "unknown"
    assert "UNFINISHED" in s["headline"]
    assert "not evidence of absence" in s["concern"]
    assert "UNCHECKED" in s["advice"]


def test_rf3060_clean_result_never_claims_good_standing():
    s = _adverse_media_summary({"status": "completed", "findings": []})
    assert "nothing found in the sources searched" in s["headline"]
    assert "not proof of good standing" in s["concern"]
    assert "native-language and offline" in s["advice"], "say what ARIA cannot see"


def test_rf3060_a_broken_digital_layer_downgrades_a_clean_result():
    """A layer that did not complete cannot support an adverse-media conclusion."""
    s = _adverse_media_summary({"status": "completed", "findings": []},
                               {"meta": {"status": "error"}})
    assert s["severity"] == "unknown"
    assert "did not complete" in s["headline"]
    assert "narrower than intended" in s["concern"]


def test_rf3060_hits_are_amber_and_hand_the_judgement_to_the_reader():
    s = _adverse_media_summary({"status": "completed", "findings": [{"title": "x"}],
                                "materiality": {"credible_count": 2}})
    assert s["severity"] == "amber"
    assert "2 item(s) require review" in s["headline"]
    assert "does not decide materiality on your behalf" in s["advice"]


def test_rf3060_never_screened_is_not_clean():
    s = _adverse_media_summary({})
    assert "NOT SCREENED" in s["headline"]
    assert "Do not treat this as clean" in s["advice"]


def test_rf3060_summary_reaches_the_markdown_and_online_surfaces():
    r = ARKDDReport()
    r.identity.entity_name = "Acme Defence Ltd"
    r.adverse_media = {"status": "completed", "findings": []}
    md = r.render_markdown()
    assert "⚑ Adverse media:" in md and "Advice:" in md
    blob = str(structured_view(r.as_dict()))
    assert "Adverse media — assessment" in blob


def test_rf3060_pdf_mirror_uses_the_same_advice_wording():
    from pathlib import Path
    js = Path("lib/reports/pdf_generator.mjs").read_text(encoding="utf8")
    for phrase in ("SCREENING UNFINISHED", "NOT SCREENED",
                   "does not decide materiality on your behalf",
                   "native-language and offline media check",
                   "Do not treat this as clean"):
        assert phrase in js, f"PDF summary mirror missing: {phrase!r}"


# ── R-F3067 — the follow-up must stamp a TERMINAL status ───────────────────
def test_rf3067_success_path_stamps_an_explicit_status():
    """THE DEFECT. The follow-up DOES complete and merge — live: dd_eb5c9f6f2e1d
    32 findings / 12 templates, dd_2556c66e95ee 118 / 30, both ok=True — but
    `run_adverse_media_deep_search` returns a dict with NO `status` key and the merge
    writes it verbatim. So the field went "in_progress" -> ABSENT and never once said
    "completed". Only the FAILURE path ever wrote a status, which is why the error
    case looked right while success silently had none."""
    import inspect
    from aria_service.intel import dd_orchestrator as ddo
    src = function_source(ddo, "_run_adverse_media_followup")
    assert 'if isinstance(_am_result, dict) and not _am_result.get("status"):' in src
    assert '_am_result["status"] = "partial" if _am_result.get("partial") else "completed"' in src
    assert '_am_result["status"] = "incomplete"' in src
    assert '_am_result.setdefault("completed_at"' in src


def test_rf3067_the_live_success_shape_now_reads_as_completed():
    """The exact shape the search returns: ok=True, findings, NO status key."""
    live = {"ok": True, "findings": [{"title": "x"}], "templates_searched": 12,
            "findings_count": 32}
    assert "COMPLETED" in _render_adverse_media(live)[0]


def test_rf3067_a_bounded_sweep_is_not_reported_as_a_failure():
    lines = _render_adverse_media({"ok": True, "partial": True, "status": "partial",
                                   "findings": [{"title": "x"}]})
    assert "COMPLETED (bounded — stopped early)" in lines[0]
    assert "DID NOT COMPLETE" not in lines[0]


# ── R-F3068 — a person is not "unscreened" ─────────────────────────────────
def test_rf3068_person_wording_does_not_overstate_the_gap():
    """The deep media sweep is gated to company subjects, but a person DOES get a
    sanctions/PEP screen. Saying "NOT RUN ... treat as UNCHECKED" implies nothing
    was done."""
    line = _render_adverse_media({}, entity_type="person")[0]
    assert "MEDIA SWEEP NOT RUN for an individual" in line
    assert "sanctions/PEP screen" in line and "DID run" in line
    assert "commission one" in line


def test_rf3068_company_wording_is_unchanged():
    line = _render_adverse_media({}, entity_type="company")[0]
    assert "Adverse media: NOT RUN" in line
    assert "UNCHECKED, not as clean" in line


def test_rf3068_person_summary_and_readiness_agree():
    from aria_service.intel.dd_schema import _dd_decision_readiness
    s = _adverse_media_summary({}, None, entity_type="person")
    assert "media sweep not run for an individual" in s["headline"]
    q = _dd_decision_readiness({"identity": {"entity_type": "person", "data_gaps": []},
                                "network": {}, "compliance": {}, "digital": {}})["questions"]["adverse_media"]
    assert "MEDIA SWEEP not run for an individual" in q["blocker"], (
        "the scorecard must not say 'did not complete' next to a summary saying it was never run")


def test_rf3068_pdf_mirror_carries_the_person_wording():
    from pathlib import Path
    js = Path("lib/reports/pdf_generator.mjs").read_text(encoding="utf8")
    assert "MEDIA SWEEP NOT RUN for an individual subject" in js
    assert "media sweep not run for an individual" in js
    assert "COMPLETED (bounded — stopped early)" in js

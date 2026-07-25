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

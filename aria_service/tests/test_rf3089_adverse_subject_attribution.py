"""R-F3089 — the adverse-media filter was inverted: it kept noise and dropped signal.

LIVE DEFECT (Mitie, operator report 2026-07-26). The report stated:

    Adverse media: 2 item(s) require review
    Concern: 2 subject-named item(s) survived de-duplication and filtering.
      · BAILII - United Kingdom Cases page 286 — bailii.org/indices/uk-cases-0286.html
      · BAILII - United Kingdom Cases page 264 — bailii.org/indices/uk-cases-0264.html

Both are court INDEX pages — a paginated table of contents of every UK case — and
neither mentions Mitie. Two stacked causes:

  1. `_adverse_has_adverse_content` returns True on DOMAIN MATCH ALONE for
     `_ADVERSE_OFFICIAL_DOMAINS` (bailii.org), so title/snippet were never read.
  2. NOTHING in `_adverse_media_materiality` ever tested that an item names the
     subject. "subject-named" was asserted by the renderer and established nowhere.

Meanwhile the one genuinely adverse item on that same report — "Deloitte hit with
£2mn fine after rule breaches over Mitie" — sat in the Cited-sources list and never
reached the assessment.

These tests drive the REAL verdict path (`_apply_adverse_media_to_verdict`) and the
REAL renderer (`dd_schema._adverse_media_summary` / `_render_adverse_media`), and
assert the user-visible outcome: the index pages are gone, the Deloitte fine is
kept, and the wording no longer claims an attribution nobody tested.
"""
from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import dd_schema


def _f(**kw):
    """A finding in the shape researcher._deep_adverse_media_search writes."""
    base = {"source_class": "press_general", "source_url": "https://news.example.com/a",
            "title": "t", "snippet": "s", "credibility_tier": 2}
    base.update(kw)
    return base


# The two items the live Mitie report presented as "subject-named".
_BAILII_INDEX_1 = _f(
    source_url="http://www2.bailii.org/indices/uk-cases-0286.html",
    title="BAILII - United Kingdom Cases page 286",
    snippet="United Kingdom Cases", source_class="legal_court_uk",
    query_executed='"Mitie" site:bailii.org', credibility_tier=1)
_BAILII_INDEX_2 = _f(
    source_url="https://www.bailii.org/indices/uk-cases-0264.html",
    title="BAILII - United Kingdom Cases page 264",
    snippet="United Kingdom Cases", source_class="legal_court_uk",
    query_executed='"Mitie" site:bailii.org', credibility_tier=1)
# The item the live report DROPPED into the citation list instead.
_DELOITTE_FINE = _f(
    source_url="https://www.ft.com/content/deloitte-mitie-fine",
    title="Deloitte hit with £2mn fine after rule breaches over Mitie",
    snippet="The FRC fined Deloitte over its audit of Mitie Group",
    credibility_tier=4)

_MITIE = "MITIE FACILITIES MANAGEMENT LIMITED"


# ── 1. index pages ─────────────────────────────────────────────────────────
def test_rf3089_court_index_page_is_not_a_record_about_anyone():
    assert ddo._adverse_is_index_page(_BAILII_INDEX_1) is True
    assert ddo._adverse_is_index_page(_BAILII_INDEX_2) is True


def test_rf3089_a_real_judgment_is_not_an_index_page():
    """The guard must not swallow the court records it exists to preserve."""
    judgment = _f(source_url="https://www.bailii.org/ew/cases/EWHC/2024/1.html",
                  title="Acme Ltd v Beta Ltd", source_class="legal_court_uk")
    assert ddo._adverse_is_index_page(judgment) is False
    gazette = _f(source_url="https://www.thegazette.co.uk/notice/1234567",
                 title="Winding-up petition — Acme Ltd")
    assert ddo._adverse_is_index_page(gazette) is False


def test_rf3089_index_guard_only_applies_to_official_domains():
    """A press article whose URL happens to contain /latest/ is still an article."""
    art = _f(source_url="https://www.reuters.com/latest/acme-fined",
             title="Acme fined for bribery", credibility_tier=4)
    assert ddo._adverse_is_index_page(art) is False


# ── 2. subject attribution ─────────────────────────────────────────────────
def test_rf3089_item_must_name_the_subject():
    toks = ddo._adverse_subject_tokens(_MITIE)
    assert "mitie" in toks
    assert "limited" not in toks and "management" not in toks, "generic words excluded"
    assert ddo._adverse_names_subject(_DELOITTE_FINE, toks) is True
    assert ddo._adverse_names_subject(_BAILII_INDEX_1, toks) is False


def test_rf3089_former_names_count_as_the_subject():
    """Coverage under a former name is still coverage of this entity."""
    toks = ddo._adverse_subject_tokens(
        _MITIE, [{"name": "MITIE FACILITIES SERVICES LIMITED"}])
    hit = _f(title="Mitie Facilities Services fined by the regulator")
    assert ddo._adverse_names_subject(hit, toks) is True


def test_rf3089_unresolvable_subject_fails_OPEN_never_clean():
    """A gate that cannot name the subject must NOT empty the review set — that
    would manufacture the clean report this system exists to prevent."""
    assert ddo._adverse_names_subject(_DELOITTE_FINE, set()) is True
    mat = ddo._adverse_media_materiality({"ok": True, "findings": [_DELOITTE_FINE]}, "")
    assert mat["subject_attribution"] == "unverified"
    assert mat["credible_count"] == 1, "fail open — the item is kept"


# ── 3. the live defect, end to end ─────────────────────────────────────────
def test_rf3089_mitie_index_pages_never_reach_the_review_set():
    """CAPABILITY: the exact live input, through the real verdict path."""
    body = {"risk_classification": "GREEN", "synthesis": {"key_findings": []},
            "identity": {"entity_name": _MITIE,
                         "previous_names": [{"name": "MITIE FACILITIES SERVICES LIMITED"}]}}
    am = {"ok": True, "findings": [_BAILII_INDEX_1, _BAILII_INDEX_2, _DELOITTE_FINE]}
    ddo._apply_adverse_media_to_verdict(body, am)

    review = am["findings_for_review"]
    urls = [r.get("source_url") for r in review]
    assert not any("indices/uk-cases" in (u or "") for u in urls), (
        "R-F3089 REGRESSION: a court INDEX page is back in the customer review set")
    assert _DELOITTE_FINE["source_url"] in urls, (
        "R-F3089 REGRESSION: the one real adverse item was dropped")

    mat = am["materiality"]
    assert mat["index_pages_dropped"] == 2
    assert mat["subject_attribution"] == "verified"


def test_rf3089_renderer_no_longer_claims_untested_attribution():
    """The phrase 'subject-named ... survived filtering' must be EARNED."""
    body = {"risk_classification": "GREEN", "synthesis": {"key_findings": []},
            "identity": {"entity_name": _MITIE}}
    am = {"ok": True, "status": "completed",
          "findings": [_BAILII_INDEX_1, _BAILII_INDEX_2, _DELOITTE_FINE]}
    ddo._apply_adverse_media_to_verdict(body, am)

    summary = dd_schema._adverse_media_summary(am, {}, entity_type="company")
    assert "1 item(s) require review" in summary["headline"], (
        "the 2 index pages must not be counted; the Deloitte fine must be")
    assert "name this entity" in summary["concern"]

    rendered = "\n".join(dd_schema._render_adverse_media(am))
    assert "2 index/listing page" in rendered, (
        "an exclusion that is never accounted for reads as an item never found")


def test_rf3089_unverified_attribution_is_disclosed_not_certified():
    body = {"risk_classification": "GREEN", "synthesis": {"key_findings": []},
            "identity": {}, "target": {}}
    am = {"ok": True, "status": "completed", "findings": [_DELOITTE_FINE]}
    ddo._apply_adverse_media_to_verdict(body, am)

    summary = dd_schema._adverse_media_summary(am, {}, entity_type="company")
    assert "could not be resolved" in summary["concern"]
    assert "subject-named" not in summary["concern"]
    rendered = "\n".join(dd_schema._render_adverse_media(am))
    assert "NOT been confirmed to reference this entity" in rendered


# ── 4. the gate must not become a false clean ──────────────────────────────
def test_rf3089_genuine_adverse_media_still_escalates():
    body = {"risk_classification": "GREEN", "synthesis": {"key_findings": []},
            "identity": {"entity_name": "Acme Ltd"}}
    am = {"ok": True, "findings": [
        _f(source_url="https://www.ft.com/a", title="Acme Ltd fined for bribery",
           credibility_tier=4),
        _f(source_url="https://www.reuters.com/b", title="Acme Ltd director convicted",
           snippet="fraud conviction upheld", credibility_tier=4),
    ]}
    out = ddo._apply_adverse_media_to_verdict(body, am)
    assert out["escalated"] is True
    assert body["risk_classification"] == "AMBER-LIGHT"


def test_rf3089_named_court_judgment_still_counts_as_official():
    body = {"risk_classification": "GREEN", "synthesis": {"key_findings": []},
            "identity": {"entity_name": "Acme Ltd"}}
    am = {"ok": True, "findings": [
        _f(source_url="https://www.bailii.org/ew/cases/EWHC/2024/1.html",
           title="Acme Ltd v Beta Ltd", snippet="judgment of the court",
           source_class="legal_court_uk", credibility_tier=1),
    ]}
    out = ddo._apply_adverse_media_to_verdict(body, am)
    assert out["official"] == 1 and out["material"] is True

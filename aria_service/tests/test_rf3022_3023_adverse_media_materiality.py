"""R-F3022 / R-F3023 — the adverse-media escalation that produced a wrong headline.

LIVE DEFECT (report dd_16db41eb5fa8, EFT CONSULT LTD). The verdict was raised
GREEN → AMBER-LIGHT on "37 credible adverse-media item(s) name this entity". All 37
were extracted in review: 10 unique URLs, NONE of them adverse media —

  * 14 Companies House officer/PSC pages (and for a DIFFERENT company, 11346584)
  * 14 `memory://` items — ARIA's own brain records citing herself
  *  9 doi.org academic papers matched on a director's name, filed as
     `legal_court_uk` / `regulatory_us_doj` ("Abdominal Aortic Trauma";
     "New Structural Model for Parachute Inflation Simulations")

Four stacked causes, each covered below:
  1. no adverse-CONTENT test — any subject-named hit counted
  2. no dedup — one URL re-counted per query template (10 → 37, 3.7×)
  3. self-references counted as corroboration
  4. the class came from the QUERY TEMPLATE, never the RESULT (R-F3023)
"""
from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import researcher as rs

# R-F3782/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _f(**kw):
    """A finding in the shape researcher._deep_adverse_media_search actually writes
    (note: `source_url`, NOT `url` — that key mismatch is its own defect below)."""
    base = {"source_class": "press_general", "source_url": "https://news.example.com/a",
            "title": "t", "snippet": "s", "credibility_tier": 2}
    base.update(kw)
    return base


# ── 1. adverse content ─────────────────────────────────────────────────────
def test_rf3022_non_adverse_hits_do_not_escalate():
    """THE HEADLINE DEFECT: an academic paper and a registry page are not adverse."""
    am = {"ok": True, "findings": [
        _f(source_url="https://doi.org/10.1016/x1",
           title="Abdominal Aortic Trauma, Iliac and Visceral Vessel Injuries",
           snippet="Rich's Vascular Trauma, 2022", source_class="legal_court_uk"),
        _f(source_url="https://doi.org/10.1016/x2",
           title="New Structural Model for Parachute Inflation Simulations",
           snippet="1999", source_class="regulatory_us_doj"),
        _f(source_url="https://find-and-update.company-information.service.gov.uk/company/11346584/officers",
           title="EFT CONSULT LTD people - GOV.UK", snippet="officers and PSCs"),
    ]}
    mat = ddo._adverse_media_materiality(am)
    assert mat["material"] is False, "none of these are adverse media"
    assert mat["credible_count"] == 0
    assert mat["non_adverse_dropped"] >= 1


def test_rf3022_real_adverse_content_still_escalates():
    """The gate must not become a false clean — genuine adverse media still counts."""
    am = {"ok": True, "findings": [
        _f(source_url="https://www.ft.com/a", title="Acme Ltd fined for bribery",
           snippet="regulator imposed a penalty", credibility_tier=4),
        _f(source_url="https://www.reuters.com/b", title="Acme Ltd director convicted",
           snippet="fraud conviction upheld", credibility_tier=4),
    ]}
    mat = ddo._adverse_media_materiality(am)
    assert mat["material"] is True and mat["credible_count"] == 2


def test_rf3022_official_domain_counts_even_when_worded_neutrally():
    """A court/regulator record is adverse by construction — do not require lexicon."""
    am = {"ok": True, "findings": [
        _f(source_url="https://www.bailii.org/ew/cases/EWHC/2024/1.html",
           title="Acme Ltd v Beta Ltd", snippet="judgment of the court",
           source_class="legal_court_uk", query_executed='"Acme" site:bailii.org',
           credibility_tier=1),
    ]}
    mat = ddo._adverse_media_materiality(am)
    assert mat["official"] == 1 and mat["material"] is True


# ── 2. dedup ───────────────────────────────────────────────────────────────
def test_rf3022_same_url_counts_once_however_many_templates_found_it():
    """10 unique URLs must never render as 37."""
    dupes = [_f(source_url="https://www.ft.com/a", title="Acme fined for fraud",
                query_executed=f"template {i}", credibility_tier=4) for i in range(8)]
    mat = ddo._adverse_media_materiality({"ok": True, "findings": dupes})
    assert mat["raw_count"] == 8 and mat["unique_count"] == 1
    assert mat["duplicates_dropped"] == 7
    assert mat["credible_count"] == 1, "one article is one item"
    assert mat["material"] is False, "a single item is below the materiality threshold"


# ── 3. self-reference ──────────────────────────────────────────────────────
def test_rf3022_aria_own_memory_cannot_corroborate_itself():
    am = {"ok": True, "findings": [
        _f(source_url="memory://brain_hook:web_search", title="brain_hook:web_search",
           snippet="fraud investigation"),
        _f(source_url="memory://brain_hook:companies_house", title="brain_hook:companies_house",
           snippet="fraud investigation"),
    ]}
    mat = ddo._adverse_media_materiality(am)
    assert mat["self_references_dropped"] == 2
    assert mat["material"] is False, "a system citing itself is not two sources"


def test_rf3022_provenance_guard_reads_the_key_the_findings_use():
    """R-F2999's guard checked url/link; findings carry source_url — so it reported
    'URLs were not carried through' about items that HAD URLs, and passed the
    memory:// items it exists to catch."""
    assert ddo._adverse_example_has_provenance(_f(source_url="https://www.ft.com/a")) is True
    assert ddo._adverse_example_has_provenance(
        _f(source_url="memory://brain_hook:web_search")) is False
    assert ddo._adverse_example_has_provenance({"title": "no url"}) is False


def test_rf3022_example_renders_the_real_url():
    out = ddo._format_adverse_example(_f(source_url="https://www.ft.com/x",
                                         title="Acme fined", date="2026-01-02"))
    assert "https://www.ft.com/x" in out


# ── 4. R-F3023 — class derived from the RESULT ─────────────────────────────
def test_rf3023_domain_extraction_and_site_operator():
    assert rs._result_domain("https://www.bailii.org/ew/x") == "bailii.org"
    assert rs._result_domain("memory://brain_hook") == "", "not an external source"
    assert rs._query_site_hosts('"Acme" site:justice.gov') == ["justice.gov"]


def test_rf3023_contradicted_class_is_false_unconstrained_is_none():
    # the live failure: a site:justice.gov query answered by doi.org
    assert rs._domain_corroborates_class("doi.org", "regulatory_us_doj",
                                         '"Acme" site:justice.gov') is False
    assert rs._domain_corroborates_class("justice.gov", "regulatory_us_doj",
                                         '"Acme" site:justice.gov') is True
    assert rs._domain_corroborates_class("www.sub.bailii.org".replace("www.", ""),
                                         "legal_court_uk", '"A" site:bailii.org') is True
    # no site: constraint → nothing to corroborate. None is NOT False.
    assert rs._domain_corroborates_class("ft.com", "press_general", '"Acme" fraud') is None


def test_rf3023_contradicted_items_do_not_count_toward_a_verdict():
    am = {"ok": True, "findings": [
        _f(source_url="https://doi.org/1", title="Cytochrome P450 in E. coli fraud study",
           source_class="regulatory_us_doj", source_class_corroborated=False,
           credibility_tier=2),
        _f(source_url="https://doi.org/2", title="Deer hunting breach of habitat",
           source_class="legal_court_uk", source_class_corroborated=False,
           credibility_tier=2),
    ]}
    mat = ddo._adverse_media_materiality(am)
    assert mat["class_contradicted_dropped"] == 2
    assert mat["material"] is False


def test_rf3023_findings_carry_result_derived_provenance():
    import inspect
    src = module_source(rs)
    assert '"source_domain": _domain' in src
    assert '"source_class_corroborated"' in src


# ── the user-visible outcome ───────────────────────────────────────────────
def test_rf3022_verdict_is_not_escalated_on_the_live_defect_input():
    """CAPABILITY: drive the real verdict path with the real live shape."""
    body = {"risk_classification": "GREEN", "synthesis": {"key_findings": []},
            "identity": {"entity_name": "EFT CONSULT LTD"}}
    findings = []
    for i in range(14):
        findings.append(_f(
            source_url="https://find-and-update.company-information.service.gov.uk/company/11346584/officers",
            title="EFT CONSULT LTD people - GOV.UK", query_executed=f"t{i}"))
    for i in range(14):
        findings.append(_f(source_url="memory://brain_hook:web_search",
                           title="brain_hook:web_search", query_executed=f"m{i}"))
    for i in range(9):
        findings.append(_f(source_url=f"https://doi.org/10.1016/{i}",
                           title="Expression of Eukaryotic Cytochromes P450 in E. coli",
                           source_class="regulatory_us_doj",
                           source_class_corroborated=False, query_executed=f"d{i}"))
    out = ddo._apply_adverse_media_to_verdict(body, {"ok": True, "findings": findings})
    assert out["escalated"] is False, "37 non-adverse hits must not move the verdict"
    assert body["risk_classification"] == "GREEN"
    assert "adverse_media_escalated" not in body
    # and it must not go silent about having looked
    note = body["synthesis"]["key_findings"][-1]
    assert "nothing material" in note["title"]
    assert "37 raw item(s)" in note["detail"]
    assert "not proof of good standing" in note["detail"]


def test_rf3022_escalation_detail_shows_the_arithmetic():
    body = {"risk_classification": "GREEN", "synthesis": {"key_findings": []},
            "identity": {"entity_name": "Acme Ltd"}}
    findings = [
        _f(source_url="https://www.ft.com/a", title="Acme Ltd fined for bribery",
           credibility_tier=4),
        _f(source_url="https://www.ft.com/a", title="Acme Ltd fined for bribery",
           credibility_tier=4),                       # duplicate
        _f(source_url="https://www.reuters.com/b", title="Acme director convicted of fraud",
           credibility_tier=4),
    ]
    out = ddo._apply_adverse_media_to_verdict(body, {"ok": True, "findings": findings})
    assert out["escalated"] is True and body["risk_classification"] == "AMBER-LIGHT"
    detail = body["synthesis"]["key_findings"][-1]["detail"]
    assert "de-duplicated by URL from 3 raw hit(s)" in detail
    assert "https://www.ft.com/a" in detail, "the example must carry its URL"

"""R-F3090 — one question, three answers: the false-clean finding titles.

LIVE DEFECT (Mitie, operator report 2026-07-26). A single report page said, of the
same company, all at once:

    [INFO] Financial profile — no red flags        (compliance findings)
           Financial health  UNKNOWN               (compliance highlight)
    ✗      Financial capacity — UNRESOLVED         (decision-readiness scorecard)

and, of the same sanctions screen:

           Sanctions matches  2                    (identity highlight)
    [INFO] Sanctions/PEP screen — no entity-name match … No real hits.  (finding)

Neither is an arithmetic bug. Both are SCOPE bugs:

  * `financial_findings` measures SHELL RISK (dormant / overdue / formation-agent
    address / inactive status). It says nothing about solvency, and its `else`
    branch fires on ABSENCE — including when COMPANIES_HOUSE_API_KEY is unset
    (financial_dd.py:82 returns score 0.0, indicators []), so a company that was
    never checked was reported as having "no red flags".
  * The "Sanctions matches" chip rendered `len(screen["matches"])` — the RAW
    pre-classification count — while `classify_matches` had already established that
    every hit was name-overlap noise. Only the prose summary carried that truth.
"""
from aria_service.intel import dd_schema
from aria_service.intel.financial_dd import financial_findings


# ── financial_dd: absence is not a clean bill ──────────────────────────────
def test_rf3090_missing_api_key_is_not_no_red_flags():
    """THE WORST CASE: never checked, reported clean."""
    profile = {"error": "COMPANIES_HOUSE_API_KEY not configured",
               "shell_indicators": [], "shell_risk_score": 0.0}
    f = financial_findings(profile)[0]
    assert "no red flags" not in f["title"].lower()
    assert "NOT ASSESSED" in f["title"]
    assert "ABSENCE OF DATA, not a clean result" in f["detail"]
    assert f["confidence"] == "UNVERIFIED"


def test_rf3090_empty_profile_is_not_no_red_flags():
    f = financial_findings({"shell_indicators": [], "shell_risk_score": 0.0,
                            "accounts_type": "unknown", "filing_history": []})[0]
    assert "NOT ASSESSED" in f["title"]


def test_rf3090_clean_screen_is_titled_by_what_it_measured():
    """Mitie's real profile: full accounts, no shell indicator. That is a SHELL
    result, and must not read as a financial-health clearance."""
    profile = {"shell_indicators": [], "shell_risk_score": 0.0,
               "accounts_type": "medium-full", "latest_accounts_date": "2025-03-31",
               "filing_history": [{"type": "AA"}],
               "financial_summary": "Accounts: medium-full | Latest filing: 2025-03-31"}
    f = financial_findings(profile)[0]
    assert f["title"] == "Shell-company risk screen — no indicators"
    assert "does NOT assess solvency" in f["detail"]
    assert "financial capacity" in f["detail"]


def test_rf3090_real_shell_indicators_still_escalate():
    """The retitling must not blunt the detector."""
    f = financial_findings({"shell_risk_score": 0.7, "accounts_type": "dormant",
                            "shell_indicators": ["DORMANT: no trading activity"]})[0]
    assert f["severity"] == "red" and "HIGH shell company risk" in f["title"]

    f2 = financial_findings({"shell_risk_score": 0.4, "accounts_type": "micro-entity",
                             "shell_indicators": ["MICRO-ENTITY: files micro accounts"]})[0]
    assert f2["severity"] == "amber"


# ── sanctions chip: the filtered count, not the raw one ────────────────────
def test_rf3090_all_noise_screen_does_not_advertise_two_matches():
    """THE LIVE SYMPTOM: 'Sanctions matches 2' above 'No real hits'."""
    screen = {"matches": [{"name": "M B CARS"}, {"name": "MG Global"}],
              "match_classification": {"total": 2, "noise_filtered": 2,
                                       "actionable": 0, "worst_severity": "info"}}
    metric = dd_schema._sanctions_match_metric(screen)
    assert metric is not None
    assert not metric.startswith("2"), "the raw count must not lead"
    assert "none" in metric and "filtered" in metric


def test_rf3090_real_hits_are_reported_with_the_noise_accounted_for():
    screen = {"matches": [{}, {}, {}],
              "match_classification": {"total": 3, "noise_filtered": 2,
                                       "actionable": 1, "worst_severity": "hard_stop"}}
    metric = dd_schema._sanctions_match_metric(screen)
    assert metric.startswith("1")
    assert "2 filtered" in metric, "a dropped match must still be accounted for"


def test_rf3090_unscreened_entity_shows_no_chip_at_all():
    assert dd_schema._sanctions_match_metric({}) is None
    assert dd_schema._sanctions_match_metric(None) is None


def test_rf3090_legacy_blob_is_labelled_not_silently_raw():
    """A report written before the classification was persisted must not have its
    raw count laundered into a filtered one."""
    metric = dd_schema._sanctions_match_metric({"matches": [{}, {}]})
    assert "raw" in metric and "unclassified" in metric


# ── the user-visible surface ───────────────────────────────────────────────
def test_rf3090_structured_view_renders_the_filtered_sanctions_chip():
    """CAPABILITY: drive `structured_view`, the contract the online report renders."""
    report = {
        "identity": {
            "entity_name": "MITIE FACILITIES MANAGEMENT LIMITED",
            "entity_type": "company", "registration_number": "02938041",
            "sanctions_screen": {
                "matches": [{"name": "M B CARS"}, {"name": "MG Global"}],
                "match_classification": {"total": 2, "noise_filtered": 2,
                                         "actionable": 0, "worst_severity": "info"},
            },
        },
    }
    sv = dd_schema.structured_view(report)
    ident = next(s for s in sv["sections"] if s["key"] == "identity")
    chip = next(h for h in ident["highlights"] if h["label"] == "Sanctions matches")
    assert chip["value"] != "2", (
        "R-F3090 REGRESSION: the raw pre-filter sanctions count is back on the report")
    assert "none" in chip["value"]

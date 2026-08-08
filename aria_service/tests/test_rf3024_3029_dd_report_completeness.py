"""R-F3024..R-F3029 — the review findings on live report dd_16db41eb5fa8.

Each defect below was verified against the live report AND (where it is a registry
fact) against the Companies House API on 2026-07-25.

R-F3024 name history — 07833187 traded as ENGINEERING FOR THE FUTURE LIMITED until
  2025-12-24, when 11346584 (SAME registered office) took that name. The report held
  ZERO name history and scored commercial-coherence 1.0/GREEN "no structural
  anomalies", ghost 0/28.
R-F3025 FCA — "EFT Consultancy Services Limited (PO5 3DZ)" was reported at AMBER as
  the subject's own status. Subject is EFT CONSULT LTD, SA7 9FG. `_name_match_score`
  was computed and never read.
R-F3026 render — directors and PSCs are collected in full and dropped by all three
  renderers, while the scorecard claims directors as identity evidence.
R-F3027 UBO — `Raven Delta Limited` holds 75-100% of shares and votes plus the right
  to appoint/remove directors. LIVE-VERIFIED: its CH `identification` is
  {legal_form, legal_authority} with NO registration_number, so the Grade-A anchor
  test dropped it silently and readiness said ownership ANSWERED.
R-F3028 financial — the CH narrative was APPENDED to the stale SEC "UNKNOWN" one; and
  `elif "full" in desc` matched `mortgage-satisfy-charge-full`.
R-F3029 disclosure — the digital layer errored "timeout after 90s" and said so in no
  data-gaps list; the PDF's only stated blocker was a tautology.
"""
from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import financial_health as fh
from aria_service.intel import fca_register as fca
from aria_service.intel.dd_schema import (
    ARKDDReport, _dd_decision_readiness, _format_officer, _format_psc,
    _format_previous_name,
)

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


def _report(name="EFT CONSULT LTD"):
    r = ARKDDReport()
    r.identity.entity_name = name
    return r


# ── R-F3024 — name history ─────────────────────────────────────────────────
_PREV = [{"name": "ENGINEERING FOR THE FUTURE LIMITED",
          "effective_from": "2011-11-03", "ceased_on": "2025-12-24"}]


def test_rf3024_recent_change_detected_old_one_is_not():
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
    old = (datetime.now(timezone.utc).date() - timedelta(days=900)).isoformat()
    assert len(ddo._recent_name_changes([{"name": "A", "ceased_on": recent}])) == 1
    assert ddo._recent_name_changes([{"name": "B", "ceased_on": old}]) == []
    # an unparseable/absent date must NOT be invented into recency
    assert ddo._recent_name_changes([{"name": "C"}]) == []
    assert ddo._recent_name_changes([{"name": "D", "ceased_on": "not-a-date"}]) == []
    assert ddo._recent_name_changes("nonsense") == []


def test_rf3024_previous_names_render():
    r = _report()
    r.identity.previous_names = _PREV
    md = r.render_markdown()
    assert "ENGINEERING FOR THE FUTURE LIMITED" in md
    assert "2025-12-24" in md


def test_rf3024_profile_carries_name_history():
    import inspect
    from aria_service.intel import companies_house as ch
    assert '"previous_company_names": data.get("previous_company_names")' \
        in function_source(ch, "get_company_profile")


# ── R-F3025 — FCA attribution ──────────────────────────────────────────────
def test_rf3025_the_live_mismatch_scores_below_threshold():
    score = fca._name_match_score("EFT CONSULT LTD", "EFT Consultancy Services Limited")
    assert score < fca._min_name_match(), (
        f"a {score:.2f} name match must not attribute another firm's FCA status")


def test_rf3025_postcode_corroboration_is_tri_state():
    assert fca._postcode_corroborates("SA7 9FG", {"Postcode": "sa79fg"}) is True
    assert fca._postcode_corroborates("SA7 9FG", {"Postcode": "PO5 3DZ"}) is False
    # absent on either side → cannot corroborate; NOT a contradiction
    assert fca._postcode_corroborates("", {"Postcode": "PO5 3DZ"}) is None
    assert fca._postcode_corroborates("SA7 9FG", {}) is None


def test_rf3025_orchestrator_reports_a_non_attribution_not_an_amber():
    import inspect
    src = module_source(ddo)
    assert 'elif _fca.get("best_candidate"):' in src
    i = src.index('elif _fca.get("best_candidate"):')
    window = src[i:i + 1400]
    assert 'severity="info"' in window, "a non-attribution can never be AMBER"
    assert "NOT this subject" in window
    assert "FCA authorisation UNKNOWN" in window


# ── R-F3026 — render the people ────────────────────────────────────────────
_DIRS = [
    {"name": "JENKINS, Christopher Michael", "officer_role": "director",
     "appointed_on": "2015-04-01", "nationality": "British",
     "officer_id": "HsDGsolTa3NnYGAnedDaLJLG02M"},
    {"name": "KIEFT, David John", "officer_role": "director", "appointed_on": "2011-11-03"},
]
_PSCS = [{"name": "Raven Delta Limited",
          "kind": "corporate-entity-person-with-significant-control",
          "natures_of_control": ["ownership-of-shares-75-to-100-percent",
                                 "voting-rights-75-to-100-percent",
                                 "right-to-appoint-and-remove-directors"],
          "identification": {"legal_form": "Private Limited Company"}}]


def test_rf3026_formatters_render_only_present_fields():
    line = _format_officer(_DIRS[0])
    assert "JENKINS, Christopher Michael" in line and "appointed 2015-04-01" in line
    assert _format_officer({}) == "" and _format_officer(None) == ""
    psc = _format_psc(_PSCS[0])
    assert "Raven Delta Limited" in psc and "ownership of shares 75 to 100 percent" in psc
    assert "until 2025-12-24" in _format_previous_name(_PREV[0])


def test_rf3026_markdown_names_directors_and_pscs():
    """CAPABILITY: the surface the scorecard already claims as evidence."""
    r = _report()
    r.identity.directors = _DIRS
    r.identity.shareholders = _PSCS
    md = r.render_markdown()
    assert "JENKINS, Christopher Michael" in md
    assert "KIEFT, David John" in md
    assert "Raven Delta Limited" in md


def test_rf3026_structured_view_names_them_too():
    from aria_service.intel.dd_schema import structured_view
    r = _report()
    r.identity.directors = _DIRS
    r.identity.shareholders = _PSCS
    r.identity.previous_names = _PREV
    blob = str(structured_view(r.as_dict()))
    assert "JENKINS, Christopher Michael" in blob
    assert "Raven Delta Limited" in blob
    assert "ENGINEERING FOR THE FUTURE LIMITED" in blob


def test_rf3026_pdf_generator_has_a_people_path():
    from pathlib import Path
    src = Path("lib/reports/pdf_generator.mjs").read_text(encoding="utf8")
    assert "_fmtOfficer" in src and "_fmtPsc" in src
    assert "Directors / officers" in src
    assert "Persons with significant control" in src


# ── R-F3027 — the untraversed controller ───────────────────────────────────
_UNANCHORED = [{"controller_name": "Raven Delta Limited",
                "controller_registration_number": "",
                "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
                "grade": "B"}]


def test_rf3027_corporate_psc_without_a_regno_is_carried_not_dropped():
    import inspect
    from aria_service.intel import companies_house as ch
    src = function_source(ch, "investigate_uk_entity")
    # R-F3037 widened the kind test from corporate-only to include legal-person
    # controllers (state / statutory bodies), which were falling through BOTH lists.
    assert '_is_controller_kind = ("corporate" in kind) or ("legal-person" in kind)' in src
    assert "if _is_controller_kind and not regno:" in src
    assert '"controlled_by_unanchored"' in module_source(ch)


def test_rf3027_ownership_is_not_answered_while_a_controller_is_untraversed():
    """THE DEFECT: a 75-100% controller absent from the walk, marked ANSWERED."""
    r = {"identity": {"shareholders": [{"name": "Raven Delta Limited",
                                        "natures_of_control": ["ownership-of-shares-75-to-100-percent"]}]},
         "network": {"ubo_chain": [{"name": "EFT CONSULT LTD"}, {"name": "a director"}],
                     "controlled_by_unanchored": _UNANCHORED,
                     "ubo_chain_walk": {"verdict": "traced", "coverage_gaps": [],
                                        "stats": {"budget_exhausted": False}}}}
    q = _dd_decision_readiness(r)["questions"]["ownership_control"]
    assert q["answered"] is False
    assert "Raven Delta Limited" in q["blocker"], "name the controller that was skipped"
    assert "NOT traversed" in q["blocker"]


def test_rf3027_ownership_still_answers_when_the_chain_is_actually_complete():
    """The gate must not become permanently un-passable."""
    r = {"identity": {"shareholders": [{"name": "Holdco Ltd", "hop": 1}]},
         "network": {"ubo_chain": [{"name": "Holdco Ltd", "hop": 1}],
                     "controlled_by_unanchored": [],
                     "ubo_chain_walk": {"stats": {"budget_exhausted": False}}}}
    q = _dd_decision_readiness(r)["questions"]["ownership_control"]
    assert q["answered"] is True and q["blocker"] == ""


# ── R-F3028 — financial coherence ──────────────────────────────────────────
def test_rf3028_superseded_summary_is_replaced_not_appended():
    prior = ("Financial health is UNKNOWN, not a clean bill. Not a US-listed filer. "
             "Companies House shows accounts filed to 2024-03-31.")
    out = fh._replace_superseded_summary(prior, "Positive net assets of GBP 69,482.")
    assert out.startswith("Positive net assets")
    assert "UNKNOWN" not in out, "the contradicted sentence must be gone"
    assert "US-listed filer" not in out
    assert "accounts filed to 2024-03-31" in out, "unrelated context is kept"


def test_rf3028_accounts_type_ignores_non_accounts_filings():
    """CAPABILITY — replay the REAL filing history that produced 'medium-full'.

    Item order is the live one: the charge filings precede the accounts filings, so
    the old loop hit `mortgage-satisfy-charge-full` and broke out before ever
    reaching an accounts filing. Companies House says every accounts filing here is
    accounts-with-accounts-type-SMALL."""
    import asyncio
    from unittest.mock import patch, AsyncMock, MagicMock
    from aria_service.intel import financial_dd

    live_filings = [
        {"date": "2026-04-30", "type": "PSC04", "category": "persons-with-significant-control",
         "description": "change-person-with-significant-control-details"},
        {"date": "2025-06-13", "type": "MR04", "category": "mortgage",
         "description": "mortgage-satisfy-charge-full"},          # ← the collision
        {"date": "2025-06-11", "type": "MR01", "category": "mortgage",
         "description": "mortgage-create-charge"},
        {"date": "2024-12-20", "type": "AA", "category": "accounts",
         "description": "accounts-with-accounts-type-small"},
    ]

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    async def go():
        client = MagicMock()

        async def _get(url, **kw):
            if "filing-history" in url:
                return _Resp({"items": live_filings})
            return _Resp({"company_name": "EFT CONSULT LTD"})

        client.get = _get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(financial_dd.httpx, "AsyncClient", return_value=client):
            return await financial_dd.get_financial_profile("07833187", ch_api_key="k")

    out = asyncio.run(go())
    assert out.get("accounts_type") == "small", (
        f"a charge satisfaction must not be read as an accounts regime "
        f"(got {out.get('accounts_type')!r})")


# ── R-F3029 — disclosure of a truncated layer ──────────────────────────────
def test_rf3029_errored_layer_states_itself_in_its_own_data_gaps():
    r = _report()
    r.digital.meta.status = "error"
    r.digital.meta.error = "timeout after 90s"
    added = ddo._mark_layer_errors_as_gaps(r)
    assert added == 1
    gap = r.digital.data_gaps[-1]
    assert "timeout after 90s" in gap and "TRUNCATED" in gap
    assert "unchecked, not as clean" in gap
    # idempotent — _assemble_bluf can run more than once
    assert ddo._mark_layer_errors_as_gaps(r) == 0
    assert len(r.digital.data_gaps) == 1


def test_rf3029_completed_layers_get_no_gap():
    r = _report()
    r.digital.meta.status = "ok"
    assert ddo._mark_layer_errors_as_gaps(r) == 0
    assert r.digital.data_gaps == []


def test_rf3029_pdf_expands_the_circular_grade_blocker():
    from pathlib import Path
    src = Path("lib/reports/pdf_generator.mjs").read_text(encoding="utf8")
    assert "quality_assessment" in src, "the PDF never read the real reasons"
    assert "qaBlocking" in src

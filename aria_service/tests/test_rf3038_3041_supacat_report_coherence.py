"""R-F3038..R-F3041 — four coherence defects found by auditing a real DD.

All four surfaced in dd_71553f511d72 (SUPACAT LIMITED 01514084, 2026-07-25), the
maker of the Jackal and Coyote high-mobility vehicles for the British Army. The run
was otherwise the best yet — 12/12 layers, 5/5 decision-critical questions answered,
real iXBRL figures that reconcile to the pound — which is exactly why these four
stood out.

R-F3038  11 sanctions lists screened, `screened_at: None`. R-F3031 stamped the FIRST
         of two places that assign identity.sanctions_screen; a company DD takes the
         SECOND (the alias/OFSI path).
R-F3039  the BLUF read "but only 5/5 decision-critical questions are answered
         (100%)" — self-contradictory, and it buried the real blocker (evidence
         grade C) behind a complaint about coverage that was complete.
R-F3040  "Export control: civilian or unclassified" on a military-vehicle
         manufacturer. `declared_activity` is set to raw SIC CODES
         ("30400, 30990, 33170, 71129") and UK SIC 30400 is verbatim "Manufacture of
         military fighting vehicles" — the classifier was handed bare digits.
R-F3041  the basis line cited "the small-company exemption" on a FULL-accounts filer
         with £19.97m net assets. That exemption does not apply to it.
"""
from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import financial_health as fh

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


# ── R-F3038 ────────────────────────────────────────────────────────────────
def test_rf3038_both_screen_assignment_sites_stamp_a_date():
    import inspect
    src = module_source(ddo)
    # R-F3219 added a THIRD site — the re-screen under the registered legal name,
    # once Companies House resolves a different name from the one supplied. This
    # test's own instruction was "if a third appears, it needs the stamp too", so
    # the count moves and the new site is held to the same rule below.
    # R-F3443 — a FOURTH site appeared (R-F3411's `_record_waived_screen`), and it is the
    # first one that must NOT stamp a date. The original instruction here was "if a fourth
    # appears, it needs the stamp too", which was right for every site that represents a
    # screen that RAN. A waived screen did not run: stamping `screened_at` would assert
    # that it did, which is precisely the false claim this file exists to prevent. So the
    # count moves and the new site is held to the OPPOSITE rule, asserted below.
    # R-F3535 — the four sites now route through `_record_sanctions_screen`, which
    # assigns the screen AND runs the evidence shadow, so a new site cannot silently
    # bypass the shadow. Counting the RECORDER calls preserves this guard's property
    # exactly (a fifth site must be classified explicitly); only the shape it counts
    # changed. The bare assignment that remains is the recorder's own.
    # Count CALLS, not the `def` — a function's signature contains the same text as a
    # call to it, and matching that inflated this count to 5 on the first cut.
    _calls = [ln for ln in src.splitlines()
              if "_record_sanctions_screen(report" in ln
              and not ln.lstrip().startswith(("def ", "#"))]
    assert len(_calls) == 4, (
        "if a fifth appears, decide explicitly whether it represents a screen that RAN "
        "(stamp the date) or one that did not (leave screened_at None) — and assert it "
        f"here. Found: {_calls}")
    assert src.count("report.identity.sanctions_screen = ") == 1, (
        "a screen is assigned outside `_record_sanctions_screen` — that site bypasses "
        "the R-F3535 evidence shadow and makes the agreement data silently partial")
    assert '"screened_at": datetime.now(timezone.utc)' in src      # site 1 (R-F3031)
    assert 'screen["screened_at"] = datetime.now(timezone.utc)' in src  # site 2 (R-F3038)
    # site 3 (R-F3219) — stamped before it is assigned, same never-overwrite rule
    _rescreen = function_source(ddo, "_rescreen_under_registered_name")
    assert 'screen["screened_at"] = datetime.now(timezone.utc)' in _rescreen
    assert 'if not screen.get("screened_at")' in _rescreen

    # site 4 (R-F3411) — the WAIVED screen. It must carry screened_at=None and screened
    # False, so the R-F3229 branch renders it as declined and it can never read as CLEAN.
    _waived = function_source(ddo, "_record_waived_screen")
    assert '"screened_at": None' in _waived, (
        "a waived screen must NOT carry a screen date — a timestamp would claim the "
        "screen was performed when the operator declined it")
    assert '"screened": False' in _waived
    assert 'datetime.now' not in _waived, (
        "no clock belongs in a screen that never ran")


def test_rf3038_existing_stamp_is_never_overwritten():
    import inspect
    src = module_source(ddo)
    assert 'if isinstance(screen, dict) and not screen.get("screened_at"):' in src


# ── R-F3039 ────────────────────────────────────────────────────────────────
def test_rf3039_full_coverage_never_reads_as_only():
    """R-F3050 moved this wording into a shared helper so BOTH BLUF writers use it;
    assert the BEHAVIOUR through that helper rather than the old inline source."""
    clause = ddo._coverage_clause({"answered": 5, "required": 5, "completion_pct": 100})
    assert clause.startswith("all 5/5")
    assert "only" not in clause
    assert "the evidence behind them does not yet meet the reliance bar" in clause


def test_rf3039_partial_coverage_still_says_only():
    clause = ddo._coverage_clause({"answered": 4, "required": 5, "completion_pct": 80})
    assert clause.startswith("only 4/5"), "the partial wording must survive"
    assert "(80%)" in clause


# ── R-F3040 ────────────────────────────────────────────────────────────────
def test_rf3040_sic_30400_is_expanded_to_its_official_description():
    txt = ddo._describe_sic_codes(["30400", "30990", "33170", "71129"])
    assert "Manufacture of military fighting vehicles" in txt
    assert "SIC 30400" in txt


def test_rf3040_unknown_codes_are_not_invented():
    assert ddo._describe_sic_codes(["99999"]) == ""
    assert ddo._describe_sic_codes([]) == ""
    assert ddo._describe_sic_codes(None) == ""


def test_rf3040_military_codes_are_identified_dual_use_ones_are_not():
    mil = ddo._military_sic_codes(["30400", "33170", "71129"])
    assert [c for c, _ in mil] == ["30400"], "only outright-military codes flag"
    assert ddo._military_sic_codes(["72190", "26300"]) == [], (
        "R&D and comms equipment are dual-use context, not a military declaration")
    assert [c for c, _ in ddo._military_sic_codes(["25400", "84220"])] == ["25400", "84220"]


def test_rf3040_the_live_declared_activity_string_yields_the_military_code():
    """The live path: declared_activity is the raw comma-joined SIC list."""
    declared = "30400, 30990, 33170, 71129"      # verbatim from the Supacat report
    codes = [c.strip() for c in declared.split(",") if c.strip().isdigit()]
    assert codes == ["30400", "30990", "33170", "71129"]
    assert "military fighting vehicles" in ddo._describe_sic_codes(codes)
    assert ddo._military_sic_codes(codes), "the registry declares military manufacture"


def test_rf3040_a_civilian_read_against_a_military_sic_is_flagged_amber():
    import inspect
    src = module_source(ddo)
    i = src.index("_mil_sics = _military_sic_codes(_sic_codes)")
    window = src[i:i + 2200]
    assert '"civilian" in _rec.lower()' in window
    assert 'severity="amber" if _contradiction else "info"' in window
    assert "Registry-declared MILITARY activity" in window
    assert "not an inference" in window
    # and it must be recorded as a gap, not just a finding
    assert "the automated read is " in window


# ── R-F3041 ────────────────────────────────────────────────────────────────
def test_rf3041_full_accounts_do_not_cite_the_small_company_exemption():
    basis = fh._balance_sheet_basis("accounts-with-accounts-type-full")
    assert "small-company exemption" not in basis
    assert "scope limit of the reader" in basis, (
        "the limit is ours, not the filer's — say so")


def test_rf3041_small_filers_still_get_the_exemption_explanation():
    for t in ("accounts-with-accounts-type-small", "micro-entity accounts",
              "accounts-with-accounts-type-dormant"):
        basis = fh._balance_sheet_basis(t)
        assert "small-company exemption" in basis, t


def test_rf3041_unknown_accounts_type_makes_no_claim_about_exemptions():
    basis = fh._balance_sheet_basis(None)
    assert "small-company exemption" not in basis
    assert "balance sheet only" in basis


# ── R-F3043 — derived text must not be served frozen from the vault ────────
def test_rf3043_vault_profile_gets_its_basis_line_re_derived():
    """LIVE (dd_f4a7635c6efa): R-F3041's corrected wording did NOT appear, because
    `uk_balance_sheet.basis` was frozen into the vault by an earlier run and
    assess() returns the cached profile verbatim (`from_vault: True`)."""
    stale = {
        "from_vault": True,
        "uk_balance_sheet": {
            "figures": {"net_assets": {"current": 19968397.0}},
            "accounts_type": "accounts-with-accounts-type-full",
            "basis": ("balance sheet only — P&L (turnover/profit) not publicly filed "
                      "under the small-company exemption"),   # the stale sentence
        },
    }
    fh._refresh_derived_text(stale)
    basis = stale["uk_balance_sheet"]["basis"]
    assert "small-company exemption" not in basis, "the stale sentence must be replaced"
    assert "scope limit of the reader" in basis


def test_rf3043_figures_are_never_touched_only_the_words():
    prof = {"uk_balance_sheet": {"figures": {"net_assets": {"current": 19968397.0}},
                                 "accounts_type": "small", "basis": "x"},
            "health_verdict": "STRONG", "data_available": True}
    fh._refresh_derived_text(prof)
    assert prof["uk_balance_sheet"]["figures"]["net_assets"]["current"] == 19968397.0
    assert prof["health_verdict"] == "STRONG" and prof["data_available"] is True
    assert "small-company exemption" in prof["uk_balance_sheet"]["basis"], (
        "a genuine small filer keeps the exemption explanation")


def test_rf3043_unavailable_explanation_is_refreshed_too():
    prof = {"financial_figures_unavailable": {
        "reason": "accounts_not_machine_readable", "made_up_to": "2025-10-31",
        "accounts_type": "accounts-with-accounts-type-full", "pages": 42,
        "explanation": "stale text"}}
    fh._refresh_derived_text(prof)
    exp = prof["financial_figures_unavailable"]["explanation"]
    assert "2025-10-31" in exp and "42" in exp and "stale text" not in exp


def test_rf3043_never_raises_on_a_malformed_profile():
    for bad in ({}, {"uk_balance_sheet": None}, {"uk_balance_sheet": {"figures": {}}},
                {"financial_figures_unavailable": "not a dict"}):
        fh._refresh_derived_text(bad)      # must not raise


def test_rf3043_is_wired_into_the_vault_hit_path():
    import inspect
    src = function_source(fh, "assess")
    i = src.index('cached["from_vault"] = True')
    window = src[i:i + 1800]
    assert "_refresh_derived_text(cached)" in window
    assert window.index("_refresh_derived_text(cached)") < window.index("return cached")


# ── R-F3050 — ONE coverage clause, used by BOTH BLUF writers ───────────────
def test_rf3050_both_bluf_writers_use_the_shared_helper():
    """The downloaded PDF of dd_f4a7635c6efa still read "but only 5/5 ... (100%)"
    after R-F3039, because a SECOND writer (_refresh_persisted_decision_readiness,
    which rewrites the BLUF once the adverse-media follow-up merges) carried its own
    copy. That follow-up is precisely what moves a report from 4/5 to 5/5, so the
    one path able to produce "only 5/5" was the one still unfixed."""
    import inspect
    src = module_source(ddo)
    assert src.count("def _coverage_clause(") == 1, "exactly one implementation"
    # R-F3116 — STRENGTHENED, not relaxed. R-F3050 made the two BLUF writers share
    # the coverage CLAUSE; they still had two copies of everything else, and that
    # remaining fork is exactly how R-F3091/R-F3092 came to be applied to only one
    # of them and shipped dead on the customer path (proven by a live Mitie run:
    # entity_scope None, next_actions as verbatim blocker restatements). The two
    # writers now share the WHOLE composition, so asserting they both call one
    # helper is superseded by asserting there is one writer.
    assert src.count("def compose_decision_bluf(") == 1, "exactly one BLUF writer"
    assert "_bluf = compose_decision_bluf(_ready, name)" in src, "synthesis delegates"
    assert "compose_decision_bluf(readiness, _name0)" in src, "follow-up delegates"
    assert "_coverage_clause(readiness)" in src, "the one writer uses the one clause"
    assert "decision-critical questions are answered ({readiness.get" not in src


def test_rf3050_clause_is_coherent_at_full_and_partial_coverage():
    full = ddo._coverage_clause({"answered": 5, "required": 5, "completion_pct": 100})
    assert full.startswith("all 5/5")
    assert "only" not in full, "the contradiction the operator saw in the PDF"
    assert "does not yet meet the reliance bar" in full
    part = ddo._coverage_clause({"answered": 3, "required": 5, "completion_pct": 60})
    assert part.startswith("only 3/5") and "(60%)" in part


def test_rf3050_defaults_are_safe_on_a_malformed_readiness():
    assert "5/5" in ddo._coverage_clause({"answered": 5})     # required defaults to 5
    assert ddo._coverage_clause({})                            # must not raise

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


# ── R-F3038 ────────────────────────────────────────────────────────────────
def test_rf3038_both_screen_assignment_sites_stamp_a_date():
    import inspect
    src = inspect.getsource(ddo)
    # there are two assignments; both must carry the stamp
    assert src.count("report.identity.sanctions_screen = ") == 2, (
        "if a third appears, it needs the stamp too")
    assert '"screened_at": datetime.now(timezone.utc)' in src      # site 1 (R-F3031)
    assert 'screen["screened_at"] = datetime.now(timezone.utc)' in src  # site 2 (R-F3038)


def test_rf3038_existing_stamp_is_never_overwritten():
    import inspect
    src = inspect.getsource(ddo)
    assert 'if isinstance(screen, dict) and not screen.get("screened_at"):' in src


# ── R-F3039 ────────────────────────────────────────────────────────────────
def test_rf3039_full_coverage_never_reads_as_only():
    import inspect
    src = inspect.getsource(ddo)
    i = src.index('_answered = _ready.get("answered", 0)')
    window = src[i:i + 1400]
    assert "if _answered >= _required:" in window
    assert 'all {_required}/{_required} decision-critical questions are answered' in window
    assert "the evidence behind them does not yet meet the reliance bar" in window


def test_rf3039_partial_coverage_still_says_only():
    import inspect
    src = inspect.getsource(ddo)
    i = src.index('_answered = _ready.get("answered", 0)')
    window = src[i:i + 1400]
    assert 'only {_answered}/{_required}' in window, "the partial wording must survive"


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
    src = inspect.getsource(ddo)
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

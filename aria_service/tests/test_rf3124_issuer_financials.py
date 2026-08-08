"""R-F3124 — answer financial capacity from the issuer's own annual report.

R-F3017 proved FOUR routes dead for a large PLC by live probe (CH iXBRL: not filed;
CH accounts PDF: a 129-page TIFF scan yielding 0 chars; FCA NSM: the API ignores the
query; subsidiary walk: a fabrication trap that billed Thales entities as Cohort's).
It concluded the only remaining route is the issuer's OWN published annual report —
"search-located, non-deterministic, needs an arithmetic self-check".

That route needs exactly the two surfaces the DD is pinned to: Brave finds the report,
Claude reads it. Non-deterministic means GUARDED, not trusted: an LLM can hallucinate a
number, and a fabricated solvency figure is the worst output this product could emit.
Four gates must ALL pass; any failure leaves the existing honest UNKNOWN untouched.

Methodology + the full route ledger: docs/DD_FINANCIAL_CAPACITY_JURISDICTION_PLAYBOOK.md
"""
import asyncio
import pathlib

import pytest

from aria_service.intel import financial_health as fh

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


# ── G1 PROVENANCE — the issuer's own domain ────────────────────────────────
@pytest.mark.parametrize("url,expected", [
    ("https://www.mitie.com/investors/Mitie-Annual-Report-2026.pdf", True),
    ("https://mitie.co.uk/results/report.pdf", True),          # .co.uk must work
    ("https://uk.investing.com/equities/mitie-group", False),  # name is in the PATH
    ("https://www.marketscreener.com/quote/MITIE-GROUP", False),
    ("", False),
])
def test_rf3124_g1_only_the_issuers_own_domain_counts(url, expected):
    """A third party's summary of a company's accounts is not the company's accounts.
    The check is on the HOST — a page that merely mentions the issuer in its path is
    not the issuer speaking."""
    assert fh._issuer_domain_matches(url, "Mitie Group PLC") is expected


# ── G4 ARITHMETIC — the anti-fabrication gate ──────────────────────────────
def test_rf3124_g4_a_real_balance_sheet_reconciles():
    ok, why = fh._arithmetic_reconciles(
        {"total_assets": 1_000_000, "total_liabilities": 600_000, "net_assets": 400_000})
    assert ok is True and "reconciles" in why


def test_rf3124_g4_fabricated_figures_are_REJECTED():
    """THE POINT. A model inventing plausible numbers will not produce a balance sheet
    that balances; a model reading a real one will."""
    ok, why = fh._arithmetic_reconciles(
        {"total_assets": 1_000_000, "total_liabilities": 600_000, "net_assets": 900_000})
    assert ok is False
    assert "does NOT reconcile" in why


def test_rf3124_g4_tolerance_is_rounding_only_not_slack():
    """2% covers presentation/rounding. It must not wave through a real mismatch."""
    ok, _ = fh._arithmetic_reconciles(
        {"total_assets": 1_000_000, "total_liabilities": 600_000, "net_assets": 405_000})
    assert ok is True, "1.5% is a rounding difference"
    ok2, _ = fh._arithmetic_reconciles(
        {"total_assets": 1_000_000, "total_liabilities": 600_000, "net_assets": 450_000})
    assert ok2 is False, "5% is a different balance sheet"


@pytest.mark.parametrize("figures", [
    {"total_assets": 1000},                                   # incomplete
    {"total_assets": "n/a", "total_liabilities": 1, "net_assets": 1},
    {"total_assets": 0, "total_liabilities": 0, "net_assets": 0},
    {},
])
def test_rf3124_g4_refuses_anything_unverifiable(figures):
    """An unverifiable figure must never answer a solvency question."""
    assert fh._arithmetic_reconciles(figures)[0] is False


# ── the gate chain fails CLOSED at every step ──────────────────────────────
def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_rf3124_no_llm_means_no_answer():
    out = _run(fh.extract_issuer_financials([{"url": "https://mitie.com/ar.pdf"}],
                                            "Mitie Group PLC", llm=None))
    assert out["ok"] is False and "no LLM" in out["reason"]


def test_rf3124_g1_blocks_a_third_party_document():
    class _LLM:
        async def complete(self, *a, **k):  # must never be reached
            raise AssertionError("G1 should have stopped this")
    out = _run(fh.extract_issuer_financials(
        [{"url": "https://uk.investing.com/equities/mitie-group", "title": "Mitie financials"}],
        "Mitie Group PLC", llm=_LLM()))
    assert out["ok"] is False
    assert out["gates"]["provenance"] is False
    assert "issuer's own domain" in out["reason"]


def test_rf3124_no_candidate_at_all_is_honest():
    out = _run(fh.extract_issuer_financials([], "Mitie Group PLC", llm=object()))
    assert out["ok"] is False and out["gates"].get("provenance") is False


def test_rf3124_result_defaults_to_not_ok():
    """The bar to move OFF the honest UNKNOWN is deliberately high; ok=False is the
    default and every early return preserves it."""
    out = _run(fh.extract_issuer_financials([], "X Ltd", llm=None))
    assert out["ok"] is False


# ── the methodology must stay recorded, not tribal ─────────────────────────
def test_rf3124_the_jurisdiction_playbook_exists_and_names_the_dead_routes():
    """OPERATOR (2026-07-26): record the steps, process and methodology so other
    jurisdictions can be built without guessing. A route ledger that is not written
    down gets re-probed blind every session — which is exactly what happened."""
    doc = pathlib.Path("docs/DD_FINANCIAL_CAPACITY_JURISDICTION_PLAYBOOK.md")
    assert doc.exists(), "the playbook is the durable deliverable"
    text = doc.read_text(encoding="utf-8")
    for dead in ("CH iXBRL", "TIFF scan", "National Storage Mechanism", "Subsidiary walk"):
        assert dead in text, f"the ledger must record {dead} and why it is dead"
    for gate in ("G1 PROVENANCE", "G2 TEXT LAYER", "G3 GROUNDING", "G4 ARITHMETIC"):
        assert gate in text
    assert "Adding a new jurisdiction" in text, "the procedure must be written down"
    assert "FABRICATION TRAP" in text, "route 5 must stay flagged as never-enable"


# ── the verdict is conservative and structural, never overreaching ─────────
@pytest.mark.parametrize("na,ta,tl,expected", [
    (400, 1000, 600, "STRONG"),        # 40% equity ratio
    (150, 1000, 850, "STABLE"),        # 15%
    (50, 1000, 950, "WEAK"),           # 5%
    (-100, 1000, 1100, "DISTRESSED"),  # balance-sheet insolvent
])
def test_rf3124_verdict_from_a_reconciled_balance_sheet(na, ta, tl, expected):
    assert fh._verdict_from_issuer_report(
        {"total_assets": ta, "total_liabilities": tl, "net_assets": na}) == expected


def test_rf3124_verdict_is_unknown_on_anything_unusable():
    """Never guess a verdict off missing figures — and NEVER report missing data as
    DISTRESSED. An all-zero extraction reading as balance-sheet insolvency is a false
    ACCUSATION, the mirror of a false clean and just as damaging."""
    for bad in ({}, {"total_assets": "x"}, {"total_assets": 0, "total_liabilities": 0,
                                            "net_assets": 0}):
        assert fh._verdict_from_issuer_report(bad) == "UNKNOWN"


def test_rf3124_does_not_claim_a_ratio_model_it_does_not_have():
    """SEC EDGAR (route 1) is the only path to an Altman Z''. Claiming more than the
    document supports is the overreach the gate chain exists to prevent."""
    import inspect
    src = function_source(fh, "_verdict_from_issuer_report")
    assert "altman" in src.lower() and "edgar" in src.lower(), (
        "the docstring must state that Altman Z'' remains EDGAR-only")
    assert "equity_ratio" in src, "the verdict is a structural equity read, nothing more"


# ── §21a — both branches reach the brain ───────────────────────────────────
def test_rf3124_success_and_failure_are_both_wired():
    """§21a — a route that silently stops working looks identical to one never tried.

    R-F3128 moved the logic out of the inline assess() block into the registered
    capability; the guarantee is unchanged, so the assertion follows it there."""
    import inspect
    src = function_source(fh, "_enrich_with_issuer_report")
    assert "wire_success(" in src, "the verified path must reach the brain"
    assert "wire_failure(" in src, "the not-usable path must reach the brain too"
    assert 'gap_type="knowledge_gap"' in src, "must be a REGISTERED gap type"


def test_rf3124_failure_leaves_the_honest_unknown_intact():
    """The whole design: any gate failure changes nothing about the existing verdict."""
    import inspect
    src = function_source(fh, "_enrich_with_issuer_report")
    assert src.index('if not iss.get("ok"):') < src.index('result["data_available"] = True'), (
        "data_available may ONLY be set AFTER the ok check returns False early")
    assert "return False" in src.split('result["data_available"] = True')[0], (
        "the not-ok path must return before anything is marked available")
    assert "still UNKNOWN" in function_source(fh, "assess"), (
        "assess() must still say UNKNOWN when the capability declined")


# ── R-F3128 — the vault must not be able to mask a new route ───────────────
def test_rf3128_issuer_report_is_a_REGISTERED_capability():
    """THE DEFECT (QinetiQ, dd_a56444e7647e): R-F3124 was wired inline in assess()
    step 3 only. assess() returns a vault profile VERBATIM when younger than
    max_age_days, so an entity assessed minutes earlier never reached step 3 and the
    report still read "figures not yet extracted" — the pre-R-F3124 text.

    That is exactly the masking R-F2834 exists to end, recurring because a new
    capability was added without REGISTERING it."""
    assert "issuer_report" in fh.current_capabilities(), (
        "R-F3128 REGRESSION: a vault-cached profile will mask the issuer-report route "
        "for the whole freshness window")
    assert fh.FINANCIAL_CAPABILITIES["issuer_report"] is fh._enrich_with_issuer_report


def test_rf3128_there_is_exactly_one_implementation():
    """The inline copy is what diverged from the registered path. One caller only."""
    import inspect
    src = module_source(fh)
    assert src.count("await extract_issuer_financials(") == 1, (
        "more than one call site means the fresh and backfill paths can drift again")
    assert "_enrich_with_issuer_report(\n                    result, name" in src, (
        "assess() must go through the registered capability, not an inline copy")


def test_rf3128_capability_only_stamps_on_success():
    """A blocked fetch or refused gate must retry on the next read, not freeze an
    UNKNOWN for 30 days — the enricher returns False so the stamp is withheld."""
    import inspect
    src = function_source(fh, "_enrich_with_issuer_report")
    assert "if not iss.get(\"ok\"):" in src and "return False" in src


def test_rf3128_does_not_override_a_stronger_route():
    """SEC EDGAR (structured) must win; the issuer report is the fallback."""
    import inspect
    src = function_source(fh, "_enrich_with_issuer_report")
    assert 'if result.get("data_available") and result.get("has_financials"):' in src


def test_rf3128_backfill_can_resolve_an_llm_without_a_request():
    """A vault backfill runs outside any HTTP request, so there is no injected
    provider — mirrors dd_orchestrator._resolve_dd_llm (R-F3087)."""
    assert callable(fh._dd_llm_for_capability)
    assert fh._dd_llm_for_capability() is None or True   # must not raise

"""R-F2838 — an explicitly DECLARED jurisdiction must beat suffix inference.

THE DEFECT, found on a live DD (run dd_7bd81330d43d, SOCAR Trading SA).
The caller passed ``jurisdiction: "CH"``. The report came back with:

    country_risk: {'iso2': 'PT', 'name': 'CH', 'cpi_score': 62, ...}

iso2 and name were effectively swapped: the declared "CH" was used only as the
DISPLAY name, while ``jurisdiction_iso2`` was INFERRED as PT. Consequences on a
real, customer-visible report:

  * the whole jurisdiction-dependent compliance layer scored PORTUGAL — cpi_score 62
    is Portugal's; Switzerland's is ~82. Basel AML, GPI and FATF all followed.
  * the UBO ownership walk looked for a PT registry and found nothing.
  * the remediation advice told the operator to check "Portal da Empresa …
    Portuguese public business database" for a SWISS company.

ROOT CAUSE, proven with a two-line control before the fix:
    _infer_jurisdiction({... 'jurisdiction': 'CH' ...}, "SOCAR Trading SA", ...) -> "PT"
    same call with "SA" stripped from the name                                  -> None
i.e. the legal-form suffix "SA" (Sociedade Anónima) mapped to PT and OVERRODE the
declaration. dd_orchestrator.py only infers ``if not jurisdiction_iso2``, and a
caller who passes ``jurisdiction="CH"`` (already a valid ISO2) never populates that
field, so inference always ran.

BLAST RADIUS: "SA" is the standard company suffix in CH, FR, BE, LU, ES, BR, AR and
PL among others — so this silently misclassified a large share of the non-UK market.

USP RELEVANCE: this does not manufacture a false CLEAN, but it is the same family of
harm — a confident, specific, WRONG assertion presented to a decision-maker. A DD
that scores the wrong country is worse than one that says UNKNOWN.
"""
import pytest

from aria_service.intel import dd_orchestrator as DDO

# R-F3782/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


# The suffixes that triggered this, plus the jurisdictions they wrongly captured.
DECLARED_CASES = [
    ("SOCAR Trading SA", "CH", "the live failure — Swiss company read as Portuguese"),
    ("Example Trading SA", "FR", "SA is also the French Société Anonyme"),
    ("Example Trading SA", "BE", "and the Belgian one"),
    ("Example Trading SA", "LU", "and the Luxembourgish one"),
    ("Example Comercio SA", "BR", "and the Brazilian Sociedade Anônima"),
]


@pytest.mark.parametrize("name,declared,why", DECLARED_CASES)
def test_declared_jurisdiction_is_honoured_over_suffix_inference(name, declared, why):
    """A declared ISO2 must survive — inference may only fill a GAP, never override."""
    resolved = DDO.resolve_jurisdiction_iso2(
        {"type": "company", "name": name, "jurisdiction": declared},
        name,
        registration_number="",
    )
    assert resolved == declared, (
        f"{why}: declared {declared!r} but resolved {resolved!r}. An explicit "
        "declaration must always beat an inference — the live SOCAR run scored "
        "Portugal for a Swiss company because it did not."
    )


def test_full_country_name_is_accepted_too():
    """Callers pass names as well as codes; both are declarations."""
    got = DDO.resolve_jurisdiction_iso2(
        {"jurisdiction": "Portugal", "name": "Example LDA"}, "Example LDA", ""
    )
    assert got == "PT"


def test_explicit_iso2_field_still_wins_outright():
    """jurisdiction_iso2 is the most explicit signal of all."""
    got = DDO.resolve_jurisdiction_iso2(
        {"jurisdiction_iso2": "CH", "jurisdiction": "Portugal", "name": "X LDA"},
        "X LDA", "",
    )
    assert got == "CH", "the dedicated iso2 field must outrank everything else"


def test_inference_still_fills_a_genuine_gap():
    """ANTI-REGRESSION: with NO declaration, inference must still work.

    The fix must narrow inference to gap-filling, not disable it — a caller who
    supplies only a name still benefits from the suffix clue.
    """
    got = DDO.resolve_jurisdiction_iso2(
        {"name": "Example Unipessoal LDA"}, "Example Unipessoal LDA", ""
    )
    assert got == "PT", "suffix inference must still apply when nothing is declared"


def test_garbage_declaration_falls_back_to_inference_not_to_nonsense():
    """An unusable declaration must not become the jurisdiction."""
    got = DDO.resolve_jurisdiction_iso2(
        {"jurisdiction": "???", "name": "Example Unipessoal LDA"},
        "Example Unipessoal LDA", "",
    )
    assert got == "PT", "an unparseable declaration should fall through to inference"


def test_no_signal_at_all_returns_none_not_a_guess():
    """USP: absence of a jurisdiction must stay UNKNOWN, never a default."""
    got = DDO.resolve_jurisdiction_iso2({"name": "Plain Company"}, "Plain Company", "")
    assert got is None, (
        "with no declaration and no clue the answer is UNKNOWN — defaulting to a "
        "country would be a confident wrong assertion, the same family of harm as "
        "a false clean"
    )


def test_orchestrator_uses_the_shared_resolver():
    """The identity layer must not keep its own copy of this logic."""
    import inspect
    src = module_source(DDO)
    idx = src.find("Auto-detect jurisdiction from clues")
    assert idx > -1, "the identity resolution block must still exist"
    window = src[idx - 800:idx + 800]
    assert "resolve_jurisdiction_iso2" in window, (
        "identity resolution must call the shared resolver; a second inline copy "
        "would drift from it, which is how the nav gate rotted in R-F2822"
    )

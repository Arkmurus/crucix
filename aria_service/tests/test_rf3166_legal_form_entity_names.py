"""R-F3166 — the sanctions screen refused to screen any company written "… plc".

MEASURED, live (Babcock International Group plc, dd_ea78770813cc):

    identity.sanctions_screen = {
        "name": "Babcock International Group plc",
        "error": "not_entity_shaped",
        "verified_sources": {
            "OFAC SDN":              {"status": "UNAVAILABLE", "via": "screen_failed"},
            "OFAC NS-CMIC":          {"status": "UNAVAILABLE", "via": "screen_failed"},
            "OFAC SSI":              {"status": "UNAVAILABLE", "via": "screen_failed"},
            "BIS Entity List":       {"status": "UNAVAILABLE", "via": "screen_failed"},
            "BIS Military End User": {"status": "UNAVAILABLE", "via": "screen_failed"},
            "UK OFSI / HMT":         {"status": "UNAVAILABLE", "via": "screen_failed"},
            "EU Consolidated":       {"status": "UNAVAILABLE", "via": "screen_failed"},
            "UN SC Consolidated":    {"status": "UNAVAILABLE", "via": "screen_failed"},
            "NDAA Sec 1260H":        {"status": "UNAVAILABLE", "via": "screen_failed"},
            "DoD Sec 1233 Russia":   {"status": "UNAVAILABLE", "via": "screen_failed"},
        }}

TEN primary sources skipped. `_dd_decision_readiness` then held "Sanctions and
export-control exposure" UNRESOLVED — correctly (sanctions_verified was False), but for
a reason no reader could guess. Note the OTHER half of that gate, export_control, was
fine: recommendation "civilian or unclassified". The blocker was never export control.

THE CAUSE: `_looks_like_entity_name` requires every non-stopword token to start with an
uppercase letter — the F39 guard against sentence fragments ("Iran nexus before
engagement"). "plc" is lowercase and is not a stopword, so:

    _looks_like_entity_name("Babcock International Group plc")  -> False
    _looks_like_entity_name("Babcock International Group PLC")  -> True

Case alone decided whether a counterparty got screened. Every UK public company written
the way it writes itself was refused — "Babcock International Group plc" is the title of
its own annual report — and the same holds for nv / bv / sa / gmbh / oy / pte across
other jurisdictions.

A compliance product silently declining to screen a whole class of counterparties is
the most serious failure mode it has. The fragment guard is doing real work and stays;
a legal form simply is not a lowercase common noun.
"""
import pytest

from aria_service.intel.sanctions import _looks_like_entity_name as looks_like_entity


# ── the exact live failures ───────────────────────────────────────────────────
@pytest.mark.parametrize("name", [
    "Babcock International Group plc",   # dd_ea78770813cc — the live rejection
    "QinetiQ Group plc",                 # rejected identically this session
    "Mitie Group plc",                   # and this one
])
def test_rf3166_the_live_rejections_now_screen(name):
    assert looks_like_entity(name) is True, (
        f"R-F3166 REGRESSION: {name!r} is refused again — the ENTIRE sanctions screen "
        f"is skipped for it and all ten primary sources record screen_failed")


@pytest.mark.parametrize("name", [
    "Rolls-Royce Holdings plc", "BAE Systems plc", "Serco Group plc",
    "Heineken nv", "Airbus se", "Siemens ag", "Nestle sa", "Ferrari spa",
    "Nokia oyj", "Tesco Stores ltd", "Grab Holdings pte ltd", "Genting Sdn bhd",
    "Volvo ab", "Maersk as", "Renault sas", "Fiat srl",
])
def test_rf3166_lowercase_legal_forms_across_jurisdictions(name):
    """This was never a UK quirk — the same rule silenced NL/DE/FR/Nordic/Asian forms."""
    assert looks_like_entity(name) is True, name


@pytest.mark.parametrize("name", [
    "Babcock International Group PLC", "Bank of America Corp",
    "Krasnoyarsk Aluminum Smelter Open Joint-Stock Company",
])
def test_rf3166_previously_accepted_names_still_accepted(name):
    assert looks_like_entity(name) is True, name


@pytest.mark.parametrize("name", [
    "Nestle S.A.", "Acme Co.", "Example plc.",
])
def test_rf3166_punctuated_forms_are_handled(name):
    """Legal forms appear as 'S.A.', 'Co.', 'plc.' in the wild."""
    assert looks_like_entity(name) is True, name


# ── the guard this must NOT weaken ────────────────────────────────────────────
@pytest.mark.parametrize("fragment", [
    "Iran nexus before engagement",                       # F39's original case
    "Iran is openly signalling it will",
    "GAMI current leadership independently before any",
    "sanctions update OFAC SDN embargo",
    "Arkmurus weekly intelligence summary Angola",
])
def test_rf3166_sentence_fragments_are_still_rejected(fragment):
    """The whole point of the gate: don't burn OpenSanctions quota on search queries."""
    assert looks_like_entity(fragment) is False, (
        f"R-F3166 over-corrected — {fragment!r} is a search query, not an entity")


def test_rf3166_legal_form_exemption_is_terminal_only():
    """Short forms like 'as', 'co', 'dd' are word-like. Exempting them ANYWHERE would
    re-admit fragments, so the exemption applies only to the last two tokens."""
    assert looks_like_entity("Iran as policy fragment") is False
    assert looks_like_entity("Something co random words") is False


def test_rf3166_a_lowercase_non_legal_word_still_fails():
    """Only legal forms are exempt — not every trailing lowercase token."""
    assert looks_like_entity("Babcock International Group holdings") is False, (
        "'holdings' is a common noun, not a legal form — the fragment guard must "
        "still apply to it")


def test_rf3166_two_token_forms_both_exempt():
    """'Pte Ltd' / 'Sdn Bhd' occupy BOTH terminal slots."""
    assert looks_like_entity("Grab Holdings pte ltd") is True
    assert looks_like_entity("Genting Plantations sdn bhd") is True


def test_rf3166_other_rejections_are_untouched():
    """Every other heuristic must keep working."""
    assert looks_like_entity("") is False
    assert looks_like_entity("x") is False
    assert looks_like_entity("Some Company plc, and more") is False      # comma
    assert looks_like_entity("Babcock International Group plc 2026") is False  # year
    assert looks_like_entity("OFAC") is False                            # denylist

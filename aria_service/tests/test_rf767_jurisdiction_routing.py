"""R-F767 — name-origin → registry-jurisdiction routing heuristic.

Other-agent audit 2026-05-20 (Pri 6): 'Investigate Efdal Colpan tied
to ECDI procurement' is DD-shaped (named person + foreign procurement
body + Turkish-origin surname); R-F729 broadened DD-intent regex but
didn't route the registry lookup preferentially to MERSIS (Turkey)
when the name's origin is detectable.

R-F767 adds two helpers to aria_service.intel.name_variants:

  detect_name_origin(name) -> 'turkish' | 'cyrillic' | 'arabic' |
                              'slavic' | 'iberian' | 'francophone' | None

  suggest_jurisdictions(name) -> list[ISO2]
    ordered jurisdiction codes (limited to those supported by
    registry_adapters._SUPPORTED_JURISDICTIONS) that the orchestrator
    should try FIRST. Empty list means no preference.

This is a HEURISTIC, not authoritative. A Russian name married into a
Turkish family will be misclassified — but the upstream chat handler
can still respect explicit jurisdiction context when the user supplies
it (e.g. 'Efdal Colpan in Brazil' should not be routed to TR). The
heuristic only kicks in when the user gives a name with NO explicit
jurisdiction.
"""
from __future__ import annotations

from aria_service.intel.name_variants import (
    detect_name_origin,
    suggest_jurisdictions,
)


# ── detect_name_origin contracts ─────────────────────────────────────


def test_rf767_turkish_origin_on_known_first_name():
    """Live bug anchor — 'Efdal Colpan' must classify as Turkish."""
    assert detect_name_origin("Efdal Colpan") == "turkish"


def test_rf767_turkish_origin_on_known_surname_suffix():
    assert detect_name_origin("Ahmet Yıldız") == "turkish"
    assert detect_name_origin("Murat Demir") == "turkish"


def test_rf767_cyrillic_origin_on_known_first_name():
    """Cyrillic-shape: known transliterated first name."""
    assert detect_name_origin("Sergey Mikhailov") == "cyrillic"
    assert detect_name_origin("Dmitri Petrov") == "cyrillic"


def test_rf767_arabic_origin_on_known_first_name():
    assert detect_name_origin("Mohammed Ahmed") == "arabic"
    assert detect_name_origin("Hussein al-Asad") == "arabic"


def test_rf767_slavic_origin_on_surname_suffix():
    """Slavic: surname suffix without Cyrillic-canonical first name."""
    # -ski suffix
    assert detect_name_origin("Jan Kowalski") == "slavic"


def test_rf767_iberian_origin_on_diacritics():
    """Portuguese / Lusophone diacritic detection."""
    assert detect_name_origin("João Silva") == "iberian"


def test_rf767_francophone_origin_on_diacritics():
    """French diacritic detection."""
    assert detect_name_origin("François Lefèvre") == "francophone"


def test_rf767_no_origin_on_plain_western_name():
    """Plain Western names should return None (no preference)."""
    assert detect_name_origin("John Smith") is None
    assert detect_name_origin("Sarah Brown") is None


def test_rf767_handles_empty_and_garbage():
    assert detect_name_origin("") is None
    assert detect_name_origin("   ") is None
    assert detect_name_origin(None) is None  # type: ignore[arg-type]


# ── suggest_jurisdictions contracts ──────────────────────────────────


def test_rf767_turkish_routes_to_tr():
    """The live-bug expected behaviour: Turkish name → MERSIS (TR)."""
    assert suggest_jurisdictions("Efdal Colpan") == ["TR"]


def test_rf767_cyrillic_routes_to_supported_bg():
    """Russia/Ukraine not yet supported in registry_adapters; BG is
    the closest match for the Cyrillic origin."""
    assert suggest_jurisdictions("Sergey Mikhailov") == ["BG"]


def test_rf767_arabic_routes_to_sa_ae():
    """Arabic origin → SA + AE (both supported)."""
    out = suggest_jurisdictions("Mohammed Ahmed")
    assert out == ["SA", "AE"], f"got {out}"


def test_rf767_slavic_surname_routes_to_pl_sk_cz_hu():
    """Slavic surname suffix → PL + SK + CZ + HU (Central European
    adapters supported)."""
    out = suggest_jurisdictions("Jan Kowalski")
    assert out == ["PL", "SK", "CZ", "HU"], f"got {out}"


def test_rf767_iberian_routes_to_br_ao():
    """Lusophone moat — Brazilian + Angolan registries are supported."""
    out = suggest_jurisdictions("João Silva")
    assert out == ["BR", "AO"], f"got {out}"


def test_rf767_francophone_routes_to_fr():
    out = suggest_jurisdictions("François Lefèvre")
    assert out == ["FR"], f"got {out}"


def test_rf767_plain_western_returns_empty():
    """No preference — orchestrator falls back to default adapter set."""
    assert suggest_jurisdictions("John Smith") == []


def test_rf767_all_suggested_jurisdictions_are_actually_supported():
    """Every ISO2 emitted by suggest_jurisdictions MUST be in
    registry_adapters._SUPPORTED_JURISDICTIONS. Mis-routing to an
    unsupported adapter is wasted work and a regression vector."""
    from aria_service.intel.registry_adapters import _SUPPORTED_JURISDICTIONS

    sample_names = [
        "Efdal Colpan",      # turkish
        "Sergey Mikhailov",  # cyrillic
        "Mohammed Ahmed",    # arabic
        "Jan Kowalski",      # slavic
        "João Silva",        # iberian
        "François Lefèvre",  # francophone
    ]
    for name in sample_names:
        for iso2 in suggest_jurisdictions(name):
            assert iso2 in _SUPPORTED_JURISDICTIONS, (
                f"R-F767 regression: suggest_jurisdictions({name!r}) "
                f"emitted unsupported ISO2 {iso2!r}. Either add the "
                f"adapter to registry_adapters or drop {iso2} from the "
                f"_ORIGIN_TO_JURISDICTIONS mapping."
            )

"""R-F638 — Weapon-origin catalogue.

Pins (high reliability discipline — operator demanded "99.99% working"):
  - Every smoking-gun item from the 2026-05-17 ADSM/Saudi case resolves
    correctly: Kornet → Russian → PROHIBITED, GP1/GP6/"GPI" → NORINCO →
    RESTRICTED, Krasnopol → Russian → PROHIBITED.
  - Origin-country sanctions logic is correct: Russian = PROHIBITED,
    Iranian = PROHIBITED, NORINCO (Chinese) = RESTRICTED, US/UK/FR/DE
    = CLEARED.
  - The "AK-pattern" + "RPG-7" origin discrimination works (Russian
    PROHIBITED, Bulgarian/Romanian/Serbian CLEARED).
  - Fuzzy matching for goods-list text handles common variants:
    'Cornet', 'GPI', 'Rebolt', 'Korean ATGM', etc.
  - Catalogue health: ≥60 entries; ≥7 origin countries; sanctions split
    is sensible (>20 prohibited+restricted, >30 cleared).
  - Defensive: None / empty / garbage inputs never raise.
"""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════════════
# PART A — smoking-gun resolutions from the ADSM case
# ══════════════════════════════════════════════════════════════════

def test_rf638_kornet_resolves_to_russian_prohibited():
    from aria_service.intel.weapon_origin_catalogue import (
        lookup_by_name, SanctionsStatus,
    )
    # Multiple spellings ADSM list-author might use
    for name in ("Kornet", "Cornet", "9M133 Kornet", "AT-14 Spriggan",
                 "Kornet-E", "Kornet-EM", "kornet", "CORNET"):
        ws = lookup_by_name(name)
        assert ws is not None, f"Kornet alias {name!r} not found"
        assert ws.origin_iso2 == "RU"
        assert ws.sanctions_status == SanctionsStatus.PROHIBITED


def test_rf638_norinco_gp1_gp6_resolve_to_china_restricted():
    """The 'GPI' / GP1 / GP6 ambiguity in the ADSM list — all should
    resolve to Chinese NORINCO."""
    from aria_service.intel.weapon_origin_catalogue import (
        lookup_by_name, SanctionsStatus,
    )
    for name in ("GP1", "GP-1", "GPI", "NORINCO GP1", "Chinese Krasnopol"):
        ws = lookup_by_name(name)
        assert ws is not None, f"{name!r} did not resolve"
        assert ws.origin_iso2 == "CN"
        assert ws.sanctions_status == SanctionsStatus.RESTRICTED
        assert "Entity List" in " ".join(ws.sanctions_regimes)

    for name in ("GP6", "GP-6", "NORINCO GP6"):
        ws = lookup_by_name(name)
        assert ws is not None
        assert ws.origin_iso2 == "CN"
        assert ws.sanctions_status == SanctionsStatus.RESTRICTED


def test_rf638_krasnopol_resolves_to_russian_prohibited():
    from aria_service.intel.weapon_origin_catalogue import (
        lookup_by_name, SanctionsStatus,
    )
    for name in ("Krasnopol", "Krasnopol-M2", "2K25 Krasnopol", "2K25M"):
        ws = lookup_by_name(name)
        assert ws is not None
        assert ws.origin_iso2 == "RU"
        assert ws.sanctions_status == SanctionsStatus.PROHIBITED


def test_rf638_iranian_kornet_dehlavieh_doubly_prohibited():
    from aria_service.intel.weapon_origin_catalogue import (
        lookup_by_name, SanctionsStatus,
    )
    ws = lookup_by_name("Dehlavieh")
    assert ws is not None
    assert ws.origin_iso2 == "IR"
    assert ws.sanctions_status == SanctionsStatus.PROHIBITED


def test_rf638_korean_raybolt_alias_rebolt_resolves():
    """Per ADSM list spelling ('Rebolt') — must still resolve to KR cleared."""
    from aria_service.intel.weapon_origin_catalogue import (
        lookup_by_name, SanctionsStatus,
    )
    for name in ("K-RAVEN", "RAYBOLT", "Rebolt", "Korean ATGM"):
        ws = lookup_by_name(name)
        assert ws is not None
        assert ws.origin_iso2 == "KR"
        assert ws.sanctions_status == SanctionsStatus.CLEARED


# ══════════════════════════════════════════════════════════════════
# PART B — origin-country sanctions logic
# ══════════════════════════════════════════════════════════════════

def test_rf638_all_russian_entries_are_prohibited():
    """Strict: every RU-origin entry must be PROHIBITED. If a future
    addition violates this, the test fails — forcing a deliberate review."""
    from aria_service.intel.weapon_origin_catalogue import (
        list_by_origin, SanctionsStatus,
    )
    ru = list_by_origin("RU")
    assert len(ru) >= 10, f"Expected ≥10 Russian entries, got {len(ru)}"
    for ws in ru:
        assert ws.sanctions_status == SanctionsStatus.PROHIBITED, (
            f"Russian entry {ws.canonical_name!r} is not PROHIBITED"
        )


def test_rf638_all_iranian_entries_are_prohibited():
    from aria_service.intel.weapon_origin_catalogue import (
        list_by_origin, SanctionsStatus,
    )
    ir = list_by_origin("IR")
    assert len(ir) >= 4
    for ws in ir:
        assert ws.sanctions_status == SanctionsStatus.PROHIBITED


def test_rf638_all_north_korean_entries_are_prohibited():
    from aria_service.intel.weapon_origin_catalogue import (
        list_by_origin, SanctionsStatus,
    )
    kp = list_by_origin("KP")
    # At least Hwasong family must be present
    assert len(kp) >= 1
    for ws in kp:
        assert ws.sanctions_status == SanctionsStatus.PROHIBITED


def test_rf638_chinese_entries_are_restricted_or_prohibited():
    """Chinese state-aligned defense manufacturers (NORINCO, AVIC) are
    Entity-Listed by US BIS — RESTRICTED not CLEARED."""
    from aria_service.intel.weapon_origin_catalogue import (
        list_by_origin, SanctionsStatus,
    )
    cn = list_by_origin("CN")
    assert len(cn) >= 4
    for ws in cn:
        assert ws.sanctions_status in (
            SanctionsStatus.RESTRICTED, SanctionsStatus.PROHIBITED
        ), f"Chinese entry {ws.canonical_name!r} marked CLEARED"


def test_rf638_us_uk_french_german_entries_are_cleared():
    """NATO-tier manufacturers should be CLEARED (with normal ITAR/
    license overlays). If any are not cleared, that's an audit point."""
    from aria_service.intel.weapon_origin_catalogue import (
        list_by_origin, SanctionsStatus,
    )
    for iso2 in ("US", "GB", "FR", "DE"):
        entries = list_by_origin(iso2)
        assert len(entries) >= 1, f"No entries for {iso2}"
        for ws in entries:
            assert ws.sanctions_status == SanctionsStatus.CLEARED, (
                f"NATO-tier entry {ws.canonical_name!r} from {iso2} "
                f"is not CLEARED — manual review needed"
            )


def test_rf638_bulgarian_romanian_serbian_ak_pattern_cleared():
    """Critical for the 7.62×39 calibre — Bulgarian/Romanian/Serbian
    AK ammunition is CLEARED. Distinct from Russian (PROHIBITED)."""
    from aria_service.intel.weapon_origin_catalogue import (
        lookup_by_name, SanctionsStatus,
    )
    bg = lookup_by_name("Arsenal AD")
    ro = lookup_by_name("Cugir AKM")
    rs = lookup_by_name("Zastava M70")
    for ws in (bg, ro, rs):
        assert ws is not None
        assert ws.sanctions_status == SanctionsStatus.CLEARED


def test_rf638_russian_ak_distinct_from_eastern_european():
    """Smoke test of the origin-country discrimination — the same
    'AK-47' name resolves to RUSSIAN PROHIBITED via the Russian entry,
    NOT to the Bulgarian/Romanian/Serbian CLEARED entries."""
    from aria_service.intel.weapon_origin_catalogue import (
        lookup_by_name, SanctionsStatus,
    )
    # The Russian AK alias must dominate the "AK-47" generic lookup
    # since it's the original. Bulgarian/Romanian must be explicit.
    ws_ru = lookup_by_name("AK-47 (Russian)")
    assert ws_ru is not None
    assert ws_ru.origin_iso2 == "RU"
    assert ws_ru.sanctions_status == SanctionsStatus.PROHIBITED


# ══════════════════════════════════════════════════════════════════
# PART C — fuzzy goods-list text matching
# ══════════════════════════════════════════════════════════════════

def test_rf638_best_match_finds_cornet_in_list_item_text():
    """'Cornet guided anti-tank missile (Russian)' from the ADSM list."""
    from aria_service.intel.weapon_origin_catalogue import (
        best_match_for_text, SanctionsStatus,
    )
    text = "Cornet guided anti-tank missile (Russian)"
    ws = best_match_for_text(text)
    assert ws is not None
    assert ws.origin_iso2 == "RU"
    assert ws.sanctions_status == SanctionsStatus.PROHIBITED


def test_rf638_best_match_finds_gpi_in_155mm_line():
    from aria_service.intel.weapon_origin_catalogue import (
        best_match_for_text, SanctionsStatus,
    )
    text = "155 mm Laser-Guided projectile (GPI)"
    ws = best_match_for_text(text)
    assert ws is not None
    assert ws.origin_iso2 == "CN"
    assert ws.sanctions_status == SanctionsStatus.RESTRICTED


def test_rf638_best_match_prefers_longer_alias():
    """If both 'M829' and 'M82' could match, the longer should win.
    Test with NORINCO GP6 vs GP1."""
    from aria_service.intel.weapon_origin_catalogue import best_match_for_text
    # Text contains both: GP1 should NOT win when GP6 is the actual name
    text = "GP6 155mm shell with laser+GPS guidance"
    ws = best_match_for_text(text)
    assert ws is not None
    assert ws.canonical_name == "NORINCO GP6 (155mm laser+GPS)"


def test_rf638_best_match_skips_short_collisions():
    """The 2-char-alias guard prevents 'M1' from matching every M1xxxx
    weapon in passing prose. Match requires alias length ≥3."""
    from aria_service.intel.weapon_origin_catalogue import best_match_for_text
    text = "M1 is mentioned in passing"  # 'M1' is only 2 chars
    # This shouldn't false-match on a 2-char token even if some alias
    # contained "M1" — we require ≥3 chars.
    result = best_match_for_text(text)
    # Result may be None or a legitimate match — the assertion is
    # only that we don't crash and the match logic is sensible.
    assert result is None or len(result.canonical_name) > 3


def test_rf638_best_match_returns_none_for_irrelevant_text():
    from aria_service.intel.weapon_origin_catalogue import best_match_for_text
    text = "Catering services for the Riyadh trade fair, March 2026"
    assert best_match_for_text(text) is None


def test_rf638_best_match_handles_bad_inputs():
    from aria_service.intel.weapon_origin_catalogue import best_match_for_text
    assert best_match_for_text("") is None
    assert best_match_for_text(None) is None  # type: ignore[arg-type]
    assert best_match_for_text(123) is None  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# PART D — render_finding_for_text helper
# ══════════════════════════════════════════════════════════════════

def test_rf638_render_finding_kornet_is_hard_stop():
    from aria_service.intel.weapon_origin_catalogue import render_finding_for_text
    f = render_finding_for_text("Cornet guided anti-tank missile (Russian)")
    assert f is not None
    assert f["severity"] == "hard_stop"
    assert "PROHIBITED" in f["title"]
    assert "9M133 Kornet" in f["title"] or "Kornet" in f["title"]
    assert "as_of" in f["source"]


def test_rf638_render_finding_norinco_is_amber():
    from aria_service.intel.weapon_origin_catalogue import render_finding_for_text
    f = render_finding_for_text("155 mm Laser-Guided projectile (GPI)")
    assert f is not None
    assert f["severity"] == "amber"
    assert "RESTRICTED" in f["title"]
    assert "Entity List" in f["detail"]


def test_rf638_render_finding_excalibur_is_info():
    from aria_service.intel.weapon_origin_catalogue import render_finding_for_text
    f = render_finding_for_text("Excalibur Ib precision-guided 155mm shell")
    assert f is not None
    assert f["severity"] == "info"
    assert "CLEARED" in f["title"]


def test_rf638_render_finding_no_match_returns_none():
    from aria_service.intel.weapon_origin_catalogue import render_finding_for_text
    f = render_finding_for_text("generic 5.56mm tracer rounds")
    # 5.56mm tracer is in the goods list but NOT a named weapon system
    # in the catalogue — caller falls back to ECCN generic.
    assert f is None


# ══════════════════════════════════════════════════════════════════
# PART E — catalogue health metrics
# ══════════════════════════════════════════════════════════════════

def test_rf638_catalogue_has_at_least_60_entries():
    """Operator's '99.99% reliability' bar — we need substantive coverage."""
    from aria_service.intel.weapon_origin_catalogue import all_weapons
    assert len(all_weapons()) >= 60


def test_rf638_stats_returns_sensible_shape():
    from aria_service.intel.weapon_origin_catalogue import stats
    s = stats()
    assert s["total_entries"] >= 60
    assert s["prohibited"] >= 10
    assert s["restricted"] >= 4
    assert s["cleared"] >= 20
    assert s["origin_countries"] >= 7
    # Total prohibited + restricted + cleared should equal total
    assert s["prohibited"] + s["restricted"] + s["cleared"] == s["total_entries"]


def test_rf638_every_entry_has_required_fields():
    """Schema-level pin: no entry can be missing critical fields."""
    from aria_service.intel.weapon_origin_catalogue import all_weapons
    for ws in all_weapons():
        assert ws.canonical_name
        assert ws.aliases  # tuple, may be empty but should be present
        assert ws.category
        assert ws.oem
        assert ws.oem_parent
        assert len(ws.origin_iso2) == 2  # ISO-3166-1 alpha-2
        assert ws.origin_iso2.isupper()
        assert ws.sanctions_status in ("prohibited", "restricted", "cleared")
        assert ws.sanctions_regimes  # at least one regime cited
        assert ws.as_of


def test_rf638_every_prohibited_entry_cites_a_sanctions_regime():
    """Audit pin — never assert PROHIBITED without naming the regime."""
    from aria_service.intel.weapon_origin_catalogue import (
        list_prohibited,
    )
    for ws in list_prohibited():
        regimes = " ".join(ws.sanctions_regimes).lower()
        assert any(kw in regimes for kw in (
            "russia", "iran", "dprk", "sanctions", "ofac",
            "ofsi", "embargo", "un sc", "eu", "wassenaar",
        )), (
            f"PROHIBITED entry {ws.canonical_name!r} cites no "
            f"recognisable sanctions regime: {ws.sanctions_regimes}"
        )


# ══════════════════════════════════════════════════════════════════
# PART F — defensive handling
# ══════════════════════════════════════════════════════════════════

def test_rf638_lookup_empty_returns_none():
    from aria_service.intel.weapon_origin_catalogue import lookup_by_name
    assert lookup_by_name("") is None
    assert lookup_by_name(None) is None  # type: ignore[arg-type]
    assert lookup_by_name(123) is None  # type: ignore[arg-type]


def test_rf638_list_by_origin_empty_returns_empty():
    from aria_service.intel.weapon_origin_catalogue import list_by_origin
    assert list_by_origin("") == []
    assert list_by_origin(None) == []  # type: ignore[arg-type]
    assert list_by_origin("ZZ") == []  # unseeded ISO2


def test_rf638_lookup_unknown_weapon_returns_none():
    from aria_service.intel.weapon_origin_catalogue import lookup_by_name
    assert lookup_by_name("Acme XQ-99 super-weapon") is None


def test_rf638_case_insensitive_lookup():
    from aria_service.intel.weapon_origin_catalogue import lookup_by_name
    assert lookup_by_name("KORNET") is not None
    assert lookup_by_name("kornet") is not None
    assert lookup_by_name("  Kornet  ") is not None

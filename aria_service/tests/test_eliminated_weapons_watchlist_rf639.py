"""R-F639 — Treaty-eliminated / banned weapons watchlist.

High-reliability pins per operator's '99.99%' bar:
  - SS-20 (the 2026-05-17 ADSM smoking-gun item) resolves to ELIMINATED
  - Pershing II, BGM-109G Gryphon (US INF) resolve
  - CWC nerve agents resolve to CATEGORICALLY_BANNED with hard_stop
  - Russian PMN-1 / Chinese Type 72 resolve to TREATY_PROHIBITED_STATE_PARTY
  - Cluster munitions (CBU-87, M483A1) resolve correctly
  - Defensive: clean weapons (Excalibur, Patriot) return None
  - render_finding emits hard_stop for ELIMINATED + CATEGORICALLY_BANNED
"""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════════════
# PART A — the SS-20 smoking gun (ADSM Item 29)
# ══════════════════════════════════════════════════════════════════

def test_rf639_ss20_resolves_to_eliminated():
    from aria_service.intel.eliminated_weapons_watchlist import (
        lookup_by_name, is_eliminated, EliminationStatus,
    )
    # Multiple spellings ADSM list-author might use
    for name in ("SS-20", "SS-20 Saber", "SS-20 rockets", "SS-20 missile",
                 "RSD-10", "RSD-10 Pioneer", "Pioneer IRBM"):
        entry = lookup_by_name(name)
        assert entry is not None, f"{name!r} did not resolve"
        assert entry.elimination_status == EliminationStatus.ELIMINATED
        assert entry.treaty.startswith("INF")
        assert entry.elimination_year == 1991
    assert is_eliminated("SS-20") is True
    assert is_eliminated("SS-20 rockets") is True


def test_rf639_ss20_render_finding_is_hard_stop():
    """The whole point of R-F639 — match must emit hard_stop with
    treaty citation visible in title."""
    from aria_service.intel.eliminated_weapons_watchlist import (
        render_finding_for_text,
    )
    f = render_finding_for_text("SS-20 rockets — 10,000")
    assert f is not None
    assert f["severity"] == "hard_stop"
    assert "ELIMINATED" in f["title"]
    assert "INF" in f["title"]
    assert "1991" in f["detail"]


# ══════════════════════════════════════════════════════════════════
# PART B — other INF-eliminated items
# ══════════════════════════════════════════════════════════════════

def test_rf639_pershing_ii_resolves_to_eliminated():
    from aria_service.intel.eliminated_weapons_watchlist import (
        lookup_by_name, EliminationStatus,
    )
    for name in ("Pershing II", "Pershing-II", "MGM-31C", "Pershing 2"):
        entry = lookup_by_name(name)
        assert entry is not None
        assert entry.elimination_status == EliminationStatus.ELIMINATED


def test_rf639_bgm109g_gryphon_resolves_to_eliminated_distinct_from_naval_tomahawk():
    """Critical discrimination — BGM-109G Gryphon (INF-eliminated GLCM)
    is distinct from naval/air Tomahawk variants (still in service)."""
    from aria_service.intel.eliminated_weapons_watchlist import (
        lookup_by_name, EliminationStatus,
    )
    for name in ("BGM-109G", "Gryphon", "GLCM", "Tomahawk GLCM"):
        entry = lookup_by_name(name)
        assert entry is not None
        assert entry.elimination_status == EliminationStatus.ELIMINATED
    # Generic "Tomahawk" (naval) is NOT on the watchlist — should not resolve
    assert lookup_by_name("Tomahawk") is None
    assert lookup_by_name("RGM-109") is None


def test_rf639_inf_eliminated_count():
    from aria_service.intel.eliminated_weapons_watchlist import (
        list_by_treaty, EliminationStatus,
    )
    inf_entries = list_by_treaty("INF")
    assert len(inf_entries) >= 6
    for e in inf_entries:
        assert e.elimination_status == EliminationStatus.ELIMINATED
        assert e.treaty_year == 1987


# ══════════════════════════════════════════════════════════════════
# PART C — CWC chemical weapons (categorically banned)
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", [
    "VX", "VX nerve agent", "Sarin", "GB", "Mustard gas", "HD",
    "Novichok", "A-234", "Tabun", "GA", "Soman", "GD", "Cyclosarin", "GF",
])
def test_rf639_cwc_agents_categorically_banned(name):
    from aria_service.intel.eliminated_weapons_watchlist import (
        lookup_by_name, EliminationStatus,
    )
    entry = lookup_by_name(name)
    assert entry is not None, f"CWC agent {name!r} not on watchlist"
    assert entry.elimination_status == EliminationStatus.CATEGORICALLY_BANNED
    assert "Chemical Weapons Convention" in entry.treaty


def test_rf639_cwc_render_finding_is_hard_stop():
    from aria_service.intel.eliminated_weapons_watchlist import (
        render_finding_for_text,
    )
    f = render_finding_for_text("100 kg VX nerve agent")
    assert f is not None
    assert f["severity"] == "hard_stop"
    assert "TREATY-BANNED" in f["title"]
    assert "Chemical Weapons Convention" in f["detail"]


# ══════════════════════════════════════════════════════════════════
# PART D — BWC biological weapons
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", [
    "Weaponised anthrax", "Bacillus anthracis (weaponised)",
    "Weaponised ricin", "Weaponised smallpox",
    "Botulinum toxin (weaponised)",
])
def test_rf639_bwc_agents_categorically_banned(name):
    from aria_service.intel.eliminated_weapons_watchlist import (
        lookup_by_name, EliminationStatus,
    )
    entry = lookup_by_name(name)
    assert entry is not None
    assert entry.elimination_status == EliminationStatus.CATEGORICALLY_BANNED
    assert "Biological Weapons Convention" in entry.treaty


# ══════════════════════════════════════════════════════════════════
# PART E — Ottawa AP-mine ban (state-party prohibited)
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", [
    "PMN-1", "PMN-2", "POMZ-2", "PMD-6", "Type 72", "Valmara 69", "VS-50",
])
def test_rf639_ap_mines_treaty_prohibited_state_party(name):
    from aria_service.intel.eliminated_weapons_watchlist import (
        lookup_by_name, EliminationStatus,
    )
    entry = lookup_by_name(name)
    assert entry is not None
    assert entry.elimination_status == EliminationStatus.TREATY_PROHIBITED_STATE_PARTY
    assert "Ottawa" in entry.treaty


def test_rf639_ottawa_finding_is_amber_not_hard_stop():
    """State-party-prohibited is AMBER (depends on buyer's status),
    not HARD STOP categorical."""
    from aria_service.intel.eliminated_weapons_watchlist import (
        render_finding_for_text,
    )
    f = render_finding_for_text("PMN-1 anti-personnel mines, 5000 units")
    assert f is not None
    assert f["severity"] == "amber"  # not hard_stop — depends on buyer
    assert "state-party" in f["title"].lower()


def test_rf639_claymore_not_on_watchlist():
    """Critical: M18A1 Claymore (command-detonated) is NOT prohibited
    under Ottawa. It must NOT be on the eliminated watchlist."""
    from aria_service.intel.eliminated_weapons_watchlist import lookup_by_name
    assert lookup_by_name("Claymore") is None
    assert lookup_by_name("M18A1") is None
    assert lookup_by_name("M18A1 Claymore") is None


# ══════════════════════════════════════════════════════════════════
# PART F — Cluster munitions (CCM)
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", [
    "CBU-87", "CBU-87B", "CBU-97", "Sensor Fuzed Weapon",
    "M483A1", "M864", "DPICM 155mm", "M261 MPSM",
])
def test_rf639_cluster_munitions_treaty_prohibited(name):
    from aria_service.intel.eliminated_weapons_watchlist import (
        lookup_by_name, EliminationStatus,
    )
    entry = lookup_by_name(name)
    assert entry is not None
    assert entry.elimination_status == EliminationStatus.TREATY_PROHIBITED_STATE_PARTY
    assert "Cluster Munitions" in entry.treaty


# ══════════════════════════════════════════════════════════════════
# PART G — clean weapons must NOT match
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", [
    "Excalibur", "M829", "Patriot", "Stinger", "Javelin", "Hellfire",
    "K2 Black Panther", "Spike", "Bayraktar TB2",
    "5.56mm tracer", "M107", "Leopard 2",
])
def test_rf639_clean_weapons_return_none(name):
    """Eliminated watchlist must NOT false-fire on legitimate weapons."""
    from aria_service.intel.eliminated_weapons_watchlist import (
        lookup_by_name, best_match_for_text,
    )
    assert lookup_by_name(name) is None
    assert best_match_for_text(name) is None


def test_rf639_kornet_not_on_eliminated_watchlist():
    """Russian Kornet is sanctioned (R-F638) but NOT on R-F639's
    eliminated/banned watchlist. They serve different purposes."""
    from aria_service.intel.eliminated_weapons_watchlist import lookup_by_name
    assert lookup_by_name("Kornet") is None
    assert lookup_by_name("9M133 Kornet") is None


# ══════════════════════════════════════════════════════════════════
# PART H — fuzzy match against goods-list text
# ══════════════════════════════════════════════════════════════════

def test_rf639_best_match_finds_ss20_in_adsm_list_format():
    from aria_service.intel.eliminated_weapons_watchlist import (
        best_match_for_text, EliminationStatus,
    )
    text = "SS-20 rockets → 10,000"  # the exact ADSM list format
    entry = best_match_for_text(text)
    assert entry is not None
    assert entry.elimination_status == EliminationStatus.ELIMINATED


def test_rf639_best_match_returns_none_for_irrelevant():
    from aria_service.intel.eliminated_weapons_watchlist import best_match_for_text
    assert best_match_for_text("catering services Riyadh") is None


def test_rf639_best_match_handles_bad_inputs():
    from aria_service.intel.eliminated_weapons_watchlist import best_match_for_text
    assert best_match_for_text("") is None
    assert best_match_for_text(None) is None  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════
# PART I — schema + coverage
# ══════════════════════════════════════════════════════════════════

def test_rf639_watchlist_coverage_minimum():
    from aria_service.intel.eliminated_weapons_watchlist import stats
    s = stats()
    assert s["total_entries"] >= 25
    assert s["eliminated"] >= 6     # at least the INF set
    assert s["categorically_banned"] >= 8   # CWC + BWC
    assert s["treaty_prohibited_state_party"] >= 8   # Ottawa + CCM


def test_rf639_every_entry_has_required_fields():
    from aria_service.intel.eliminated_weapons_watchlist import all_entries
    for entry in all_entries():
        assert entry.canonical_name
        assert entry.elimination_status in (
            "eliminated", "categorically_banned",
            "treaty_prohibited_state_party",
        )
        assert entry.treaty
        assert entry.treaty_year > 1900
        # Eliminated entries must have elimination_year
        if entry.elimination_status == "eliminated":
            assert entry.elimination_year is not None
            assert entry.elimination_year >= entry.treaty_year


def test_rf639_case_insensitive_lookup():
    from aria_service.intel.eliminated_weapons_watchlist import lookup_by_name
    assert lookup_by_name("ss-20") is not None
    assert lookup_by_name("SS-20") is not None
    assert lookup_by_name("Ss-20") is not None


def test_rf639_render_finding_confidence_is_confirmed():
    """Treaty status is Tier-1a authoritative (intergovernmental
    convention) — confidence is CONFIRMED, not PROBABLE."""
    from aria_service.intel.eliminated_weapons_watchlist import (
        render_finding_for_text,
    )
    f = render_finding_for_text("SS-20 rockets")
    assert f is not None
    assert f["confidence"] == "CONFIRMED"

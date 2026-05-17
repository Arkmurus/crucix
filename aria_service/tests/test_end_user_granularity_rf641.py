"""R-F641 — End-user granularity scorer tests."""
from __future__ import annotations

import pytest


def test_rf641_saudi_gov_generic_is_insufficient_or_country_level():
    """The ADSM case verbal '=Saudi Gov' must NOT pass for EUC."""
    from aria_service.intel.end_user_granularity import (
        score, is_sufficient_for_euc, GranularityLevel,
    )
    result = score("Saudi Government", country_iso2="SA")
    assert result.level == GranularityLevel.COUNTRY_LEVEL
    assert is_sufficient_for_euc("Saudi Government", "SA") is False
    assert is_sufficient_for_euc("Saudi Gov", "SA") is False


def test_rf641_rslf_passes_as_specific_directorate():
    from aria_service.intel.end_user_granularity import (
        score, is_sufficient_for_euc, GranularityLevel,
    )
    for name in ("Royal Saudi Land Forces", "RSLF", "Saudi Army"):
        result = score(name, country_iso2="SA")
        assert result.level == GranularityLevel.SPECIFIC_DIRECTORATE
        assert is_sufficient_for_euc(name, "SA") is True


def test_rf641_sang_passes():
    from aria_service.intel.end_user_granularity import (
        score, GranularityLevel,
    )
    for name in ("Saudi Arabian National Guard", "SANG", "National Guard"):
        result = score(name, country_iso2="SA")
        assert result.level == GranularityLevel.SPECIFIC_DIRECTORATE


def test_rf641_saudi_mod_is_ministry_level_borderline():
    from aria_service.intel.end_user_granularity import (
        score, GranularityLevel,
    )
    result = score("Saudi MoD", country_iso2="SA")
    assert result.level == GranularityLevel.MINISTRY_LEVEL


def test_rf641_uae_edge_resolves():
    from aria_service.intel.end_user_granularity import (
        score, GranularityLevel,
    )
    for name in ("EDGE Group", "EDGE", "Tawazun", "EDIC"):
        result = score(name, country_iso2="AE")
        assert result.level == GranularityLevel.SPECIFIC_DIRECTORATE


def test_rf641_us_dsca_resolves():
    from aria_service.intel.end_user_granularity import (
        score, GranularityLevel,
    )
    result = score("DSCA", country_iso2="US")
    assert result.level == GranularityLevel.SPECIFIC_DIRECTORATE


def test_rf641_unknown_text_returns_insufficient():
    from aria_service.intel.end_user_granularity import (
        score, GranularityLevel,
    )
    result = score("private buyer abc", country_iso2="SA")
    assert result.level == GranularityLevel.INSUFFICIENT


def test_rf641_empty_or_none_safe():
    from aria_service.intel.end_user_granularity import (
        score, GranularityLevel, is_sufficient_for_euc,
    )
    assert score("").level == GranularityLevel.INSUFFICIENT
    assert score(None).level == GranularityLevel.INSUFFICIENT  # type: ignore
    assert is_sufficient_for_euc("") is False


def test_rf641_render_finding_hard_stop_for_insufficient():
    from aria_service.intel.end_user_granularity import render_finding
    f = render_finding("some random buyer name")
    assert f["severity"] == "hard_stop"
    assert "HARD STOP" in f["title"]


def test_rf641_render_finding_info_for_specific_directorate():
    from aria_service.intel.end_user_granularity import render_finding
    f = render_finding("Royal Saudi Land Forces", country_iso2="SA")
    assert f["severity"] == "info"
    assert "adequately specified" in f["title"]


def test_rf641_render_finding_red_for_country_level():
    from aria_service.intel.end_user_granularity import render_finding
    f = render_finding("Saudi Government", country_iso2="SA")
    assert f["severity"] == "red"
    assert "country level" in f["title"].lower() or "INSUFFICIENT" in f["title"]


def test_rf641_list_known_entities_covers_priority_countries():
    from aria_service.intel.end_user_granularity import list_known_entities
    for iso2 in ("SA", "AE", "EG", "JO", "GB", "US", "FR", "DE"):
        entities = list_known_entities(iso2)
        assert len(entities) >= 1


def test_rf641_cross_country_search_works_without_iso2_filter():
    """Without country_iso2 arg, the search scans all countries."""
    from aria_service.intel.end_user_granularity import (
        score, GranularityLevel,
    )
    result = score("Bundeswehr")  # no iso2
    assert result.level == GranularityLevel.SPECIFIC_DIRECTORATE
    assert result.iso2 == "DE"

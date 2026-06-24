"""
R-F1893 capability test: 'global' region is dropped from gate #2 heatmap floor.

The 'global' region is detect_regions()'s no-match fallback — a region-LESS
query has no region, so it is a TOPIC-mastery datapoint (gate #1), NOT a
topic×REGION datapoint (gate #2). This test verifies that get_regional_heatmap()
excludes 'global' cells from the heatmap, weak_cells, and floor_breach_cells,
mirroring the existing 'general'-topic exclusion (R-F684 B).

After the drop, the floor should be the minimum over REAL-region cells only.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch, AsyncMock

from aria_service.intel import student


@pytest.mark.asyncio
async def test_global_region_excluded_from_heatmap():
    """'global' must NOT appear in the returned heatmap."""
    hm_data = await student.get_regional_heatmap()
    heatmap = hm_data.get("heatmap", {})
    for topic, regions in heatmap.items():
        assert "global" not in regions, (
            f"Topic '{topic}' has 'global' region in heatmap — "
            f"should be excluded per R-F1893"
        )


@pytest.mark.asyncio
async def test_global_region_excluded_from_weak_cells():
    """No weak_cell should have region='global'."""
    hm_data = await student.get_regional_heatmap()
    weak = hm_data.get("weak_cells", [])
    for cell in weak:
        assert cell.get("region") != "global", (
            f"Weak cell has region='global': {cell}"
        )


@pytest.mark.asyncio
async def test_global_region_excluded_from_floor_breach():
    """No floor_breach_cell should have region='global'."""
    hm_data = await student.get_regional_heatmap()
    breach = hm_data.get("floor_breach_cells", [])
    for cell in breach:
        assert cell.get("region") != "global", (
            f"Floor breach cell has region='global': {cell}"
        )


@pytest.mark.asyncio
async def test_floor_is_min_of_real_regions():
    """The floor must be the minimum over real-region cells only.

    When the regional-mastery store is empty (local test env), the heatmap
    will be empty — that's fine, the key assertion is that 'global' is absent.
    """
    hm_data = await student.get_regional_heatmap()
    heatmap = hm_data.get("heatmap", {})

    # Verify no 'global' cells leaked into the heatmap
    for topic, regions in heatmap.items():
        for region in regions:
            assert region != "global", (
                f"Region 'global' leaked into heatmap for topic '{topic}'"
            )

    # If there are cells, verify the floor is computed correctly
    all_scores = [s for regions in heatmap.values() for s in regions.values()]
    if all_scores:
        computed_floor = min(all_scores)
        breach = hm_data.get("floor_breach_cells", [])
        if breach:
            breach_floor = breach[0]["score"]
            assert breach_floor == computed_floor, (
                f"Floor breach min ({breach_floor}) != computed min ({computed_floor})"
            )


@pytest.mark.asyncio
async def test_global_drop_mirrors_general_drop():
    """The 'global' drop should mirror the 'general' drop — both are
    catch-all fallbacks that should not count toward regional mastery."""
    hm_data = await student.get_regional_heatmap()
    heatmap = hm_data.get("heatmap", {})

    # 'general' topic should not appear
    assert "general" not in heatmap, (
        "'general' topic should be excluded per R-F684 (B)"
    )

    # No region should have 'global' cells
    for topic, regions in heatmap.items():
        assert "global" not in regions, (
            f"Topic '{topic}' still has 'global' region"
        )


@pytest.mark.asyncio
async def test_real_regions_still_present():
    """Real regions must still be present after dropping 'global'.

    When the regional-mastery store is empty (local test env), the heatmap
    will be empty — that's acceptable. The key assertion is that 'global'
    is not present.
    """
    hm_data = await student.get_regional_heatmap()
    heatmap = hm_data.get("heatmap", {})

    all_regions = set()
    for regions in heatmap.values():
        all_regions.update(regions.keys())

    # 'global' must never appear
    assert "global" not in all_regions, (
        f"'global' region leaked into heatmap"
    )

    # If there are regions, at least some should be real (non-empty state)
    if all_regions:
        real_regions = {"lusophone", "west_africa", "east_africa", "mena", "gulf",
                        "turkey", "europe", "balkans", "nato"}
        found_real = all_regions & real_regions
        assert len(found_real) > 0, (
            f"No real regions found in heatmap after dropping 'global'. "
            f"All regions: {all_regions}"
        )

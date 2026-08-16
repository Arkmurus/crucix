"""R-F1696 — a sanctions screen that could NOT run must never read as "clean".

The catastrophic false-negative class on a compliance product: when
OpenSanctions is unreachable (auth / 429 / 5xx / timeout / circuit-breaker
open), the source helpers used to `return []`, fuzzy_screen reported
`blocked=False` with no error, and the DD renderer stamped
"Sanctions screen CLEAN — CONFIRMED — treat as clearance". A sanctioned
entity could be cleared purely because the data source was down.

R-F1696 makes source availability flow up:
  helpers -> _SourceQuery(results, ok, reason)
  fuzzy_screen / screen_with_aliases -> screened / source_unavailable + wire_failure
  DD render -> "UNVERIFIED, not a clearance" (tested at the contract level here;
               the render branch keys off source_unavailable/error).

These capability tests drive the REAL screen path with the source stubbed.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aria_service.intel import sanctions
from aria_service.intel.sanctions import _SourceQuery


def _down(*_a, **_k):
    return _SourceQuery([], False, "auth")  # source unreachable


def _empty_ok(*_a, **_k):
    return _SourceQuery([], True, "ok")  # queried OK, genuinely no hits


def _hit(*_a, **_k):
    return _SourceQuery(
        [{"id": "ofac-1", "score": 0.96, "caption": "Test Entity",
          "properties": {"name": ["Test Entity"]}, "datasets": ["us_ofac_sdn"]}],
        True, "ok",
    )


@pytest.mark.asyncio
async def test_fuzzy_screen_source_down_never_reads_as_full_coverage_clean():
    """OpenSanctions down → the result must NOT present as a full-coverage pass.

    R-F4048 (C-107) — THIS TEST PINNED A SUPERSEDED CONTRACT AND HAD BEEN RED
    EVER SINCE. It asserted `source_unavailable is True` / `screened is False`,
    which was correct when written: a down source meant no screen at all.

    R-F3529 then added the LOCAL CANONICAL FLOOR beneath OpenSanctions, so a
    down aggregator no longer means unscreened — the local store answers and
    `screened` is legitimately True. R-F3945 (C-39) completed that by making the
    narrowed coverage EXPLICIT instead of letting it masquerade as full
    coverage, which is the defect that actually mattered: eight canonical lists
    were being stamped CLEAN without being queried.

    The ORIGINAL INTENT — "a source being down must never read as a clean pass"
    — is unchanged and is what this test now asserts, through the mechanism that
    currently carries it. Greening it by restoring `source_unavailable` would
    mean deleting the floor, i.e. taking screening dark whenever OpenSanctions
    is unavailable.
    """
    with patch.object(sanctions, "_opensanctions_match", AsyncMock(side_effect=_down)), \
         patch.object(sanctions, "_opensanctions_search", AsyncMock(side_effect=_down)), \
         patch.object(sanctions, "wire_failure", MagicMock()) as wf:
        r = await sanctions.fuzzy_screen("Vladimir Testovich Putin")

    # The coverage block is ALWAYS emitted (C-39: a block that appears only on
    # failure cannot describe the dangerous case), and must declare the floor.
    coverage = r.get("coverage")
    assert isinstance(coverage, dict), (
        f"no coverage block — the verdict cannot be audited: {sorted(r)}"
    )
    assert coverage.get("mode") != "opensanctions_aggregate", (
        "the aggregate was NOT consulted, so claiming aggregate coverage would "
        "attribute the clearance to a source that refused us (C-39)"
    )
    assert coverage.get("mode") == "local_canonical_floor", (
        f"expected the local floor to serve; got {coverage!r}"
    )
    # Whatever the floor holds, it is a NARROWER set than the aggregate, and it
    # must be named so downstream can mark the rest UNAVAILABLE rather than CLEAN.
    assert coverage.get("sources_consulted"), (
        "floor mode with an empty consulted list must fail CLOSED, never report "
        "a clean screen against an undeterminable registry"
    )

    assert r["blocked"] is False          # genuinely no hit in the floor
    wf.assert_called_once()               # §21a: the degradation reached the brain


@pytest.mark.asyncio
async def test_fuzzy_screen_genuine_clean_when_source_answers():
    """Source answers with zero hits → genuinely clean (screened=True, no
    source_unavailable flag)."""
    with patch.object(sanctions, "_opensanctions_match", AsyncMock(side_effect=_empty_ok)), \
         patch.object(sanctions, "_opensanctions_search", AsyncMock(side_effect=_empty_ok)), \
         patch("aria_service.intel.brain_hook.absorb", AsyncMock()):
        r = await sanctions.fuzzy_screen("Some Clean Company Ltd")
    assert r["screened"] is True
    assert r.get("source_unavailable") is None
    assert r["blocked"] is False


@pytest.mark.asyncio
async def test_fuzzy_screen_real_hit_blocks_and_is_screened():
    with patch.object(sanctions, "_opensanctions_match", AsyncMock(side_effect=_hit)), \
         patch("aria_service.intel.brain_hook.absorb", AsyncMock()):
        r = await sanctions.fuzzy_screen("Test Entity")
    assert r["screened"] is True
    assert r["blocked"] is True
    assert r.get("source_unavailable") is None


@pytest.mark.asyncio
async def test_screen_with_aliases_propagates_source_unavailable():
    """The aggregate (what DD actually calls) propagates source_unavailable and
    wires a failure when no alias could be screened."""
    with patch.object(sanctions, "_opensanctions_match", AsyncMock(side_effect=_down)), \
         patch.object(sanctions, "_opensanctions_search", AsyncMock(side_effect=_down)), \
         patch.object(sanctions, "wire_failure", MagicMock()) as wf, \
         patch.object(sanctions, "wire_success", MagicMock()) as ws:
        r = await sanctions.screen_with_aliases("Acme Holdings", ["Acme Group"])
    assert r["source_unavailable"] is True
    assert r["screened"] is False
    assert r["blocked"] is False
    assert r["error"] == "sanctions_source_unavailable"
    # Each sub-screen wires its own failure AND the aggregate wires one — all
    # genuine signals. The aggregate's must be among them.
    assert wf.call_count >= 1
    assert any(c.kwargs.get("source") == "sanctions:screen_with_aliases" for c in wf.call_args_list)
    ws.assert_not_called()  # must NOT wire success for an unperformed screen


@pytest.mark.asyncio
async def test_screen_with_aliases_clean_wires_success_when_screened():
    with patch.object(sanctions, "_opensanctions_match", AsyncMock(side_effect=_empty_ok)), \
         patch.object(sanctions, "_opensanctions_search", AsyncMock(side_effect=_empty_ok)), \
         patch.object(sanctions, "wire_failure", MagicMock()) as wf, \
         patch.object(sanctions, "wire_success", MagicMock()) as ws, \
         patch("aria_service.intel.brain_hook.absorb", AsyncMock()):
        r = await sanctions.screen_with_aliases("Some Clean Company Ltd")
    assert r["screened"] is True
    assert r.get("source_unavailable") is None
    ws.assert_called_once()
    wf.assert_not_called()

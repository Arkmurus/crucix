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
async def test_fuzzy_screen_source_unavailable_is_not_clean():
    """Source down → result is UNVERIFIED (source_unavailable), NOT a clean pass,
    and a failure signal is wired so the brain learns the screen didn't run."""
    with patch.object(sanctions, "_opensanctions_match", AsyncMock(side_effect=_down)), \
         patch.object(sanctions, "_opensanctions_search", AsyncMock(side_effect=_down)), \
         patch.object(sanctions, "wire_failure", MagicMock()) as wf:
        r = await sanctions.fuzzy_screen("Vladimir Testovich Putin")
    assert r["source_unavailable"] is True
    assert r["screened"] is False
    assert r["blocked"] is False
    assert r["error"] == "sanctions_source_unavailable"
    wf.assert_called_once()  # §21a: failure reached the brain, not dark


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

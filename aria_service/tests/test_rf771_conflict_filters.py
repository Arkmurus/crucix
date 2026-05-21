"""R-F771 (2026-05-21) — conflict-detect noise filters + bulk clear.

The 2026-05-21 forensic dump of `/api/aria/neural/conflicts` showed the
queue had become unusable for human review: ~90% of entries were false
positives produced by ARIA's own autonomous loops re-absorbing canonical
news scaffolding (e.g. `[COMPREHENSION PASS — Clause 21...]`) against
entities whose neuron metadata was seeded HIGH-risk from news copy.
Substring keyword match in detect_conflict then fired HIGH→LOW dozens
of times for the same entity (Baykar 7x, DoD 15x in a 5-day slice).

R-F771 adds three guards + a bulk-clear:
  1. Source filter — skip when source startswith `chat:autonomous:`
  2. Scaffolding filter — skip when new_text contains response-contract
     / comprehension-pass markers
  3. clear_all_conflicts() + POST /neural/conflicts/clear so the
     existing pile can be reset and the post-filter signal observed
     from a clean baseline.

These tests pin the filter conditions so future R-numbers cannot
silently undo them (cf. R-F647 only-Arkmurus filter was insufficient).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch


# ── Filter A: autonomous-loop source ──────────────────────────────────────

def test_detect_conflict_skips_autonomous_source():
    """A `chat:autonomous:*` source must short-circuit detect_conflict
    before any neuron lookup. Live evidence: WEEKLY-HW-PLATFORMS produced
    15+ DoD entries and 7+ Baykar entries in a single 5-day slice.
    """
    from aria_service.intel import neural_memory as nm

    # Sanity: the prefix tuple actually contains the autonomous prefix
    assert any(p.startswith("chat:autonomous:") for p in nm._NOISY_SOURCE_PREFIXES), (
        "R-F771: `chat:autonomous:` source-prefix filter missing"
    )

    # Even if the entity + signal text would normally trigger a conflict,
    # an autonomous source must skip detection. Use a real-looking text
    # that does NOT contain scaffolding markers so we're sure the source
    # filter (not the scaffolding filter) is what short-circuits.
    with patch.object(nm, "_loaded", True), \
         patch.object(nm, "_find_neuron") as fn:
        result = nm.detect_conflict(
            "Baykar",
            "Baykar sanctioned for export violation; risk profile elevated.",
            source="chat:autonomous:WEEKLY-HW-PLATFORMS:2026-05-11",
        )
    assert result is None
    # The neuron lookup must NEVER be reached when the source is filtered
    fn.assert_not_called()


# ── Filter B: ARIA scaffolding markers ─────────────────────────────────────

def test_detect_conflict_skips_comprehension_pass_text():
    """Text containing the [COMPREHENSION PASS …] prefix is ARIA's own
    response-contract instructions, not new intel. Skip the scan.
    Live evidence: ~30% of conflict queue entries had this prefix in
    their new_text_preview."""
    from aria_service.intel import neural_memory as nm

    assert "[COMPREHENSION PASS" in nm._SCAFFOLDING_MARKERS, (
        "R-F771: COMPREHENSION PASS marker missing from filter set"
    )

    text = (
        "[COMPREHENSION PASS — Clause 21, Understand Before Act]\n"
        "Confidence: CLEAR\n"
        "High-stakes keywords present: procurement, sanctions, verified\n"
        "Baykar export licence cleared per registry check."
    )
    with patch.object(nm, "_loaded", True), \
         patch.object(nm, "_find_neuron") as fn:
        result = nm.detect_conflict("Baykar", text, source="chat:user_query")
    assert result is None
    fn.assert_not_called()


def test_detect_conflict_skips_response_contract_text():
    """Sibling guard: `RESPONSE CONTRACT for this turn` marker."""
    from aria_service.intel import neural_memory as nm

    assert "RESPONSE CONTRACT for this turn" in nm._SCAFFOLDING_MARKERS

    text = (
        "RESPONSE CONTRACT for this turn:\n"
        "  1. Start your reply with 'UNDERSTOOD'\n"
        "  2. Verify entity Baykar against sanctions list before any claim.\n"
    )
    with patch.object(nm, "_loaded", True), \
         patch.object(nm, "_find_neuron") as fn:
        result = nm.detect_conflict("Baykar", text)
    assert result is None
    fn.assert_not_called()


# ── Negative: legitimate counterparty intel still triggers ────────────────

def test_detect_conflict_still_fires_on_real_intel():
    """The R-F771 filters must NOT swallow a genuine conflict. A normal
    user-chat source + no scaffolding markers + opposing risk signals
    must still produce a conflict dict."""
    from aria_service.intel import neural_memory as nm

    fake_neuron = {
        "id": "test-neuron-rf771",
        "label": "Test Entity",
        "metadata": {"summary": "previously sanctioned per OFAC listing"},
        "source": "registry:ofac",
        "confidence": "ASSESSED",
    }
    with patch.object(nm, "_loaded", True), \
         patch.object(nm, "_find_neuron", return_value=fake_neuron), \
         patch.object(nm, "_edges", {}):
        result = nm.detect_conflict(
            "Test Entity",
            "Test Entity has been cleared and is now reputable per UK ECJU review.",
            source="chat:user_query",
        )
    assert result is not None, (
        "R-F771 regression: a real HIGH→LOW conflict from a non-filtered "
        "source with no scaffolding markers must still be detected."
    )
    assert result["existing_assessment"] == "HIGH_RISK"
    assert result["new_assessment"] == "LOW_RISK"


# ── learn_from_text plumbs source through to detect_conflict ──────────────

def test_learn_from_text_passes_source_to_detect_conflict():
    """The autonomous-source filter is only useful if learn_from_text
    actually plumbs its `source` arg to detect_conflict. R-F771 added
    `source=source` to the call site; this test pins the call signature
    so a future refactor cannot silently drop it."""
    src = Path(
        "aria_service/intel/neural_memory.py"
    ).read_text(encoding="utf-8")
    # The call site inside learn_from_text's conflict loop must pass source
    assert re.search(
        r"detect_conflict\(\s*concept\s*,\s*text\s*,\s*source\s*=\s*source\s*\)",
        src,
    ), (
        "R-F771 regression: learn_from_text no longer passes source= to "
        "detect_conflict — the autonomous-loop noise filter will be a no-op."
    )


# ── F: bulk clear ─────────────────────────────────────────────────────────

def test_clear_all_conflicts_drops_list_and_returns_count():
    """clear_all_conflicts() must empty the list and return the previous
    count so the operator can confirm how many entries were dropped."""
    from aria_service.intel import neural_memory as nm

    sample = [{"entity": "Baykar"}, {"entity": "DoD"}, {"entity": "FAA"}]
    rs = nm.rs

    state = {"data": sample.copy()}

    async def fake_get_json(_key):
        return state["data"]

    async def fake_set_json(_key, value, **_kw):
        state["data"] = value

    with patch.object(rs, "get_json", side_effect=fake_get_json), \
         patch.object(rs, "set_json", side_effect=fake_set_json):
        count = asyncio.run(nm.clear_all_conflicts())
    assert count == 3
    assert state["data"] == []


def test_clear_all_conflicts_on_empty_returns_zero():
    """No entries → returns 0, doesn't write."""
    from aria_service.intel import neural_memory as nm

    writes: list = []

    async def fake_get_json(_key):
        return []

    async def fake_set_json(_key, value, **_kw):
        writes.append(value)

    with patch.object(nm.rs, "get_json", side_effect=fake_get_json), \
         patch.object(nm.rs, "set_json", side_effect=fake_set_json):
        count = asyncio.run(nm.clear_all_conflicts())
    assert count == 0
    assert writes == [], "must not write when there's nothing to clear"


# ── Route: POST /neural/conflicts/clear ───────────────────────────────────

def test_clear_route_registered_in_aria_router():
    """The bulk-clear endpoint must be declared at
    POST /neural/conflicts/clear so the operator dashboard can hit it."""
    src = Path(
        "aria_service/routes/aria.py"
    ).read_text(encoding="utf-8")
    assert re.search(
        r'@router\.post\(\s*"/neural/conflicts/clear"\s*\)',
        src,
    ), "R-F771: POST /neural/conflicts/clear route decorator missing"
    # And the handler must await neural_memory.clear_all_conflicts
    assert re.search(
        r'@router\.post\(\s*"/neural/conflicts/clear"\s*\)\s*\n'
        r'async def \w+\([^)]*\):\s*\n'
        r'\s+cleared\s*=\s*await neural_memory\.clear_all_conflicts\(\)',
        src,
    ), "R-F771: handler does not await neural_memory.clear_all_conflicts()"

"""R-F647 — neural_memory.detect_conflict skips operator-self entities.

Live evidence 2026-05-17 07:21:09 UTC:
  [conflict] Arkmurus: existing=HIGH_RISK new=LOW_RISK — flagged for review

Arkmurus is the operator's own org (per memory/arkmurus_org_profile). Every
eval prompt and many chat turns describe scenarios Arkmurus is *screening*,
so Arkmurus appears in the same text as both _HIGH_RISK_SIGNALS
("sanctioned", "fraud", "embargo") AND _LOW_RISK_SIGNALS ("verified",
"approved", "compliant") — depending on which scenario the prompt frames.

The pre-R-F647 detector treated Arkmurus as a counterparty and recorded
spurious "conflicts" between successive prompts. The conflict log
(crucix:aria:neural_conflicts) has no TTL since 2026-04-21 — these noise
entries accumulate permanently.

Fix: skip _OPERATOR_SELF_ENTITIES (Arkmurus, Crucix) from detect_conflict
early. They are not counterparties; their "risk" assessment is undefined.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from aria_service.intel import neural_memory as nm


_SRC = (Path(__file__).resolve().parent.parent / "intel" / "neural_memory.py").read_text(
    encoding="utf-8"
)


def test_rf647_operator_self_constant_present():
    """The exclusion list must exist as a module-level constant so tests
    + future code can reference it."""
    assert hasattr(nm, "_OPERATOR_SELF_ENTITIES"), (
        "R-F647: _OPERATOR_SELF_ENTITIES constant missing from neural_memory"
    )
    s = nm._OPERATOR_SELF_ENTITIES
    assert isinstance(s, (set, frozenset)), (
        "R-F647: _OPERATOR_SELF_ENTITIES must be a set for O(1) lookup"
    )
    # Lowercased keys (the check normalises before comparing)
    assert "arkmurus" in s
    assert "crucix" in s


def test_rf647_detect_conflict_skips_arkmurus(monkeypatch):
    """Even when the module is loaded and a conflicting risk signal pair
    is supplied, an operator-self entity must short-circuit to None."""
    # Force the module to look loaded so the early-return for `_loaded`
    # doesn't shadow the R-F647 check we want to exercise.
    monkeypatch.setattr(nm, "_loaded", True)

    text_with_conflict = (
        "Arkmurus has been sanctioned and is a confirmed shell company — "
        "but our records show it is verified and approved."
    )
    # Both HIGH and LOW signals are present in the text. Without the
    # R-F647 skip, the loaded-module path would proceed; with it, we
    # short-circuit before _find_neuron even runs.
    assert nm.detect_conflict("Arkmurus", text_with_conflict) is None
    assert nm.detect_conflict("arkmurus", text_with_conflict) is None
    assert nm.detect_conflict("  ARKMURUS  ", text_with_conflict) is None
    assert nm.detect_conflict("Crucix", text_with_conflict) is None


def test_rf647_detect_conflict_still_works_for_counterparties(monkeypatch):
    """The exclusion must be narrow: a real counterparty name with mixed
    signals must still flow into the normal detect_conflict path (which
    will then return None for its own reasons — e.g. no matching neuron
    — but not because of the R-F647 short-circuit)."""
    monkeypatch.setattr(nm, "_loaded", True)
    # No neuron exists, so detect_conflict returns None via the
    # `_find_neuron` path — not via R-F647. That's fine: what we're
    # verifying is that this code path is REACHED for non-self entities.
    captured = {}

    def _spy(entity):
        captured["called_with"] = entity
        return None  # no neuron, normal None return

    monkeypatch.setattr(nm, "_find_neuron", _spy)
    nm.detect_conflict("Acme Trading Co", "sanctioned by OFAC")
    assert captured.get("called_with") == "Acme Trading Co", (
        "R-F647 over-broad: _find_neuron was not reached for a regular "
        "counterparty entity — the operator-self skip is matching too much"
    )


def test_rf647_source_carries_marker_comment():
    """The R-F647 marker comment anchors the fix so future refactors that
    move the exclusion know it's load-bearing."""
    assert "R-F647" in _SRC, "R-F647 marker comment missing from neural_memory.py"
    # And the constant is referenced inside detect_conflict
    detect_block = _SRC[_SRC.find("def detect_conflict"):_SRC.find("def detect_conflict") + 2000]
    assert "_OPERATOR_SELF_ENTITIES" in detect_block, (
        "R-F647: detect_conflict does not reference the exclusion set"
    )

"""R-F3765 — CAPABILITY: production verifications must not silently join the delivery gate.

`avg_grounded_rate` is consumed by operating_modes.evaluate_auto_transition, which
DEGRADES the whole platform below 30% and thereby SUPPRESSES ALL EXTERNAL DELIVERY.

Today every record in the index is an EVAL record: record_verification has exactly
one production caller (eval_runner.py:762, tool_used="eval"), and the chat path is an
acknowledged no-op — aria_engine.py:5398 says "Intentional no-op until source_verifier
verdict is plumbed here".

The moment production verifications start being recorded they would join that average
and change what the delivery gate MEANS — with no code change and no signal. Eval
questions are curated; production traffic is not. The two populations are not
interchangeable, and conflating them is how a correct feature causes an outage.

This split is written while it is provably INERT (no production records exist), so that
enabling production recording later is a visible, deliberate decision rather than a
silent reinterpretation of a live safety threshold.

Run: python -m pytest aria_service/tests/test_rf3765_eval_production_split.py -v
"""
from __future__ import annotations

import asyncio
import time

import pytest

from aria_service.intel import source_verifier as sv


def _entry(rate, tool, ts=None):
    return {"verdict": "grounded", "grounded_rate": rate,
            "tool_used": tool, "ts": ts if ts is not None else time.time()}


def _stats(monkeypatch, index):
    async def _get(key):
        return index
    monkeypatch.setattr(sv.rs, "get_json", _get)
    monkeypatch.setattr(sv.rs, "get_json_strict", _get)
    return asyncio.run(sv.get_verification_stats())


def test_eval_only_today_is_declared(monkeypatch):
    """THE HEADLINE: the gate must SAY it is running on the benchmark alone."""
    s = _stats(monkeypatch, [_entry(0.9, "eval"), _entry(0.8, "eval")])
    assert s["gate_is_eval_only"] is True
    assert s["production_sample_size"] == 0
    assert s["production_grounded_rate"] is None
    assert s["eval_sample_size"] == 2


def test_production_records_are_reported_separately(monkeypatch):
    """When chat recording is wired, production must be visible on its own."""
    s = _stats(monkeypatch, [
        _entry(1.0, "eval"), _entry(1.0, "eval"),
        _entry(0.0, "chat"), _entry(0.2, "dd"),
    ])
    assert s["eval_sample_size"] == 2
    assert s["eval_grounded_rate"] == 1.0
    assert s["production_sample_size"] == 2
    assert s["production_grounded_rate"] == 0.1
    assert s["gate_is_eval_only"] is False, (
        "production records exist but the stats still claim the gate is "
        "eval-only — that is the silent reinterpretation this guards against"
    )


def test_the_headline_rate_still_exists_for_the_gate(monkeypatch):
    """operating_modes reads avg_grounded_rate; it must not disappear."""
    s = _stats(monkeypatch, [_entry(0.9, "eval")])
    assert "avg_grounded_rate" in s and s["avg_grounded_rate"] is not None
    assert "effective_sample_size" in s, (
        "effective_sample_size is what R-F3764's minimum-sample floor reads; "
        "removing it would silently disable that guard"
    )


def test_no_samples_still_yields_None_not_zero(monkeypatch):
    """Pre-existing and load-bearing: 0.0 would degrade the platform on ABSENCE."""
    s = _stats(monkeypatch, [])
    assert s["avg_grounded_rate"] is None
    assert s["eval_grounded_rate"] is None
    assert s["production_grounded_rate"] is None
    assert s["gate_is_eval_only"] is True


def test_an_untagged_record_counts_as_production_not_eval(monkeypatch):
    """Fail toward VISIBILITY: an unlabelled record must not hide inside 'eval'.

    Only an explicit tool_used=="eval" is eval. Anything else — including a
    missing tag — is production, so a new caller that forgets to tag itself
    shows up rather than quietly inflating the benchmark population.
    """
    s = _stats(monkeypatch, [_entry(0.5, ""), _entry(0.5, None)])
    assert s["production_sample_size"] == 2
    assert s["eval_sample_size"] == 0
    assert s["gate_is_eval_only"] is False

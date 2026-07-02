"""R-F2285 — chat-DD ↔ "New DD" button quality parity.

Two defects made a chat-triggered DD lower-quality than a button DD:
  (3a) the prose entity-capture regex grabbed sentence fragments after a
       "background on"/"deep dive into" trigger → junk DD reports persisted with
       names like "is explicitly covered" / "and Investigation cognitive and
       reasoning". The button can't (it takes a required form field).
  (3b) the full-depth background upgrade (which persists a button-quality report)
       fired ONLY when the inline run time-boxed — a thin-but-not-timed-out chat
       run persisted its weak report as final.

These capability tests drive the REAL gate + the REAL upgrade-decision helper.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aria_service.routes import aria as A


class TestPlausibleEntityGate:
    @pytest.mark.parametrize("bad", [
        "is explicitly covered",                      # observed junk report
        "and Investigation cognitive and reasoning",  # observed junk report
        "of the company",
        "the is a test",
        "was reviewed yesterday",
    ])
    def test_rejects_sentence_fragments(self, bad):
        assert A._is_plausible_dd_entity(bad) is False

    @pytest.mark.parametrize("good", [
        "Boeing",
        "Acme Defence GmbH",
        "the Boeing Company",          # stop-word prefix BUT corporate suffix → keep
        "QinetiQ Group plc",
        "John A. Sample",
        "modirumgespi.com",            # URL-ish
        "https://modirumgespi.com/en",
    ])
    def test_accepts_real_entities(self, good):
        assert A._is_plausible_dd_entity(good) is True


class TestDetectIntentRejectsGarbage:
    def test_garbage_fragment_yields_no_dd(self):
        # triggers DD intent ("background on ") + captures a stop-word-led fragment
        assert A._detect_dd_intent("give me background on and Investigation cognitive and reasoning.") is None

    def test_clean_entity_still_detected(self):
        got = A._detect_dd_intent("run due diligence on Boeing")
        assert got is not None
        assert "boeing" in (got.get("name") or "").lower()


class TestDeepUpgradeParity:
    def test_time_boxed_triggers_upgrade(self):
        r = SimpleNamespace(time_boxed=True, confidence_gate_triggered=False, rf409_auto_escalated=False)
        assert A._dd_report_warrants_deep_upgrade(r, "standard") is True

    def test_thin_report_confidence_gate_triggers_upgrade(self):
        # the NEW behaviour — previously a thin (not-timed-out) run was NOT upgraded
        r = SimpleNamespace(time_boxed=False, confidence_gate_triggered=True, rf409_auto_escalated=False)
        assert A._dd_report_warrants_deep_upgrade(r, "standard") is True

    def test_healthy_report_not_upgraded(self):
        r = SimpleNamespace(time_boxed=False, confidence_gate_triggered=False, rf409_auto_escalated=False)
        assert A._dd_report_warrants_deep_upgrade(r, "standard") is False

    def test_already_deep_not_double_upgraded(self):
        r = SimpleNamespace(time_boxed=True, confidence_gate_triggered=True, rf409_auto_escalated=False)
        assert A._dd_report_warrants_deep_upgrade(r, "deep") is False

    def test_already_escalated_not_double_upgraded(self):
        r = SimpleNamespace(time_boxed=True, confidence_gate_triggered=True, rf409_auto_escalated=True)
        assert A._dd_report_warrants_deep_upgrade(r, "standard") is False

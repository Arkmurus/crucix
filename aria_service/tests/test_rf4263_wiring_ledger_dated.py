"""R-F4263 — the wiring debt ledger must be dateable (dossier E9).

The audit reported `OK — no NEW dark modules` while 63 modules had no brain
wiring. That is the ledger working as designed. What was not working: the
baseline carried **no `recorded_at` at all**, so nothing recorded how old the
debt was; it held 66 entries against 63 actual, so 3 were stale; and 5 modules
had changed category without being re-recorded.

A ledger of debt that cannot be aged is a ledger nobody pays down — 63 dark
modules were indistinguishable from a decision nobody remembers making.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

BASELINE = pathlib.Path(__file__).resolve().parents[2] / "docs/wiring_audit_baseline.json"
AUDIT = pathlib.Path(__file__).resolve().parents[2] / "scripts/ci/wiring_audit.py"


@pytest.fixture(scope="module")
def ledger() -> dict:
    if not BASELINE.is_file():
        pytest.skip("wiring baseline not present here")
    return json.loads(BASELINE.read_text(encoding="utf-8"))


class TestTheLedgerIsDated:
    def test_it_carries_a_recorded_at(self, ledger):
        assert ledger.get("recorded_at"), (
            "an undated debt ledger cannot be aged, which is why 63 dark "
            "modules read as a decision nobody remembers making"
        )

    def test_the_date_is_a_real_utc_timestamp(self, ledger):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                            ledger["recorded_at"]), ledger["recorded_at"]


class TestTheLedgerAgreesWithItself:
    def test_the_count_matches_the_entries(self, ledger):
        """66 entries against 63 actual is exactly the drift E9 found. A
        self-declared count makes that visible without re-running the scan."""
        assert ledger.get("module_count") == len(ledger.get("known_dark") or {})

    def test_entries_are_path_keyed_with_a_category(self, ledger):
        known = ledger.get("known_dark") or {}
        assert known, "an empty ledger would silently exempt everything"
        for path, verdict in known.items():
            assert path.startswith("aria_service/"), path
            assert verdict in {"no-wiring", "missing-failure",
                               "missing-success", "other"}, (path, verdict)


class TestItStaysADebtLedgerNotAnExemptionList:
    def test_the_comment_still_says_so(self, ledger):
        comment = ledger.get("_comment") or ""
        assert "NOT" in comment and "exemption list" in comment
        assert "wire it instead" in comment

    def test_the_writer_stamps_the_date(self):
        """A date written once by hand would rot at the next re-baseline."""
        source = AUDIT.read_text(encoding="utf-8")
        assert '"recorded_at"' in source
        assert "time.strftime" in source

    def test_the_gate_still_fails_on_a_new_dark_module(self):
        """The date must not have softened the thing the ledger is for."""
        source = AUDIT.read_text(encoding="utf-8")
        assert "AUDIT FAILED" in source
        assert "Do NOT add it to the baseline to go green." in source

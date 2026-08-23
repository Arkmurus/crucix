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

    def test_the_gate_still_fails_on_a_new_dark_module(self, tmp_path, monkeypatch):
        """BEHAVIOURAL, not a prose grep.

        Two earlier drafts of this test matched the refusal message as a
        string and failed on the line wrap — the message is split across three
        literals, so neither a literal match nor whitespace normalisation can
        join it. Matching source text was the wrong idea both times. What
        matters is that the gate EXITS NON-ZERO when a dark module is not in
        the ledger, so that is what is asserted: hand it a ledger with one
        entry removed and require a failure.
        """
        import json as _json
        import sys as _sys
        _sys.path.insert(0, "scripts/ci")
        import importlib
        audit = importlib.import_module("wiring_audit")

        real = _json.loads(BASELINE.read_text(encoding="utf-8"))
        known = dict(real["known_dark"])
        dropped = sorted(known)[0]
        del known[dropped]                      # pretend one dark module is new
        short = tmp_path / "baseline.json"
        short.write_text(_json.dumps({**real, "known_dark": known}), encoding="utf-8")

        monkeypatch.setattr(audit, "BASELINE", short)
        monkeypatch.chdir(pathlib.Path(__file__).resolve().parents[2])
        monkeypatch.setattr(_sys, "argv", ["wiring_audit.py"])
        assert audit.main() == 1, (
            f"the gate accepted {dropped} as unrecorded — dating the ledger "
            f"must not soften what it exists for"
        )

    def test_the_gate_passes_when_the_ledger_is_complete(self):
        """The other direction — a gate that always fails is not a gate."""
        import sys as _sys
        _sys.path.insert(0, "scripts/ci")
        import importlib
        audit = importlib.import_module("wiring_audit")
        import os
        cwd = os.getcwd()
        os.chdir(pathlib.Path(__file__).resolve().parents[2])
        try:
            argv = _sys.argv
            _sys.argv = ["wiring_audit.py"]
            try:
                assert audit.main() == 0
            finally:
                _sys.argv = argv
        finally:
            os.chdir(cwd)

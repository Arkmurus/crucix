"""R-F4255 / C-222 — a compliance control that could be switched off in silence.

`vetting/crypto.encryption_enabled()` gates crypto-shredding, the mechanism that
makes UK GDPR Art. 17 erasure possible at all. Its own docstring is explicit that
the default is the compliant one and the switch exists *"for migrating an existing
plaintext store, not as a routine setting."*

**With it off, nothing said so.** `routes/vetting.py` and
`routes/vetting_portal.py` both take `if encryption_enabled():` to False and write
**plaintext identity and criminal-offence data into the append-only evidence
store** — which exposes no delete. No log, no gap, no health surface.

The consequence surfaced only at ERASURE time, through
`retention._PLAINTEXT_RESIDUE_NOTE` — *"those artifacts are plaintext in an
append-only store and destroying the case key does not erase them"*. That is the
exact moment a data subject has exercised an Art. 17 right and the data is already
durably unerasable. **A control whose failure is discovered only when you are
legally obliged not to have failed is not a control.**

## Why the report lives in `encryption_enabled()`

At the ONE decision point, not at the two upload sites. Curating call sites is
whack-a-mole: R-F3946 records exactly that for the Brave DD gate, where the ninth
route silently re-opened it. A third caller of `encryption_enabled()` inherits this
automatically — and a test below proves both existing callers are covered without
either of them being touched.

## Once per process, and WARNING not ERROR

It is a CONFIG state, not a per-document event, so a gap per upload would be the
flood shape that has twice filled a 500-slot ledger (§18 records the same
once-per-process choice for `sanctions_coverage_degraded`). A restart re-reports
it, which is correct — the state is still true.

WARNING rather than ERROR because it is a deliberate operator setting, and R-F4248
records that an ERROR for an operator condition resets the Phase A gate-#3 streak.
"""
from __future__ import annotations

import logging

import pytest

from aria_service.vetting import crypto as vc


@pytest.fixture(autouse=True)
def _reset():
    vc._reset_disabled_report_for_test()
    yield
    vc._reset_disabled_report_for_test()


@pytest.fixture
def sink(monkeypatch):
    got = {"failure": []}
    import aria_service.intel.engine_wiring as ew
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: got["failure"].append(kw), raising=True)
    monkeypatch.setattr(ew, "wire_success", lambda **kw: None, raising=True)
    return got


def _off(monkeypatch, value="0"):
    monkeypatch.setenv("ARIA_VETTING_ENCRYPT_DOCUMENTS", value)


class TestTheDefaultIsStillCompliant:

    def test_unset_means_encryption_on(self, monkeypatch, sink):
        monkeypatch.delenv("ARIA_VETTING_ENCRYPT_DOCUMENTS", raising=False)
        assert vc.encryption_enabled() is True
        assert not sink["failure"], "the compliant default must not page"

    @pytest.mark.parametrize("val", ["1", "true", "on", "yes", ""])
    def test_affirmative_values_stay_on_and_silent(self, monkeypatch, sink, val):
        monkeypatch.setenv("ARIA_VETTING_ENCRYPT_DOCUMENTS", val)
        assert vc.encryption_enabled() is True
        assert not sink["failure"]


class TestBeingOffIsReportedImmediately:

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "OFF"])
    def test_every_disabling_value_reports(self, monkeypatch, sink, val):
        vc._reset_disabled_report_for_test()
        _off(monkeypatch, val)
        assert vc.encryption_enabled() is False
        assert sink["failure"], f"{val!r} disabled encryption without reporting it"

    def test_it_names_the_consequence_and_the_action(self, monkeypatch, sink):
        _off(monkeypatch)
        vc.encryption_enabled()
        d = sink["failure"][0]
        assert d["gap_type"] == "data_protection_violation"
        assert "Art. 17" in d["detail"]
        assert "OPERATOR ACTION" in d["detail"], (
            "a compliance gap must say what to do — unsetting the switch alone "
            "does not erase what was already written in plaintext")
        assert "purge" in d["detail"]

    def test_it_warns_and_never_errors(self, monkeypatch, sink, caplog):
        """ERROR would reset Phase A gate #3 for an operator setting (R-F4248)."""
        _off(monkeypatch)
        with caplog.at_level(logging.DEBUG, logger="aria.vetting.crypto"):
            vc.encryption_enabled()
        said = [r for r in caplog.records if "[R-F4255]" in r.getMessage()]
        assert said and any(r.levelno == logging.WARNING for r in said)
        assert not [r for r in said if r.levelno >= logging.ERROR]


class TestItCannotFloodTheLedger:

    def test_repeated_checks_report_once_per_process(self, monkeypatch, sink):
        _off(monkeypatch)
        for _ in range(25):
            vc.encryption_enabled()
        assert len(sink["failure"]) == 1, (
            f"a CONFIG state must report once, not once per call — every upload "
            f"calls this; got {len(sink['failure'])}")


class TestTheOneDecisionPointCoversEveryCaller:

    def test_both_upload_routes_gate_on_this_function(self):
        """Proof the fix reaches both callers WITHOUT touching either.

        If a future upload path stops calling `encryption_enabled()` and reads
        the env var itself, this fails — which is the drift R-F3946 describes.
        """
        import pathlib
        from ._source_probe import repo_path

        for rel in ("aria_service/routes/vetting.py",
                    "aria_service/routes/vetting_portal.py"):
            src = pathlib.Path(repo_path(rel)).read_text(encoding="utf-8")
            assert "encryption_enabled()" in src, f"{rel} no longer gates on it"
            assert "ARIA_VETTING_ENCRYPT_DOCUMENTS" not in src, (
                f"{rel} reads the env var directly — it would bypass the one "
                f"decision point and the disabled state would go unreported "
                f"again")


class TestReportingCannotBreakTheControl:

    def test_a_broken_sink_still_returns_the_right_answer(self, monkeypatch):
        import aria_service.intel.engine_wiring as ew

        def _boom(**kw):
            raise RuntimeError("brain unreachable")
        monkeypatch.setattr(ew, "wire_failure", _boom, raising=True)
        _off(monkeypatch)
        assert vc.encryption_enabled() is False, (
            "the control's ANSWER must survive its own reporting failing")

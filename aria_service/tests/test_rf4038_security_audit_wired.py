"""R-F4038 (C-102) — the security audit's OUTCOME must reach the brain.

§21a defines a path as wired only if BOTH its success and failure branches emit
to `brain_hook` / `capability_gaps` / `mistake_ledger` / a metric. "Logged to
console" is explicitly DARK.

`run_security_audit` was dark on every branch. Its CRITICAL findings — leaked
API keys, system-prompt fragments in the knowledge base — reached only a
`logger.warning` in `self_improve` (`self_improve.py`, Step 6) and the HTTP
response. Nothing reached the brain, so:

  * ARIA could not see her own security findings,
  * the coder loop could never pick one up (§21e: a finding that can be a Gap
    MUST become a Gap), and
  * a check that SKIPPED — i.e. went blind — was indistinguishable from one
    that passed.

The wires in this module sat on the knowledge-INGESTION function (R-F996), which
is why a grep for `wire_failure` made it look covered. Same shape as C-97, where
R-F4022's own failure branches were dark.

Flood control matters here: the audit runs every 2h, and `record_gap` dedupes
only 1h, so a persistent finding would emit ~12 gaps/day into a 500-slot ledger.
The outcome is therefore reported when the finding SET CHANGES, not every cycle.
"""
from __future__ import annotations

import pytest

from aria_service.intel import security_protocol as sp


@pytest.fixture(autouse=True)
def _reset_signature():
    sp._LAST_AUDIT_SIGNATURE = None
    yield
    sp._LAST_AUDIT_SIGNATURE = None


def _capture(monkeypatch):
    fails: list[dict] = []
    succs: list[dict] = []
    monkeypatch.setattr(sp, "wire_failure", lambda **kw: fails.append(kw))
    monkeypatch.setattr(sp, "wire_success", lambda **kw: succs.append(kw))
    return fails, succs


def test_clean_audit_wires_success(monkeypatch):
    """§21a success branch — 'the audit ran and found nothing' is real telemetry."""
    fails, succs = _capture(monkeypatch)
    sp._wire_audit_outcome({"critical": [], "warning": [], "issues_found": 0})
    assert succs, "a clean audit emitted nothing to the brain"
    assert succs[0].get("module") == "security_protocol"
    assert not fails


def test_critical_finding_wires_failure(monkeypatch):
    """A leaked key must become a Gap the coder can see, not a log line."""
    fails, succs = _capture(monkeypatch)
    sp._wire_audit_outcome({
        "critical": ["CHECK 1 FAIL: API key pattern found in knowledge base"],
        "warning": [],
        "issues_found": 1,
    })
    assert fails, "a CRITICAL security finding never reached the brain"
    kw = fails[0]
    assert kw.get("module") == "security_protocol"
    assert "CHECK 1" in kw.get("detail", "")
    assert kw.get("gap_type")
    assert not succs, "a critical finding must not also report success"


def test_a_skipped_check_is_reported_as_a_failure(monkeypatch):
    """A blind check must be visible — it is not a pass."""
    fails, _ = _capture(monkeypatch)
    sp._wire_audit_outcome({
        "critical": [],
        "warning": ["CHECK 2 SKIP: Could not scan for paths: boom"],
        "issues_found": 1,
    })
    assert fails, "a SKIPPED check went unreported — 'could not look' read as clean"
    assert "SKIP" in fails[0].get("detail", "")


def test_unchanged_findings_do_not_re_report(monkeypatch):
    """2h cadence vs a 1h record_gap dedupe would be ~12 gaps/day otherwise."""
    fails, _ = _capture(monkeypatch)
    payload = {
        "critical": ["CHECK 1 FAIL: API key pattern found in knowledge base"],
        "warning": [],
        "issues_found": 1,
    }
    sp._wire_audit_outcome(payload)
    sp._wire_audit_outcome(dict(payload))
    sp._wire_audit_outcome(dict(payload))
    assert len(fails) == 1, (
        f"the same standing finding was reported {len(fails)}x — this is the "
        f"ledger-flood shape CLAUDE.md records for sanctions_coverage_degraded"
    )


def test_a_new_finding_does_re_report(monkeypatch):
    """Suppression must not hide a CHANGE — that would be the flood cure eating the signal."""
    fails, _ = _capture(monkeypatch)
    sp._wire_audit_outcome({"critical": ["CHECK 1 FAIL: a"], "warning": [], "issues_found": 1})
    sp._wire_audit_outcome({
        "critical": ["CHECK 1 FAIL: a", "CHECK 3 FAIL: b"], "warning": [], "issues_found": 2,
    })
    assert len(fails) == 2, "a NEW critical finding was suppressed as a duplicate"


def test_recovery_to_clean_is_reported(monkeypatch):
    """Going from failing to clean is a state change worth knowing."""
    fails, succs = _capture(monkeypatch)
    sp._wire_audit_outcome({"critical": ["CHECK 1 FAIL: a"], "warning": [], "issues_found": 1})
    sp._wire_audit_outcome({"critical": [], "warning": [], "issues_found": 0})
    assert len(fails) == 1 and len(succs) == 1, (
        "recovery to clean must emit — otherwise the brain's last word on this "
        "module stays 'failing' forever"
    )


def test_wiring_never_raises_into_the_caller(monkeypatch):
    """Telemetry must never break the audit itself."""
    def _boom(**kw):
        raise RuntimeError("brain down")

    monkeypatch.setattr(sp, "wire_failure", _boom)
    monkeypatch.setattr(sp, "wire_success", _boom)
    sp._wire_audit_outcome({"critical": [], "warning": [], "issues_found": 0})


@pytest.mark.asyncio
async def test_run_security_audit_wires_end_to_end(monkeypatch):
    """The real entry point must wire — not just the helper."""
    from aria_service.intel import knowledge

    fails, succs = _capture(monkeypatch)

    async def _facts():
        return [{"content": "benign", "source": "research:web:a.com"}]

    monkeypatch.setattr(knowledge, "get_all_facts", _facts)
    await sp.run_security_audit()
    assert succs or fails, "run_security_audit() emitted nothing to the brain"

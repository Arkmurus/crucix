"""R-F3500 — a deleted DD kept being monitored, and its subject was logged by name.

Operator report, live 2026-07-30 15:08, ~30 lines in one second:

    R-F469: OpenSanctions breaker OPEN — skipping match() for 'RFSL'
    R-F469: OpenSanctions breaker OPEN — skipping match() for 'ROSSI FACILITY SERVICES'
    R-F469: OpenSanctions breaker OPEN — skipping match() for 'Chemring Group PLC'
    R-F469: OpenSanctions breaker OPEN — skipping match() for 'Roketsan A.S.'
    R-F469: OpenSanctions breaker OPEN — skipping match() for 'Silverbrook Capital Management'
    ...

Three separate defects sit behind those lines.

1. DELETION DOES NOT STOP MONITORING. ``delete_report`` removes the report blob,
   the index entry and the vault case — but never the WATCHLIST entry. The
   autonomous dd_monitor runs every 300s against the FULL watchlist
   (get_watchlist(user_id=None) is deliberately unscoped so monitoring covers
   every entity), so a subject the user deleted from their account keeps being
   re-screened indefinitely. ``remove_from_watchlist`` exists and is correct; it
   simply had exactly one caller — the explicit "remove watchlist entry"
   endpoint. Nothing cascaded.

   This is a data-retention defect, not a tidiness one: the user asked for the
   record to be gone and ARIA kept processing the subject.

2. SUBJECT NAMES AT INFO. For a due-diligence product the list of entities under
   investigation IS confidential commercial information — it reveals who a client
   is looking at. log_redaction (R-F2851) covers credentials only: query params,
   bearer tokens, headers. Names flow straight through to the fly log stream.

3. A LOG STORM THAT SAYS NOTHING. While the breaker is OPEN no work happens, yet
   every alias of every watched entity emits its own line, every cycle. That is
   ~30 lines per pass, every 5 minutes, carrying no information a single summary
   would not carry better — and it buries real signals.
"""
from __future__ import annotations

import logging

import pytest

from aria_service.intel import sanctions, dd_orchestrator

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


class TestBreakerSkipDoesNotNameTheSubject:

    def test_skip_note_omits_the_entity_name(self, caplog):
        """The subject list is confidential; it must not reach the log stream."""
        sanctions._reset_breaker_skip_notes()
        with caplog.at_level(logging.DEBUG, logger="aria.sanctions"):
            for nm in ("Silverbrook Capital Management", "ROSSI FACILITY SERVICES",
                       "Chemring Group PLC", "Roketsan A.S."):
                sanctions._note_breaker_skip(nm)
            sanctions._flush_breaker_skip_notes(force=True)
        blob = " ".join(r.getMessage() for r in caplog.records)
        for nm in ("Silverbrook", "ROSSI", "Chemring", "Roketsan"):
            assert nm not in blob, f"subject name {nm!r} leaked into the logs: {blob}"

    def test_the_fact_and_the_volume_are_still_reported(self, caplog):
        """Suppressing names must not suppress the SIGNAL — silence would be its
        own dishonesty (§21a). The count still has to be visible."""
        sanctions._reset_breaker_skip_notes()
        with caplog.at_level(logging.INFO, logger="aria.sanctions"):
            for i in range(7):
                sanctions._note_breaker_skip(f"Entity {i}")
            sanctions._flush_breaker_skip_notes(force=True)
        blob = " ".join(r.getMessage() for r in caplog.records)
        assert "7" in blob, f"the number of skipped screenings was not reported: {blob}"
        assert "breaker" in blob.lower()

    def test_one_summary_not_one_line_per_alias(self, caplog):
        sanctions._reset_breaker_skip_notes()
        with caplog.at_level(logging.INFO, logger="aria.sanctions"):
            for i in range(30):
                sanctions._note_breaker_skip(f"Entity {i}")
            sanctions._flush_breaker_skip_notes(force=True)
        lines = [r for r in caplog.records if "breaker" in r.getMessage().lower()]
        assert len(lines) <= 2, (
            f"{len(lines)} log lines for 30 skipped calls — the storm is still there"
        )


class TestDeletingADdStopsTheMonitoring:

    @pytest.mark.asyncio
    async def test_delete_report_removes_the_watchlist_entry(self, monkeypatch):
        """The load-bearing property: deletion must actually stop processing."""
        removed: list[dict] = []

        async def _spy_remove(name, user_id="", user_email_domain=""):
            removed.append({"name": name, "user_id": user_id})
            return {"ok": True, "removed": 1}

        monkeypatch.setattr(dd_orchestrator, "remove_from_watchlist", _spy_remove)
        await dd_orchestrator._unwatch_deleted_subject(
            {"subject": "Silverbrook Capital Management", "user_id": "u1"})

        assert removed, "deleting a DD left its subject on the watchlist"
        assert removed[0]["name"] == "Silverbrook Capital Management"
        assert removed[0]["user_id"] == "u1", (
            "the cascade must stay OWNER-SCOPED — an unscoped delete would let one "
            "tenant's deletion remove another tenant's watchlist entry (R-F2401)"
        )

    @pytest.mark.asyncio
    async def test_cascade_is_reached_from_delete_report(self):
        """Guard the WIRING, not just the helper."""
        import ast, inspect, textwrap
        src = textwrap.dedent(function_source(dd_orchestrator, "delete_report"))
        tree = ast.parse(src)
        called = {getattr(c.func, "id", "") or getattr(c.func, "attr", "")
                  for c in ast.walk(tree) if isinstance(c, ast.Call)}
        assert "_unwatch_deleted_subject" in called, (
            "delete_report does not stop monitoring the deleted subject"
        )

    @pytest.mark.asyncio
    async def test_a_missing_subject_is_a_no_op_not_a_crash(self, monkeypatch):
        """Deletion must never fail because the blob lacked a subject."""
        called = []
        monkeypatch.setattr(dd_orchestrator, "remove_from_watchlist",
                            _record(called))
        await dd_orchestrator._unwatch_deleted_subject({})
        await dd_orchestrator._unwatch_deleted_subject(None)
        assert called == [], "attempted an unscoped watchlist delete with no subject"

    @pytest.mark.asyncio
    async def test_cascade_failure_never_breaks_the_delete(self, monkeypatch):
        """The user's delete must succeed even if the watchlist write fails —
        but the failure must not be silent."""
        async def _boom(*_a, **_kw):
            raise RuntimeError("store unavailable")

        monkeypatch.setattr(dd_orchestrator, "remove_from_watchlist", _boom)
        await dd_orchestrator._unwatch_deleted_subject(
            {"subject": "Acme", "user_id": "u1"})  # must not raise


def _record(sink):
    async def _f(name, user_id="", user_email_domain=""):
        sink.append(name)
        return {"ok": True}
    return _f

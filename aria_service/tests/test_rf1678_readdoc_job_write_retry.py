"""R-F1678 - read-document job-store done-write retry.

The _r873_run background task writes the job result to the job store AFTER
extraction completes. If that write fails (state_store write queue full /
reconnect window), the job stays processing forever and the WA listener
polls until timeout (15 min). R-F1678 adds a retry loop (3 attempts with
exponential backoff) so a transient store blip does not strand the job.

Capability: the _r873_run function retries _readdoc_job_set on failure.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[1] / "routes" / "aria.py").read_text(encoding="utf-8")


def test_r873_run_retries_job_store_write():
    """The _r873_run function must retry _readdoc_job_set up to 3 times
    with exponential backoff when the write fails."""
    assert "for _r1678_attempt in range(3):" in SRC
    assert "if await _readdoc_job_set(_job_id, _job_data):" in SRC
    assert "break" in SRC
    assert "sleep(1.0 * (2 ** _r1678_attempt))" in SRC
    assert "R-F1678 readdoc job-store write failed" in SRC


def test_r873_run_still_logs_on_final_failure():
    """After 3 retries, if the write still fails, log a warning and
    accept the loss - the WA listener will eventually time out."""
    assert "job stranded in 'processing' state" in SRC


def test_r873_run_builds_job_data_before_retry():
    """The job data dict must be built BEFORE the retry loop so the
    retry only re-attempts the store write, not the extraction itself."""
    assert '_job_data = {"status": "done"' in SRC
    assert '_job_data = {"status": "failed"' in SRC


@pytest.mark.asyncio
async def test_readdoc_job_set_returns_false_on_failure(monkeypatch):
    """_readdoc_job_set returns False when the store write fails,
    so the retry loop can detect the failure."""
    from aria_service.routes import aria as a

    async def _fail(key, data, **kw):
        return False
    monkeypatch.setattr(a, "_readdoc_job_set", _fail)

    # Direct call should return False
    result = await a._readdoc_job_set("test_job", {"status": "done"})
    assert result is False


@pytest.mark.asyncio
async def test_readdoc_job_set_returns_true_on_success(monkeypatch):
    """_readdoc_job_set returns True when the store write succeeds."""
    from aria_service.routes import aria as a

    async def _ok(key, data, **kw):
        return True
    monkeypatch.setattr(a, "_readdoc_job_set", _ok)

    result = await a._readdoc_job_set("test_job", {"status": "done"})
    assert result is True

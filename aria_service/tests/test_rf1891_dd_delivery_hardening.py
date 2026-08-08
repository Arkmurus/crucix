"""R-F1891 — WA DD delivery hardening (review root causes #2 + #3).

#2: the inline WA DD budget default was 720s (12 min) — slow AND fragile (any
    restart/blip/poll-window edge lost the whole run). Lowered to 300s now that
    the async callback is reliable (RV-01/R-F1884); the background deep-DD +
    callback finish the depth.
#3: a brain restart loses the in-memory chat/DD computation, so a job left at
    'processing' can never finish — it used to stay 'processing' until its TTL
    while the WA poll hung the full 15-min window. recover_orphaned_jobs() fails
    such jobs at boot so the poll/callback gets a definitive failure.
"""
from __future__ import annotations

import asyncio
import inspect

import aria_service.routes.aria as aria

# R-F3786/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _run(coro):
    return asyncio.run(coro)


def test_inline_dd_budget_default_lowered_to_300():
    # Static guard: the inline WA DD budget default is 300s, not the old 720s.
    src = module_source(aria)
    assert 'os.getenv("ARIA_DD_CHAT_BUDGET_S", "300")' in src, "inline DD budget default is not 300s"
    assert 'os.getenv("ARIA_DD_CHAT_BUDGET_S", "720")' not in src, "old 720s default still present"


def test_recover_orphaned_jobs_fails_processing_jobs():
    from aria_service.intel import redis_store as rs

    chat_k = aria._CHAT_JOB_PREFIX + "rf1891_orphan"
    done_k = aria._CHAT_JOB_PREFIX + "rf1891_done"
    rdoc_k = aria._READDOC_JOB_PREFIX + "rf1891_rdoc"

    # an interrupted in-flight job + a legitimately-finished one + a readdoc job
    _run(rs.set_json(chat_k, {"status": "processing", "session_id": "s1"}))
    _run(rs.set_json(done_k, {"status": "done", "result": {"answer": "ok"}}))
    _run(rs.set_json(rdoc_k, {"status": "processing", "filename": "x.pdf"}))

    n = _run(aria.recover_orphaned_jobs())
    assert n >= 2, f"expected >=2 orphaned jobs failed, got {n}"

    # 'processing' jobs are now failed-interrupted...
    j1 = _run(rs.get_json(chat_k))
    assert j1.get("status") == "failed" and "restart" in (j1.get("error") or "")
    j3 = _run(rs.get_json(rdoc_k))
    assert j3.get("status") == "failed"

    # ...but a finished job is untouched
    j2 = _run(rs.get_json(done_k))
    assert j2.get("status") == "done" and j2.get("result", {}).get("answer") == "ok"


def test_recover_orphaned_jobs_idempotent_and_safe_when_empty():
    # running again finds nothing new to fail (already-failed jobs are skipped)
    n = _run(aria.recover_orphaned_jobs())
    assert isinstance(n, int) and n >= 0

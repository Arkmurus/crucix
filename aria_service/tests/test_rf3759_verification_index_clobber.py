"""R-F3759 — CAPABILITY: a blind read must not wipe the signal that degrades the platform.

Found by tracing a LIVE symptom, not by a test. aria-intel sat in DEGRADED from
2026-08-05T18:00:52Z on "grounded rate 0% < 30%", and the store reported total=0 with
lifetime_sample_size=0 — the verification index was empty LIFETIME.

THE DEFECT, identical to R-F3717 in honesty_judge: `get_json(...) or []` treats a store
FAILURE exactly like an empty index (get_json returns None on error). One entry is
inserted and written back, replacing up to 500 verifications with a list of ONE.

WHY THIS ONE IS WORSE THAN R-F3717, and it is the whole reason it rates a P-level fix:
  * operating_modes.evaluate_auto_transition reads avg_grounded_rate and DEGRADES the
    platform below 30%. DEGRADED SUPPRESSES EXTERNAL DELIVERY (operating_modes.py:189)
    and makes the autonomous engine SKIP TASKS (autonomous/engine.py:670).
  * it is the verification axis of the Phase A gate-#1 composite.

So a store blip did not merely lose data — it took ARIA's delivery offline and stopped
scheduled work, and the reason logged was "grounded rate 0%", which reads as a quality
collapse rather than data loss. The measurement layer is blameless:
get_verification_stats returns None when there are no samples and NEVER fabricates a
zero, and operating_modes deliberately treats None as healthy. The 0% was computed from
a real but CLOBBERED index.

Run: python -m pytest aria_service/tests/test_rf3759_verification_index_clobber.py -v
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import source_verifier as sv


def _args():
    """record_verification(verification, *, question_preview=..., ...) — the
    verification dict is POSITIONAL and the previews are keyword-only. A first
    version of this fixture passed question=/response= and got a TypeError, which
    failed the tests for the wrong reason."""
    return dict(
        verification={"verdict": "grounded", "grounded_rate": 0.9,
                      "cited_urls": ["u"], "unverified": []},
        question_preview="q", response_preview="r")


def test_a_failed_index_read_does_not_overwrite_it(monkeypatch):
    """THE HEADLINE: one transient read must not destroy 500 verifications."""
    from aria_service.intel import redis_store
    writes: list = []

    async def _set(key, val, **kw):
        writes.append((key, val))

    async def _boom(key):
        raise redis_store.StoreReadError("no read connection (reconnect in progress)")

    monkeypatch.setattr(sv.rs, "set_json", _set)
    monkeypatch.setattr(sv.rs, "get_json_strict", _boom)

    asyncio.run(sv.record_verification(**_args()))

    idx = [v for k, v in writes if k == sv.VERIFICATIONS_KEY]
    assert idx == [], (
        "the verification index was REWRITTEN after a failed read. That replaces up "
        "to 500 verifications with one, and the resulting grounded rate DEGRADES the "
        "platform — suppressing delivery and skipping tasks on data loss."
    )


def test_the_individual_verification_is_still_persisted(monkeypatch):
    """Skipping the index must not lose the verification itself."""
    from aria_service.intel import redis_store
    writes: list = []

    async def _set(key, val, **kw):
        writes.append(key)

    async def _boom(key):
        raise redis_store.StoreReadError("WAL recovery")

    monkeypatch.setattr(sv.rs, "set_json", _set)
    monkeypatch.setattr(sv.rs, "get_json_strict", _boom)

    asyncio.run(sv.record_verification(**_args()))
    assert any(k.startswith(sv.VERIFICATION_KEY_PREFIX) for k in writes), (
        "the verification record is stored under its own key and must survive an "
        "index failure — only the index entry is skipped"
    )


def test_a_healthy_index_is_still_appended(monkeypatch):
    """The guard must not stop normal recording."""
    writes: dict = {}

    async def _set(key, val, **kw):
        writes[key] = val

    async def _get_ok(key):
        return [{"id": "old"}]

    monkeypatch.setattr(sv.rs, "set_json", _set)
    monkeypatch.setattr(sv.rs, "get_json_strict", _get_ok)

    asyncio.run(sv.record_verification(**_args()))
    idx = writes.get(sv.VERIFICATIONS_KEY)
    assert idx is not None and len(idx) == 2, f"healthy index must gain and KEEP: {idx}"
    assert any(e.get("id") == "old" for e in idx)


def test_the_clobber_is_wired_to_the_brain():
    """§21a — losing the signal that gates delivery must not be silent."""
    from ._source_probe import function_source
    src = function_source(sv, "record_verification")
    assert "wire_failure" in src and "get_json_strict" in src


def test_stats_never_fabricate_a_zero_grounded_rate(monkeypatch):
    """The measurement layer is blameless and must STAY that way.

    operating_modes treats None as healthy and 0.0 as a real collapse, so a stats
    function that returned 0.0 for "no samples" would degrade the platform on
    absence. It correctly returns None — pin that.
    """
    async def _empty(key):
        return []

    monkeypatch.setattr(sv.rs, "get_json", _empty)
    monkeypatch.setattr(sv.rs, "get_json_strict", _empty)
    s = asyncio.run(sv.get_verification_stats())
    assert s.get("avg_grounded_rate") is None, (
        f"with zero samples avg_grounded_rate is {s.get('avg_grounded_rate')!r}; "
        f"anything but None degrades the platform on ABSENCE of evidence"
    )
    assert s.get("rate_sample_size") == 0

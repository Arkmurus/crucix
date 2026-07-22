"""R-F2878 — wiring_monitor must count ACTIONABLE staged items, not the raw history.

LIVE (10-cycle monitor 2026-07-22): wiring_monitor recorded a capability gap
"Staged queue has 90 items — not draining fast enough. Oldest is Nonemin old."
But `/api/aria/self/staged` showed only 3 actionable items.

Root cause: `crucix:aria:staged_improvements` is an append-only list that RETAINS
deployed/rejected entries. wiring_monitor counted its raw length (90), while
`self_improve.get_staged()` filters to `status == "staged"` (3). So the queue WAS
draining (3 actionable, 87 already processed) and the alarm was a miscount — the same
wrong-count / cry-wolf class. Secondary bug: `staged_oldest_age_minutes` was None
(old entries lack timestamps) and got rendered into the string as literal "Nonemin".

Fix: count only `status == "staged"` (align with get_staged) and handle a None age.
"""
from aria_service.intel.wiring_monitor import _actionable_staged


def _mk(n, status, ts=None):
    return [{"status": status, "id": f"{status}{i}", **({"timestamp": ts} if ts else {})}
            for i in range(n)]


def test_rf2878_counts_only_actionable_not_raw_history():
    """The live case: 3 staged + 87 processed -> count 3, NOT 90."""
    raw = _mk(3, "staged") + _mk(50, "deployed") + _mk(37, "rejected")
    count, _ = _actionable_staged(raw)
    assert count == 3, "must count status=='staged' only, not the append-only history"


def test_rf2878_all_processed_reads_zero_actionable():
    raw = _mk(90, "deployed")
    count, oldest = _actionable_staged(raw)
    assert count == 0
    assert oldest is None


def test_rf2878_oldest_age_none_when_no_timestamps():
    """The 'Nonemin' bug: actionable items without timestamps -> None, handled cleanly."""
    count, oldest = _actionable_staged(_mk(3, "staged"))
    assert count == 3
    assert oldest is None, "no timestamps -> None age (not a crash, not literal 'None')"


def test_rf2878_oldest_age_computed_over_actionable_only():
    import time
    raw = (_mk(1, "staged", ts=time.time() - 600)      # 10min-old ACTIONABLE
           + _mk(1, "deployed", ts=time.time() - 99999))  # ancient but PROCESSED — must be ignored
    count, oldest = _actionable_staged(raw)
    assert count == 1
    assert oldest is not None and 9.0 <= oldest <= 11.0, \
        "oldest age must be computed over ACTIONABLE items only (ignore processed)"

"""R-F1670 — audit_log must use the lightweight signal path, not heavy absorb.

audit_log.record() fires per AUDIT ACTION (high-frequency). It was calling the
heavy brain_hook.absorb (mastery+knowledge+neural per action) → absorb(audit_log)
p95 52s → event-loop freeze (2026-06-18 WA contract-review outage). It must use
brain_hook.record_signal (the §21a metric). Audit content stays durable in the
audit log; only the redundant per-action neural encode is dropped.
"""
import inspect

from aria_service.intel import audit_log

# R-F3781/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_audit_feed_brain_uses_record_signal_not_heavy_absorb():
    src = module_source(audit_log)
    # Find the feed_brain block.
    assert "feed_brain" in src
    body = src.split("if feed_brain:", 1)[1]
    # The brain-feed block must call the lightweight record_signal...
    assert "record_signal(" in body, (
        "R-F1670: audit_log feed_brain must call brain_hook.record_signal (lightweight)."
    )
    # ...and must NOT call the heavy absorb.
    assert "brain_hook.absorb(" not in body and ".absorb(" not in body, (
        "R-F1670: audit_log feed_brain must NOT call the heavy absorb — it is "
        "per-action telemetry that flooded the absorb pipeline (p95 52s wedge)."
    )

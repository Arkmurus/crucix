"""R-F2727 — window-aware verdicts for the ecosystem diagnostic probes.

Prospector 2026-07-18 finding #4: the probes (adversarial_agent_audit / every_agent_probe)
asserted fixed LIFETIME-magnitude thresholds against WINDOWED brain stats, and treated
"not signalled in the last N hours" as a hard FAIL. That confuses "not observed recently"
with "missing/broken" — an idle, event-driven, or quiet-window subsystem was branded a
failure. The result was false failures + summaries that contradicted the wiring view
("62/78 fail" alongside "0 dark").

These pure helpers encode the honest contract, matching `check()`'s tri-state
(True=PASS, None=WARN, False=FAIL):
  - a WINDOWED metric below a soft floor is a WARN, never a FAIL (a quiet window is not a
    broken system); a genuinely-broken stats endpoint is caught by the separate
    "stats accessible" check, so magnitude never needs to hard-fail.
  - RECENCY is never a hard FAIL: "not observed recently" is indeterminate (idle event-driven
    vs dead can't be told apart from recency alone), so it is PASS (recent) or WARN (stale).
"""


def windowed_ok(value, floor):
    """PASS if a windowed metric is above the soft floor; else WARN. None/absent → WARN
    (indeterminate). NEVER returns False — a low/quiet window is not a hard failure."""
    if value is None:
        return None
    try:
        return True if value > floor else None
    except TypeError:
        return None


def recency_ok(ago_h, threshold_h):
    """PASS if a subsystem was observed within threshold_h; else WARN. Missing reading → WARN.
    NEVER returns False — 'not observed recently' is not evidence of 'broken'."""
    if ago_h is None:
        return None
    try:
        return True if ago_h < threshold_h else None
    except TypeError:
        return None

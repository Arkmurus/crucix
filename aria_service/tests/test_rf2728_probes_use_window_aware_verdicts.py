"""R-F2728 — completes Batch 3 (Prospector #4): the sibling probe every_agent_probe.py had the
same residual false-failures R-F2548 missed (total_signals>50k as a hard FAIL; two "Active NOW"
recency hard-fails). Both ecosystem probes now route windowed-magnitude + recency through the
shared window-aware verdicts, so 'not observed recently' is never a hard FAIL.
"""
import os

_SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")


def _read(name):
    with open(os.path.join(_SCRIPTS, name), encoding="utf-8") as f:
        return f.read()


def test_both_probes_use_the_shared_window_aware_verdicts():
    for probe in ("adversarial_agent_audit.py", "every_agent_probe.py"):
        src = _read(probe)
        assert "windowed_ok" in src and "recency_ok" in src, f"{probe} must use the honest verdicts"


def test_no_residual_hard_fail_recency_or_windowed_thresholds():
    for probe in ("adversarial_agent_audit.py", "every_agent_probe.py"):
        src = _read(probe)
        # the old false-failure shapes: a bare boolean recency/magnitude passed as check()'s `ok`
        assert "last_signal_ago_h', 99) < " not in src, f"{probe} still hard-FAILs on recency"
        assert "total_signals', 0) > 50000" not in src, f"{probe} still hard-FAILs on a windowed magnitude"

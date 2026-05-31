"""R-F1226: Capability test — System Health RED fixes.

Verifies that all 15 previously-failing modules now pass their entry
function check, and that all warning modules are registered in
brain_hook._MODULE_TOPICS.
"""
import pytest
from aria_service.intel.self_diagnostic import _check_entry, _check_brain_registered
from aria_service.intel.brain_hook import _MODULE_TOPICS

# The 15 modules that were failing with "entry function not found"
FIXED_MODULES = [
    ("stream_honesty", "aria_service.intel.stream_honesty", "apply_stream_honesty"),
    ("tool_claim_guard", "aria_service.intel.tool_claim_guard", "guard"),
    ("sanctions_claim_guard", "aria_service.intel.sanctions_claim_guard", "guard_context_block"),
    ("ground_truth_guard", "aria_service.intel.ground_truth_guard", "verify"),
    ("reasoning_library", "aria_service.intel.reasoning_library", "record_response"),
    ("regional_navigation", "aria_service.intel.regional_navigation", "get_regional_context"),
    ("sanctions_divergence", "aria_service.intel.sanctions_divergence", "analyze_divergence"),
    ("autonomous_scheduler", "aria_service.intel.autonomous_scheduler", "AutonomousScheduler"),
    ("autonomy_scorer", "aria_service.intel.autonomy_scorer", "compute_composite"),
    ("operating_modes", "aria_service.intel.operating_modes", "get_mode"),
    ("calibration_review", "aria_service.intel.calibration_review", "run_calibration_review"),
    ("self_healing", "aria_service.intel.self_healing", "start_self_healing"),
    ("self_restart", "aria_service.intel.self_restart", "start_blackout_detector"),
    ("dead_letter_queue", "aria_service.intel.dead_letter_queue", "enqueue"),
    ("circuit_breaker", "aria_service.intel.circuit_breaker", "get_breaker"),
]


def test_all_entry_functions_exist():
    """Every module's claimed entry function must exist on the module."""
    import importlib
    for name, mod_path, entry in FIXED_MODULES:
        mod = importlib.import_module(mod_path)
        status, note = _check_entry(mod, entry)
        assert status == "PASS", (
            f"{name}: entry `{entry}` not found on {mod_path}: {note}"
        )


def test_all_modules_registered_in_brain_topics():
    """Every module must be registered in brain_hook._MODULE_TOPICS."""
    for name, mod_path, entry in FIXED_MODULES:
        assert name in _MODULE_TOPICS, (
            f"{name} not found in brain_hook._MODULE_TOPICS"
        )
        assert _MODULE_TOPICS[name], (
            f"{name} has empty topic list in _MODULE_TOPICS"
        )


def test_newly_registered_modules_in_brain_topics():
    """Newly added modules must be in _MODULE_TOPICS."""
    new_modules = [
        "crawl_enhancements",
        "compliance_watch",
        "self_claim_guard",
        "capability_gaps",
        "error_log_handler",
        "engine_wiring",
        "wiring_monitor",
        "stream_honesty",
        "reasoning_library",
        "regional_navigation",
        "sanctions_divergence",
        "autonomous_scheduler",
        "autonomy_scorer",
        "operating_modes",
        "calibration_review",
        "self_restart",
        "dead_letter_queue",
        "circuit_breaker",
    ]
    for name in new_modules:
        assert name in _MODULE_TOPICS, (
            f"{name} missing from brain_hook._MODULE_TOPICS"
        )
        assert _MODULE_TOPICS[name], (
            f"{name} has empty topic list"
        )


def test_self_diagnostic_run_returns_no_failures():
    """self_diagnostic.run_diagnostic should return no FAIL entries
    for entry function checks."""
    from aria_service.intel.self_diagnostic import _MODULES
    import importlib
    
    failures = []
    for entry in _MODULES:
        mod_path = entry["module"]
        entry_name = entry["entry"]
        try:
            mod = importlib.import_module(mod_path)
            status, note = _check_entry(mod, entry_name)
            if status == "FAIL":
                failures.append(f"{entry['name']}: {note}")
        except Exception as e:
            failures.append(f"{entry['name']}: ImportError: {e}")
    
    assert not failures, (
        f"System Health entry function failures:\n" + "\n".join(failures)
    )

"""R-F2415: web_integrity_agent + self_healing called record_gap(module=, description=)
which are not params -> TypeError, gap never recorded. Guard the kwarg contract."""
import inspect
import pytest
from aria_service.intel import capability_gaps


def test_rf2415_fixed_caller_kwargs_bind():
    sig = inspect.signature(capability_gaps.record_gap)
    # exact shape the fixed callers now use — must bind cleanly
    sig.bind(gap_type="web_integrity_failure", detail="x",
             title="web_integrity:/api/x", severity="HIGH", source="web_integrity_agent")


def test_rf2415_old_broken_kwargs_rejected():
    sig = inspect.signature(capability_gaps.record_gap)
    with pytest.raises(TypeError):
        sig.bind(gap_type="x", module="m", description="d")

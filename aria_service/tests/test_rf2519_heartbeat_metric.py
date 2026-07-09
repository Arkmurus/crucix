"""R-F2519 (log-review F1) — heartbeat-stall metric getter for /health/perf."""
from aria_service.intel import continuous_profiler as cp


def test_get_stall_stats_shape_and_values():
    cp._state["stall_count"] = 4
    cp._state["max_stall_s"] = 2.63
    cp._state["last_stall_at"] = 0.0  # never stalled since boot
    s = cp.get_stall_stats()
    assert s["stall_count"] == 4
    assert s["max_stall_s"] == 2.63
    assert s["last_stall_age_s"] is None  # 0.0 -> None (never)
    assert "threshold_s" in s and s["threshold_s"] > 0


def test_get_stall_stats_defaults_safe():
    for k in ("stall_count", "max_stall_s", "last_stall_at"):
        cp._state.pop(k, None)
    s = cp.get_stall_stats()
    assert s["stall_count"] == 0
    assert s["max_stall_s"] == 0.0
    assert s["last_stall_age_s"] is None


if __name__ == "__main__":
    test_get_stall_stats_shape_and_values(); print("PASS shape")
    test_get_stall_stats_defaults_safe(); print("PASS defaults")
    print("ALL PASS")

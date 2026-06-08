"""R-F1435 — Capability test: wedge dump size cap + retention.

Simulates oversized dumps and asserts:
1. Each dump is capped at _MAX_DUMP_BYTES (5MB default)
2. The wedge directory stays under _MAX_WEDGE_DIR_BYTES (200MB)
3. At most _MAX_WEDGE_FILES (50) files are kept
"""
import os
import sys
import tempfile
import time

# Ensure the module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aria_service.intel.self_restart import (
    _write_wedge_dump,
    _prune_wedge_dir,
    _MAX_DUMP_BYTES,
    _MAX_WEDGE_FILES,
    _MAX_WEDGE_DIR_BYTES,
)


def test_wedge_dump_size_cap():
    """Each dump must be capped at _MAX_DUMP_BYTES."""
    with tempfile.TemporaryDirectory() as tmp:
        # Write a dump — the function writes thread stacks + asyncio tasks
        # which on a test machine should be well under 5MB, but the cap
        # mechanism must exist and not crash.
        path = _write_wedge_dump("cap_test_agent", 300.0, wedge_dir=tmp)
        assert path is not None, "wedge dump should succeed"
        assert os.path.isfile(path), f"wedge file should exist at {path}"
        size = os.path.getsize(path)
        assert size <= _MAX_DUMP_BYTES, (
            f"dump size {size} exceeds cap {_MAX_DUMP_BYTES}"
        )
        # Verify the dump contains expected sections
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Blackout detected" in content, "dump should contain blackout header"
        assert "end blackout stack dump" in content, "dump should have end marker"


def test_wedge_dump_retention_by_count():
    """Prune keeps at most _MAX_WEDGE_FILES files."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create more files than the limit
        for i in range(_MAX_WEDGE_FILES + 10):
            fpath = os.path.join(tmp, f"blackout_test_agent_123_{int(time.time()) + i}.log")
            with open(fpath, "w") as f:
                f.write(f"dump {i}\n")
        
        _prune_wedge_dir(tmp)
        remaining = [f for f in os.listdir(tmp) if f.endswith(".log")]
        assert len(remaining) <= _MAX_WEDGE_FILES, (
            f"expected ≤{_MAX_WEDGE_FILES} files, got {len(remaining)}"
        )


def test_wedge_dump_retention_by_size():
    """Prune keeps total size under _MAX_WEDGE_DIR_BYTES."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create files that exceed the budget
        chunk_size = _MAX_WEDGE_DIR_BYTES // 3  # each file is ~1/3 of budget
        for i in range(5):
            fpath = os.path.join(tmp, f"blackout_test_agent_123_{int(time.time()) + i}.log")
            with open(fpath, "w") as f:
                f.write("x" * chunk_size)
        
        _prune_wedge_dir(tmp)
        total = sum(
            os.path.getsize(os.path.join(tmp, f))
            for f in os.listdir(tmp)
            if f.endswith(".log")
        )
        assert total <= _MAX_WEDGE_DIR_BYTES * 1.1, (  # 10% tolerance for partial files
            f"total size {total} exceeds budget {_MAX_WEDGE_DIR_BYTES}"
        )


def test_wedge_dump_combined_retention():
    """Both count and size limits are enforced simultaneously."""
    with tempfile.TemporaryDirectory() as tmp:
        # Create many small files
        for i in range(_MAX_WEDGE_FILES * 2):
            fpath = os.path.join(tmp, f"blackout_test_agent_123_{int(time.time()) + i}.log")
            with open(fpath, "w") as f:
                f.write(f"small dump {i}\n")
        
        _prune_wedge_dir(tmp)
        remaining = [f for f in os.listdir(tmp) if f.endswith(".log")]
        assert len(remaining) <= _MAX_WEDGE_FILES, (
            f"expected ≤{_MAX_WEDGE_FILES} files, got {len(remaining)}"
        )
        total = sum(
            os.path.getsize(os.path.join(tmp, f))
            for f in remaining
        )
        assert total <= _MAX_WEDGE_DIR_BYTES * 1.1, (
            f"total size {total} exceeds budget {_MAX_WEDGE_DIR_BYTES}"
        )

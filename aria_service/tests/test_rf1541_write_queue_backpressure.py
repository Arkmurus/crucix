"""
R-F1541 capability test: prove the bounded write queue eliminates the
timeout-and-drop failure class.

The old model: _upsert called _conn.execute() with a 30s timeout. When the
worker thread was saturated, writes were silently dropped after the timeout.
This caused a cascade: timeout WARNING → error_log_handler → record_error →
more timeouts → brain_hook circuit trip → all learning stops.

The new model: _upsert enqueues writes to a bounded async queue. If the queue
is full, StateWriteError is raised immediately (backpressure). No timeout,
no WARNING, no feedback loop.

This test proves:
1. Normal writes succeed (enqueue + flush-on-read)
2. When the queue is full, StateWriteError is raised immediately (no timeout)
3. The error_log_handler does NOT forward "write queue full" messages to
   record_error (structural feedback loop break)
"""
import asyncio
import logging
import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_write_queue_normal_operation():
    """Normal writes enqueue and are visible on the next read."""
    from aria_service.intel import state_store as _ss
    
    # Ensure connected
    if _ss._conn is None:
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        db_path = tmp.name
        tmp.close()
        os.environ["ARIA_STATE_DB_PATH"] = db_path
        await _ss.connect()
    else:
        db_path = None
    
    try:
        # Write a value
        await _ss.set_key("_test_rf1541_normal", "hello_world")
        
        # Read it back — should see the value (flush-on-read)
        val = await _ss.get("_test_rf1541_normal")
        assert val == "hello_world", f"Expected 'hello_world', got {val!r}"
        
        # Write JSON
        await _ss.set_json("_test_rf1541_json", {"key": "value", "num": 42})
        obj = await _ss.get_json("_test_rf1541_json")
        assert obj == {"key": "value", "num": 42}, f"Expected dict, got {obj!r}"
        
        # Increment
        await _ss.incr("_test_rf1541_counter")
        await _ss.incr("_test_rf1541_counter", 5)
        val = await _ss.get("_test_rf1541_counter")
        assert val == "6", f"Expected '6', got {val!r}"
        
    finally:
        await _ss.delete("_test_rf1541_normal")
        await _ss.delete("_test_rf1541_json")
        await _ss.delete("_test_rf1541_counter")
        if db_path:
            try:
                os.unlink(db_path)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_write_queue_backpressure():
    """When the queue is full, StateWriteError is raised immediately.
    
    This proves the old timeout-and-drop model is replaced by immediate
    backpressure. No 30s timeout, no silent data loss.
    """
    from aria_service.intel import state_store as _ss
    
    # Ensure connected
    if _ss._conn is None:
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        db_path = tmp.name
        tmp.close()
        os.environ["ARIA_STATE_DB_PATH"] = db_path
        await _ss.connect()
    else:
        db_path = None
    
    try:
        # Save the original queue max
        original_max = _ss._WRITE_QUEUE_MAX
        
        # Create a tiny queue (max 1 item)
        _ss._QUEUED_WRITES = asyncio.Queue(maxsize=1)
        
        # Fill the queue
        await _ss._enqueue_write(
            "INSERT INTO state(key, value, kind) VALUES(?, ?, ?)",
            ("_test_fill", "fill", "string"),
        )
        
        # The queue is now full — next write should raise StateWriteError immediately
        with pytest.raises(_ss.StateWriteError) as exc_info:
            await _ss._enqueue_write(
                "INSERT INTO state(key, value, kind) VALUES(?, ?, ?)",
                ("_test_overflow", "overflow", "string"),
            )
        
        error_msg = str(exc_info.value)
        assert "write queue full" in error_msg, (
            f"Expected 'write queue full' in error, got: {error_msg}"
        )
        
        # Verify the write was NOT silently dropped — the caller got an error
        # This is the key behavioural change from the old model
        
        # Restore the queue
        _ss._QUEUED_WRITES = asyncio.Queue(maxsize=original_max)
        
    finally:
        _ss._QUEUED_WRITES = asyncio.Queue(maxsize=_ss._WRITE_QUEUE_MAX)
        if db_path:
            try:
                os.unlink(db_path)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_write_queue_full_message_filtered_from_error_log():
    """The error_log_handler does NOT forward 'write queue full' messages.
    
    This is the structural fix for the feedback loop. In the old model,
    a timeout WARNING was caught by error_log_handler → record_error →
    state_store write → timeout WARNING → loop. Now, 'write queue full'
    is in the _SKIP_SUBSTRINGS list so it never reaches record_error.
    """
    from aria_service.intel import error_log_handler as _elh
    
    # Verify the skip string exists
    assert any("write queue full" in s for s in _elh._SKIP_SUBSTRINGS), (
        "'write queue full' must be in _SKIP_SUBSTRINGS to break the feedback loop"
    )


@pytest.mark.asyncio
async def test_write_queue_flush_on_read():
    """Flush-on-read ensures set() → get() consistency.
    
    This proves that the write queue does not break the synchronous
    semantics that callers depend on.
    """
    from aria_service.intel import state_store as _ss
    
    # Ensure connected
    if _ss._conn is None:
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        db_path = tmp.name
        tmp.close()
        os.environ["ARIA_STATE_DB_PATH"] = db_path
        await _ss.connect()
    else:
        db_path = None
    
    try:
        # Write multiple values in quick succession
        for i in range(10):
            await _ss.set_key(f"_test_rf1541_batch_{i}", f"value_{i}")
        
        # Read them all back — flush-on-read ensures they're all visible
        for i in range(10):
            val = await _ss.get(f"_test_rf1541_batch_{i}")
            assert val == f"value_{i}", (
                f"Batch item {i}: expected 'value_{i}', got {val!r}"
            )
        
        # Cleanup
        for i in range(10):
            await _ss.delete(f"_test_rf1541_batch_{i}")
        
    finally:
        if db_path:
            try:
                os.unlink(db_path)
            except Exception:
                pass

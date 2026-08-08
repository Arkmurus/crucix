"""R-F1395 — Kill switch (POST /api/aria/autonomous/pause) must halt ALL loops.

Live evidence 2026-06-07: pause_engine() sets a Redis flag checked only by
the autonomous engine's task scheduler (engine.py:276). Three independent
loops do NOT check the flag:
  1. self_coder.run_forever() in self_coder.py:234
  2. gap_detector.run_forever() in gap_detector.py:1568
  3. _self_improve_loop() in main.py:1180

Plus ~10 main.py background loops (research, quiz, reading, library,
proactive, weekly, watchlist, tender, memory_wal_drain).

This test proves the gap by inspecting each loop's source for a pause check.
After the fix, all loops MUST check is_engine_paused() before each cycle.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

# R-F3789/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


@pytest.mark.asyncio
async def test_self_coder_does_not_check_pause_flag():
    """self_coder.run_forever() must check is_engine_paused() before each cycle.

    Currently it does NOT — this test proves the gap by inspecting the loop body.
    """
    from aria_service.autonomous import self_coder as sc

    import inspect
    source = function_source(sc.ARIACoder, "run_forever")

    # The loop should check is_engine_paused or equivalent
    has_pause_check = (
        "is_engine_paused" in source
        or "engine_paused" in source
        or "_PAUSE_KEY" in source
        or "safety.is_engine_paused" in source
    )

    assert has_pause_check, (
        "FAIL: self_coder.run_forever() does NOT check the engine pause flag. "
        "POST /api/aria/autonomous/pause will NOT stop the coder loop. "
        "Fix: add `if await safety.is_engine_paused(): await asyncio.sleep(60); continue` "
        "at the top of the while True loop."
    )


@pytest.mark.asyncio
async def test_gap_detector_does_not_check_pause_flag():
    """gap_detector.run_forever() must check is_engine_paused() before each scan."""
    from aria_service.autonomous import gap_detector as gd

    import inspect
    source = function_source(gd.GapDetector, "run_forever")

    has_pause_check = (
        "is_engine_paused" in source
        or "engine_paused" in source
        or "_PAUSE_KEY" in source
        or "safety.is_engine_paused" in source
    )

    assert has_pause_check, (
        "FAIL: gap_detector.run_forever() does NOT check the engine pause flag. "
        "POST /api/aria/autonomous/pause will NOT stop the gap detector loop."
    )


@pytest.mark.asyncio
async def test_self_improve_loop_does_not_check_pause_flag():
    """_self_improve_loop() in main.py must check is_engine_paused() before each cycle."""
    # The loop is defined inline in main.py, so we check the source file
    import inspect
    from aria_service import main

    source = module_source(main)

    # Find the _self_improve_loop function
    import re
    match = re.search(r"async def _self_improve_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _self_improve_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _self_improve_loop() does NOT check the engine pause flag. "
        "POST /api/aria/autonomous/pause will NOT stop the self-improve loop."
    )


@pytest.mark.asyncio
async def test_engine_loop_does_check_pause_flag():
    """The autonomous engine loop MUST check the pause flag (positive control).

    engine.py has a module-level _engine_loop() function (not a class method).
    It checks safety.is_engine_paused() at line 276.
    """
    from aria_service.autonomous import engine as ae

    import inspect
    source = function_source(ae, "_engine_loop")

    has_pause_check = (
        "is_engine_paused" in source
        or "engine_paused" in source
        or "_PAUSE_KEY" in source
        or "safety.is_engine_paused" in source
    )

    assert has_pause_check, (
        "FAIL: The engine loop itself does not check the pause flag — "
        "the entire pause mechanism is broken."
    )


@pytest.mark.asyncio
async def test_pause_endpoint_wires_to_brain():
    """pause_engine must wire success to brain (positive control)."""
    from aria_service.autonomous import safety as sf

    import inspect
    source = function_source(sf, "pause_engine")

    assert "wire_success" in source, (
        "FAIL: pause_engine does not wire success to brain"
    )
    assert "wire_failure" in source, (
        "FAIL: pause_engine does not wire failure to brain"
    )


@pytest.mark.asyncio
async def test_research_loop_checks_pause():
    """_research_loop() in main.py must check is_engine_paused()."""
    import inspect
    from aria_service import main

    source = module_source(main)
    import re
    match = re.search(r"async def _research_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _research_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _research_loop() does NOT check the engine pause flag."
    )


@pytest.mark.asyncio
async def test_quiz_loop_checks_pause():
    """_quiz_loop() in main.py must check is_engine_paused()."""
    import inspect
    from aria_service import main

    source = module_source(main)
    import re
    match = re.search(r"async def _quiz_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _quiz_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _quiz_loop() does NOT check the engine pause flag."
    )


@pytest.mark.asyncio
async def test_reading_loop_checks_pause():
    """_reading_loop() in main.py must check is_engine_paused()."""
    import inspect
    from aria_service import main

    source = module_source(main)
    import re
    match = re.search(r"async def _reading_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _reading_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _reading_loop() does NOT check the engine pause flag."
    )


@pytest.mark.asyncio
async def test_library_consolidate_loop_checks_pause():
    """_library_consolidate_loop() in main.py must check is_engine_paused()."""
    import inspect
    from aria_service import main

    source = module_source(main)
    import re
    match = re.search(r"async def _library_consolidate_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _library_consolidate_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _library_consolidate_loop() does NOT check the engine pause flag."
    )


@pytest.mark.asyncio
async def test_memory_wal_drain_loop_checks_pause():
    """_memory_wal_drain_loop() in main.py must check is_engine_paused()."""
    import inspect
    from aria_service import main

    source = module_source(main)
    import re
    match = re.search(r"async def _memory_wal_drain_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _memory_wal_drain_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _memory_wal_drain_loop() does NOT check the engine pause flag."
    )


@pytest.mark.asyncio
async def test_proactive_loop_checks_pause():
    """_proactive_loop() in main.py must check is_engine_paused()."""
    import inspect
    from aria_service import main

    source = module_source(main)
    import re
    match = re.search(r"async def _proactive_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _proactive_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _proactive_loop() does NOT check the engine pause flag."
    )


@pytest.mark.asyncio
async def test_weekly_report_loop_checks_pause():
    """_weekly_report_loop() in main.py must check is_engine_paused()."""
    import inspect
    from aria_service import main

    source = module_source(main)
    import re
    match = re.search(r"async def _weekly_report_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _weekly_report_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _weekly_report_loop() does NOT check the engine pause flag."
    )


@pytest.mark.asyncio
async def test_watchlist_rescreen_loop_checks_pause():
    """_watchlist_rescreen_loop() in main.py must check is_engine_paused()."""
    import inspect
    from aria_service import main

    source = module_source(main)
    import re
    match = re.search(r"async def _watchlist_rescreen_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _watchlist_rescreen_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _watchlist_rescreen_loop() does NOT check the engine pause flag."
    )


@pytest.mark.asyncio
async def test_tender_monitor_loop_checks_pause():
    """_tender_monitor_loop() in main.py must check is_engine_paused()."""
    import inspect
    from aria_service import main

    source = module_source(main)
    import re
    match = re.search(r"async def _tender_monitor_loop.*?(?=\n\s{4}(?:async def|#))", source, re.DOTALL)
    assert match is not None, "Could not find _tender_monitor_loop in main.py"

    loop_source = match.group()
    has_pause_check = (
        "is_engine_paused" in loop_source
        or "engine_paused" in loop_source
        or "_PAUSE_KEY" in loop_source
        or "safety.is_engine_paused" in loop_source
    )

    assert has_pause_check, (
        "FAIL: _tender_monitor_loop() does NOT check the engine pause flag."
    )

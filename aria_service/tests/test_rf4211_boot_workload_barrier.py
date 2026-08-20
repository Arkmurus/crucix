"""R-F4211: heavy boot workloads must not race graph hydration and state reads."""

import asyncio
from types import SimpleNamespace

import pytest

from aria_service import main
from ._source_probe import function_source


@pytest.mark.asyncio
async def test_heavy_workload_barrier_blocks_until_graph_hydration_completes():
    """Drive the real readiness helper through blocked and released states."""
    ready = asyncio.Event()
    app = SimpleNamespace(state=SimpleNamespace(heavy_graph_ready=ready))

    waiter = asyncio.create_task(main._await_heavy_graph_ready(app))
    await asyncio.sleep(0)
    assert not waiter.done(), "heavy workload escaped before graph hydration"

    ready.set()
    await asyncio.wait_for(waiter, timeout=1.0)


def test_every_boot_stampede_source_waits_on_the_shared_barrier():
    """Pin the complete wiring so a future edit cannot bypass the barrier."""
    source = function_source(main, "lifespan")
    gated_functions = (
        "_boot_continuation",
        "_health_precompute_loop",
        "_bootstrap_autonomous_engine_bg",
        "_seed_bg",
        "_seed_knowledge_bg",
        "_start_aria_coder_bg",
        "_start_web_integrity_bg",
    )
    for name in gated_functions:
        body = source.split(f"async def {name}", 1)[1].split("async def ", 1)[0]
        assert "await _await_heavy_graph_ready(app)" in body, name

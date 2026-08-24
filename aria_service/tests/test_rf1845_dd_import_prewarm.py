"""R-F1845 — DD event-loop wedge: pre-warm the heavy writers/anthropic import.

LIVE WEDGE 2026-06-23: the first DD per process stalled the event loop 6-10s
(R-F703 watchdog) and "produced nothing". The main-thread wedge stack showed the
cause: the commercial-coherence layer (Layer 5c) lazily ran
`from ..writers.procurement_paper_writer import OFFSET_REGIMES` to read a constant
dict — but that triggers writers/__init__, which eager-imports the anthropic SDK:
a multi-second synchronous module load ON the request loop.

Fix: pre-warm that import at boot, off the event loop (lifespan, asyncio.to_thread),
so the lazy import in the DD path is a cache hit.

These tests prove (a) the warm path works — the exact module the DD path imports
loads and exposes OFFSET_REGIMES, and (b) the boot pre-warm is actually wired into
lifespan.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PREWARM_MODULE = "aria_service.writers.procurement_paper_writer"


def test_prewarmed_module_exposes_offset_regimes():
    """The exact import _assess_offset_claims does must resolve to a real dict —
    that's the data the DD offset-claims check needs."""
    m = importlib.import_module(PREWARM_MODULE)
    from aria_service.writers.procurement_paper_writer import OFFSET_REGIMES
    assert isinstance(OFFSET_REGIMES, dict) and OFFSET_REGIMES, "OFFSET_REGIMES must be a non-empty dict"


def test_second_import_is_a_cache_hit():
    """After the module is imported once (the boot pre-warm), re-importing is a
    sys.modules cache hit — no fresh heavy load on the request loop."""
    importlib.import_module(PREWARM_MODULE)
    assert PREWARM_MODULE in sys.modules
    # writers/__init__ (the eager parent that pulls anthropic) is also cached.
    assert "aria_service.writers" in sys.modules


def test_lifespan_wires_the_prewarm():
    """The boot pre-warm must actually be in lifespan — else the wedge returns
    on every cold process. AST-check the lifespan function body."""
    src = (REPO / "aria_service" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    lifespan = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"),
        None,
    )
    assert lifespan is not None, "lifespan() not found"
    body_src = ast.get_source_segment(src, lifespan) or ""
    assert "_prewarm_heavy_imports" in body_src, "lifespan must define the pre-warm"
    assert "asyncio.to_thread" in body_src, "pre-warm must run OFF the event loop"

    # R-F4289 — the module NAME is no longer a literal inside lifespan: it was
    # extracted to `_HEAVY_PREWARM_MODULES` and the body now iterates that tuple.
    # Asserting the substring made this test red for a REFACTOR while the
    # capability was perfectly intact (main.py:1128 still lists it, and the loop
    # still imports every entry via asyncio.to_thread). Same fragility class as
    # R-F2254's source-substring locks; read the list the code actually uses.
    from aria_service.main import _HEAVY_PREWARM_MODULES

    assert PREWARM_MODULE in _HEAVY_PREWARM_MODULES, (
        "the heavy DD-path module dropped out of the boot pre-warm; the first DD "
        "per process would import anthropic ON the request loop again"
    )
    assert "_HEAVY_PREWARM_MODULES" in body_src, (
        "lifespan no longer reads the pre-warm list, so entries added to it "
        "would never be warmed"
    )
    scheduled = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_task"
        and node.args
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
        and node.args[0].func.id == "_prewarm_heavy_imports"
        for node in ast.walk(lifespan)
    )
    assert scheduled, "pre-warm must be scheduled at boot"


def test_lifespan_warms_sanctions_source_caches():
    """R-F1846 — the boot pre-warm must also warm the four list-based sanctions
    sources, whose synchronous XML parse on the first DD starved the loop."""
    src = (REPO / "aria_service" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    lifespan = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"), None)
    body_src = ast.get_source_segment(src, lifespan) or ""
    for s in ("ofac_sdn", "fcdo_sanctions", "un_sc_sanctions", "worldbank_debarred"):
        assert s in body_src, f"pre-warm must warm sanctions source {s}"
    assert "_load_records()" in body_src, "pre-warm must call _load_records to populate the cache"


def test_warmed_sanctions_sources_expose_load_records():
    """Guard: the four warmed sources must actually have the async _load_records
    the pre-warm calls (else the warm-up silently no-ops on every boot)."""
    import importlib, inspect
    for s in ("ofac_sdn", "fcdo_sanctions", "un_sc_sanctions", "worldbank_debarred"):
        mod = importlib.import_module(f"aria_service.intel.sources.{s}")
        fn = getattr(mod, "_load_records", None)
        assert fn is not None and inspect.iscoroutinefunction(fn), \
            f"{s}._load_records must be an async function"


def test_offset_claims_still_imports_from_the_warmed_module():
    """Guard: the DD path's import target hasn't drifted away from what we warm.
    If _assess_offset_claims changes its import source, the warm-up would miss."""
    cc_src = (REPO / "aria_service" / "intel" / "commercial_coherence.py").read_text(encoding="utf-8")
    assert "from ..writers.procurement_paper_writer import OFFSET_REGIMES" in cc_src, \
        "DD offset-claims import drifted — update the R-F1845 pre-warm target to match"

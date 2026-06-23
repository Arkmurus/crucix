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
    assert PREWARM_MODULE in body_src, "pre-warm must target the heavy DD-path module"
    assert "asyncio.create_task(_prewarm_heavy_imports())" in body_src, "pre-warm must be scheduled at boot"


def test_offset_claims_still_imports_from_the_warmed_module():
    """Guard: the DD path's import target hasn't drifted away from what we warm.
    If _assess_offset_claims changes its import source, the warm-up would miss."""
    cc_src = (REPO / "aria_service" / "intel" / "commercial_coherence.py").read_text(encoding="utf-8")
    assert "from ..writers.procurement_paper_writer import OFFSET_REGIMES" in cc_src, \
        "DD offset-claims import drifted — update the R-F1845 pre-warm target to match"

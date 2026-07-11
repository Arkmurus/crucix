"""Thin compatibility shim over `mem0`.

Only one caller: `cited_artifact_verifier.py:144` probes for
`hasattr(mem0_notebook, "search")` and uses it to confirm cited document
IDs against the operator's mem0 notebook. Before 2026-04-20 this module
didn't exist; the try/except in cited_artifact_verifier silently swallowed
the `ModuleNotFoundError` and skipped the whole mem0 verification pass.

We keep this module tiny on purpose — if the mem0 API grows, callers
should import `mem0` directly rather than adding new functions here.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

from typing import Any

from . import mem0 as _mem0


def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return mem0 notebook entries matching `query`. Used by
    cited_artifact_verifier to confirm cited document IDs. Returns a list
    of hit dicts (possibly empty).

    Upstream `mem0.retrieve_for_query` returns a single concatenated string
    of relevant context; that works for chat-context injection but not for
    hit-counting. For now we return [{"context": <string>}] if there's any
    match, else []. If downstream callers need structured hits, expose a
    proper search API from mem0.py and update this shim.
    """
    if not query:
        return []
    try:
        ctx = _mem0.retrieve_for_query(query)
    except Exception:
        return []
    if not ctx or not str(ctx).strip():
        return []
    # R-F1001 - wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="mem0_notebook",
        summary="Search",
        source_id="mem0_notebook:R-F1001",
    )

    return [{"context": str(ctx)[:2000], "query": query}]

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.

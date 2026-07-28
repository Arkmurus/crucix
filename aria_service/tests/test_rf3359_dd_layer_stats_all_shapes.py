"""R-F3359 — /dd/health could only ever populate 7 of the 11 layers it listed.

THE DEFECT, in two halves that hid each other.

HALF 1 — THE RECORDER UNDERSTOOD ONE LAYER SHAPE. `_finalize_dd_run` recorded
per-layer stats with:

    layer_obj = getattr(report, layer_name, None)
    if layer_obj is None: continue
    meta = getattr(layer_obj, 'meta', None)
    if meta is None: continue          # <-- silently drops everything else

DD layers come in three shapes, and R-F3061 had ALREADY established that and
built the canonical measure `_dd_layer_state()` to handle them — including
`_DD_LAYER_ATTR_ALIAS` for layers stored under a different attribute
(sweep_intelligence → sweep_data). R-F3061 migrated the layer-state consumer at
_dd_layer_state's call site but NOT this recorder, so a second, naive fork
survived — the same "two aggregators, one measure" pattern that produced the
Phase-A gate disagreement. Consequence: counter_intelligence,
sanctions_divergence, sweep_intelligence, forensic and deterministic_primitives
ran on every DD and recorded NOTHING, for the whole 7-day window.

HALF 2 — THE ENDPOINT'S LIST HAD DRIFTED FROM THE PRODUCER. `_DD_LAYER_NAMES`
in routes/aria.py was hardcoded and listed `extensions`, which is never a layer
(it is a dict of module payloads and never reaches `layers_run`), while OMITTING
`forensic` and `deterministic_primitives`, which do run. So even a correct
recorder would have been invisible for two real layers.

WHY IT LOOKED FINE. An unrecorded layer renders as `{}` — indistinguishable from
"healthy, nothing to report" — on a surface whose own docstring says it exists to
let the operator "see at a glance which DD layers are healthy and which are
silently failing". Measured live 2026-07-28: 4 layers `{}` across ~99 DD runs.

'unobservable' IS THE HONEST THIRD STATE. A layer that ran but stored nothing is
neither success nor failure; scoring it 'error' would be a fabricated negative
(R-F3061's own words). These tests pin that distinction.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import dd_orchestrator as ddo


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _Meta:
    status: str = "ok"


@dataclass
class _Section:
    meta: _Meta = field(default_factory=_Meta)


def _Report(**attrs: Any):
    """A REAL ARKDDReport with the layers under test attached.

    An earlier version of this test used a minimal stub; `_finalize_dd_run`
    touches many report fields before it reaches the recorder, so the stub threw
    and the finalizer's own `except` swallowed it — the recorder never ran and
    every assertion failed for the wrong reason. Drive the real object.
    """
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    r.layers_run = list(attrs.pop("layers_run", []))
    for k, v in attrs.items():
        setattr(r, k, v)
    return r


def _record(report) -> dict[str, str]:
    """Drive the real finalizer and return {layer_name: status} actually written."""
    written: dict[str, str] = {}

    async def _hincrby(key, field_, amount):
        # key shape: crucix:dd:layer_stats:{layer}:{YYYY-MM-DD}  (R-F3364 buckets)
        written[key.split(":")[3]] = field_

    with patch("aria_service.intel.redis_store.hincrby", new=AsyncMock(side_effect=_hincrby)), \
         patch("aria_service.intel.redis_store.expire", new=AsyncMock()):
        _run(ddo._finalize_dd_run(report))
    return written


# ── HALF 1: every layer shape is recorded ───────────────────────────────────

def test_section_with_meta_is_recorded_ok():
    r = _Report(layers_run=["identity"], identity=_Section())
    assert _record(r).get("identity") == "ok"


def test_section_with_error_meta_is_recorded_error():
    r = _Report(layers_run=["identity"], identity=_Section(meta=_Meta(status="error")))
    assert _record(r).get("identity") == "error"


def test_plain_dict_layer_is_recorded_not_skipped():
    """counter_intelligence / sanctions_divergence are Optional[dict] on the
    schema — no `.meta` at all. These were dropped entirely."""
    r = _Report(
        layers_run=["counter_intelligence", "sanctions_divergence"],
        counter_intelligence={"findings": []},
        sanctions_divergence={"divergences": 0},
    )
    got = _record(r)
    assert got.get("counter_intelligence") == "ok", got
    assert got.get("sanctions_divergence") == "ok", got


def test_plain_dict_with_explicit_failure_is_error():
    r = _Report(layers_run=["counter_intelligence"], counter_intelligence={"ok": False})
    assert _record(r).get("counter_intelligence") == "error"


def test_aliased_layer_is_resolved_via_the_alias_map():
    """sweep_intelligence stores its result under `sweep_data` (R-F3061)."""
    assert ddo._DD_LAYER_ATTR_ALIAS.get("sweep_intelligence") == "sweep_data"
    r = _Report(layers_run=["sweep_intelligence"], sweep_data={"signals": 3})
    assert _record(r).get("sweep_intelligence") == "ok"


def test_layer_with_no_stored_attribute_is_unobservable_not_error():
    """forensic / deterministic_primitives run but store nothing. Calling that
    an error is a fabricated negative; calling it ok is a fabricated positive."""
    r = _Report(layers_run=["forensic", "deterministic_primitives"])
    got = _record(r)
    assert got.get("forensic") == "unobservable", got
    assert got.get("deterministic_primitives") == "unobservable", got


def test_no_layer_in_layers_run_is_silently_dropped():
    """The whole point: whatever ran must leave a record."""
    names = [
        "identity", "counter_intelligence", "sanctions_divergence",
        "sweep_intelligence", "forensic", "deterministic_primitives",
    ]
    r = _Report(
        layers_run=names,
        identity=_Section(),
        counter_intelligence={"a": 1},
        sanctions_divergence={"b": 2},
        sweep_data={"c": 3},
    )
    got = _record(r)
    missing = [n for n in names if n not in got]
    assert not missing, f"layers ran but recorded nothing: {missing}"


# ── HALF 2: producer and consumer share ONE list ────────────────────────────

def test_canonical_layer_names_are_exported_by_the_producer():
    assert hasattr(ddo, "DD_LAYER_NAMES"), (
        "the layer vocabulary must live with the producer, not be re-typed in "
        "the route — that is how `extensions` drifted in and `forensic` out"
    )
    assert isinstance(ddo.DD_LAYER_NAMES, (tuple, list))


def test_endpoint_uses_the_producers_list_not_its_own():
    from pathlib import Path
    src = (Path(ddo.__file__).parent.parent / "routes" / "aria.py").read_text(encoding="utf-8")
    assert "DD_LAYER_NAMES" in src
    assert '"extensions", "verification", "synthesis",' not in src, (
        "the hardcoded drifted list is still present in the route"
    )


def test_canonical_list_matches_what_the_orchestrator_actually_runs():
    """Root guard against the drift that put `extensions` on the surface and
    left `forensic` off it: every name assigned to `layer_name` (or appended
    literally) must be declared, and nothing else may be."""
    import re
    from pathlib import Path
    src = Path(ddo.__file__).read_text(encoding="utf-8")
    assigned = set(re.findall(r'^\s+layer_name = "([a-z_]+)"', src, re.M))
    literal = set(re.findall(r'layers_run\.append\("([a-z_]+)"\)', src))
    actual = assigned | literal
    assert actual, "guard is blind — found no layer names"
    declared = set(ddo.DD_LAYER_NAMES)
    assert not (actual - declared), f"layers run but undeclared: {sorted(actual - declared)}"
    assert not (declared - actual), f"declared but never run: {sorted(declared - actual)}"


def test_extensions_is_not_a_layer():
    assert "extensions" not in set(ddo.DD_LAYER_NAMES)

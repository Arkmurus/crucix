#!/usr/bin/env python3
"""R-F1782 — comprehensive AST probe of the §21 wiring surface.

Eradication-grade audit: instead of finding defects one module at a time, this
walks the ENTIRE target surface with the AST (not regex — regex misses multi-line
sigs, async generators, decorator order) and surfaces every landmine the fail_wire
backfill must handle, so the harness is built for all of them up front.

Surfaces, per target dir:
  - public sync / async functions (the real scope, not "271 modules")
  - ASYNC GENERATORS (async def + yield) — CRITICAL: a `return await func()` wrapper
    BREAKS these (you async-for an async-gen, you can't await it). Must be excluded
    or wrapped differently.
  - SYNC GENERATORS (def + yield) — same: can't be wrapped naively.
  - already-wired functions (fail_wire / wire_success / wire_failure / record_gap)
  - functions with control-flow `raise` in the body (gap-spam risk)
  - decorator stacks (order matters: @property/@staticmethod/@classmethod/@lru_cache)
  - streaming/SSE-ish names (stream/generate/iter) — §13 / response-body risk
"""
from __future__ import annotations

import ast
import os
import sys

TARGET_DIRS = [
    "aria_service/intel",
    "aria_service/routes",
    "aria_service/autonomous",
    "aria_service",  # engines / top-level (non-recursive handled below)
]
WIRE_TOKENS = ("fail_wire", "wire_success", "wire_failure", "record_gap", "brain_hook")
STREAM_HINTS = ("stream", "generate", "iter", "sse", "chunk")


def _has_yield(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            return True
    return False


def _has_controlflow_raise(node: ast.AST) -> bool:
    # a bare `raise X(...)` inside the body (not just re-raise) => possible control flow
    for n in ast.walk(node):
        if isinstance(n, ast.Raise) and n.exc is not None:
            return True
    return False


def _decorators(fn: ast.AST) -> list[str]:
    out = []
    for d in getattr(fn, "decorator_list", []):
        if isinstance(d, ast.Name):
            out.append(d.id)
        elif isinstance(d, ast.Attribute):
            out.append(d.attr)
        elif isinstance(d, ast.Call):
            f = d.func
            out.append(f.id if isinstance(f, ast.Name) else getattr(f, "attr", "?"))
    return out


def probe_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            src = f.read()
        tree = ast.parse(src, filename=path)
    except Exception as e:
        return {"error": str(e)}
    res = {
        "pub_async": [], "pub_sync": [], "async_gen": [], "sync_gen": [],
        "wired": [], "ctrlflow_raise": [], "stream_like": [], "special_decor": [],
    }
    module_wired = any(t in src for t in WIRE_TOKENS)
    # only TOP-LEVEL functions are the public surface (methods are class-internal)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        if name.startswith("_"):
            continue
        is_async = isinstance(node, ast.AsyncFunctionDef)
        decos = _decorators(node)
        gen = _has_yield(node)
        wired = "fail_wire" in decos or any(
            t in (ast.get_source_segment(src, node) or "") for t in WIRE_TOKENS)
        entry = f"{name}"
        if gen and is_async:
            res["async_gen"].append(entry)
        elif gen and not is_async:
            res["sync_gen"].append(entry)
        elif is_async:
            res["pub_async"].append(entry)
        else:
            res["pub_sync"].append(entry)
        if wired:
            res["wired"].append(entry)
        if _has_controlflow_raise(node):
            res["ctrlflow_raise"].append(entry)
        if any(h in name.lower() for h in STREAM_HINTS):
            res["stream_like"].append(entry)
        for d in decos:
            if d in ("property", "staticmethod", "classmethod", "lru_cache", "cached_property"):
                res["special_decor"].append(f"{name}:@{d}")
    res["module_wired"] = module_wired
    return res


def main():
    files = []
    for d in TARGET_DIRS:
        if not os.path.isdir(d):
            continue
        recursive = d != "aria_service"
        if recursive:
            for root, _, fs in os.walk(d):
                for f in fs:
                    if f.endswith(".py") and not f.startswith("_"):
                        files.append(os.path.join(root, f))
        else:
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if f.endswith(".py") and not f.startswith("_") and os.path.isfile(p):
                    files.append(p)
    files = sorted(set(files))

    tot = {k: 0 for k in ("pub_async", "pub_sync", "async_gen", "sync_gen",
                          "wired", "ctrlflow_raise", "stream_like", "special_decor")}
    async_gens, sync_gens, ctrl, streams = [], [], [], []
    n_modules = 0
    n_dark_modules = 0
    for p in files:
        r = probe_file(p)
        if "error" in r:
            print(f"  PARSE-ERR {p}: {r['error'][:60]}")
            continue
        n_modules += 1
        pubcount = len(r["pub_async"]) + len(r["pub_sync"])
        if pubcount and not r["wired"]:
            n_dark_modules += 1
        for k in tot:
            tot[k] += len(r[k])
        rel = p.replace("\\", "/")
        for g in r["async_gen"]:
            async_gens.append(f"{rel}::{g}")
        for g in r["sync_gen"]:
            sync_gens.append(f"{rel}::{g}")
        for c in r["ctrlflow_raise"]:
            ctrl.append(f"{rel}::{c}")
        for s in r["stream_like"]:
            streams.append(f"{rel}::{s}")

    print("=" * 70)
    print("§21 WIRING SURFACE — FULL AST PROBE (R-F1782)")
    print("=" * 70)
    print(f"modules scanned:            {n_modules}")
    print(f"modules with 0 wiring:      {n_dark_modules}")
    print(f"public ASYNC functions:     {tot['pub_async']}")
    print(f"public SYNC functions:      {tot['pub_sync']}")
    print(f"  -> total public surface:  {tot['pub_async'] + tot['pub_sync']}")
    print(f"already-wired functions:    {tot['wired']}")
    print(f"functions w/ control-flow raise (GAP-SPAM RISK): {tot['ctrlflow_raise']}")
    print()
    print("!! LANDMINES the harness MUST handle (naive wrap BREAKS these) !!")
    print(f"  ASYNC GENERATORS (await-wrap breaks them): {len(async_gens)}")
    for g in async_gens[:40]:
        print(f"    {g}")
    print(f"  SYNC GENERATORS:                           {len(sync_gens)}")
    for g in sync_gens[:25]:
        print(f"    {g}")
    print(f"  STREAM/SSE-like public fns (§13 / body risk): {len(streams)}")
    for s in streams[:25]:
        print(f"    {s}")
    print(f"  special decorators (order matters):        {tot['special_decor']}")


if __name__ == "__main__":
    main()

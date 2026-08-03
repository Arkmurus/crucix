#!/usr/bin/env python
"""R-F3657 — mechanical call-arity gate for aria_service.

WHY THIS EXISTS
---------------
The 2026-08-03 Prospector sweep found the same defect four separate times, and
every instance had been live for months:

  * R-F3647  dd_orchestrator passed a transaction dict POSITIONALLY to
             tbml_detection.analyze_transaction, which is keyword-only. Every
             call raised TypeError into `except Exception: continue`, so the
             trade-based money-laundering screen produced NOTHING on every DD.
  * R-F3648  routes/aria.py called dd_versioning.canonical_entity_id
             positionally — also keyword-only — so the deep-DD de-dup key was
             silently downgraded to a raw lowercased name.
  * R-F3646  news_monitor passed summary=/source_id= to wire_failure(), which
             accepts neither, so vault-source failures never reached the brain.
  * R-F1842  the ORIGINAL instance of exactly this, fixed in isolation without
             a gate — which is why it came back three more times.

A call that violates the callee's signature can NEVER succeed. It is not a style
issue and not a probabilistic bug: it is dead code that *looks* live. Because
ARIA wraps almost every such call in a swallowing `except`, the symptom is
silence — "no findings" — which is indistinguishable from a clean result. That
is a direct violation of the "unknown is never success" doctrine.

RESOLUTION MODEL (this is what keeps it honest)
-----------------------------------------------
A call is only checked when the callee can be resolved through a REAL IMPORT in
the calling file. Two forms are followed:

    from . import tbml_detection as _tbml   ->  _tbml.analyze_transaction(...)
    from ..intel.dd_versioning import canonical_entity_id as _canon  ->  _canon(...)

plus module-level defs in the calling file itself. Everything else is skipped.
An earlier draft matched on bare function NAME and produced 8,000 findings —
`x.strip()` resolving to an unrelated `def strip()` elsewhere in the tree. Name
matching is not resolution; if the import cannot be followed, the call is not
checked.

WHAT IT REPORTS
---------------
  POSITIONAL_TO_KWONLY   positional args passed where the callee is keyword-only
  UNKNOWN_KWARG          a keyword the callee does not accept (and no **kwargs)
  MISSING_REQUIRED       a required parameter with no value supplied
  TOO_MANY_POSITIONAL    more positional args than the callee can accept

Also skipped, deliberately: decorated callees (a decorator may rewrite the
signature), callees taking *args/**kwargs for the affected check, and call sites
using * / ** splats. tests/ are excluded.

Exit 0 = clean, 1 = findings.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ROOT = os.path.join(REPO, "aria_service")
SKIP_DIRS = {"__pycache__", "tests", "node_modules", ".venv", "static"}


class FuncSig:
    __slots__ = ("name", "path", "lineno", "posargs", "kwonly", "required",
                 "has_varargs", "has_kwargs", "decorated")

    def __init__(self, node, path, is_method):
        a = node.args
        self.name = node.name
        self.path = path
        self.lineno = node.lineno
        self.decorated = bool(node.decorator_list)
        pos = [p.arg for p in (list(a.posonlyargs) + list(a.args))]
        if is_method and pos and pos[0] in ("self", "cls"):
            pos = pos[1:]
        self.posargs = pos
        self.kwonly = [p.arg for p in a.kwonlyargs]
        self.has_varargs = a.vararg is not None
        self.has_kwargs = a.kwarg is not None
        nd = len(a.defaults)
        req_pos = pos[: len(pos) - nd] if nd else list(pos)
        req_kw = [p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is None]
        self.required = set(req_pos) | set(req_kw)

    @property
    def accepts(self):
        return set(self.posargs) | set(self.kwonly)


def iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py") and not fn.startswith("test_"):
                yield os.path.join(dirpath, fn)


def mod_name(path):
    """aria_service/intel/foo.py -> aria_service.intel.foo"""
    rel = os.path.relpath(path, REPO).replace("\\", "/")
    if rel.endswith("/__init__.py"):
        rel = rel[: -len("/__init__.py")]
    elif rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def resolve_relative(cur_mod, node):
    """Absolute module name for an ImportFrom, honouring relative levels."""
    if not node.level:
        return node.module or ""
    parts = cur_mod.split(".")
    # inside a package __init__ the module IS the package; otherwise drop the file
    base = parts[:-node.level] if node.level <= len(parts) else []
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base)


def build(root):
    defs_by_mod = {}      # module name -> {func name: FuncSig}
    trees = {}
    for path in iter_py(root):
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
            tree = ast.parse(src)
        except (SyntaxError, OSError):
            continue
        trees[path] = tree
        table = {}
        stack = []

        class V(ast.NodeVisitor):
            def visit_ClassDef(self, n):
                stack.append(n.name)
                self.generic_visit(n)
                stack.pop()

            def _fn(self, n):
                # module-level functions only (methods are ambiguous without
                # type inference, and that is where false positives come from)
                if not stack:
                    table[n.name] = FuncSig(n, path, is_method=False)

            def visit_FunctionDef(self, n):
                self._fn(n)

            def visit_AsyncFunctionDef(self, n):
                self._fn(n)

        V().visit(tree)
        defs_by_mod[mod_name(path)] = table
    return defs_by_mod, trees


def imports_for(tree, cur_mod):
    """(module_aliases, name_imports) for one file."""
    mod_alias = {}   # local alias -> absolute module name
    name_imp = {}    # local name  -> (absolute module, original name)
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            base = resolve_relative(cur_mod, n)
            for a in n.names:
                local = a.asname or a.name
                # `from . import mod as m` -> module alias
                mod_alias[local] = f"{base}.{a.name}" if base else a.name
                # ...and simultaneously a possible name import
                name_imp[local] = (base, a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    mod_alias[a.asname] = a.name
                else:
                    mod_alias[a.name.split(".")[0]] = a.name.split(".")[0]
    return mod_alias, name_imp


def check(root):
    defs_by_mod, trees = build(root)
    findings = []

    for path, tree in trees.items():
        cur_mod = mod_name(path)
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        mod_alias, name_imp = imports_for(tree, cur_mod)
        local_defs = defs_by_mod.get(cur_mod, {})

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            sig = None
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                target = mod_alias.get(node.func.value.id)
                if target:
                    sig = (defs_by_mod.get(target) or {}).get(node.func.attr)
            elif isinstance(node.func, ast.Name):
                nm = node.func.id
                if nm in name_imp:
                    base, orig = name_imp[nm]
                    sig = (defs_by_mod.get(base) or {}).get(orig)
                    if sig is None:   # `from . import mod` then mod(...) — not a call
                        sig = (defs_by_mod.get(f"{base}.{orig}") or {}).get(orig)
                if sig is None:
                    sig = local_defs.get(nm)
            if sig is None or sig.decorated:
                continue
            if any(isinstance(a, ast.Starred) for a in node.args):
                continue
            if any(k.arg is None for k in node.keywords):
                continue

            npos = len(node.args)
            passed_kw = {k.arg for k in node.keywords if k.arg}

            def add(kind, detail):
                findings.append({
                    "kind": kind, "file": rel, "line": node.lineno,
                    "callee": sig.name,
                    "defined_at": os.path.relpath(sig.path, REPO).replace("\\", "/")
                                  + f":{sig.lineno}",
                    "detail": detail,
                })

            if npos and not sig.posargs and sig.kwonly and not sig.has_varargs:
                add("POSITIONAL_TO_KWONLY",
                    f"{npos} positional arg(s) but {sig.name} is keyword-only "
                    f"(kwonly={sig.kwonly[:6]})")
                continue
            if npos > len(sig.posargs) and not sig.has_varargs:
                add("TOO_MANY_POSITIONAL",
                    f"{npos} positional arg(s), {sig.name} accepts {len(sig.posargs)}")
                continue
            if not sig.has_kwargs:
                extra = sorted(passed_kw - sig.accepts)
                if extra:
                    add("UNKNOWN_KWARG",
                        f"{sig.name} does not accept {extra}; accepts "
                        f"{sorted(sig.accepts)[:8]}")
                    continue
            supplied = set(sig.posargs[:npos]) | passed_kw
            missing = sorted(sig.required - supplied)
            if missing and not sig.has_varargs:
                add("MISSING_REQUIRED", f"{sig.name} requires {missing}")
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", default="")
    args = ap.parse_args()

    findings = check(args.root)
    if args.baseline and os.path.exists(args.baseline):
        accepted = {(f["file"], f["callee"], f["kind"])
                    for f in json.load(open(args.baseline, encoding="utf-8"))}
        findings = [f for f in findings
                    if (f["file"], f["callee"], f["kind"]) not in accepted]

    if args.json:
        print(json.dumps(findings, indent=2))
        return 1 if findings else 0
    if not findings:
        print("call-arity gate: CLEAN — no impossible calls found")
        return 0
    by_kind = defaultdict(list)
    for f in findings:
        by_kind[f["kind"]].append(f)
    print(f"call-arity gate: {len(findings)} IMPOSSIBLE CALL(S)\n")
    for kind, items in sorted(by_kind.items()):
        print(f"--- {kind} ({len(items)}) ---")
        for f in items:
            print(f"  {f['file']}:{f['line']}  -> {f['callee']}  [def {f['defined_at']}]")
            print(f"      {f['detail']}")
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())

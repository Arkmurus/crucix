"""R-F3597 — read a function's CURRENT source by NAME, never by line number.

THE DEFECT THIS REPLACES, measured on 2026-07-31.

The first complete single-process full-suite run reported 147 failures. Triage
classified 72 as "order-dependent" — they failed in the full run and passed alone.
Exactly half of those (36) assert on SOURCE TEXT via `inspect.getsource`.

They were not order-dependent. They were CONCURRENT-EDIT dependent.

`inspect.getsource(func)` takes the line range from the IMPORTED code object
(`func.__code__.co_firstlineno`) and slices the file FROM DISK at call time. In a
77-minute run on a shared tree, a peer agent committed four times (22:27, 22:47,
23:07) and touched `aria_service/routes/aria.py` — the exact file
`test_dd_auto_deep_retry_rf409.py` reads. The suite had imported it at 21:48. By the
time those tests ran, `getsource` was slicing the NEW file at OLD offsets and
returning a DIFFERENT function's body. Eight tests failed on text that was never
theirs.

That explains every observation: passes alone, passes when the 76 preceding files are
replayed, fails only inside a long run. Nothing leaked; the instrument moved.

WHY THIS IS WORTH A HELPER RATHER THAN A RULE. "Do not edit during a measurement run"
is unenforceable on a tree two agents share, and the failure is SILENT — the slice
still parses, still contains plausible Python, and the assertion just stops matching.
A test that reads code must resolve it the way a reader would: find the function BY
NAME in the file as it is NOW.

It also fixes the R-F3595 class in passing: an AST node knows where a function ENDS,
so no fixed byte window can be pushed off the end by someone adding a comment.
"""
from __future__ import annotations

import ast
import functools
import pathlib
import sys as _sys        # R-F3771 — resolve a class's defining module


#: Repo root, derived from this file's own location (aria_service/tests/_source_probe.py).
#: Never hardcode an absolute path in a test: the tree that R-F3597 was written on
#: ("C:/code/crucix") no longer exists, and every test that named it read a checkout
#: that is not the one under test — on a new machine it is a plain FileNotFoundError.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def repo_path(relative: str) -> pathlib.Path:
    """Absolute path to `relative`, resolved from the repo root of THIS checkout.

    Works for any file type — unlike `module_source`/`function_source`, which parse
    Python. Use it for `server.mjs`, `public/*.html`, and other non-Python targets.
    """
    return REPO_ROOT / relative


class SourceProbeError(LookupError):
    """Raised when the target cannot be resolved. Never returns empty text.

    A probe that returns "" on failure turns every `assert "X" in src` into a
    failure that looks like a missing feature. The distinction between "the code
    does not say this" and "I could not read the code" is the whole point.
    """


@functools.lru_cache(maxsize=64)
def _parse(path: str) -> tuple[str, ast.Module]:
    p = pathlib.Path(path)
    if not p.exists():
        raise SourceProbeError(f"source file not found: {path}")
    text = p.read_text(encoding="utf-8", errors="replace")
    try:
        return text, ast.parse(text)
    except SyntaxError as e:                       # pragma: no cover - defensive
        raise SourceProbeError(f"{path} does not parse: {e}") from e


def _resolve_target(target) -> tuple[str, str]:
    """(path, class_name) for a module, a path, or a CLASS.

    R-F3771 — a CLASS is now a first-class target. Previously only a module or a
    path resolved: `getattr(cls, "__file__")` is None, so a class fell through to
    `str(cls)` and raised "source file not found". That left every
    `inspect.getsource(SomeClass.method)` test unmigratable — roughly 120 of the
    ~196 files in the §16 backlog, i.e. the majority, blocked on a two-line gap.

    Resolving a class also makes the lookup STRICTER, which is the real gain. The
    module-level search matches a method by NAME anywhere in the file, so
    `function_source(mod, "start")` would happily return `OtherClass.start`. Given
    the class, the method is found inside THAT class's body only — no ambiguity,
    and no silent wrong-slice, which is the entire point of this module.
    """
    if isinstance(target, type):
        mod = _sys.modules.get(target.__module__)
        path = getattr(mod, "__file__", None)
        if not path:
            raise SourceProbeError(
                f"cannot locate the file defining {target.__module__}."
                f"{target.__name__} — its module is not importable by name"
            )
        return str(path), target.__name__

    # R-F3778 — a DOTTED MODULE NAME is a target.
    #
    # This unblocks the shape that dominates what is left of the §16 backlog: 160 of
    # the ~231 remaining getsource calls are `inspect.getsource(foo)` on a bare name.
    # A bare name carries no module to resolve against, and for the common
    # `from a.b.c import foo` there IS no module bound in the file — `a.b.c` was
    # never named, only `foo` was. So there was literally nothing to pass here, and
    # the converter had to skip the majority of the work.
    #
    # A dotted string closes it: `function_source("a.b.c", "foo")` is exactly as
    # specific as the import that introduced the name, and needs no new import line
    # in the test (which is what makes the conversion mechanical and safe).
    #
    # Distinguishing a module name from a PATH is done on structure, not a guess: a
    # path has a separator or a .py suffix, a module name has neither. Anything
    # ambiguous stays a path, preserving the pre-existing contract.
    if isinstance(target, str):
        looks_like_path = ("/" in target or "\\" in target or target.endswith(".py"))
        if not looks_like_path and "." in target:
            mod = _sys.modules.get(target)
            if mod is None:
                try:
                    import importlib
                    mod = importlib.import_module(target)
                except Exception as e:
                    raise SourceProbeError(
                        f"cannot import module {target!r} to read its source: {e} — "
                        f"a module that will not import is a real failure, not a "
                        f"read failure"
                    ) from e
            path = getattr(mod, "__file__", None)
            if not path:
                raise SourceProbeError(
                    f"module {target!r} has no __file__ (namespace package or "
                    f"builtin) — there is no source to read"
                )
            return str(path), ""

    return str(getattr(target, "__file__", None) or target), ""


def function_source(module_or_path, name: str) -> str:
    """The CURRENT source of a function, method, or one-level-nested definition.

    Resolves by NAME through the AST of the file as it exists now, so a concurrent
    edit shifts nothing: worst case the function moved and we find it at its new
    position. `module_or_path` may be a module, a filesystem path, or (R-F3771) a
    CLASS — pass the class to scope the lookup to that class's own body, which is
    both unambiguous and the only way to read a method whose name is reused
    elsewhere in the file.
    """
    path, cls_name = _resolve_target(module_or_path)
    text, tree = _parse(str(path))
    lines = text.splitlines(keepends=True)

    def _in_body(nodes):
        for n in nodes:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                return n
        return None

    def _find(nodes):
        # R-F3771 — when a class was named, search ONLY that class. Falling back
        # to a file-wide search here would reintroduce the ambiguity the class
        # argument exists to remove, and would do it silently.
        if cls_name:
            for n in nodes:
                if isinstance(n, ast.ClassDef) and n.name == cls_name:
                    return _in_body(n.body)
            return None
        for n in nodes:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
                return n
            # one level of nesting covers methods and closures without walking the
            # whole tree and matching an unrelated inner helper of the same name
            if isinstance(n, ast.ClassDef):
                for sub in n.body:
                    if (isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and sub.name == name):
                        return sub
        return None

    node = _find(tree.body)
    if node is None:
        _where = f"class {cls_name} in {path}" if cls_name else str(path)
        raise SourceProbeError(
            f"no function named {name!r} in {_where} — it was renamed or removed, "
            f"which is a real change, not a read failure"
        )
    start = (node.decorator_list[0].lineno - 1) if node.decorator_list else (node.lineno - 1)
    return "".join(lines[start:node.end_lineno])


def class_source(module_or_path, name: str) -> str:
    """The CURRENT source of a CLASS, resolved by NAME through the AST.

    R-F3787 — the last shape in the §16 backlog with no reader. `function_source`
    searches for FunctionDef/AsyncFunctionDef only, so `inspect.getsource(SomeClass)`
    had nowhere to go: converting it to `function_source(mod, "SomeClass")` raises
    "no function named 'SomeClass'", and the converter correctly refused those files
    rather than emit a call that cannot work.

    Same contract as function_source, for the same reason: resolve by NAME against the
    file as it is NOW, so a concurrent edit cannot hand back a different class's body.
    The slice starts at the first decorator, so a @dataclass or @final is included —
    without that, an assertion about a decorator would fail on a correct class.

    Scoped to TOP-LEVEL classes. A nested class is not searched, because a nested and a
    top-level class of the same name would be ambiguous and this module exists to
    refuse ambiguity rather than resolve it silently.
    """
    path, _cls = _resolve_target(module_or_path)
    text, tree = _parse(str(path))
    lines = text.splitlines(keepends=True)
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == name:
            start = (n.decorator_list[0].lineno - 1) if n.decorator_list else (n.lineno - 1)
            return "".join(lines[start:n.end_lineno])
    raise SourceProbeError(
        f"no class named {name!r} in {path} — it was renamed or removed, which is a "
        f"real change, not a read failure"
    )


def module_source(module_or_path) -> str:
    """The CURRENT full text of a module, read fresh (no linecache staleness)."""
    path = getattr(module_or_path, "__file__", None) or str(module_or_path)
    return _parse(str(path))[0]


def invalidate() -> None:
    """Drop the parse cache. Call when a test deliberately rewrites a source file."""
    _parse.cache_clear()

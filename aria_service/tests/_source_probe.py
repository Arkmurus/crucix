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


def function_source(module_or_path, name: str) -> str:
    """The CURRENT source of top-level (or one-level-nested) function `name`.

    Resolves by NAME through the AST of the file as it exists now, so a concurrent
    edit shifts nothing: worst case the function moved and we find it at its new
    position. `module_or_path` may be a module object or a filesystem path.
    """
    path = getattr(module_or_path, "__file__", None) or str(module_or_path)
    text, tree = _parse(str(path))
    lines = text.splitlines(keepends=True)

    def _find(nodes):
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
        raise SourceProbeError(
            f"no function named {name!r} in {path} — it was renamed or removed, "
            f"which is a real change, not a read failure"
        )
    start = (node.decorator_list[0].lineno - 1) if node.decorator_list else (node.lineno - 1)
    return "".join(lines[start:node.end_lineno])


def module_source(module_or_path) -> str:
    """The CURRENT full text of a module, read fresh (no linecache staleness)."""
    path = getattr(module_or_path, "__file__", None) or str(module_or_path)
    return _parse(str(path))[0]


def invalidate() -> None:
    """Drop the parse cache. Call when a test deliberately rewrites a source file."""
    _parse.cache_clear()

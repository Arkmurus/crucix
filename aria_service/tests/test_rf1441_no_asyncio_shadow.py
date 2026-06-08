"""R-F1441 — regression guard: no function-local bare `import asyncio` in
routes/aria.py (it shadows the module-global and causes UnboundLocalError).

Live incident 2026-06-08: `_read_document_ep_impl` did
`await asyncio.to_thread(_extract_pdf_text_sync, ...)` (line ~9706) to parse
PDFs, but later in the SAME function had two bare `import asyncio` statements
(9723, 10032). In Python, importing a name anywhere in a function makes that
name function-local for the ENTIRE function, so the earlier `asyncio.to_thread`
reference raised:

    UnboundLocalError: cannot access local variable 'asyncio' where it is not
    associated with a value

PDF extraction therefore crashed -> no document text reached the chat context
-> document review silently failed (the operator saw off-topic / "still
working" replies instead of a review). Same scoping class as the F28 outage
(CLAUDE.md §9).

routes/aria.py imports asyncio at module level (line 6) and elsewhere uses the
`import asyncio as _aioXXX` alias convention specifically to avoid this shadow.
This guard enforces that invariant: NO function may bare-`import asyncio`.
"""
from __future__ import annotations

import ast
from pathlib import Path


def _routes_aria_path() -> Path:
    return Path(__file__).resolve().parents[1] / "routes" / "aria.py"


def test_no_function_local_bare_import_asyncio():
    src = _routes_aria_path().read_text(encoding="utf-8")
    tree = ast.parse(src)

    offenders: list[int] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.func_depth = 0

        def _visit_func(self, node: ast.AST) -> None:
            self.func_depth += 1
            self.generic_visit(node)
            self.func_depth -= 1

        visit_FunctionDef = _visit_func  # type: ignore[assignment]
        visit_AsyncFunctionDef = _visit_func  # type: ignore[assignment]

        def visit_Import(self, node: ast.Import) -> None:
            if self.func_depth > 0:
                for alias in node.names:
                    # Bare `import asyncio` (no alias) inside a function shadows
                    # the module global. `import asyncio as _x` is safe.
                    if alias.name == "asyncio" and alias.asname is None:
                        offenders.append(node.lineno)
            self.generic_visit(node)

    _Visitor().visit(tree)

    assert not offenders, (
        f"Function-local bare `import asyncio` at lines {offenders} in "
        f"routes/aria.py — this shadows the module-global `asyncio` for the "
        f"whole function and causes UnboundLocalError on any earlier "
        f"`asyncio.<x>` use. Use the module-level asyncio, or alias it "
        f"(`import asyncio as _aioXXX`)."
    )

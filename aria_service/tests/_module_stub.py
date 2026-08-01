"""R-F3605 — stub a submodule so that `from . import X` actually sees the stub.

THE DEFECT, measured 2026-08-01. Three test files stubbed a submodule with

    monkeypatch.setitem(sys.modules, "aria_service.intel.knowledge", _K)

and all three pass ALONE and fail inside the full suite. Seven failures, one cause:

**`from . import X` resolves from the PARENT PACKAGE ATTRIBUTE, not `sys.modules`.**

Once anything has imported `aria_service.intel.knowledge`, the import machinery sets
`aria_service.intel.knowledge` as an *attribute* on the `aria_service.intel` package
object. A later `from . import knowledge` reads that attribute and never consults
`sys.modules`. Patching `sys.modules` alone is therefore a no-op against exactly the
import form the production code uses:

    pkg has .knowledge attr BEFORE importing aria_engine : False
    pkg has .knowledge attr AFTER                        : True
    from-import gets the STUB?                            False

Run the file alone and nothing has imported it yet, so the stub appears to work. Run it
after anything that pulls in `aria_engine`, and the real module answers instead. The
symptom is silent: `mem0.retrieve_for_query` returned "" and the owner-scoping test read
that as "the other user's fact was correctly withheld".

This is R-F3449 mechanism (e) — "a stale parent-package attribute defeating
`sys.modules.pop`" — reappearing in the opposite direction. R-F3449 fixed the removal
case; this is the substitution case, and it had been red for months in `test_rf463`
and `test_rf468` (both named in CLAUDE.md §16's long-red cluster list since 2026-05-20).

WHY A HELPER, NOT A NOTE. The correct incantation is two lines that must stay in step,
and getting it wrong fails silently and only in a long run — the worst combination for
something a person is expected to remember.
"""
from __future__ import annotations

import importlib
import sys


def stub_submodule(monkeypatch, dotted: str, replacement) -> None:
    """Make `dotted` resolve to `replacement` for BOTH import forms.

        stub_submodule(monkeypatch, "aria_service.intel.knowledge", _K)

    Patches `sys.modules[dotted]` (covers `import a.b.c` / `importlib.import_module`)
    AND the attribute on the parent package (covers `from . import c`, which is what
    the lazy imports throughout `intel/` actually use).

    monkeypatch restores both on teardown, so this cannot leak into the next test —
    which is the other half of the same failure class (R-F3449).
    """
    parent_name, _, leaf = dotted.rpartition(".")
    if not parent_name:
        raise ValueError(f"{dotted!r} is not a submodule path")

    monkeypatch.setitem(sys.modules, dotted, replacement)

    # The parent must EXIST for the attribute to matter. Importing it here is safe:
    # if it was already imported this is a no-op, and if it was not, the attribute we
    # set is what a later `from . import leaf` will read.
    try:
        parent = importlib.import_module(parent_name)
    except Exception:                       # noqa: BLE001 - a stub for an unimportable
        return                              # parent still gets the sys.modules entry
    monkeypatch.setattr(parent, leaf, replacement, raising=False)

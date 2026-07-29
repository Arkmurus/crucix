"""R-F772 — autonomy_surface must eager-import counterparty_claim_ledger.

Wedge cause captured on 2026-05-21 in /data/wedge_stacks/wedge_674_1779350869.log:
main asyncio thread blocked inside anthropic/types/beta_error.py while a lazy
`from . import counterparty_claim_ledger` fired on the first
/api/aria/autonomy/surface call after a cold-machine restart. 81.60s loop stall,
then 11.78s + 13.25s + 36.64s aftershocks, then fly health-check failure on
port 8000 at 08:15:51 UTC.

The fix moves the import to module-level in autonomy_surface so the
anthropic-SDK load is paid once during boot rather than on the hot path.

This regression test ensures the eager-import is never silently reverted.
"""
from __future__ import annotations

import sys


def test_autonomy_surface_eager_imports_counterparty_claim_ledger():
    # R-F3449 — popping sys.modules ALONE does not force a re-import here, and that made
    # this one of the fifteen order-dependent failures in the R-F3448 baseline.
    #
    # `from . import counterparty_claim_ledger` (the eager import under test) resolves from
    # the PARENT PACKAGE'S ATTRIBUTE when one exists. `sys.modules.pop` does not remove
    # `aria_service.intel.counterparty_claim_ledger` as an attribute of the
    # `aria_service.intel` package object, so once ANY earlier test has imported it the
    # re-import below binds that stale attribute and never re-registers the module —
    # leaving sys.modules without it and failing the assertion.
    #
    # MEASURED: import both, pop both from sys.modules, and the package still has the
    # attribute; re-importing autonomy_surface then leaves counterparty_claim_ledger absent
    # from sys.modules. So standalone this test passed only because nothing had imported it
    # first — in-suite it was not exercising the eager import at all.
    #
    # Clearing the package attributes too makes the import genuinely re-execute, which is
    # what this regression guard has always claimed to do.
    import aria_service.intel as _intel_pkg

    for mod in (
        "aria_service.intel.autonomy_surface",
        "aria_service.intel.counterparty_claim_ledger",
    ):
        sys.modules.pop(mod, None)
        attr = mod.rsplit(".", 1)[1]
        if hasattr(_intel_pkg, attr):
            delattr(_intel_pkg, attr)

    import aria_service.intel.autonomy_surface  # noqa: F401

    assert "aria_service.intel.counterparty_claim_ledger" in sys.modules, (
        "R-F772 regression: importing autonomy_surface did NOT transitively "
        "load counterparty_claim_ledger. A lazy import has been re-introduced "
        "and will re-create the wedge cascade in wedge_674_1779350869.log "
        "(81.60s main-loop stall + cascading aftershocks + fly health-check "
        "failure on port 8000)."
    )


def test_resilience_floor_no_local_import_of_counterparty_claim_ledger():
    """Belt-and-braces: the source of autonomy_surface must not contain a
    `from . import counterparty_claim_ledger` line inside a function body.
    The string-grep is intentionally cheap and explicit so a future refactor
    that re-introduces a local import here trips this immediately."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "intel" / "autonomy_surface.py"
    text = src.read_text(encoding="utf-8")

    module_level_line = "from . import counterparty_claim_ledger as _claim_ledger_module"
    assert module_level_line in text, (
        "R-F772 regression: the module-level eager import of "
        "counterparty_claim_ledger is missing from autonomy_surface.py."
    )

    indented_lazy = "        from . import counterparty_claim_ledger"
    assert indented_lazy not in text, (
        "R-F772 regression: a lazy (indented) `from . import "
        "counterparty_claim_ledger` has been re-introduced inside a function. "
        "Move it back to module level — see header comment in autonomy_surface.py."
    )

"""ARIA Vetting — deterministic employment-screening engine.

R-F3136 vendored the standalone `aria_vetting` package here unchanged apart
from import paths, so the engine keeps the property that makes it worth
having: `rules.assess()` is a pure function of (case, pack, as_of) with no
I/O, no network and no system-clock read. Persistence, tenancy and HTTP live
strictly OUTSIDE it (`store.py`, `service.py`, `routes/aria.py`).

Importing this package registers the built-in packs (see `packs.builtin`).
"""

from __future__ import annotations

from .packs import builtin as _builtin  # noqa: F401  (registers built-in packs)
from .packs.base import registry
from .service import AssessmentService
from .store import CaseNotFound, CasePersistenceError, get_case_store

__all__ = [
    "AssessmentService",
    "CaseNotFound",
    "CasePersistenceError",
    "get_case_store",
    "registry",
]

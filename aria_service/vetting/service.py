"""Assessment service — the only entry point for running an assessment.

R-F3137: the in-memory `CaseStore` stand-in is gone; this now sits on the
tenant-scoped sqlite store in `store.py`. Every method takes an explicit
`tenant_id` and resolves the case THROUGH it, so an assessment can only ever
be produced for a case the caller owns.

Callers supply (tenant_id, case_id, as_of); the service resolves the pack
EXCLUSIVELY from the case's immutable manifest (pack_id, version, content
hash). No caller can inject an arbitrary pack object, and hash verification
catches any tampering with a registered pack definition.
"""

from __future__ import annotations

from datetime import date

from .models import VettingCase
from .packs.base import PackRegistry
from .rules import assess as _assess
from .store import CaseNotFound, VettingCaseStore


class AssessmentService:
    def __init__(self, store: VettingCaseStore, registry: PackRegistry) -> None:
        self._store = store
        self._registry = registry

    def create_case(
        self, case: VettingCase, pack_id: str = "uk_bs7858"
    ) -> VettingCase:
        return self._store.create(case, self._registry, pack_id)

    def get_case(self, tenant_id: str, case_id: str) -> VettingCase | None:
        return self._store.get(tenant_id, case_id)

    def assess(self, tenant_id: str, case_id: str, as_of: date) -> dict:
        """Assess one case as of an explicit date.

        `as_of` stays REQUIRED all the way down. The engine never reads the
        system clock, which is what makes an assessment replayable
        byte-identically months later — the property that lets us answer
        "what did the file actually show on the day the decision was made?"
        The route layer defaults it to today; the domain never does.
        """
        case = self._store.get(tenant_id, case_id)
        if case is None:
            # Fail-closed: indistinguishable from "belongs to another tenant".
            raise CaseNotFound(f"case '{case_id}' not found")
        if case.manifest is None:
            raise ValueError(f"case {case_id} has no pinned pack manifest")
        pack = self._registry.get_exact(
            pack_id=case.manifest.pack_id,
            version=case.manifest.pack_version,
            content_hash=case.manifest.pack_hash,
        )
        return _assess(case, pack, as_of=as_of)

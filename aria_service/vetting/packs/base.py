"""Jurisdiction packs v2 — Phase 0 hardening per external review.

Changes: expanded lifecycle (PackStatus), legal coverage separated from
technical status, employment-decision eligibility explicit, content-hashed
immutable versions, version-aware registry (no silent overwrite), exact
resolution by (pack_id, version, hash) for case-manifest pinning.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum

from pydantic import BaseModel, Field

from ..models import CareerEntryType, DocumentType, Money


class PackStatus(str, Enum):
    DRAFT = "DRAFT"
    TECHNICALLY_VALIDATED = "TECHNICALLY_VALIDATED"
    LEGAL_REVIEW = "LEGAL_REVIEW"
    PILOT = "PILOT"
    PRODUCTION = "PRODUCTION"
    SUSPENDED = "SUSPENDED"
    DEPRECATED = "DEPRECATED"
    WITHDRAWN = "WITHDRAWN"


class LegalCoverage(str, Enum):
    FRAMEWORK_ONLY = "FRAMEWORK_ONLY"          # evidence discipline only
    JURISDICTION_REVIEWED = "JURISDICTION_REVIEWED"
    CLIENT_POLICY_ONLY = "CLIENT_POLICY_ONLY"


class ChecklistSpec(BaseModel):
    field: str
    label: str
    reference: str


class SignoffTrigger(BaseModel):
    code: str
    reference: str
    message: str
    predicate: str
    threshold: Money | None = None      # never silently currency-converted


class ScreeningPack(BaseModel):
    model_config = {"frozen": True}

    pack_id: str
    jurisdiction: str
    display_name: str
    version: str
    status: PackStatus
    legal_coverage: LegalCoverage
    employment_decision_eligible: bool
    legal_review_ref: str | None = None
    source_references: list[str] = Field(default_factory=list)

    default_screening_years: int
    alternative_screening_years: list[int] = Field(default_factory=list)
    min_age_floor: int = 16

    max_unverified_gap_days: int
    limited_screening_years: int | None = None

    full_screening_weeks: dict[int, int] = Field(default_factory=dict)
    extension_weeks: int = 0
    declaration_max_days_per_block: int | None = None

    checklist: list[ChecklistSpec] = Field(default_factory=list)
    accepted_evidence: dict[CareerEntryType, list[DocumentType]] = Field(default_factory=dict)
    evidence_references: dict[CareerEntryType, str] = Field(default_factory=dict)
    # R-F3174 — BS 7858 7.7 b): where a previous employer cannot confirm a
    # period, the fallback is "two or more different items" of documentary
    # evidence. The engine previously accepted ONE, which is a weaker file than
    # the standard asks for. Set per pack because it is a rule of the framework,
    # not a house preference.
    min_documentary_items_without_reference: int = 1
    # Evidence that IS a direct reference, and therefore stands alone.
    direct_reference_documents: list[DocumentType] = Field(default_factory=list)

    # R-F3189 — documents BS 7858 7.4 c)/d) expects to be sighted in the
    # ORIGINAL, not accepted as a copy. Pack data rather than a hardcoded list,
    # because which documents demand an original is a rule of the framework and
    # will differ in the next jurisdiction.
    originals_required: list[DocumentType] = Field(default_factory=list)

    criminality_routes: list[DocumentType] = Field(default_factory=list)
    criminality_reference: str = ""
    signoff_triggers: list[SignoffTrigger] = Field(default_factory=list)

    retention_unsuccessful_months: int | None = None
    retention_post_employment_years: int | None = None
    controller_notes: list[str] = Field(default_factory=list)

    def content_hash(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class DuplicatePackVersion(Exception):
    pass


class PackIntegrityError(Exception):
    pass


class PackNotUsable(Exception):
    pass


PackKey = tuple[str, str]


class PackRegistry:
    """Version-aware: registering (pack_id, version) twice is an error;
    old versions remain resolvable forever for case replay."""

    def __init__(self) -> None:
        self._packs: dict[PackKey, ScreeningPack] = {}

    def register(self, pack: ScreeningPack) -> str:
        key = (pack.pack_id, pack.version)
        if key in self._packs:
            raise DuplicatePackVersion(f"{key} already registered")
        self._packs[key] = pack
        return pack.content_hash()

    def ensure_registered(self, pack: ScreeningPack) -> str:
        """R-F3136 — idempotent bootstrap for the import-time built-in packs.

        `register()` stays STRICT: registering the same (pack_id, version)
        twice is a genuine authoring error and must raise, so callers that
        mean "add a new pack" still get told. But `packs/builtin.py` runs its
        registration loop at IMPORT time, and a module imported under two
        sys.modules keys (a reload in tests, or the same tree reachable as
        both `aria_service.vetting.packs.builtin` and a bare `vetting.…`
        path) executes that loop twice — which would raise
        DuplicatePackVersion *during import* and take the whole boot down.
        §9 exists because exactly that class of import-time failure broke
        prod once already: 1109 unit tests passed and lifespan() still died.

        Re-registering the IDENTICAL pack is a no-op. Re-registering a
        DIFFERENT pack under the same (pack_id, version) is still refused —
        that is the immutability property case replay depends on, and
        silently accepting it would let a mutated pack answer for a
        manifest hash pinned to the original.
        """
        key = (pack.pack_id, pack.version)
        existing = self._packs.get(key)
        if existing is None:
            return self.register(pack)
        incoming_hash = pack.content_hash()
        if existing.content_hash() != incoming_hash:
            raise PackIntegrityError(
                f"{pack.pack_id} v{pack.version} is already registered with "
                f"different content; pack versions are immutable — publish a "
                f"new version instead of editing a released one."
            )
        return incoming_hash

    def get_exact(self, pack_id: str, version: str, content_hash: str) -> ScreeningPack:
        # R-F3136 — an unknown (pack_id, version) used to escape as a bare
        # KeyError, which surfaces at the HTTP layer as an opaque 500. A case
        # whose manifest names a pack this process does not carry is a
        # DEFINITE, explainable condition, not a server fault.
        pack = self._packs.get((pack_id, version))
        if pack is None:
            raise PackNotUsable(
                f"pack '{pack_id}' v{version} is not registered in this "
                f"process; a case pinned to it cannot be assessed here."
            )
        if pack.content_hash() != content_hash:
            raise PackIntegrityError(
                f"{pack_id} v{version}: stored hash does not match manifest hash"
            )
        return pack

    @staticmethod
    def _version_key(version: str) -> tuple:
        """R-F3175 — order versions NUMERICALLY, not as strings.

        `max()` on the raw string picks "1.9.0" over "1.10.0", so the tenth
        revision of a pack would silently never be issued to new cases while
        the registry still reported it as PRODUCTION. Nothing would fail; the
        wrong rules would just quietly stay in force — the worst shape of bug
        for a compliance threshold.
        """
        parts = []
        for chunk in str(version).split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts)

    def latest_usable(self, pack_id: str) -> ScreeningPack:
        """For NEW cases only: the newest PRODUCTION version. Existing cases
        always resolve via get_exact from their manifest."""
        candidates = [p for (pid, _), p in self._packs.items()
                      if pid == pack_id and p.status == PackStatus.PRODUCTION]
        if not candidates:
            status = next((p.status.value for (pid, _), p in self._packs.items()
                           if pid == pack_id), "UNREGISTERED")
            raise PackNotUsable(
                f"No PRODUCTION version of '{pack_id}' (found: {status}); "
                f"production cases require a PRODUCTION pack."
            )
        return max(candidates, key=lambda p: p.version)

    def list_packs(self) -> list[dict]:
        return [{"pack_id": p.pack_id, "version": p.version,
                 "status": p.status.value, "legal_coverage": p.legal_coverage.value,
                 "decision_eligible": p.employment_decision_eligible}
                for p in self._packs.values()]


registry = PackRegistry()

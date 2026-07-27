"""Jurisdiction packs v2 — Phase 0 hardening per external review.

Changes: expanded lifecycle (PackStatus), legal coverage separated from
technical status, employment-decision eligibility explicit, content-hashed
immutable versions, version-aware registry (no silent overwrite), exact
resolution by (pack_id, version, hash) for case-manifest pinning.
"""

from __future__ import annotations

import hashlib
import json
import logging
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger("aria.vetting.packs")

from ..models import CareerEntryType, DocumentRequirement, DocumentType, Money


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

    # ── R-F3207 — the file-level document set ───────────────────────────────
    #
    # `accepted_evidence` answers a per-PERIOD question: what confirms this
    # engagement. It cannot express the intake set — application form, CV,
    # identity, criminality certificate, two proofs of address — because none
    # of those belong to a career period. The result was that the engine had no
    # opinion whatsoever on whether the core documents were on file: an officer
    # could reach READY_FOR_CONTROLLER_REVIEW without an identity document,
    # because nothing ever asked. This is the list that asks.
    required_documents: list[DocumentRequirement] = Field(default_factory=list)

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
        """R-F3240 — the hash of this pack's RULES, not of the model's shape.

        `exclude_defaults=True` is load-bearing and was the cause of a P0.
        Hashing the FULL dump meant that adding a field to this class rewrote
        the hash of every ALREADY-PUBLISHED version at once: R-F3207 added
        `required_documents`, so uk_bs7858 v1.2.0 went
        d9f648cdcb151baa… -> 04660ce4941ebd23… without anyone touching v1.2.0.
        Every existing case pins its hash in the CaseManifest, so `get_exact`
        began raising PackIntegrityError for the entire live estate — 14 HTTP
        500s across assess, retention and subject-access before it was found.

        A field left at its default carries no rule, so it must contribute no
        hash. With this, the next additive schema change cannot repeat it.

        What is still detected — and a test pins this — is a CHANGED rule: set
        `max_unverified_gap_days` to 30 and the hash moves, because 30 is not
        the default the pack was published with.
        """
        canonical = json.dumps(
            self.model_dump(mode="json", exclude_defaults=True),
            sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


# ── R-F3241: hashes that were already issued to real cases ────────────────
#
# Changing how a hash is COMPUTED does not change the rules a case was screened
# under, but it does invalidate every manifest already written. These are the
# exact values produced by the pre-R-F3240 scheme, recomputed from the tree at
# a75a20e7^ (the commit before R-F3207 added `required_documents`). A case
# pinned to one of them is resolved to the same (pack_id, version) it always
# named — the rules it carries are unchanged.
#
# This is an ALLOW-LIST of specific 64-char values, not a relaxation. An
# arbitrary mismatch is still refused, and a pack whose RULES change still
# produces a hash that appears nowhere here.
#
# DO NOT REGENERATE THIS TABLE FROM THE CURRENT CODE. Doing so records today's
# hashes, which every live case already fails to match, and the table would
# then resolve nothing while looking correct. A test asserts the recorded
# prefixes, so a regeneration fails loudly.
# TWO eras are recorded, because two different hashes were issued to real cases:
#   [A] pre-R-F3207 (tree at a75a20e7^) — the estate that existed before the
#       `required_documents` field was added. These are the manifests that broke.
#   [B] R-F3207-era (tree at 4598730c, the deployed SHA) — cases opened AFTER
#       that deploy and BEFORE R-F3240 pinned the full-dump hash of the widened
#       model. Omitting these would fix the old estate and break everything
#       created in between, which is the same outage with a different cohort.
_LEGACY_CONTENT_HASHES: dict[PackKey, tuple[str, ...]] = {
    ("uk_bs7858", "1.1.0"): (
        "90178f66f31a741e83966bd2799d5a07dfe071e2c7ef29c683d276a82f16737e",  # [A]
        "a4e86844b6625b58603cb72a2c08dbba3cea3c3fa53ff57b39bd0636370aa607",  # [B]
    ),
    ("uk_bs7858", "1.2.0"): (
        "d9f648cdcb151baab84304962426fa9351dc946adac6cc55b93d92f4dacb8e6b",  # [A]
        "04660ce4941ebd238f67189e143024be42d58ede377a7d84579bfb315248f4e2",  # [B]
    ),
    ("uk_bs7858", "1.3.0"): (
        "f92eb027143e77687c870fb4885a3e872a1be999912bb7fdce87fa8250b47244",  # [B]
    ),
    ("intl_baseline", "1.1.0"): (
        "b18d0ebf3194af09ab323c9ba8ff604c6cf53f1adc1a8f4e4d076f83a5094c12",  # [A]
        "26371f10019dba2d6d4eb78516911a7f9a609d6a3714adc23babfba2aa614e17",  # [B]
    ),
    ("intl_baseline", "1.2.0"): (
        "9ca08d2423770f44132584a89ee3c2baadd0997f95048a3052860e64fa7b8d80",  # [B]
    ),
    ("pt_generic", "0.2.0"): (
        "6f3e3d0d3daa3a73e9c23a48501a06c12b305f9ccdf54cc399097675ebdf0014",  # [A]
        "3cdff2e36e6bc83396d1822bb95d32229e14fa0c87754eb81977eb7b5ca928ac",  # [B]
    ),
}


def legacy_hashes_for(pack_id: str, version: str) -> tuple[str, ...]:
    """Historical content hashes still honoured for this (pack_id, version)."""
    return _LEGACY_CONTENT_HASHES.get((pack_id, version), ())


class DuplicatePackVersion(Exception):
    pass


class PackNotUsable(Exception):
    pass


class PackIntegrityError(PackNotUsable):
    """R-F3242 — a pack whose integrity cannot be verified IS a pack that is
    not usable, so this is a SUBCLASS rather than a sibling.

    It was a sibling, and that is why the P0 surfaced as HTTP 500 rather than
    as an explained refusal: eight handlers already wrote
    `except PackNotUsable` and none of them could catch this, so it escaped as
    a server fault on assess, retention and subject-access alike.

    Fixing it by adding `except PackIntegrityError` to all eight sites would
    have left the ninth — the one someone adds next year — uncovered. Putting
    the relationship in the type makes every present and future handler correct
    by construction, which is the only version of this fix that stays true.
    """


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
            # R-F3241 — a hash issued under the previous scheme names the same
            # rules. Accepting it is what lets the live estate keep working
            # across a hashing change; anything not on the allow-list is still
            # a genuine integrity failure and is still refused.
            if content_hash in legacy_hashes_for(pack_id, version):
                logger.info(
                    "[R-F3241] %s v%s resolved via a legacy content hash "
                    "(%s…): same rules, hashed under the pre-R-F3240 scheme.",
                    pack_id, version, content_hash[:16],
                )
                return pack
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
        # R-F3216 — this is the call site R-F3175 was written for, and it was
        # never wired: the selection stayed `key=lambda p: p.version`, a STRING
        # compare, while `_version_key` sat beside it exercised only by a unit
        # test of the helper itself. The test passed; the behaviour was
        # unchanged. That is the §3c failure exactly — a helper test is not a
        # capability test, and a fix that is not on the path it names has not
        # shipped. Live consequence: at v1.10.0 the registry would keep issuing
        # v1.9.0 to every new case while still reporting the newer pack as
        # PRODUCTION — the wrong compliance rules quietly in force, with
        # nothing failing to say so.
        return max(candidates, key=lambda p: self._version_key(p.version))

    def list_packs(self) -> list[dict]:
        return [{"pack_id": p.pack_id, "version": p.version,
                 "status": p.status.value, "legal_coverage": p.legal_coverage.value,
                 "decision_eligible": p.employment_decision_eligible}
                for p in self._packs.values()]


registry = PackRegistry()

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

from .models import CaseManifest, VettingCase
from .packs.base import PackNotUsable, PackRegistry, PackStatus
from .rules import assess as _assess
from .store import CaseNotFound, VettingCaseStore


class PackMigrationRefused(Exception):
    """A requested move to a different rule pack is not allowed.

    Distinct from PackNotUsable, which says the target pack cannot be resolved
    at all. This says the target resolved fine and the MOVE is refused — the
    caller asked for something the audit trail could not honestly record.
    """


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

    # ── R-F3266 — moving a case onto a newer version of its rule pack ───────

    def migrate_pack(
        self,
        tenant_id: str,
        case_id: str,
        *,
        to_version: str | None = None,
        migrated_by: str,
        reason: str = "",
        at: date,
        pack_id: str | None = None,
    ) -> dict:
        """Move a case forward onto a newer PRODUCTION version of its pack.

        The pin exists so an assessment can be replayed under the rules it was
        actually made under, and nothing here weakens that: the case simply
        acquires a NEW pin, and the old one survives in `pack_migrations`.
        Before this existed there was no path at all, so a case created before
        a pack revision stayed on the old rules for its whole life — the live
        symptom being a file pinned to uk_bs7858 v1.1.0 reporting "required
        documents: none defined" while v1.3.0 defined eight.

        Refused in five situations, each because the migration could not be
        recorded honestly:

        * a different pack_id — uk_bs7858 to intl_baseline is not an upgrade,
          it is a different standard, and a case screened under one must not be
          retro-labelled as screened under the other;
        * a version that is not strictly newer — a "migration" onto older rules
          silently weakens the standard a named person is screened against,
          which is the failure this module exists to prevent;
        * a pack that is not PRODUCTION — `create` holds new cases to that bar
          because a DRAFT pack has not been legally reviewed for the
          jurisdiction it claims, and a live case deserves the same bar;
        * a case with a recorded decision — the rules are the basis of a
          decision already communicated to a person, and changing them
          afterwards is a rewrite of history, not a data migration;
        * no named migrator — an unattributed change to the governing rules of
          a screening file is not an audit trail.

        `at` is explicit for the same reason it is on assess(): the domain
        never reads the system clock.
        """
        case = self._store.get(tenant_id, case_id)
        if case is None:
            # Fail-closed, exactly as assess() does — a case owned by another
            # tenant is indistinguishable from one that does not exist.
            raise CaseNotFound(f"case '{case_id}' not found")
        if case.manifest is None:
            raise ValueError(f"case {case_id} has no pinned pack manifest")

        who = (migrated_by or "").strip()
        if not who:
            raise PackMigrationRefused(
                "a pack migration must name who performed it")

        target_pack_id = pack_id or case.manifest.pack_id
        if target_pack_id != case.manifest.pack_id:
            raise PackMigrationRefused(
                f"a case can only move between versions of the same pack: "
                f"{case.manifest.pack_id!r} cannot become {target_pack_id!r}")

        if case.decisions or case.outcome != "PENDING":
            raise PackMigrationRefused(
                "this case already carries a recorded decision; the pack it "
                "was decided under cannot be changed afterwards")

        if to_version is None:
            target = self._registry.latest_usable(target_pack_id)
        else:
            target = self._registry._packs.get((target_pack_id, to_version))
            if target is None:
                raise PackNotUsable(
                    f"pack '{target_pack_id}' v{to_version} is not registered "
                    f"in this process")
            if target.status != PackStatus.PRODUCTION:
                raise PackMigrationRefused(
                    f"{target_pack_id} v{to_version} is {target.status.value}, "
                    f"not PRODUCTION; live cases require a PRODUCTION pack")

        key = self._registry._version_key
        if key(target.version) <= key(case.manifest.pack_version):
            raise PackMigrationRefused(
                f"a pack migration only ever moves forward: this case is on "
                f"v{case.manifest.pack_version} and v{target.version} is not "
                f"newer")

        record = {
            "from_version": case.manifest.pack_version,
            "from_hash": case.manifest.pack_hash,
            "to_version": target.version,
            "to_hash": target.content_hash(),
            "at": at.isoformat(),
            "migrated_by": who,
            "reason": (reason or "").strip()[:500],
        }
        # save() defaults to mark_stale=True, which is exactly right here and
        # is left to do its job: the cached verdict was computed under rules
        # that no longer apply, so it must not survive the change. Everything
        # the applicant supplied — career, documents, inputs — is untouched;
        # this changes which rules apply, not what the file holds.
        self._store.save(case.model_copy(update={
            "manifest": CaseManifest(
                pack_id=target.pack_id,
                pack_version=target.version,
                pack_hash=target.content_hash(),
            ),
            "pack_migrations": [*case.pack_migrations, record],
        }))
        return record

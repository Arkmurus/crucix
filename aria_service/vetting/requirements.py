"""R-F3207/R-F3211 — what the file must hold, and what it actually holds.

The module could assess a screening file to READY_FOR_CONTROLLER_REVIEW
without an identity document on it. Not because a rule was wrong, but because
no rule existed: `accepted_evidence` is indexed by CareerEntryType and answers
"what confirms this engagement", so it has nothing to say about the documents
that belong to the PERSON rather than to a period — application form, CV,
identity, criminality certificate, proofs of address. Those are the first
things a vetting officer chases and the engine had no opinion on any of them.

── Submitted is not accepted, and the states say so ──────────────────────
Five states, and the distinctions are the whole point:

  OUTSTANDING  nothing on the file satisfies this
  PARTIAL      some, but fewer than the requirement asks for (two proofs of
               address with one on file)
  RECEIVED     enough documents are present, but at least one still needs a
               human — a failed extraction, an authenticity flag, or an
               original that has not been sighted
  ACCEPTED     enough are present and none of them is waiting on a human
  WAIVED       a named person decided not to pursue it, and said why

Collapsing RECEIVED into ACCEPTED would be the false clean this module exists
to avoid: a PDF nobody could read is on the file, and "on the file" would then
read as "checked". Collapsing OUTSTANDING into PARTIAL would hide the opposite.

── Counting ─────────────────────────────────────────────────────────────
Documents are de-duplicated by the digest of the plaintext that was examined,
so the same bank statement uploaded twice is one item. Without that, "two
proofs of address" is satisfied by uploading one file twice — the exact
shortcut a rushed intake takes, and one that leaves a file looking compliant.

── Purity ───────────────────────────────────────────────────────────────
Everything here is a pure function of (case, pack). No clock, no I/O — same
contract as rules.assess(), because these feed it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Mirrors documents.CONFIDENCE_FLOOR. Imported rather than re-stated so a
# change to the floor cannot leave two different answers to "was this read?".
from .documents import CONFIDENCE_FLOOR
from .models import (
    DocumentRequirement,
    DocumentType,
    UploadedDocument,
    VettingCase,
)
from .packs.base import ScreeningPack

__all__ = [
    "RequirementState",
    "ResolvedRequirement",
    "resolve_requirements",
    "requirements_for",
    "summarise",
]


class RequirementState:
    OUTSTANDING = "OUTSTANDING"
    PARTIAL = "PARTIAL"
    RECEIVED = "RECEIVED"
    ACCEPTED = "ACCEPTED"
    WAIVED = "WAIVED"


# Ordered worst-first: the officer's queue is "what still needs me?", so this
# is the sort the UI uses and the order the summary counts in.
STATE_ORDER = {
    RequirementState.OUTSTANDING: 0,
    RequirementState.PARTIAL: 1,
    RequirementState.RECEIVED: 2,
    RequirementState.WAIVED: 3,
    RequirementState.ACCEPTED: 4,
}


@dataclass(frozen=True)
class MatchedDocument:
    """One document counted against a requirement, with why it still needs a
    human if it does. The reason travels with the document because the officer
    acts on the document, not on the aggregate."""

    document_id: str
    doc_type: str
    issuer: str | None
    covers_from: str | None
    covers_to: str | None
    sighting: str
    examined_by: str
    needs_human: bool
    reasons: tuple[str, ...]
    # Kept apart from `reasons` because rules.sighting_findings is already the
    # clause-referenced authority on originals (7.4 c)/d)). Both states are
    # true and both belong on the card, but only one of them may become a
    # finding, or the officer reads the same problem twice under two names.
    sighting_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedRequirement:
    requirement: DocumentRequirement
    state: str
    matched: tuple[MatchedDocument, ...]
    needed: int
    held: int
    waived_by: str = ""
    waived_reason: str = ""

    @property
    def outstanding_count(self) -> int:
        return max(0, self.needed - self.held)

    def as_dict(self) -> dict:
        r = self.requirement
        return {
            "key": r.key,
            "label": r.label,
            "accepted": [t.value for t in r.accepted],
            "min_count": r.min_count,
            "reference": r.reference,
            "basis": r.basis,
            "stage": r.stage,
            "mandatory": r.mandatory,
            "note": r.note,
            "origin": r.origin,
            "state": self.state,
            "held": self.held,
            "needed": self.needed,
            "outstanding": self.outstanding_count,
            "waived_by": self.waived_by,
            "waived_reason": self.waived_reason,
            "documents": [
                {
                    "document_id": m.document_id,
                    "doc_type": m.doc_type,
                    "issuer": m.issuer,
                    "covers_from": m.covers_from,
                    "covers_to": m.covers_to,
                    "sighting": m.sighting,
                    "examined_by": m.examined_by,
                    "needs_human_review": m.needs_human,
                    "reasons": list(m.reasons),
                    "sighting_reasons": list(m.sighting_reasons),
                }
                for m in self.matched
            ],
        }


def _document_key(document: UploadedDocument) -> str:
    """What makes two uploads the same item.

    The plaintext digest, when we have one. Falling back to the document id
    means a pre-encryption record counts on its own — which is right: those
    predate the digest and we cannot prove they are duplicates, and asserting
    a duplicate we cannot prove would silently drop evidence off the file.
    """
    return document.plaintext_sha256 or document.document_id


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    return tuple(v for v in values if not (v in seen or seen.add(v)))


def _review_reasons(
    document: UploadedDocument, originals_required: set[DocumentType],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Why this document is not yet something an officer can rely on.

    Returns (document reasons, sighting reasons). Stated as reasons rather
    than a boolean because the officer's next action differs per reason: read
    it yourself, look at the original, or name who already did.

    A note on the first reason. `decode_text_best_effort` returns "" for PDFs
    and images, so in practice almost every real document arrives with
    `extraction_unavailable`. That is honest — nothing read it — and it is
    why RECEIVED exists as a state distinct from ACCEPTED. The officer
    confirming the type by eye is the work; pretending the file did it would
    be the fabrication.
    """
    reasons: list[str] = []
    for flag in document.authenticity_flags:
        if flag.startswith("extraction_unavailable"):
            reasons.append("not machine-readable — a human must read it")
        elif flag.startswith("authenticity:"):
            reasons.append(flag[len("authenticity:"):].strip())
        elif flag.startswith("low_confidence_classification"):
            reasons.append("classified with low confidence — confirm the type")
        elif flag.startswith("uploaded_via_portal") or flag.startswith("no "):
            continue          # provenance, or a repeat of the reason above
        elif flag.startswith("unrecognised_doc_type"):
            reasons.append("the proposed type was not recognised — set it by hand")
        elif flag.startswith("inconsistent_coverage_dates"):
            reasons.append("the dates it covers contradict each other")
    if (
        document.extraction_confidence < CONFIDENCE_FLOOR
        and not any(r.startswith("not machine-readable") for r in reasons)
    ):
        reasons.append("not confidently classified — confirm the type")

    sighting: list[str] = []
    if document.doc_type in originals_required:
        if document.sighting == "COPY_ONLY":
            sighting.append("only a copy is held — the original must be sighted")
        elif document.sighting == "NOT_RECORDED":
            sighting.append("not recorded whether the original was sighted")
        elif document.sighting == "ORIGINAL_SEEN" and not document.examined_by.strip():
            sighting.append("original sighted but no examiner named")
    return _dedupe(reasons), _dedupe(sighting)


def requirements_for(
    case: VettingCase, pack: ScreeningPack,
) -> list[DocumentRequirement]:
    """The pack's requirements plus this case's manual ones.

    A manual requirement whose key collides with a pack requirement REPLACES
    it, so an officer can raise `min_count` for a client that wants three
    proofs of address without inventing a second, competing row for the same
    thing. It cannot lower a mandatory pack requirement below what the pack
    asks — that is what a waiver is for, and a waiver is signed.
    """
    by_key: dict[str, DocumentRequirement] = {}
    for requirement in pack.required_documents:
        by_key[requirement.key] = requirement
    for extra in case.extra_requirements:
        base = by_key.get(extra.key)
        candidate = extra.model_copy(update={"origin": "MANUAL"})
        if base is not None and base.mandatory:
            candidate = candidate.model_copy(update={
                "min_count": max(candidate.min_count, base.min_count),
                "mandatory": True,
                # Keep the clause the pack cited: a customer raising the count
                # does not change which clause the requirement answers.
                "reference": candidate.reference or base.reference,
            })
        by_key[extra.key] = candidate
    return list(by_key.values())


def resolve_requirements(
    case: VettingCase, pack: ScreeningPack,
) -> list[ResolvedRequirement]:
    """Match the file's documents against everything it is asked to hold."""
    originals = set(pack.originals_required)
    waivers = {w.key: w for w in case.requirement_waivers}
    resolved: list[ResolvedRequirement] = []

    for requirement in requirements_for(case, pack):
        accepted = set(requirement.accepted)
        matched: list[MatchedDocument] = []
        counted: set[str] = set()
        for document in case.documents:
            if document.doc_type not in accepted:
                continue
            key = _document_key(document)
            if key in counted:
                continue
            counted.add(key)
            reasons, sighting_reasons = _review_reasons(document, originals)
            matched.append(MatchedDocument(
                document_id=document.document_id,
                doc_type=document.doc_type.value,
                issuer=document.issuer,
                covers_from=document.covers_from.isoformat() if document.covers_from else None,
                covers_to=document.covers_to.isoformat() if document.covers_to else None,
                sighting=document.sighting,
                examined_by=document.examined_by,
                needs_human=bool(reasons or sighting_reasons),
                reasons=reasons,
                sighting_reasons=sighting_reasons,
            ))

        held = len(matched)
        needed = max(1, requirement.min_count)
        waiver = waivers.get(requirement.key)
        if waiver is not None:
            state = RequirementState.WAIVED
        elif held == 0:
            state = RequirementState.OUTSTANDING
        elif held < needed:
            state = RequirementState.PARTIAL
        elif any(m.needs_human for m in matched):
            state = RequirementState.RECEIVED
        else:
            state = RequirementState.ACCEPTED

        resolved.append(ResolvedRequirement(
            requirement=requirement,
            state=state,
            matched=tuple(matched),
            needed=needed,
            held=held,
            waived_by=waiver.waived_by if waiver else "",
            waived_reason=waiver.reason if waiver else "",
        ))

    resolved.sort(key=lambda r: (
        STATE_ORDER.get(r.state, 9),
        0 if r.requirement.mandatory else 1,
        r.requirement.label,
    ))
    return resolved


def summarise(resolved: list[ResolvedRequirement]) -> dict:
    """Counts for the card face.

    `accepted` is ACCEPTED only. A file with three documents waiting on a
    human is not three-quarters done, and a progress number that says it is
    would be read as progress toward a verdict.
    """
    counts = {state: 0 for state in STATE_ORDER}
    for item in resolved:
        counts[item.state] = counts.get(item.state, 0) + 1
    mandatory = [r for r in resolved if r.requirement.mandatory]
    return {
        "total": len(resolved),
        "mandatory": len(mandatory),
        "accepted": counts[RequirementState.ACCEPTED],
        "received": counts[RequirementState.RECEIVED],
        "partial": counts[RequirementState.PARTIAL],
        "outstanding": counts[RequirementState.OUTSTANDING],
        "waived": counts[RequirementState.WAIVED],
        # Mandatory-only view: what actually stops this file being complete.
        "mandatory_accepted": sum(
            1 for r in mandatory if r.state == RequirementState.ACCEPTED),
        "mandatory_outstanding": sum(
            1 for r in mandatory
            if r.state in (RequirementState.OUTSTANDING, RequirementState.PARTIAL)),
    }

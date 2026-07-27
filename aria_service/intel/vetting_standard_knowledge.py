"""R-F3215 — BS 7858 screening knowledge, into ARIA's brain.

Before this, the standard existed in exactly one place: as clause reference
strings inside `aria_service/vetting/`. A repo-wide search for "BS 7858"
returned the vetting module, its tests, and two policy documents — nothing in
`intel/`, nothing in the RAG, nothing the chat could reach. So ARIA enforced
the standard and could not discuss it. Asked "what does BS 7858 require for an
unverified gap?", she had only a general model's recollection to fall back on,
which is the same failure mode as answering a compliance question from memory.

── What is ingested, and what deliberately is not ────────────────────────
The register in `vetting/standard_map.py`: clause numbers, ARIA's own
statement of each obligation, how the module implements it, and — just as
importantly — what the module does NOT model.

The text of BS 7858:2019 is BSI copyright and is never stored, ingested or
served. This ingests our own encoding, which is what we are entitled to hold
and is the only thing an answer should be grounded in anyway. An answer built
from our register can be traced to code; an answer built from a model's
recollection of a paywalled standard cannot be traced to anything.

── The single most important chunk ───────────────────────────────────────
`bs7858_not_modelled` — the scope ARIA does NOT cover. Retrieval will surface
it beside the coverage chunks, so a question about an unencoded clause has a
chance of meeting "this module does not model that" rather than a confident
paragraph assembled from the clauses that happen to be nearby. A knowledge
base that only contains what it knows will answer everything.
"""
from __future__ import annotations

import logging

from .engine_wiring import wire_failure, wire_success

logger = logging.getLogger("aria.vetting_standard_knowledge")

_MODULE = "vetting_standard_knowledge"


def _sections() -> dict[str, dict]:
    """Build the knowledge sections from the live clause register.

    Derived, never duplicated. A second hand-written copy of the clause list
    would be a second thing to keep in step, and the one that drifts is the
    one nobody is watching — so the register in `standard_map.py` stays the
    single source and this reads it.
    """
    from ..vetting.packs.base import registry
    from ..vetting.standard_map import (
        CLAUSES, UNMAPPED_SCOPE, ClauseStatus, coverage_report,
    )

    sections: dict[str, dict] = {}

    # One chunk per clause. Per-clause rather than one large document because
    # retrieval answers a question about ONE clause, and a single document
    # would return the whole standard's worth of text for "what does 7.7 say
    # about gaps?" — burying the answer in twenty-five neighbours.
    for clause in CLAUSES:
        key = "bs7858_" + clause.clause.lower().replace(" ", "_").replace(")", "").replace("(", "")
        implemented = "; ".join(clause.implemented_by) or "not implemented in code"
        sections[key] = {
            "title": f"BS 7858 {clause.clause} — {clause.title}",
            "content": (
                f"BS 7858:2019 clause {clause.clause} — {clause.title}.\n\n"
                f"What it requires (ARIA's own statement, not the standard's "
                f"text, which is BSI copyright): {clause.requirement}\n\n"
                f"How ARIA's vetting module handles it: {clause.status}. "
                f"Implemented by: {implemented}.\n"
                + (f"Note: {clause.note}\n" if clause.note else "")
                + (f"Verified against the licensed standard on "
                   f"{clause.verified_on}.\n" if clause.verified_on else
                   "NOT verified against the licensed standard.\n")
                + ("This is an ORGANISATIONAL control. ARIA surfaces it to the "
                   "screening officer but cannot perform or verify it, and "
                   "does not claim to.\n"
                   if clause.status == ClauseStatus.OPERATOR_CONTROL else "")
            ),
            "tags": ["bs7858", "vetting", "screening", "compliance",
                     clause.clause],
        }

    # The boundary chunk. See the module docstring — this is the one that has
    # to be retrievable, because it is the answer to every question about a
    # clause we have not encoded.
    sections["bs7858_not_modelled"] = {
        "title": "BS 7858 — what ARIA does NOT model",
        "content": (
            "Scope limits of ARIA's BS 7858 implementation. If a question "
            "concerns anything in this list, the honest answer is that ARIA "
            "does not model it and cannot answer from its own rules:\n\n"
            + "\n".join(f"- {item}" for item in UNMAPPED_SCOPE)
            + "\n\nARIA's vetting module implements a specific set of clauses "
              "and makes no claim about the rest of BS 7858. The text of the "
              "standard is BSI copyright; a licensed copy is required to audit "
              "against it, and ARIA does not hold or reproduce it."
        ),
        "tags": ["bs7858", "vetting", "scope", "limitations", "honesty"],
    }

    try:
        report = coverage_report(registry.latest_usable("uk_bs7858"))
        counts = report["counts"]
        sections["bs7858_coverage_summary"] = {
            "title": "BS 7858 — how much of it ARIA implements",
            "content": (
                f"{report['honest_summary']}\n\n"
                f"Clauses in the register: {counts['total']}. "
                f"Implemented in code: {counts['encoded']}, of which "
                f"{counts['corroborated']} are corroborated against the live "
                f"rule pack. Organisational controls surfaced but not "
                f"performed by ARIA: {counts['operator_control']}.\n\n"
                f"Every implemented clause is cross-checked against the live "
                f"pack when the coverage report is served: a clause that "
                f"claims implementation the pack cannot corroborate is "
                f"reported as CLAIMED_NOT_CORROBORATED rather than counted. "
                f"The coverage figure is therefore falsifiable, which is the "
                f"only kind of compliance-coverage claim worth making."
            ),
            "tags": ["bs7858", "vetting", "coverage", "compliance"],
        }
    except Exception as exc:  # noqa: BLE001 — the clause chunks still stand
        logger.warning("BS 7858 coverage summary unavailable: %s", exc)

    return sections


async def ingest_to_knowledge() -> dict:
    """Store the BS 7858 clause register into knowledge + RAG.

    Same contract as the other `intel/*` knowledge modules so it can join the
    boot seed list unchanged: idempotent, deduped downstream by source URL,
    and never fatal.
    """
    from . import knowledge, rag_store

    results: dict = {}
    total_chunks = 0
    sections = _sections()

    for name, data in sections.items():
        try:
            await knowledge.store_fact(
                topic=f"vetting_{name}",
                content=data["content"],
                source=f"vetting_standard:{name}",
                # CONFIRMED is right here and is not a boast: these are OUR
                # encoded rules, read against the licensed standard on a
                # recorded date, and each one names the code that enforces it.
                # It is a confirmed statement about what ARIA does — not a
                # confirmed reproduction of what BSI wrote.
                confidence="CONFIRMED",
            )
            result = await rag_store.ingest_document(
                text=data["content"],
                source=f"vetting_standard:{name}",
                source_type="vetting_standard",
                title=data["title"],
                url=f"internal://aria/vetting_standard/{name}",
                extra_metadata={
                    "domain": "employment_screening",
                    "tags": ",".join(data["tags"]),
                    "module": "vetting_standard_knowledge",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[name] = {"status": "OK", "chunks": chunks}
        except Exception as exc:  # noqa: BLE001 — one bad section must not
            # take the rest of the standard down with it.
            logger.warning("BS 7858 section %s not ingested: %s", name, exc)
            results[name] = {"status": "ERROR", "error": str(exc)[:200]}

    failed = [k for k, v in results.items() if v.get("status") != "OK"]
    if failed:
        wire_failure(
            module=_MODULE,
            detail=(f"{len(failed)} of {len(sections)} BS 7858 knowledge "
                    f"sections were not ingested: {', '.join(failed[:5])}"),
            gap_type="knowledge_gap",
            source="vetting_standard_knowledge.ingest_to_knowledge",
        )
    else:
        wire_success(
            module=_MODULE,
            summary=(f"BS 7858 clause register ingested: {len(sections)} "
                     f"sections, {total_chunks} chunks"))
    return {"sections": len(sections), "chunks": total_chunks,
            "results": results}

# freeze.md — Phase 1: feature freeze and supported surface

**Crucix Cure Protocol — Phase 1** · declared 2026-08-05 · commit `3f47c67a`

This document is the Phase 1 deliverable: the corrective freeze in writing (1.1) and
the declaration of what Crucix actually warrants (1.2). It is the honest answer to any
client, partner or downstream project asking what this system guarantees.

---

## 1.1 — Feature freeze

**Crucix accepts corrective and stabilising changes only, effective 2026-08-05.**

Not accepted while the freeze holds:

- new capabilities or endpoints
- refactors for elegance
- store migrations
- framework upgrades
- extractions or re-architecture

A PR is admissible only if it is traceable to one of:

1. an entry in [`defects.md`](defects.md), **fixture-first**;
2. a recorded step on the deletion ladder in `deletion_ledger.md` (not yet created —
   no deletion is authorised, see §Gate below);
3. an item on the Phase 5 stability list.

Anything else is rejected by policy. **Operational R-numbers remain allowed** — CLAUDE.md
§1 already carves those out, and a production incident does not wait for a protocol.

**Security exception.** A fix for a confirmed vulnerability with a known exploit path is
corrective by definition and is admissible under (3), even when it requires a version
bump the freeze would otherwise refuse. See defects.md C-11.

### The freeze does NOT bind the imaria rebuild

The rebuild proceeds in parallel and inherits every correction as an *expectation*.
Nothing fixed here is lost when Crucix eventually retires.

---

## 1.2 — The supported surface

**Under warranty.** These paths are monitored at outcome level, carry regression
fixtures, and a defect in them is a P0/P1:

| Surface | Primary modules | Tier |
|---|---|---|
| DD run end-to-end (initiate → layers → synthesis → report render) | `intel/dd_orchestrator.py`, `intel/dd_schema.py`, `intel/company_investigator.py`, `lib/reports/pdf_generator.mjs` | intel + web |
| Vetting case lifecycle | `routes/vetting.py`, `routes/vetting_portal.py`, `aria_service/vetting/` | intel |
| Entity resolution | `lib/aria/entityMatcher.mjs`, resolution paths in `intel/` | web + intel |
| Sanctions / registry adapters | `intel/sources/`, `intel/rca_screening.py`, `intel/sanctions.py` | intel |
| Report delivery | `lib/reports/`, export/PDF paths | web |
| WA delivery (until its ADR-WA-001 migration) | `services/wa-listener/aria_wa_listener.mjs`, `lib/whatsapp/waListener.mjs` | wa + web |
| Auth / session | `routes/aria.py:292` router gate, `lib/auth/`, `server.mjs` | intel + web |

**DORMANT-BY-DECLARATION.** Everything else may work; it is **not warranted**. It queues
for the rebuild's capability ledger. This explicitly includes the autonomous/coder
subsystem, which standing rule already excludes from the evidence path.

### What the surface declaration does NOT yet have

Stated plainly, because a warranty nobody can verify is a marketing claim:

- **No gold-set fixtures exist.** Phase 2.1 requires fixtures 1–4 with *corrected*
  expectations. None are built, and they cannot be until the DR-1 evidence arrives
  (defects.md §A).
- **No end-to-end smoke exists.** Phase 2.3 requires one scripted DD run asserting
  outcome-level success, wired to `build_rev`. Not built.
- **Outcome monitoring is partial.** R-F3723 wired one degradation path on the WA
  document surface. The other surfaces above are not yet instrumented at outcome level.

Until those exist, "supported" means *"this is what we intend to warrant and where fixes
are prioritised"* — not *"this is proven correct."* Do not quote it as the latter.

---

## Gate — what is currently authorised

| Action | Authorised? | Why |
|---|---|---|
| Fix a defect in `defects.md`, fixture-first | **Yes** | Phase 3 |
| Fix a confirmed vulnerability (C-11) | **Yes** | security exception above |
| Stability items from the Phase 5 list | **Yes** | Phase 5 |
| Operational R-numbers / incident response | **Yes** | CLAUDE.md §1 |
| **Delete anything** | **NO** | Phase 0.3 runtime overlay has not run; every module carries `proof_runtime: UNKNOWN`, so the three-proof rule (4.1) is unmet |
| Deploy a cure PR | **OPERATOR-AUTHORISED, case by case** (was NO) | See the 2026-08-09 amendment. The Phase 2.3 smoke still does not exist; the gate is now an explicit operator decision per deploy, not an automatic pass |
| Any new capability, refactor, or migration | **NO** | 1.1 |

**The deletion gate is the load-bearing one.** 109 DEAD-CANDIDATE modules are identified
and none is deletable. The Phase 0.3 collection window has to be *opened* — Fly's
retention is short and is not a 14-day access record, so it **cannot be reconstructed
retrospectively**. Every day it stays unopened is a day added to Phase 4.

---

## Amendments

By decision record only, appended here with a date and a reason. The protocol itself is
amended the same way (Cure Protocol, Standing rules).

| Date | Amendment | Reason |
|---|---|---|
| 2026-08-05 | Security exception added to 1.1 | `npm audit` found 6 high-severity vulnerabilities with known fixes; a freeze that forbids patching them trades a real risk for a process one |
| 2026-08-09 | **Deploy gate relaxed to operator-authorised (R-F3814)** | Same trade as the 2026-08-05 amendment, one layer along. The freeze was written to stop UNREVIEWED change reaching production; it had begun holding back fixes for defects that are live *now*. Three in this batch: **R-F3798** — the R-F3715 thread-pool bound had never once applied (bare `os` in a module that aliases it; the NameError was swallowed into a warning), so production runs the host-sized default pool on the `redis_store` hot path, which is the exact thrash R-F3715 exists to prevent. **R-F3802** — the adverse-media relevance gate reduced `"Acme Ventures Ltd"` to `{"acme"}` and attributed *other companies'* wrongdoing to a DD subject, inflating the adverse exposure that feeds the evidence grade. **R-F3806** — a 12-char legal-form token could confirm two unrelated entities as one. Keeping a compliance-integrity defect in production to protect a process gate inverts what the gate is for. Operator decision, 2026-08-09, reaffirmed after the constraint was surfaced. **This does NOT open the deletion gate**, which stays NO on the three-proof rule, and it does not authorise a blanket deploy: each cure deploy remains an explicit operator call until the Phase 2.3 smoke exists. Building that smoke is now the highest-value Phase 2 item, precisely because this amendment is what stands in for it. |

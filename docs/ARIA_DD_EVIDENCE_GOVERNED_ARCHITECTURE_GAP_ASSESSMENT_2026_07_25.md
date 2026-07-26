# ARIA DD — Evidence-Governed Architecture Gap Assessment

**Assessment date:** 25 July 2026  
**Repository baseline:** `5e0b4e93`  
**Compared against:** the 32-part ARIA implementation blueprint supplied by the operator  
**Related forensic review:** `docs/ARIA_DD_360_BUSINESS_CODE_FORENSIC_REVIEW_2026_07_25.md`

## Executive decision

The blueprint is the correct strategic direction. It directly addresses ARIA's proven
failure classes: company-to-person contamination, source timeout ambiguity,
non-reproducible Brave/Claude execution, report-surface drift, process-global
diagnostics, and incomplete entity-specific decision contracts.

ARIA should adopt the blueprint's central law:

> The language model may organise, compare, explain and draft. It may never create,
> upgrade or conceal evidence.

However, ARIA should **not** be rebuilt from scratch and should not immediately adopt
Temporal, Kubernetes, a graph database, or a new forest of services. The current
system already contains valuable controls. The correct programme is to make those
controls one mandatory DD execution spine, then incrementally extract the monolithic
orchestrator behind stable contracts.

The current directional maturity score is **56/128, or 44%** against the supplied
blueprint. This is an architecture gap measure over 32 equally weighted
recommendations—not a production-quality percentage and not a statistical estimate.
Several strong components exist, but most are not universally enforced on the DD
path.

## 1. Scoring method

| Score | Meaning |
|---:|---|
| 0 | Absent |
| 1 | Fragment exists, optional, disconnected, or materially incomplete |
| 2 | Partial implementation on meaningful paths |
| 3 | Substantial implementation with bounded gaps |
| 4 | Mandatory, versioned, tested, monitored, and release-enforced |

The score reflects current code evidence. A module existing earns little credit if DD
does not call it. A prompt or policy statement is not equivalent to a code-enforced
gate. “Live” requires an exercised production path, not only a health endpoint.

## 2. Thirty-two-point scorecard

| # | Blueprint recommendation | Score | Current ARIA evidence | Decisive gap |
|---:|---|---:|---|---|
| 1 | Evidence-governed core architecture | 2 | Structured `Finding` and `Evidence`, seven DD layers, evidence/readiness grades | No mandatory evidence → claim → policy → release pipeline |
| 2 | Four planes | 1 | Governance, execution, persistence, and rendering functions exist | Responsibilities are interleaved across orchestrator, routes, stores, and renderers |
| 3 | Standard evidence contract | 1 | `dd_schema.Evidence`, `verified_intel.SourceRecord`, source-result dictionaries | No single immutable record with outcome, hash, raw artefact, parser/adapter/schema versions |
| 4 | Claim ledger | 1 | Counterparty ledger, grounded reasoner claims, verified facts | Three separate claim models; DD reports are not generated exclusively from any one ledger |
| 5 | Typed relationships | 1 | UBO/control fields and graph structures exist | Relationship vocabulary and edge provenance are inconsistent; directors have historically bled into ownership semantics |
| 6 | Identity resolution before research | 2 | Canonical entity IDs, registry matching guards, person/company isolation | DD still begins substantive work without a separately approved resolution object/state |
| 7 | Multidimensional capability profiles | 1 | Registry coverage and capability-card fragments | No case planner resolves coverage by jurisdiction + entity type + purpose + access tier + date |
| 8 | Versioned jurisdiction/source profiles | 1 | Code/R-number history and some coverage timestamps | No immutable profile version bound to every case |
| 9 | Formal source catalogue | 2 | Portal registry, corpus registry, source monitors, registry coverage | No unified DD source definition with licence, permitted use, snapshot, schema and owner controls |
| 10 | Deterministic narrow adapters | 2 | Many registry and specialist adapters with explicit unavailable/stub states | Adapter return schemas vary; some mix retrieval, parsing and interpretation |
| 11 | Specialist workflows | 3 | Sanctions, registry, ownership, financial, media, procurement, legal and other specialists | Outputs lack one universal worker-result contract and cannot all be independently replayed |
| 12 | Deterministic state machine | 2 | Ordered seven-layer orchestration and running/completed/error states | No durable state transition contract from scope through human review and release |
| 13 | Hostile-input handling | 2 | SSRF guard, content scanner, prompt-injection guards and tests | Not proven mandatory at every document/web-to-LLM boundary; retrieved evidence lacks a universal trust wrapper |
| 14 | Separate extraction and reasoning | 1 | Some extraction and verification modules are separate | Several DD paths still construct findings/conclusions directly during collection |
| 15 | Deterministic verification rules | 3 | Confirmation demotion, registry matching, sanctions checks, readiness gates | Rules are distributed and sometimes depend on display strings or provider-specific shapes |
| 16 | First-class contradiction engine | 2 | `verified_intel.ContradictionDetector`, knowledge contradictions, claim-ledger contradictions | Not one DD-scoped contradiction ledger blocking release |
| 17 | Explainable confidence dimensions | 2 | Source tiers, verification status, evidence grade, citation grounding | User-facing confidence still mixes single labels/scores; dimensions are not canonical per claim |
| 18 | Evidence quality separate from readiness | 3 | `_dd_quality_assessment` and `_dd_decision_readiness`; `NOT_CLEARED` fail-closed | Decision questions remain limited and policy is not selected by customer decision type |
| 19 | Mandatory risk-based human review | 1 | “Decision ready for human review” and review fragments | No DD release gate proves required reviewer identity, disposition and affected claims |
| 20 | Restricted report generator | 1 | Structured view and Markdown/PDF renderers | Renderers do not consume an immutable report-eligible claim set; presentation logic is duplicated |
| 21 | Controlled negative language | 3 | Strong “not checked ≠ clean” and adverse-media limitation language | No central versioned language library/release validator covers every surface |
| 22 | Multilingual research | 2 | Multilingual search and specialist modules | Query-language manifest, translation provenance and material translation review are incomplete |
| 23 | Comprehensive observability | 2 | Source monitors, traces/metrics/logs, layer timings, brain audit signals | No complete case trace or immutable provider activity ledger; process-global search diagnostics remain |
| 24 | Serious test programme | 3 | Large DD suite, capability/golden/adversarial tests, UI parity tests | Broad DD selection is not green; durable-store isolation and mutation coverage are incomplete |
| 25 | Model evaluations as release gates | 2 | Evaluation framework and grounding/honesty suites | Not every DD prompt/model/parser version is bound to an evaluation result that blocks deployment |
| 26 | Security/access model | 2 | Secrets platform, authentication, IDOR guards, SSRF/content controls | Tenant enforcement is application-level in important paths; evidence access/audit and append-only guarantees are incomplete |
| 27 | Recommended technical stack | 1 | Python/FastAPI, Redis, files/SQLite, Node web | No transactional canonical claim store/object evidence store/durable workflow; stack migration not yet justified by measured load |
| 28 | Domain-oriented repository | 1 | Many domain modules exist | 15k-line orchestrator and cross-cutting routes prevent clear domain boundaries |
| 29 | Evidence-first implementation sequence | 1 | Product grew sources/reporting before a universal evidence spine | Must retrofit via strangler pattern without stopping current DD service |
| 30 | Country definition of done | 2 | Registry coverage distinguishes live, stub and unsupported states | Public capability is not consistently dimension-specific and version-bound |
| 31 | Sixteen-check release gate | 1 | Readiness/evidence grade block optimistic clearance | No immutable candidate artefact passes a single atomic release validator |
| 32 | AI at interpretation edges | 2 | Many deterministic sanctions/registry gates and grounded reasoning controls | LLM-derived and deterministic outputs are not universally separated by enforceable schemas |

## 3. What should be retained

ARIA already has the beginnings of the target platform:

1. **Fail-closed report semantics.** Current decision readiness separates evidence
   grade from risk and returns `NOT_CLEARED` when critical questions remain open.
2. **Entity-aware progress.** Current code now makes company ownership and financial
   questions `NOT_APPLICABLE` for a person rather than manufacturing company evidence
   to fill them.
3. **Structured findings.** `dd_schema.Finding` includes sources, URL, source tier,
   retrieval time, confidence demotion and gate reasons.
4. **Verification primitives.** `verified_intel` models source records, verified
   facts, contradictions, expiry, source independence and human-required status.
5. **Claim-oriented prototypes.** `grounded_reasoner` and
   `counterparty_claim_ledger` prove that claim extraction and contradiction concepts
   already exist.
6. **Tamper-evident report lineage.** `verifiable_ledger` hashes, signs and chains DD
   report versions.
7. **Source coverage honesty.** Registry adapters distinguish production, partial,
   stub and unavailable outcomes.
8. **Security foundations.** SSRF defence, content scanning and prompt-injection
   guards exist.
9. **Strong tests.** The repository contains meaningful failure-path, UI,
   provenance, source-unavailability and adversarial tests.

These should be consolidated, not replaced.

## 4. The central architectural defect

ARIA has multiple evidence, claim, verification, provenance, contradiction and audit
models, but DD is not forced through one of them.

Today the approximate flow is:

```text
source-specific dicts
  → orchestrator mutates section dataclasses
  → findings and prose are assembled
  → report dict is persisted in several stores
  → web/Markdown/PDF render independently
```

The target mandatory flow must be:

```text
CaseScope vN
  → IdentityResolution vN
  → SourceAttempt
  → immutable EvidenceRecord
  → atomic Claim
  → typed Relationship / Contradiction
  → DecisionPolicy evaluation
  → ReviewDecision where required
  → frozen ReleaseCandidate
  → one canonical PresentationDocument
  → web / Markdown / PDF
```

Every arrow must be referentially complete. A renderer must not be able to reach a
source adapter, search engine, mutable run object, or LLM research tool.

## 5. ARIA-specific target architecture

### 5.1 Keep three deployable services initially

Do not prematurely create dozens of microservices. Retain:

- `aria-intel`: control, execution and evidence APIs/workers;
- `aria-web`: presentation and customer delivery;
- `aria-wa`: messaging surface.

Within `aria-intel`, enforce domain boundaries first. Physical service extraction
should occur only when scaling, security isolation, or failure containment is
measured and material.

### 5.2 Introduce a canonical DD kernel

Add a small dependency-inward domain kernel:

```text
aria_service/dd_domain/
  cases.py
  identity.py
  evidence.py
  claims.py
  relationships.py
  contradictions.py
  source_catalogue.py
  capability_profiles.py
  decision_policies.py
  reviews.py
  releases.py
```

This kernel must not import FastAPI, Redis, HTTP clients, LLM factories, renderers or
provider SDKs. It contains schemas and deterministic invariants only.

### 5.3 Use a strangler façade around current adapters

Do not rewrite all source modules. Introduce:

```python
class DDSourceActivity(Protocol):
    source_id: str

    async def retrieve(self, request: SourceRequest) -> SourceAttemptResult:
        ...
```

Wrap each existing adapter. The wrapper:

1. validates the resolved subject;
2. checks source policy and capability;
3. records the attempt;
4. calls the legacy adapter;
5. preserves the raw response where lawful;
6. creates an `EvidenceRecord`;
7. returns no conclusion.

Legacy output may temporarily coexist, but a report section becomes release-eligible
only through the new evidence path.

### 5.4 One evidence contract

The proposed blueprint record is sound but needs these additions:

- `tenant_id`
- `case_scope_version`
- `subject_entity_id`
- `source_attempt_id`
- `query_identifiers` or a privacy-safe manifest of searched aliases/IDs
- `http_status` and provider error taxonomy
- `raw_artifact_retention_class`
- `classification/sensitivity`
- `supersedes_evidence_id`
- `ingested_at`
- `signature/key_version` where tamper evidence is required

`ZERO_RESULTS` and `NO_MATCH` should remain distinct:

- `ZERO_RESULTS`: the query executed and returned no candidate records.
- `NO_MATCH`: candidates or a dataset were evaluated and none satisfied a defined
  matching policy.

Neither means “not sanctioned” or “no adverse media”.

### 5.5 One claim contract

Do not reuse the existing three incompatible `Claim` classes unchanged. Migrate them
into one DD claim ledger. Add:

- claim type/value schema;
- source text/page/record locator;
- extraction activity ID;
- extraction model/parser version;
- deterministic versus model-derived flag;
- review/release state;
- authority and independence dimensions;
- supersession and invalidation;
- allegation/adjudication taxonomy;
- access/retention classification.

Confidence must not be freely assigned by the extractor. It must be calculated or
capped by policy from evidence authority, identity resolution, corroboration,
temporal relevance, extraction quality and contradiction state.

### 5.6 Durable workflow without premature infrastructure

The deterministic state machine is mandatory; Temporal is not mandatory on day one.

Phase A can use PostgreSQL rows plus transactional state transitions and an outbox:

```text
CREATED
SCOPE_VALIDATED
IDENTITY_RESOLUTION
IDENTITY_REVIEW_REQUIRED
PLANNED
COLLECTING
EXTRACTING
VERIFYING
CONTRADICTION_REVIEW
SUFFICIENCY_EVALUATION
HUMAN_REVIEW_REQUIRED
RELEASE_CANDIDATE
RELEASED
```

If crash recovery, timers, long-running retries and operational load demonstrate that
the database workflow is insufficient, migrate activities to Temporal behind the
same state/activity contracts. This avoids introducing operational complexity as a
fashion choice.

### 5.7 Atomic release

The release validator receives one frozen `ReleaseCandidate` containing:

- scope and identity resolution;
- report-eligible claim IDs;
- evidence IDs and artefact hashes;
- contradictions and dispositions;
- achieved capability coverage;
- source attempts/failures;
- decision policy result;
- required review decisions;
- code, profile, source, parser, prompt and model versions.

It returns either:

- `RELEASED`;
- `RELEASED_WITH_LIMITATIONS`; or
- a typed blocking result.

It never modifies evidence or claims. A new correction produces a new revision.

### 5.8 Canonical presentation

Build one `PresentationDocument` from the released case revision. It contains
paragraph blocks, tables, charts, citations, claim IDs and limitation blocks. Web,
Markdown and PDF are dumb renderers over that same object.

This is the structural solution to the observed online/PDF mismatch.

## 6. Blueprint refinements

The blueprint should be adopted with the following corrections:

1. **PROV-O as a mapping, not the operational schema.** Model ARIA's domain in
   relational/event schemas and expose a PROV-O-compatible export. Requiring RDF/OWL
   internally would add complexity without solving evidence integrity.
2. **A document store is not enough.** Evidence immutability requires database
   metadata, versioned object storage, hashes, access policy and an append-only audit
   trail.
3. **No absolute automated sanctions false-negative claim.** A zero measured
   false-negative rate on a named test set is a release gate for that sample—not proof
   of zero real-world false negatives.
4. **Entity resolution cannot always fully block research.** Candidate-specific
   research may be necessary to resolve ambiguity. It must run in quarantined
   candidate branches and cannot be attributed to the subject until resolution.
5. **Human review must be policy-triggered, not universal bureaucracy.** Low-risk,
   complete monitoring updates may be automatically released if a versioned policy
   permits it. Defence, dual-use, sanctions, material allegations and ambiguous
   ownership should remain mandatory-review cases.
6. **Raw snapshots require licence/privacy policy.** Hash and metadata can be
   retained even where the raw source cannot lawfully be stored.
7. **“Every paragraph maps to claim IDs” is necessary but insufficient.** The
   mapping needs coverage validation: every material sentence/span, table cell and
   chart datum must map to claims/evidence.
8. **Do not confuse source authority with truth.** Official sources can be stale,
   scoped, wrong, superseded or describe a different legal concept. Authority is one
   confidence dimension, not an automatic truth switch.

## 7. Implementation programme

### Phase 0 — Evidence and Decision Standard

Produce and approve:

- evidence/outcome taxonomy;
- claim and allegation taxonomy;
- typed relationship vocabulary;
- entity-type readiness profiles;
- decision-policy format;
- controlled-language rules;
- review triggers;
- source acceptance/licence policy;
- retention and privacy classification;
- release-gate specification.

**Exit gate:** versioned schemas and executable validation tests exist. No prose-only
standard counts as complete.

### Phase 1 — Canonical case, attempt and evidence ledger

Implement:

- transactional case/scope records;
- identity-resolution object;
- source-attempt ledger;
- immutable evidence metadata;
- versioned object storage;
- per-run Brave/Claude/provider attestation;
- outbox for vault/index/watchlist projections.

Wrap Companies House, one sanctions provider and Brave first.

**Exit gate:** induced timeout, auth failure, zero result, no match, parser failure and
storage failure all persist as distinct outcomes and cannot become clean findings.

### Phase 2 — Unified claims and relationships

Implement:

- claim ledger and evidence locators;
- typed relationship edges;
- deterministic authority/corroboration caps;
- contradiction objects;
- supersession/invalidation;
- candidate quarantine.

Migrate company identity and sanctions before broader specialist findings.

**Exit gate:** Charles Woodburn and adversarial homonym cases cannot receive
company-only claims; GLEIF accounting parent cannot become UBO.

### Phase 3 — Decision policy and human review

Implement:

- policy selection from client decision and case risk;
- entity-type capability requirements;
- mandatory-gap rules;
- review queue and immutable dispositions;
- re-review on changed claims/evidence.

**Exit gate:** no defence/dual-use, sanctions candidate, material contradiction or
unresolved ownership case reaches release without required review.

### Phase 4 — Release candidate and canonical renderer

Implement:

- 16-point release validator;
- frozen release candidate;
- presentation document;
- web/Markdown/PDF adapters;
- semantic parity validator;
- correction/new-revision workflow.

**Exit gate:** a material unsupported sentence or unresolved critical citation blocks
all formats; the three test subjects render identical substantive content everywhere.

### Phase 5 — Observability and durable workflow hardening

Implement:

- one trace/correlation ID across web, brain, workers and stores;
- OpenTelemetry-compatible spans/metrics/logs;
- source schema canaries;
- provider/source success and failure dashboards;
- workflow recovery and reconciliation;
- SLOs for decision-critical dimensions.

**Exit gate:** an operator can reconstruct every case activity and explain every
missing dimension without reading application logs manually.

### Phase 6 — Incremental specialist/jurisdiction migration

Move each specialist and country through:

1. source/legal acceptance;
2. adapter wrapper;
3. evidence contract tests;
4. claim mapping;
5. golden/adversarial cases;
6. policy and language review;
7. monitoring;
8. production capability declaration.

## 8. Highest-priority epics

| Priority | Epic | Why first |
|---|---|---|
| P0 | ARIA Evidence and Decision Standard v1 | Prevents building another incompatible schema |
| P0 | Source-attempt/provider ledger | Closes Brave/Claude and source-failure audit gap |
| P0 | Canonical evidence record + object hash | Makes preserved evidence the truth boundary |
| P0 | Unified claim/relationship schema | Prevents company/person and accounting-parent/UBO semantic leakage |
| P0 | Release validator in measure mode | Reveals unsupported output before enforcement |
| P1 | Decision policies and review decisions | Converts `NOT_CLEARED` into decision-specific governance |
| P1 | Canonical presentation document | Eliminates online/PDF semantic drift |
| P1 | Durable state/outbox | Eliminates partial-store and orphan lifecycle ambiguity |
| P1 | Run-local diagnostics and tracing | Eliminates global search cross-talk |
| P2 | Orchestrator layer extraction | Reduces 15k-line change risk after contracts are stable |

## 9. Migration safety rules

- Do not replace the current DD system in one release.
- Do not let legacy and new evidence independently generate conclusions.
- Run new release gates in **measure mode** first, but never use measure mode to claim
  compliance.
- Dual-write only with reconciliation and mismatch metrics.
- Every migrated adapter needs success, zero, no-match, timeout, auth, rate-limit,
  parser and malformed-response tests.
- Do not migrate a renderer before the canonical release candidate exists.
- Do not add a graph database until query/load evidence justifies it.
- Do not add Kubernetes while three Fly applications remain operationally adequate.
- Preserve historical reports as historical revisions; never silently rewrite them.

## 10. State-of-the-art acceptance criteria

The architecture reaches the target only when:

1. Every source attempt creates a terminal, immutable outcome.
2. Every material claim references preserved evidence and exact locators.
3. Every relationship uses a typed, temporally bounded, evidenced edge.
4. Every subject is resolved or explicitly ambiguous before attribution.
5. Every contradiction is resolved, disclosed or release-blocking.
6. Evidence strength and decision sufficiency are separate.
7. Every decision outcome names the policy/version used.
8. Every mandatory human review has an immutable disposition.
9. Every report is rendered from one frozen released revision.
10. Every material sentence/cell/chart datum maps to claims.
11. Every provider/model/prompt/parser/source profile is version-attested.
12. Every failure is distinguishable from zero/no match.
13. Every tenant boundary is enforced at the persistence layer.
14. Every case is reconstructible from its manifest and audit trail.
15. Every release gate is tested against absence, contradiction, injection and
    partial-failure cases.
16. Production can prove the exact build and policy versions that released a report.

## 11. Standards fit

- **W3C PROV-O** is appropriate as the conceptual/export mapping for evidence,
  activity, agent, derivation, revision and attribution provenance.
- **OWASP LLM prompt-injection guidance** supports treating retrieved source material
  as hostile data and enforcing tool/schema boundaries rather than trusting prompt
  wording alone.
- **OpenTelemetry** is appropriate for vendor-neutral correlation of traces, metrics
  and logs, but telemetry is not the immutable evidential audit ledger.
- **NIST AI RMF Generative AI Profile** is appropriate for programme governance and
  evaluation discipline; it does not replace ARIA's domain-specific legal and
  decision policies.

Primary sources:

- https://www.w3.org/TR/prov-o/
- https://genai.owasp.org/llmrisk/llm01-prompt-injection/
- https://opentelemetry.io/docs/
- https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence

## Final recommendation

Approve the blueprint as ARIA's target operating model with the refinements in §6.
Start with the evidence and decision standard, source-attempt ledger, canonical
evidence record and unified claim/relationship schema.

Do **not** begin by splitting services, deploying Kubernetes, introducing a graph
database, or rewriting the orchestrator. Those are implementation choices. The
essential change is to make evidence governance the only legal route from retrieval
to release.

The target invariant is:

> No source activity without a recorded outcome. No material claim without preserved
> evidence. No relationship without a typed edge. No conclusion without a versioned
> decision policy. No released report without a frozen revision and satisfied review
> gate.

---

## Addendum — verified corrections (2026-07-26)

This assessment was cross-reviewed. Its **architectural conclusion stands**: a strict
in-memory evidence contract exists; an evidence-governed DD system does not. 17 source
adapters and 13 `_run_*` DD layers remain wired to zero of the evidence modules — the
only consumers of `dd_evidence_standard.py` / `dd_evidence_store.py` are
`routes/aria.py` endpoints and tests.

Four factual corrections, each re-verified against the tree before being recorded here.

**1. Line reference.** `"immutable": True` is at `dd_evidence_standard.py:407`, not
`:402`. Line 402 is `def describe_standard()`. *(Verified by direct read.)*

**2. The 13/16 failure counts do not reproduce.** Measured at the stated denominator
(`pytest aria_service/tests -k dd`, parent `6c67c34d`): **11 failed, 1142 passed, 1
skipped**. The nine touched files unfiltered give **17 concrete / 14 entries**. Neither
is 13/16 — that baseline was taken at an earlier point in the session. The
*entries-vs-cases* distinction the review drew is valid; the numbers must not be
quoted.

**3. mypy/bandit are UNVERIFIABLE here, not disproven.** Neither is installed in the
project venv, so that finding could not be reproduced either way. An AST proxy finds
**3** `try/except/pass` handlers in `semantic_search.py` — lines **156, 188, 226** —
not 2. *(Verified: `ast` walk over the module.)*

**4. "UUIDs are required" is imprecise.** `_uuid_text` validates `evidence_id`,
`case_id`, `subject_entity_id` and `source_attempt_id` (`:235-239`). **`tenant_id` is
NOT UUID-validated** — it goes through `_required_text` (`:241`), and the canonical
fixture uses the non-UUID value `"tenant-test"`.

**Also note:** the zero-skips figure is environment-dependent. `fitz` and `chromadb`
are installed locally, and a module-level `importorskip` fires at **collection**, so it
counts even when `-k dd` deselects the file. Do not read "0 skipped" as a property of
the suite.

### Items closed since this assessment was written

It audited `79090fd5`; eight commits landed after it, all ancestors of the live build.
Three of its ten "indispensable" items are **done**:

- **R-F3083** (`d00351d0`) — append-only SQLite evidence store: no `UPDATE`/`DELETE`
  anywhere in the module, content-addressed artifacts via `mkstemp`+`fsync`+`os.link`,
  content hash recomputed from raw bytes and rejected on mismatch (`:204-207`),
  integrity re-verified on read (`:292-300`), idempotency via
  `UNIQUE(tenant_id, source_attempt_id)`.
- **R-F3085** (`d582f14e`) — WAL/schema init once, thread-safe singleton, SQLite off
  the event loop.
- **R-F3087** (`88ab830f`) — Claude-pinned work fails closed instead of silently
  degrading to DeepSeek.

So items 1 (append-only persistence), 2 (verified artifact hashing) and the idempotency
gap are **closed**. Items 3-9 — adapter enforcement, claim ledger, contradiction
objects, report-to-claim citation binding — remain **fully open** as described.

### Defects this addendum found and fixed

- **R-F3095** — the R-number registry had **372 `in_progress` entries with no SHA**
  (including R-F3083, whose dependant R-F3085 *was* marked). §2 was unenforced.
  `reserve_r_number.py reconcile` now derives ship state from git; 230 reconciled,
  37 body-only mentions held back for human judgement, 139 remain open.
- **R-F3096** — `gap_type="evidence_contract_violation"` (`:358`) was never registered,
  so every contract rejection logged "Unknown gap type … recording anyway".
  `test_rf2644_gap_type_registry_drift` **caught this and was red at HEAD** — the guard
  worked; the work shipped past it.
- **R-F3097** — `get_index_stats` under-reported search capability during a cold
  window (see the module docstring for why the fix is additive).

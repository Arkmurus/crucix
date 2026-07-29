# The Twenty Fundamentals as ARIA's DD root structure

**Date:** 2026-07-29 · **Status:** analysis + one shipped fix (**R-F3397**, Part 9).
**Sources:** `Top Twenty Fundamentals` (4pp, operator-supplied) — Parts 0-7;
operator pipeline design (claim-first architecture, 7 steps) — Part 8.
**Method:** every claim below is anchored to `file:line` read in this session (§22). Where I could
not establish something, it says UNKNOWN rather than inferring.

> **Reading order.** Parts 0-7 answer *what a report must contain*. Part 8 answers *what must be
> true between collection and rendering for those contents to be trustworthy*. They are the same
> programme seen from two ends: Part 1's question catalogue IS Part 8's DD Standard, and Part 8's
> four-state evidence model IS the resolution state in §4.2. Where the two differ, Part 8 wins on
> mechanism and Parts 0-7 win on scope, and the differences are reconciled in §8.6.

---

## 0. What the document actually is

It is not a list of checks. It makes three architectural claims, and only the first is obvious:

1. **A field schema.** Twenty rows that every report returns — present, absent, or explained.
   "Treat the twenty as the field schema, the rows vendors are scored against in the Overlap
   Matrix, and the checklist each report returns."
2. **A product boundary.** The `established by` column (data / supplied / hybrid) is a *commercial*
   line, not a data-quality note: fourteen rows are the automated core; five (3, 8, 16, 19, and the
   individual half of 4) are where document collection and IDV begin. "State that line plainly …
   it is a feature to state plainly, not a gap to hide."
3. **An evidence contract.** Two invariants: every ownership and screening trail **resolves to a
   natural person** ("a chain that stops at a company is incomplete"), and every finding carries a
   **source and a timestamp**. Plus one modelling rule: jurisdiction/country risk is an **overlay
   that weighs several fundamentals**, never a line item of its own.

Claims 2 and 3 are the ones that change ARIA's architecture. Claim 1 is mostly a relabelling of
things she already computes.

---

## 1. The structural fit is much closer than it looks

The document's five clusters and ARIA's five decision-readiness questions
(`dd_schema.py:2511-2653`) are very nearly the same object:

| Document cluster | Fundamentals | ARIA readiness question (`_dd_decision_readiness`) |
|---|---|---|
| Existence & identity | 1–4 | `identity` — "Verified legal identity" |
| Ownership & control | 5–8 | `ownership_control` — "Ownership and control" |
| Financial standing | 9–12 | `financial_capacity` — "Financial capacity" |
| Integrity screening | 13–17 | `sanctions_export_control` + `adverse_media` |
| Legitimacy & regulation | 18–20 | (no question — the one real cluster-level gap) |

This is the single most important finding in this analysis: **the twenty are not a new framework to
bolt on. They are the missing second level of a tree ARIA already has.** Today each cluster is
answered by one boolean derived from a handful of heuristics. The document says each cluster should
be answered by 3–5 named fundamentals, each with its own source, timestamp, and established-by.

The consequence: adopting the twenty is a *refinement*, not a rewrite. The five questions survive as
**derived rollups** over the twenty. Nothing downstream (BLUF, PDF, `structured_view`,
`decision_readiness`, the Grade-A cap at `dd_schema.py:1825-1840`) has to change its contract.

Note also that clusters 4 and 5 already split correctly in ARIA's engine but *merge* in the
scorecard: `sanctions_export_control` fuses fundamental 13 (sanctions) with 18 (regulatory status)
and export control, and R-F3244 (`dd_schema.py:2553-2580`) had to add `sanctions_evidenced` /
`export_control_evidenced` sub-flags precisely because a composite question's blocker "tells the
reader nothing about what to do". That fix is the twenty-fundamentals decomposition, discovered
one question at a time. Doing it deliberately, once, for all five is the cheaper path.

---

## 2. Coverage of the twenty against live code

Honest status per row. **COVERED** = a real adapter runs and its result reaches the report.
**PARTIAL** = runs but materially incomplete. **GAP** = nothing executes this.

### Existence & identity

| # | Fundamental | Status | Evidence |
|---|---|---|---|
| 1 | Legal existence & active status | **COVERED** | `companies_house.get_company_profile` (`companies_house.py:379`); ~30 non-UK adapters in `registry_adapters.py` (CH/NO/EE/PL/RO/TR/BR/NG/UAE/IN/SK/CZ/HU/DE/FR/AO/KE/SA/GH/ZA/IL/FI/PA/US-FL/US-DE…); GLEIF fallback (`registry_adapters.py:429`). Liveness vocabulary is multi-lingual and fails closed (`dd_schema.py:2369-2408`). |
| 2 | Verified legal identity | **COVERED** | Same, plus `identity.previous_names` (R-F3024) and `identity.lei_registration` incl. lapsed-LEI state (R-F3021, `dd_schema.py:313-318`). |
| 3 | Identity of the individual (doc + liveness) | **GAP** | No IDV vendor is integrated. `intel/vetting`-side has document intake but it is a *separate case type*, not part of a DD run. |
| 4 | Proof of address | **PARTIAL** | Entity side covered (`registered_office_address`, `companies_house.py:384`). Individual side: GAP (same reason as #3). |

### Ownership & control

| # | Fundamental | Status | Evidence |
|---|---|---|---|
| 5 | Beneficial ownership | **COVERED (with a known honest edge)** | `get_psc` (`companies_house.py:664`), PSC exemptions with an explain-the-empty helper (`companies_house.py:557,618`), UBO walker → `network.ubo_chain_walk`. R-F3027 makes an *untraversed corporate controller* fail the ownership question rather than pass it (`dd_schema.py:2486-2498`). Open Ownership API: not integrated (grep: no hits). |
| 6 | Directors, officers & controllers | **COVERED** | `get_officers` (`companies_house.py:468`), paginated to 500, officer_id preserved (`companies_house.py:450`) — which is what makes anchored traversal possible rather than name-matching. |
| 7 | Group & ownership structure | **PARTIAL → the sharpest single gap** | GLEIF is queried for `/lei-records` only (`sources/gleif.py:24,167`). **GLEIF Level 2 — direct-parent / ultimate-parent / relationship-records — is never called** (grep for parent/relationship/ultimate in `sources/gleif.py`: no matches). So "ultimate parent" is answered only when a UK PSC chain happens to yield it. `network.controlled_by_unanchored` (R-F3027) exists exactly because the anchored path runs out. |
| 8 | Authority to act | **GAP** | Exists only as an *interview question* in `active_challenge_engine.py:183-184` ("Is the signatory authority to sign EUCs … confirmed?"). No field, no requirement, no state. |

### Financial standing

| # | Fundamental | Status | Evidence |
|---|---|---|---|
| 9 | Good standing / filing compliance | **PARTIAL** | `get_filing_history` (`companies_house.py:830`) and an `accounts_overdue` flag (`companies_house.py:356`), `confirmation_next_due` (`:437`). Present as data; **not a named report row**, so a reader cannot see it was checked. |
| 10 | Financial standing & solvency | **PARTIAL** | Real and good where it reaches: SEC EDGAR XBRL → Altman Z'' (`financial_health.py:176,261`), and UK iXBRL accounts *are* wired (`financial_health.py:1338` → `companies_house.fetch_accounts_figures:971`). R-F3017 gives a *named obstacle* when accounts are scanned PDFs rather than a bare "unknown" (`dd_schema.py:2505-2510`). No credit-reference bureau (Creditsafe/Experian/D&B are registry entries in `vendor_registry.py`, not integrations). |
| 11 | Insolvency & bankruptcy history | **PARTIAL → effectively a boolean** | Only `has_insolvency_history` from the profile (`companies_house.py:417`, consumed at `:1232`). **The Gazette API is not called** — `thegazette.co.uk` appears once, as a trusted *domain* in an adverse-media allowlist (`dd_orchestrator.py:10915`). So "was there an insolvency, when, and what kind" is unanswerable. |
| 12 | Charges & encumbrances | **GAP in substance** | Only `has_charges` (`companies_house.py:416`). `/company/{n}/charges` is never called (no occurrence in `companies_house.py`). A boolean cannot tell a buyer whether a debenture sits over the assets they are about to pay for. |

### Integrity screening

| # | Fundamental | Status | Evidence |
|---|---|---|---|
| 13 | Sanctions & watchlist | **COVERED — strongest row** | Multi-list screen with per-source verdicts and `screened_at`; never-false-clean hardened repeatedly (R-F3217/3222/3227-3230; `dd_schema.py:838-848` is the fourth surface fixed). Readiness requires `verified_sources` and rejects `source_unavailable` (`dd_schema.py:2426-2429`). |
| 14 | PEP status | **COVERED** | `rca_screening.screen_with_relatives` (`rca_screening.py:143`) implements FATF R.12 — PEPs *and* relatives/close associates, with inherited-risk classification (`:90`). |
| 15 | Adverse media | **COVERED** | Dedicated sweep with template counting; readiness fails closed on an unprovable legacy blob (`dd_schema.py:2445-2470`), and R-F3060/3068 give headline/concern/advice and an individual-subject carve-out. GDELT is present in the wider source set but is not the DD adverse-media backbone. |
| 16 | Criminal convictions & enforcement | **PARTIAL** | Regulator/enforcement signal arrives via sanctions+debarment (World Bank debarred, `vendor_registry.py:187`) and media. No formal-check path (DBS etc.) — correctly so, that is the supplied side. |
| 17 | Litigation history | **PARTIAL** | `court_records.search_us_courts` (CourtListener, `:117`) and `search_uk_courts` (BAILII via an RSS proxy, `:241`). **No CCJ source** — Registry Trust is not integrated (grep: no hits), so the single most common UK counterparty judgment signal is invisible. |

### Legitimacy & regulation

| # | Fundamental | Status | Evidence |
|---|---|---|---|
| 18 | Regulatory status & attention | **PARTIAL** | `fca_register.lookup_firm` (`fca_register.py:143`) with authorised-status parsing, clone/scam-warning detection (`:54`) and postcode corroboration (`:134`) — genuinely careful. Credential-gated (`is_configured`, `:34`). FINRA/SEC IAPD and sector regulators: not integrated. |
| 19 | Source of funds & source of wealth | **GAP (by design, but undeclared)** | Defined in the discipline catalogue (`dd_disciplines.py:374,418`) and required for commodity/PEP entity types (`:1091,1129`) — but nothing executes it and nothing collects it. It is silently absent from every report. |
| 20 | Nature & purpose of business | **PARTIAL** | SIC codes (`companies_house.py:397`), RDAP domain ownership (`domain_ownership_verifier.py`, IANA-bootstrapped per-TLD), Wayback vintage (`dd_orchestrator._wayback_earliest`, R-F1636). What is missing is the *judgement*: "is the proposed relationship economically rational" — `commercial_coherence` asks a version of this for deal shape, not for the counterparty's declared activity. |

**Tally:** 6 COVERED, 10 PARTIAL, 4 GAP. The document's own estimate — "roughly fourteen of the
twenty from data alone" — is close to what ARIA could reach; she is short mainly on rows 7, 11, 12
and 17, all of which are **free or near-free APIs she does not call**.

---

## 3. Four structural defects the document exposes

These are root-structure problems, not missing adapters. Each is a known ARIA defect class.

### D1 — ARIA already has a checklist, and it is measured by proxy

`report.discipline_coverage` (`dd_schema.py:589`) is populated at `dd_orchestrator.py:13855-13885`
by mapping *layers that ran* onto *disciplines covered*:

```python
if _section_active(report.identity):
    _covered.extend(["identity_verification", "sanctions_screening", "ubo_chain"])
    _covered.append("pep_screening")            # "PEP screen typically rides on identity sub-calls"
if _section_active(report.verification):
    _covered.extend(["adverse_media"])          # verification is a triangulation layer
if _section_active(report.synthesis):
    _covered.append("financial_soundness")      # "synthesis touches financial soundness via aggregation"
```

`_section_active` returns True if the section has any red/amber finding, an info finding with a
detail ≥40 chars, **or a populated `directors` list** (`:13838-13851`). So:

- a populated director list asserts **`sanctions_screening` covered** — including on a run where the
  screen returned `screened: False`, the exact state R-F3229 had to stop rendering as "CLEAN ✅";
- an active *verification* layer asserts **`adverse_media` covered**, though that layer does no
  media search at all;
- an active *compliance* section asserts `end_use_verification`, `reexport_diversion_risk` and
  `technology_classification` covered for defence targets, whether or not export control ran.

This directly contradicts `_dd_decision_readiness`, which measures the same things honestly
(`sanctions_verified` requires `verified_sources`; `adverse_ok` requires a template that actually
searched). **Two aggregators, disagreeing, on the same question** — the exact shape of the Phase A
gate #4 / #6 fabrications recorded in CLAUDE.md §1.

Worse, the gate cannot pass. For `defence_broker`, 21 disciplines are required
(`dd_disciplines.py:1069-1079`) and the mapping above can emit at most 15 distinct ids. `source_of_funds`,
`banking_verification`, `modern_slavery_human_rights`, `cyber_data_protection`,
`nato_stanag_compliance` and `defence_offset` appear in **no** branch. So
`gate_passes = len(missing) == 0` (`dd_disciplines.py:1220`) is **identically False**, and
`coverage_pct` is capped at 71.4% (58.3% for `commodity_broker_oil_lng`). A structurally
un-closeable gate is the mirror of a structurally un-failable one, and both stop carrying
information.

And none of it is rendered. `discipline_coverage` appears in `dd_schema.py` exactly once — the field
declaration. Not in `render_markdown`, not in `structured_view`, not in `lib/reports/pdf_generator.mjs`
(its only "discipline" hits are constitution-clause labels, `:512,686`). This is the R-F3026 class:
computed, stored, dropped by every renderer.

**Implication for the twenty:** do not add the twenty as a second coverage list beside this one.
Replace this one. Otherwise ARIA has three answers to "did you check X?".

### D2 — There is no `established by` axis anywhere in DD

The document's most commercially load-bearing column has no representation in `ARKDDReport`. Every
field is implicitly data-sourced. Consequences:

- Rows 3, 8, 16, 19 and the individual half of 4 cannot be *reported as out of scope*. They are
  simply absent, which reads as "not checked" — the failure mode the document explicitly warns
  against ("a feature to state plainly, not a gap to hide").
- A customer cannot be *asked* for the supplied half. There is no place to put it if they send it.

The vocabulary already exists, unused by DD: `SourceAuthority.USER_SUPPLIED`
(`dd_evidence_standard.py:40`). And the *machinery* exists in a sibling module — see D3.

### D3 — Fundamental-level state is collapsed into five booleans

`_dd_decision_readiness` emits `ANSWERED | UNRESOLVED | INCOMPLETE | NOT_APPLICABLE` per *cluster*.
The document needs that per *fundamental*, and needs two more states the DD side has never had:
**WAIVED** (a named person decided not to pursue it) and **PARTIAL** (some but not enough).

ARIA already built exactly this — in `aria_service/vetting/`:

```python
# vetting/requirements.py:62-78
OUTSTANDING · PARTIAL · RECEIVED · ACCEPTED · WAIVED     # ordered worst-first
```

with a `DocumentRequirement` carrying `accepted: list[DocumentType]`, `min_count`, `basis`
(STANDARD / STATUTORY / HOUSE_PRACTICE / CLIENT_CONTRACT), `mandatory`, and a clause `reference`
(`vetting/models.py:131-161`); a `RequirementWaiver` that renders WAIVED and *never* renders
satisfied (`:163-178`); and de-duplication by plaintext digest so "two proofs of address" cannot be
satisfied by uploading one file twice (`requirements.py:27-31`).

That module's own docstring states the principle the twenty need verbatim:

> "Collapsing RECEIVED into ACCEPTED would be the false clean this module exists to avoid: a PDF
> nobody could read is on the file, and 'on the file' would then read as 'checked'."

**The DD side has the sources; the vetting side has the requirement ledger.** They have never met.

### D4 — Two document invariants are principles, not schema constraints

- **"Resolve every trail to a natural person."** Half-enforced, expensively, one incident at a time:
  R-F3027 blocks a false ANSWERED when a corporate PSC is untraversed (`dd_schema.py:2486-2498`);
  R-F2845 blocks the subject counting as its own owner (`dd_schema.py:1885-1931`). Both are patches
  to a *scorecard*, not an invariant on the *graph*. Nothing prevents a future producer writing a
  corporate terminal node and a future consumer reading it as complete.
- **"Timestamp and source each finding."** `Finding` gained `url` / `source_tier` / `retrieved_at`
  only at R-F2691 and they are **all optional**, deliberately (`dd_schema.py:162-185`) — with the
  comment noting the tier gate still rides on a display string (`f"{name} [from {url}]"`). So
  provenance is available but not required, and the Tier-1a gate reads a formatted label.
  `EvidenceRecord` (`dd_evidence_standard.py:173-208`) makes `retrieved_at` and
  `request_fingerprint` **mandatory** — but nothing in the DD report path emits one.

- **Jurisdiction as overlay, not line item.** This one ARIA is closest to getting right, by
  accident: R-F3098 added `context_only` / `context_kind` to `Finding` (`dd_schema.py:186-209`)
  after a sovereign-debt statistic rendered inside the compliance section and read as an adverse
  signal about the subject. That *is* the overlay principle, discovered from a live complaint. What
  is missing is the other half — country risk currently *labels* itself as environment, but does not
  **weight** any fundamental. The document says it "weighs several fundamentals rather than standing
  alone."

---

## 4. The root-structure proposal

One new module, no new layer, no new pipeline.

### 4.1 `intel/dd_fundamentals.py` — the field schema

A frozen catalogue of twenty `Fundamental` records:

```python
@dataclass(frozen=True)
class Fundamental:
    id: str                      # "legal_existence", "beneficial_ownership", …
    number: int                  # 1–20, the document's own numbering (stable, quotable)
    cluster: str                 # EXISTENCE_IDENTITY | OWNERSHIP_CONTROL | FINANCIAL_STANDING
                                 # | INTEGRITY_SCREENING | LEGITIMACY_REGULATION
    what_must_be_established: str
    applies_to: str              # ENTITY | INDIVIDUAL | BOTH
    established_by: str          # DATA | SUPPLIED | HYBRID      ← the product boundary
    tier: str                    # CDD | EDD                     ← the escalation set (14,15,16,19)
    resolvers: tuple[str, ...]   # source_ids that can satisfy it, best-first
    jurisdiction_weighted: bool  # does the country-risk overlay bite on this row
```

Three deliberate choices:

- **`resolvers` are `source_id`s, not function calls.** The fundamental says *what must be
  established*; the resolver registry says *who can establish it here*. Adding Estonia, or swapping
  Companies House for a paid tier, is a resolver edit — the twenty never change. This is also what
  makes the document's concentration warning ("Companies House appears in ten of the twenty")
  computable rather than anecdotal.
- **`established_by` is on the schema, not inferred.** A SUPPLIED row renders as
  `AWAITING_COUNTERPARTY`, never as a gap, and never as a pass.
- **`tier` encodes the escalation set** so EDD is a *derived* scope change, not a separate mode.

### 4.2 Resolution state — reuse the vetting vocabulary verbatim

```python
ESTABLISHED   evidence present, source named, timestamped
PARTIAL       some of what the row asks for (e.g. UBO chain traced 2 of 3 hops)
UNRESOLVED    a resolver ran and could not establish it        ← we looked, nothing there
UNAVAILABLE   no resolver could run (no key, no coverage, timeout)  ← we could not look
AWAITING       SUPPLIED/HYBRID row, counterparty evidence not received
WAIVED        a named person decided not to pursue it, with a reason
NOT_APPLICABLE the row does not apply to this subject type
```

`UNRESOLVED` vs `UNAVAILABLE` is not pedantry — it is `RetrievalOutcome`'s existing distinction
(`dd_evidence_standard.py:43-57`: `ZERO_RESULTS`/`NO_MATCH` are *answers*, `TIMEOUT`/`ACCESS_DENIED`
are not), which `sources/_common.py:106-137` already stamps on adapter results. Wiring it through
to the fundamental is mostly plumbing that already exists at both ends.

### 4.3 The resolution record — use `EvidenceRecord`, do not invent a third shape

Each fundamental resolves to one or more `EvidenceRecord`s
(`dd_evidence_standard.py:173-208`): `source_id`, `source_authority`, `retrieval_outcome`,
`retrieved_at`, `content_hash`, `source_url`, `effective_from/to`, `jurisdiction`. The store is
append-only and already built (`dd_evidence_store.py:80,112`). Its own docstring says the plan was
always "subsequent R-numbers will wrap individual adapters and persist accepted records"
(`dd_evidence_standard.py:8-11`). **The twenty fundamentals are the reason to finish that
sentence** — they give the records something to be *about*.

This also settles D4's provenance half without touching 127 `Finding` construction sites: a
fundamental cannot be `ESTABLISHED` without at least one `EvidenceRecord`, and an `EvidenceRecord`
cannot exist without `retrieved_at`. Provenance becomes mandatory at the level where it is
*asserted*, while staying optional at the level where it is *displayed*.

### 4.4 The five questions become derived rollups

```python
identity            = rollup(1, 2, 3, 4)
ownership_control   = rollup(5, 6, 7, 8)
financial_capacity  = rollup(9, 10, 11, 12)
integrity           = rollup(13, 14, 15, 16, 17)
legitimacy          = rollup(18, 19, 20)      # new fifth question
```

Rollup rule, and it must be the pessimistic one: a cluster is ANSWERED iff every **mandatory,
applicable, DATA-established** fundamental in it is `ESTABLISHED` or `PARTIAL`-above-threshold; a
`SUPPLIED` row in `AWAITING` caps the cluster at "awaiting counterparty" — which is a *different
sentence to the customer* from "unresolved", and is exactly the product boundary the document asks
us to state plainly.

`decision_readiness` keeps its current output shape. `blocking_reasons` gets sharper for free:
today's `"ownership/control is unresolved"` becomes `"#7 group structure: GLEIF L2 not queried;
#5 beneficial ownership: 1 corporate controller untraversed (Raven Delta Limited)"`.

### 4.5 Replace `discipline_coverage`, do not sit beside it

`dd_disciplines.DD_DISCIPLINES` (30 entries) is **not redundant** — it holds the *verification
procedures*, *common failure modes* and *why it matters* prose that the twenty do not carry. The
right move is to make each discipline declare which fundamental(s) it serves, delete the
layer→discipline proxy mapping at `dd_orchestrator.py:13855-13882`, and derive coverage from
fundamental state. The disciplines beyond the twenty (modern slavery, cyber, ESG, end-use, offset,
AIS tracking) then become **sector extensions** — fundamentals 21+ for a given entity type — which
is what the document implies when it says the twenty are the *baseline*.

Do this in the same change or not at all. Leaving both alive re-creates the two-aggregator problem
CLAUDE.md §1 spent three R-numbers killing on the Phase A gates.

---

## 5. Where this expands to — the part that matters beyond the twenty

The reason to build the schema this way is what it makes cheap *later*.

**(a) Jurisdictions become resolver entries, not report surgery.** Adding Switzerland or Kenya today
means touching `registry_adapters`, the coverage heuristics, and hoping the readiness vocabulary
recognises the status string (`dd_schema.py:2369-2383` is a hand-maintained multi-lingual token list
that has already been widened twice, R-F2803). Under the fundamental schema a new jurisdiction
registers resolvers against rows 1/2/6/9 and inherits every downstream surface unchanged.

**(b) The Overlap Matrix becomes computable.** `vendor_registry.COVERAGE_TAGS`
(`vendor_registry.py:77-89`) is 11 coarse tags — `registry`, `ubo`, `financials`, `sanctions`…
Re-express vendor coverage as fundamental ids and the document's own scoring exercise ("a vendor
contributing to none is not earning its place") runs as a query. So does the buy-next decision: rank
candidate vendors by *how many currently-UNAVAILABLE mandatory rows they would move to ESTABLISHED*,
weighted by how often those rows block real reports. That is a defensible procurement argument, and
it directly serves §17 cost discipline.

**(c) Source concentration becomes a monitored property.** The document flags Companies House
appearing in ten of twenty as "efficient but also a single point of dependency". With
`resolvers` declared, ARIA can compute her own dependency concentration, alert when a single
`source_id` is the sole resolver for more than N mandatory rows, and — more usefully — report to a
customer *which fundamentals degrade* when CH rate-limits, instead of a generic degraded banner.
This is §25 proprioception applied to evidence supply rather than to output delivery.

**(d) The escalation set drives EDD automatically.** Rows 14/15/16/19 are `tier=EDD`. The
orchestrator already auto-escalates to deep mode (`run_diagnostics` records "auto-deep escalation").
Bind that trigger to *baseline rows flagging* rather than to keyword heuristics, and the EDD scope
is the EDD rows — declared, auditable, and explainable to a regulator in one sentence.

**(e) The supplied side is where DD and vetting converge.** Rows 3, 8, 16, 19 and individual-4 are
document-collection rows. `aria_service/vetting/` already does document collection to an auditable
standard: encrypted evidence with crypto-shred retention (`vetting/crypto.py`, `retention.py`), legal
basis recorded per document (`legal_basis.py`), original-sighting tracking (R-F3189), waivers with a
named person. **A DD case should be able to open a vetting requirement set for its supplied rows**
and read the state back. That is one integration, and it converts the four "gaps" into a *product
line* — the document's point exactly. It is also the only route by which ARIA can honestly claim to
cover more than fourteen of twenty.

**(f) The twenty become the evaluation schema.** Phase A gate #1 is currently blocked on sample
volume, not capability — honesty n=0, verification n=1 against `_MIN_SIGNAL_SAMPLES=5`
(see `gate1_blocked_on_sample_volume_2026_07_28`). A per-fundamental resolution state is a *machine-
checkable label on every DD run*: for a known entity, was row 5 established, and was it right? That
is a far better eval substrate than a 500-question golden set for measuring the thing ARIA actually
sells. The same labels feed the regional-mastery heatmap (gate #2) with an honest per-cell signal:
"can she establish fundamentals 1–12 for entities in jurisdiction X" beats a reading-comprehension
proxy.

**(g) Reasoning gets a scaffold instead of a prompt.** Today the LLM is asked to reason about a
counterparty from an assembled report. With twenty declared rows, each with state, source, timestamp
and an explicit *what must be established*, the model reasons over a **structured claim ledger**: it
can be asked what is missing, what would change the verdict, and which single unresolved row carries
the most decision weight. That is the difference between a summariser and an analyst, and it needs
no new model — it needs the schema underneath it.

---

## 6. What must not happen

Given this repo's own failure history, four explicit non-goals:

1. **Never certify a fundamental by proxy.** "The identity layer ran" must not mark row 13
   established. That is D1, and it is the same class as the fabricated Phase A gates.
2. **Absent is never satisfied, and never false.** A row with no resolver reads `UNAVAILABLE` with a
   named obstacle — never a silent pass (`AWAITING` is not a pass either), and never an accusation.
3. **A `SUPPLIED` row must not be quietly dropped from the denominator.** R-F3063 correctly made
   ownership/financial `NOT_APPLICABLE` for individuals; the temptation here is to do the same to
   rows 3/8/16/19 so the percentage looks good. Those rows *are* applicable — they are awaiting. The
   completion figure must say so.
4. **One aggregator.** If `dd_fundamentals` ships, `discipline_coverage`'s proxy mapping goes in the
   same change, and a regression test asserts both surfaces read the same measure — the R-F2639
   pattern.

---

## 7. Suggested sequencing (no code written; each step needs its own R-number)

| Step | Change | Why first |
|---|---|---|
| 1 | `dd_fundamentals.py` catalogue + rollup, **derived from existing report fields**, rendered on markdown / `structured_view` / PDF | Pure addition. Immediately makes the twenty visible and shows the true coverage without touching any adapter. |
| 2 | Retire the `discipline_coverage` proxy mapping; disciplines declare their fundamental(s) | Removes the second aggregator before it can disagree. |
| 3 | Free-API gap closure: GLEIF L2 (#7), CH `/charges` (#12), CH `/insolvency` + Gazette (#11), Registry Trust CCJ (#17) | Four rows, all free or cheap, all currently answered by a boolean or by nothing. Biggest honesty gain per pound. |
| 4 | `established_by` surfacing: rows 3/8/16/19/4-individual render as `AWAITING_COUNTERPARTY` with a named collection path | Converts four gaps into a stated boundary — the document's central commercial claim. |
| 5 | Bind fundamentals to `EvidenceRecord` + the append-only store | Makes provenance mandatory where it is asserted; finishes R-F3069's stated plan. |
| 6 | DD ↔ vetting requirement-set integration for the supplied rows | The product line. Only worth doing once 1–5 make the boundary explicit. |

**Phase note (CLAUDE.md §1):** steps 1–5 are honesty-foundation work — they make the report state
what it did and did not establish — so they sit inside Phase A rather than out of phase. Step 6 is a
product expansion and should wait for an explicit operator call.

---

---

# Part 8 — The pipeline review: making the data matter

Parts 0-7 define *what a report must contain*. This part reviews the proposed restructuring of what
happens **between collection and rendering** — the layer that currently lets correct data fail to
reach the page. Verdict: **the direction is right**, and the mechanisms behind the observed failures
are identifiable in code, which shortens two steps and sharpens two others.

## 8.1 The mechanism behind the contradiction class

The proposition was that "the deterministic layer held the correct PSC data while the LLM layer
printed *no PSC data found*". The code shows the pathway, and it changes the remedy.

The DD chat path does:

```python
md = await asyncio.to_thread(report.render_markdown, concise=False)   # routes/aria.py:8725
# "Render as markdown and return as tool_context so the LLM writes its final
#  answer grounded in the structured report."            (routes/aria.py:8723-8724)
```

**The model's input is a lossy human-readable render, not the claim set.** `render_markdown`:

- truncates directors and PSCs at `[:5 if concise else 25]` (`dd_schema.py:786-794`);
- prints a `… and N more` remainder for **directors but not for PSCs** — beyond the cap, PSCs
  vanish with no marker at all;
- caps findings at `[:6/20]` and data gaps at `[:6]` (`:853-856`);
- wraps three whole blocks (evidence grade, entity scope, decision readiness) in
  `except Exception: pass` (`:706, :734, :759`).

So the contradiction class has **two** producers, not one: the model ignoring data it was given,
*and* the render silently deleting the data before the model ever sees it.

**Consequence for the design.** A consistency gate that rejects any paragraph containing an entity,
date, number or negation not resolving to a claim ID is necessary but **not sufficient**. If the
claim was dropped upstream by a display cap, the gate correctly rejects the false paragraph and the
report still cannot state the truth — it just fails silently in a new place. The gate needs a
partner rule:

> **The model reads the claim set; the rendered document is generated FROM claims. The render is
> never the model's input.**

## 8.2 This is the general form of a fix ARIA has applied one field at a time

`dd_orchestrator.py:1898-1904`, dated 2026-05-11:

> R-F287 — include explicit per-source verified-status **so the LLM renderer can NEVER fabricate
> "NOT CHECKED" claims** for sources OpenSanctions actually queried.

That is the claim-consistency gate, hand-built, for one field. The same class then recurred as
R-F3229 (a screen with no verdict rendering "CLEAN ✅"), R-F3055 and R-F3060 (adverse media a
top-level key no renderer read), R-F3026 (directors fully populated, every renderer dropped them —
*while the scorecard cited directors as its evidence*), R-F2998, R-F3012. **Six-plus point fixes for
one class** is the strongest available argument for generalising it.

## 8.3 What is smaller than it looks

**Conflict resolution.** `verification.conflicts` is exactly **one hardcoded rule** — ghost=GREEN vs
country=RED (`dd_orchestrator.py:8297-8306`) — and it already carries a `resolution` string, so the
record shape is right. There is no per-predicate reconciliation of any kind. But the tier hierarchy
already exists three times over (`_TIER_1A_SOURCE_PREFIXES` in `dd_schema.py:93-125`,
`Finding.source_tier`, `dd_evidence_standard.SourceAuthority`). The resolver is mostly wiring.

**Independence.** Better than assumed, and the stated worry is precisely the unsolved half.
`dd_independent_verifier.origin_key` (`:103`) resolves a source to a publisher family (`:73`) or a
content-story fingerprint, so five outlets republishing one story count as **one** origin; R-F3388
deliberately hardened the failure mode so an unusable family table **undercounts** rather than
manufacturing corroboration. What it does not model is **data lineage**: GLEIF and Companies House
are different publisher families and therefore count as two origins, although GLEIF's UK records
derive from Companies House. The fix is a `derives_from` edge on the source registry, not a new
independence engine.

**Recommended rule:** a derived source corroborates **nothing** about its parent. Undercounting
independence is the safe direction (R-F2666: the false-positive rate on independence must be 0).

## 8.4 Person subjects — the asymmetry, and one live defect

The proposal that natural persons become first-class subjects with a mandatory question subset is
correct. The current state is asymmetric, and one half was a live defect:

- **PSC persons ARE deterministically screened** — `_screen_psc_sanctions`
  (`dd_orchestrator.py:3611`, called `:4858`), capped at 10, honest about unperformed screens. Note
  it only began working days before this review: R-F3353's docstring records that it *"called a
  phantom entrypoint"* and had never run in production.
- **Officers/directors were NOT.** Found and fixed during this review — see **Part 9 (R-F3397)**.
- **The Disqualified Directors register is never queried.** `disqualified-directors` appears once in
  the tree, as a domain fragment in an adverse-media allowlist (`dd_orchestrator.py:10916`) — the
  same pattern as The Gazette in §2 (#11). It belongs in the mandatory person subset.

## 8.5 Four sharpenings to the proposed design

### (a) Four states are not enough — seven, and the missing three are where the lies live

`CORROBORATED / SINGLE_SOURCE / ATTEMPTED_INCONCLUSIVE / NOT_RUN` cannot express:

| Missing state | Why its absence is a lie |
|---|---|
| `NOT_APPLICABLE` | R-F3063 exists because asking an individual about "financial capacity" capped every person DD at 3/5 and read as a **deficiency in the subject** rather than a question nobody asked. |
| `AWAITING_COUNTERPARTY` | Without it the `established_by = SUPPLIED` rows (§4.1) become permanent `NOT_RUN` — a lie in the opposite direction, and the one the source document explicitly warns against. |
| `WAIVED` | A named person decided not to pursue it. A file that looks complete because someone quietly stopped asking is the failure `vetting/requirements.py` was written to prevent. |

`vetting/requirements.py:62-78` already runs five of the seven in production
(`OUTSTANDING / PARTIAL / RECEIVED / ACCEPTED / WAIVED`). Take that vocabulary rather than minting a
fourth one.

### (b) Absence must be a claim, not a missing claim

A claim record shaped `{subject, predicate, value, source, retrieved_at}` cannot say *"Companies
House returned zero PSC rows for this company at 14:02"*. If absence is only inferable from claims
that are **not there**, the system certifies by absence — the exact failure CLAUDE.md §1 documents
three separate times (gates #3, #4 and #6 each passed on the emptiness of something).

ARIA already has the honest version. `companies_house.explain_empty_psc` (`:618-657`) distinguishes
four different states that all look like "no PSC":

1. no PSC entries **and the exemption register was not checked**;
2. no PSC entries, **active exemption on file** (lawful);
3. no PSC entries, **only exemption has EXPIRED**;
4. no PSC entries **and no exemption** — a statement about the register itself.

That distinction must survive into the claim model as a **first-class negative claim** with its own
source, outcome and timestamp. Otherwise "no PSC data found" becomes unfalsifiable again by a
different route — and this time with a claim-ID gate vouching for it.

### (c) `subject_canonical_id` is stated as a given and is the hardest thing in the design

Claims reconcile only if the subject id is right. A wrong id makes conflicting claims about two
entities look like **agreeing** claims about one — a silent false clean, strictly worse than the
visible contradiction it replaces.

ARIA has the ingredients (`canonical_entity_id` on the report, `entity_resolver.py`, and R-F3024's
`previous_names`, which exists precisely because evidence gathered under an old name may belong to a
different legal entity) and a live proof of the hazard: R-F3123 records that one query, *"MITIE
FACILITIES MANAGEMENT LIMITED"*, matched six Companies House records and resolved to **two different
legal entities on two runs** — one DISSOLVED, one ACTIVE — with neither report saying the name was
ambiguous (`dd_orchestrator.py:4606-4618`).

**Rule:** resolution carries its own evidence grade, and a low-confidence resolution **blocks**
reconciliation rather than merging quietly.

### (d) Claim ≠ EvidenceRecord — keep both

`dd_evidence_standard.EvidenceRecord` (`:173-208`) is **retrieval**-level: one source attempt, with
`retrieval_outcome`, `request_fingerprint`, `content_hash`, `licence_policy_id`. The proposed claim
is **assertion**-level. One retrieval yields many claims; one claim gets re-evidenced by many
retrievals over time.

Collapsing them loses one of two things: the `RetrievalOutcome` vocabulary (`:43-57`) — where
`ZERO_RESULTS`/`NO_MATCH` are *answers* and `TIMEOUT`/`ACCESS_DENIED` are *not*, which is the entire
honesty win — or the ability to supersede a claim without re-fetching. Add `claim.evidence_ids[]`
and leave the append-only store (`dd_evidence_store.py:80,112`) alone.

## 8.6 Revised build order

Two changes to the proposed sequence.

**Pull render validation forward, from last to second.** The most frequent *live* defect class in
this repo is the renderer dropping data that was correctly collected (§8.2 lists six). A validator
asserting *every populated structured field has a rendering, and every truncation prints its
remainder* is days of work, kills a class that is live today, and is a **precondition** for the
consistency gate — because until the render stops deleting data, the gate is checking output against
an input that was already incomplete (§8.1).

**Split the claim migration.** Full migration touches ~30 adapters and ~127 `Finding` construction
sites — weeks. The specific contradiction class is killable now with the narrow version: build the
model's context from **structured fields rather than the truncated markdown**, and reject any
"no X found" assertion whenever the structured field for X is non-empty. That is R-F287 generalised
across a handful of predicates. Do the full migration once step 1 has shown which predicates carry
decisions.

| # | Step | Rationale |
|---|---|---|
| 1 | Question catalogue (§4.1) + seven-state model (§8.5a) | Fixes scorecard honesty; static artefact; days not weeks |
| 2 | **Render validator** | Precondition for step 3; kills the live drop class |
| 3 | Narrow negation/contradiction gate | Kills the contradiction class without the migration |
| 4 | Person-subject spawning | ~~ordering half~~ **shipped as R-F3397**; remainder = disqualified-directors + other-directorships + own-name media |
| 5 | Deterministic conflict resolver (§8.3) | Mostly wiring; tier hierarchy exists |
| 6 | Full claim migration + `evidence_ids` (§8.5d) | The expensive one; do it informed |
| 7 | Pattern detectors over claims | Reproducible analysis; inputs hashed |

## 8.7 The item missing from the seven steps

**The standard must be versioned onto the report, and re-grading must be an explicit event.**

If the DD Standard is versioned like code, a report records `standard_version` — and re-running an
old case under a new standard must not silently re-grade it, or a customer's delivered GREEN quietly
becomes AMBER. ARIA already has the case-file versioning machinery (`canonical_entity_id`,
`version_number`, `previous_run_id`, `version_diff` — R-F591, `dd_schema.py:592-608`) and has
already hit the problem in miniature: R-F2808's comment records that *"a delivered GREEN report
flipping to that wording reads as a retraction rather than a disclosure"* (`dd_schema.py:2587-2599`).

Bind `standard_version` into `version_diff` so a re-grade is a stated event, not a discovery.

## 8.8 On the "drier reports" trade-off

Reducing the model's role to summarising and narrating verified claims is the correct trade, and it
is **not a new one here** — the house has made it twice already, unprompted:

- R-F2413 keeps `independent_source_verification_run = False` even though a verifier does run,
  because citation grounding is not full source verification (`dd_schema.py:389-395`).
- R-F2793 renamed `CLEARED_FOR_RELIANCE` to `DECISION_READY_FOR_HUMAN_REVIEW` because the original
  *"over-claims and R-F2786's own author flagged it"* (`dd_schema.py:2700-2706`).

The house style is already *say less, prove it*. This extends an existing commitment to the prose
layer rather than introducing a new constraint — which also means the commercial repositioning
("the evidence architecture is auditable") is mostly **already true and under-stated** in the
product.

---

# Part 9 — Shipped from this analysis: R-F3397

**The officer sanctions screen ran before any officer existed.** Found while verifying Part 8's
step 4; fixed, tested and deployed the same session.

**Three faults in one inline block** (`dd_orchestrator.py:4495-4549`, pre-fix):

1. **Position.** It consumed `_directors_in = list(target.get("directors") or [])` at `:4476` — the
   directors the **caller** typed into the request — and ran at `:4503`. Companies House officers are
   not written to `report.identity.directors` until `:4765`, 260 lines later, and
   `_apply_registry_result` writes the non-GB ones elsewhere. **A DD launched from chat or the web
   button with just a company name deterministically screened ZERO officers**, while the report
   listed them in full.
2. **Never-false-clean breach.** It never checked `screened`/`error`. `screen_with_aliases` returns
   `{"screened": False, "source_unavailable": True}` when the source is unreachable
   (`sanctions.py:1345`), so an unreachable source returned no matches, fell to the else-branch, and
   emitted `"<role> <name> — sanctions screen CLEAN"` at `confidence="CONFIRMED"`. A screen that
   reached no list, certifying a named human being clean.
3. **Untrusted provenance.** It passed no `source=`, so register-sourced names arrived as
   `free_text` and went through the R-F3228 search-query shape heuristic — the gate that produced
   the R-F3217 false clean.

Faults 2 and 3 are exactly what R-F3353 gave the PSC path. The officer path was never extracted, so
it inherited none of them — which is the argument for Part 8's central claim in miniature: **honesty
rules only hold where they are structurally enforced, never where they are merely intended.**

**Fix.** Mirror the R-F3353 extraction rather than moving the call:

- `_officer_screen_candidates(report, target)` builds the set from registry officers ∪
  caller-supplied directors ∪ contact-derived names, de-duplicated by casefolded name, **registry
  first** so the cap truncates the caller's guesses and never the register's record; resigned
  officers skipped (mirrors the PSC `ceased_on` skip).
- `_screen_officer_sanctions(report, target)` applies the R-F1696 guard (an unperformed screen
  leaves `SANCTIONS_SOURCE_UNVERIFIED` and **no CLEAN finding**), declares `registry`/`operator`
  provenance, and **discloses cap truncation** rather than dropping names silently.
- Called **once**, after every registry write and **outside** the GB branch — placing it inside the
  Companies House block would leave every non-GB DD screening nobody, the same defect one
  jurisdiction over.

`source="sanctions.director_screen"` is preserved deliberately: the SAR cross-board trigger
(`:8732`) and the ACH shell hypothesis (`:8829`) key off that exact string. **Both were starved by
the defect and are fed for the first time by this fix.**

**Verification.** 14 capability tests (`test_rf3397_officer_screen_after_registry.py`); 13 failed
before the fix, 14 pass after. Includes an AST guard asserting the screen is called at a line
**after** every `report.identity.directors` assignment inside `_run_identity` — because position is
the fault, and no outcome assertion can pin it. Pass 2 regression: 183 DD/sanctions/identity test
files, **1394 passed, 0 failed**. Full-tree compile gate: 0 broken files.

**Noted, not fixed (scope):** `_run_identity_person` (`:1860,1873`) keeps the
`_screen_fn = getattr(...) or getattr(...)` pattern with a `{"matches": []}` fallback — the same
phantom-entrypoint shape R-F3353 killed on the PSC path. It is a different function on a different
subject type and belongs to its own R-number.

---

# Part 10 — What actually shipped, and what the evidence supports

Part 8's design and Part 9's single fix were written before the build. This part records what
landed, because a design document that outlives its own implementation becomes the stale line a
future session trusts (the failure mode §18 of CLAUDE.md exists to prevent).

## 10.1 The claim-first pipeline, as built

| Step (Part 8) | Shipped as | State |
|---|---|---|
| Question catalogue as the unit | **R-F3402** `dd_standard.py` — 24 questions, `STANDARD_VERSION = "1.0.0"` | Built |
| Evidence state per claim | **R-F3402** seven-state model | Built |
| Wire the catalogue into the report | **R-F3410** — replaced the `discipline_coverage` proxy with `_FUNDAMENTAL_TO_DISCIPLINES` | Built |
| Checklist reads real evidence | **R-F3426** — FS-11/FS-12/IS-16b/IS-17c read what the DD now gathers | Built |
| Scope selection | **R-F3406** (waiver model), **R-F3411** (scope reaches the engine) | Built |
| Elected sections must run | **R-F3408** — an unfulfilled election is a broken promise, not a silent skip | Built |
| Conflict resolution / pattern layer | — | **Not built** |

**The two ideas worth restating, because they are the ones that changed behaviour:**

*Declining a check is a waiver, not a toggle.* A waived section records who waived it and why, and
**stays in the denominator**. It cannot improve a score. This is the direct application of §8's rule
that absence must be represented, not deleted — the failure mode where opting out of a check makes a
company look cleaner is precisely the false clean the whole document is about.

*Electing a section creates an obligation.* R-F3408 makes an elected-but-unrun section a **defect**,
not a gap. This closes the shape the operator named directly: *"once those selections are made the
DD MUST search those, we cannot have issues."* A paid section that silently no-ops is worse than one
that was never offered.

## 10.2 The new registers — all free, all keyless or CH-keyed

| Source | R-number | Note |
|---|---|---|
| The Gazette (insolvency notices) | **R-F3403** | Free, no key |
| CH charges / insolvency / disqualified officers | **R-F3404**, wired by **R-F3422** | Existing CH key |
| UK employment tribunal decisions | **R-F3424** | Free gov.uk search API |

**Three false positives were caught before shipping, and they are the point of the exercise.** A
tribunal query returned **31,098 "results"** for a small company — the API had OR-matched the search
terms rather than the company; a Gazette query returned 20 notices of which **only 6 named the
company**; and disqualification hits match on **name only**, so they are capped at amber and carry
`match_basis: "name_only"`. Each of these, shipped naively, would have produced exactly the
name-coincidence fabrication this document argues against. **A new source is a liability until its
false-positive shape is measured.**

The Gazette adapter also needs its own HTTP client: `_common.http_get_json` sends
`Accept: application/json`, which makes The Gazette return **HTTP 500**. That is recorded in code
because it is the kind of fact that costs an hour to rediscover.

## 10.3 Measured effect

Same company, same mode, before and after: **6/19 questions answered (31.6%) → 10/19 (52.6%)**, with
corroborated claims 2 → 3. The gain is from evidence actually gathered, not from grading changes —
which is the only kind of improvement this document accepts (cf. the north-star rule that a grade
improving without new evidence *is* the false clean).

## 10.4 The exposure audit Part 9 left open — now run

Part 9's appendix listed as UNKNOWN whether any delivered report had actually missed officers.
**Measured: 5 of 5 pre-fix reports, 16 directors never screened, and no report disclosed it.** The
defect was not theoretical and the reports did not say so. This is the single strongest argument in
the document for structural enforcement over intent.

## 10.5 Test-suite integrity — R-F3433

The suite's live-network guard (R-F3319) hooked `socket.connect`. Measured over the 140-file DD set
with the guard **enabled**: **25 live DNS lookups to 9 external hosts and zero connects.** The guard
reported clean while the suite was still reaching the internet — and it is switched on specifically
to rule live I/O *out* when diagnosing a hang. R-F3433 extends it to `getaddrinfo` (IP literals stay
allowed, because `is_safe_url` legitimately resolves them to classify). Blast radius: one test,
improved rather than exempted — it now pins DNS rebinding and fail-closed resolution, which the live
version could not test at all.

**Update — the hang is now ROOT-CAUSED (R-F3439).** The paragraph that stood here said the DNS
attribution was unproven, because no stack dump had been caught mid-hang. Rather than keep waiting
for a ~1-in-3 event, the condition it needs was **reproduced**: a plugin that delays `getaddrinfo`
for external hostnames, simulating an unhealthy resolver, with every other variable held constant.

| Arm | Condition | Result |
|---|---|---|
| Control | degraded resolver, guard OFF | **273.6s**, 6 stalled lookups |
| Fix | *same* degraded resolver, guard ON | **15.4s**, 0 stalled lookups |

Same file, same six tests. **17.7×.** The file in question tests *jurisdiction-string normalisation*
and has no business touching a network at all.

On the full DD set the same condition produced the hang signature outright: ordinary tests inflated
to 45–92s (`test_valid_iso2_is_unchanged`, a string test, reached **91.68s**), only 710 of 1090 tests
finished, and **no summary line was ever printed**. That last detail is the whole reason this went
undiagnosed for so long: `pytest.ini` sets `timeout = 120`, and on Windows pytest-timeout uses the
THREAD method, which kills the process. A test slowed past 120s by a stalling resolver takes the
whole run down with no summary — indistinguishable from a hang, and impossible to attribute to the
test that caused it.

Two of my own hypotheses died on evidence first, and both are worth recording because they are the
obvious ones: there is **no thread leak** (peak 6 threads, final census 3), and it is **not
order-dependence** (`pytest-randomly` is not installed, so the `-p no:randomly` in this repo's notes
has always been a no-op).

**The remaining honest gap:** the guard is OFF BY DEFAULT, so it protects whoever switches it on,
not a default run. Flipping the default is justified by the evidence above but must not be done on
that evidence alone — the off-by-default choice was itself deliberate and measured, so it needs a
full-suite blast-radius measurement naming every test that breaks.

---

## Appendix — claims I could not establish

- Whether GLEIF L2 relationship records are reachable without credentials at ARIA's call volume:
  **UNKNOWN** (not probed this session).
- ~~Registry Trust CCJ API commercial terms: **UNKNOWN**~~ — **RESOLVED (Part 10):** Registry Trust
  (TrustOnline) charges **£6–£10 per search**, has **no free tier and no public API**. It is the
  only authoritative source of CCJ data for England & Wales; there is no free substitute, and a CCJ
  section cannot be honestly answered without it. **This is an operator spend decision, not an
  engineering one.** Until it is taken, the CCJ questions must render as a named obstacle
  (`ATTEMPTED_INCONCLUSIVE` / `NOT_RUN` with the reason), never as a clean line.
- Whether any customer-facing surface has ever rendered `discipline_coverage`:
  I found no renderer in `dd_schema.py`, `lib/reports/pdf_generator.mjs`, or the structured view.
  I did not exhaustively read `public/`.
- Which specific producer emitted the observed *"no PSC data found"* string: **UNKNOWN**. §8.1
  establishes the pathway by which either producer (model ignoring data, or render deleting it
  first) can generate it, and both are closed by the same two rules. I did not attempt to
  reconstruct the individual run.
- Whether GLEIF publishes a machine-readable lineage declaration for its Level-1 records (needed to
  automate the `derives_from` edge in §8.3): **UNKNOWN** — not probed. It can be hand-declared for
  the ~30 known adapters meanwhile.
- ~~Whether any officer was in fact missed on a delivered customer report because of R-F3397:
  **UNKNOWN**~~ — **RESOLVED (§10.4): the audit was run. 5 of 5 pre-fix reports were affected,
  16 directors were never screened, and no report disclosed the omission.**

- ~~Whether the intermittent suite hang is caused by live DNS: **UNKNOWN**~~ — **RESOLVED (§10.5):
  reproduced under a controlled degraded resolver, 273.6s → 15.4s with the guard on, and the full
  DD set produced the no-summary hang signature.** The lesson worth keeping is the method, not the
  answer: the event was too rare to wait for, so the CONDITION it needs was manufactured and the
  fix tested against it. A mechanism you can switch on and off is proven; one you merely observe
  correlating is not.

- Find Case Law (National Archives) licensing: the data is free, but the **Open Justice Licence bars
  computational analysis without a separate application**. Whether ARIA's use counts as
  "computational analysis" is a **legal reading, not a technical one** — operator decision.

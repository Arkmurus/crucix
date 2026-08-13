<!-- Cure Protocol Phase 0.4 — defect register. Hand-maintained.
     Census-derived findings cite census.json; adjudicated defects cite file:line. -->
# defects.md — consolidated defect register

**Crucix Cure Protocol — Phase 0.4** · seeded 2026-08-05
· census read local working tree at commit `5277e187`
· live `aria-intel` build_rev at seeding: `R-F3716 · sha ab637b13`

> The two SHAs differ, and it was checked rather than assumed. `ab637b13` is an
> **ancestor** of `5277e187`, and the single commit between them is
> `chore: ship-mark R-F3716/R-F3717 @ ab637b13` — an R-number registry update touching
> no `aria_service/` code. Local `HEAD` also equals `origin/main`. **Production and this
> census describe the same code**; the gap is a bookkeeping commit, not drift.

> **Severity.** P0 blocks trust · P1 misleads a reader · P2 cosmetic.
> **Status.** `ADJUDICATED` = evidence in hand, location proven ·
> `UNADJUDICATED` = named by the protocol, no evidence artifact in this repo ·
> `LEAD` = census surfaced it, cause not yet proven.

---

## A0. PARTIALLY UNBLOCKED — the ENGINEERING BRIEF has arrived (2026-08-05)

The operator supplied `ARIA_ENGINEERING_BRIEF`, landed at
`docs/ARIA_ENGINEERING_BRIEF.pdf` (+ `.extracted.txt`, since this box has no
poppler and the PDF is not greppable). It is the source of **the twelve
invariants** that Cure Protocol Appendix A binds as "unchanged and binding" but
never enumerated — they were unavailable for every prior session.

**A self-audit of this session's own shipped work against the twelve found one
violation, now fixed:**

| Inv | Rule | This session |
|---|---|---|
| 5 | Every user-visible action reports success AND failure; delivery ≠ generation | **R-F3723 exists precisely to satisfy this** — WA doc extraction reported neither |
| 6 | Domain code uses the shared abstractions, never raw DB/SDK | ✅ `cure_usage` goes through `state_store` |
| 8 | Unknown/unmeasurable state is never converted into success | ✅ `snapshot()` returns `available:false` on a read failure rather than an empty result — and R-F3734 was exactly this invariant breaking |
| 9 | Background work idempotent, restart-safe, non-blocking | ✅ no I/O on the request path; coalesced flush; no respawn task |
| **10** | **No new store without ownership, retention, erasure, backup, recovery** | ❌ **VIOLATED by R-F3730** — two keys shipped undeclared. Fixed in **R-F3736** |
| 11 | Streaming/non-streaming parity for any audit hook | ✅ the counter is HTTP middleware, so it wraps both paths |
| 12 | A deploy is complete only at live `build_rev` match | ✅ every deploy this session was verified live, not by a green build |

**Invariant 10 (R-F3736).** `crucix:cure:usage_routes` and `crucix:cure:usage_meta`
now declare owner, retention class, erasure, backup/recovery and
model-context/training eligibility. The erasure answer rests on a structural
property worth stating: **only the ROUTE TEMPLATE is recorded**, never the
resolved path, query, body, headers, caller or IP — so there is no data subject.
A regression test asserts the middleware cannot start recording `request.url.path`,
because doing so would silently convert a counter into a personal-data store.

**Still missing:** the DR-1 defect register / adjudications and the transition
runtime-capability-state-parity ledgers. §A below stands for those.

## A. Blocking gap — the DR-1 evidence does not exist in this repository

Phase 0.1 instructs: *"Reuse Spencer's audit first… Request them; do not redo them."*
**None of those inputs are present.** A repo-wide search for the transition runtime /
capability / state / parity ledgers, `ARIA_ENGINEERING_BRIEF`, the Hardening Charter,
and any DR-1 defect register returned nothing. The only near-match was
`docs/rf557_stream_chat_parity_plan_2026_05_17.md`, which is unrelated.

~~Consequently **every DR-1 entry in §B below is `UNADJUDICATED`**~~ — **SUPERSEDED
2026-08-05: 4 of the 12 are now ADJUDICATED without the register** (D-02/R-F3747,
D-03/R-F3745, D-05/R-F3746, D-06/R-F3748). The blocking claim was too broad. An entry
naming a **testable invariant** can be adjudicated against this repo's own code; only
an entry naming a **symptom or a rate** genuinely needs the missing evidence.

The remaining eight do name symptoms or rates — D-01 is "0-in-n at a chosen bound",
which cannot be derived from source at all — so for those the paragraph still holds:
seeding them with guessed module paths would manufacture exactly the kind of unfounded
certainty the Honesty clause forbids. D-03 proved the cost of a confident guess: the one
entry that HAD a confident location was specified wrongly, and implementing it as
written would have forced a false verdict.

**Action required (operator):** supply the DR-1 adjudications and the transition
ledgers, or authorise a fresh adjudication pass against real DD runs. Until then
Phase 3 cannot begin — its loop step 1 is *"write the failing fixture first"*, and a
fixture cannot be written for a symptom nobody has evidenced.

---

## B. The DR-1 dozen — 7 ADJUDICATED (2026-08-05), 5 awaiting evidence

Listed in the protocol's Phase 3 priority order. `Suspected location` is left blank
where the census could not resolve it to a defensible file — a blank is honest, a guess
is not.

| # | Defect class | Sev | Status | Suspected location | Gold fixture |
|---|---|---|---|---|---|
| D-01 | PI-leak gate (0-in-n at chosen bound) | P0 | UNADJUDICATED | — | none |
| D-02 | Matcher surname / dataset gates | P0 | **ADJUDICATED — SATISFIED IN THE LIVE PATH (R-F3747)** | live: `_sanctions_classify.py:680-687,697+`; suspected `entityMatcher.mjs` is DORMANT | `test_rf3747_dr1_d02_matcher_gates.py` |
| D-03 | Status ↔ verdict reconciliation (no GREEN over NOT CLEARED) | P0 | **ADJUDICATED — MIS-SPECIFIED (R-F3745)** | `dd_schema.py:3133`+scope_note, `pdf_generator.mjs:857-862,999,1811` | `test_rf3745_dr1_d03_verdict_readiness.py` |
| D-04 | Materiality filter (the FRC class) | P1 | **ADJUDICATED — SATISFIED (R-F3749)** | `dd_orchestrator.py:13585,12842`; `dd_disciplines.py` only INSTRUCTS | `test_rf3749_dr1_d04_materiality.py` |
| D-05 | Export-control classifier (no default "civilian") | P1 | **ADJUDICATED — ALREADY SATISFIED (R-F3746)** | `tech_classifier.py:639-640,650` | `test_rf3746_dr1_d05_export_default.py` |
| D-06 | Financial-verdict vintage (`LAST_KNOWN_WITH_AGE` or refuse) | P1 | **ADJUDICATED — REAL GAP, FIXED (R-F3748)** | `financial_health.py:347-372` | `test_rf3748_dr1_d06_financial_vintage.py` |
| D-07 | PSC second hop | P1 | **ADJUDICATED — SATISFIED (R-F3751)** | `companies_house.py:841` `walk_psc_ownership` | `test_rf3542_psc_second_hop.py` (15 tests, complete) |
| D-08 | Waiver rendering on page 1 | P1 | **ADJUDICATED — SATISFIED (R-F3750)** | `dd_schema.py:734-748,1033-1042`; `pdf_generator.mjs` has ZERO waiver refs | `test_rf3750_dr1_d08_waiver_rendering.py` |
| D-09 | Person dedup | P2 | UNADJUDICATED | — | none |
| D-10 | Findings duplication | P2 | UNADJUDICATED | — | none |
| D-11 | Telemetry / `(Phase 2)` leakage | P2 | UNADJUDICATED | — | none |
| D-12 | Truncation artifacts | P2 | UNADJUDICATED | — | none |
| D-13 | Count reconciliation, grade legends | P2 | UNADJUDICATED | — | none |

**On D-08 — ADJUDICATED, SATISFIED (R-F3750, 2026-08-05). Suspected location wrong
for the THIRD time in six.** `lib/reports/pdf_generator.mjs` contains **zero** waiver
references. Waivers are rendered in `dd_schema.py`, which builds the verdict string
the PDF prints; adjudicating against the PDF generator would have found nothing.

Satisfied by two earlier fixes that state the reasoning exactly:
- **R-F3410** (`:734-748`) — `dd_scope.waivers` is PERSISTED, not derived: *"a WAIVER
  cannot be recomputed from the evidence. 'Nobody screened this' and 'the operator
  declined the screen, by name, for this reason' look identical in the output and are
  completely different facts."*
- **R-F3411** (`:1033-1042`) — a DECLINED screen is not the same sentence as a FAILED
  one. Rendered verdict: `WAIVED by <who> — <why> (declined for this run; not a
  clearance)`. Neither a silent tick nor an unexplained gap.

The fixture asserts the rendered string (via `_source_probe`, not
`inspect.getsource`), and its last check FAILS if waiver logic ever moves into the
PDF generator — at which point D-08 must be re-adjudicated there.

**On D-04 — ADJUDICATED, SATISFIED (R-F3749, 2026-08-05).** "The FRC class" decides
the entry: the Financial Reporting Council is a **regulator**, and a
regulator/court/government finding is tier 1 ("official") in
`_adverse_finding_tier` (`dd_orchestrator.py:12842`). So the FRC class is the case
where a **single regulatory finding must be material on its own** — demanding
corroboration for a regulator is precisely how a real enforcement action gets
filtered out as noise.

`_adverse_media_materiality` (`:13585`) sets
`material = (len(official) >= 1) or (len(credible) >= _min_credible)`, so one
official-tier finding is material with no second source, while weak tiers (3
industry, 5 general) cannot move a verdict alone — the single-source
false-positive guard. **`dd_disciplines.py` only INSTRUCTS** materiality (prompt
text `:293`, schema `:312`); enforcement lives in the orchestrator. A suspected
location can be the documentation, not the mechanism.

The fixture also pins R-F3022 (a non-material sweep must not go SILENT — "matched
names, no adverse content" is a more useful statement than nothing, and silence
lets a reader assume the search never ran) and R-F3084 (raw diagnostics stay
separate from the filtered set; the PDF once called 26 RAW hits items that
"survived filtering"), plus three guards: weak-source-alone is not material, a
duplicate URL counts once, and adverse media may RAISE a verdict but never soften
a worse one.

**Two fixture premises were wrong on the first run** — recorded because the next
adjudication will hit them: `credibility_tier` is the INT web_search scale, and the
subject name must appear in the finding's **text** (`_adverse_names_subject`
tokenises content; a `subject_named` flag is not consulted). `_apply_adverse_media_
to_verdict` also requires `ok: True` — a sweep that did not succeed must not move a
verdict, which is itself correct.

**On D-06 — ADJUDICATED: a REAL gap, and the first one (R-F3748, 2026-08-05).**
Three prior adjudications found no live defect (D-02, D-03, D-05). This one did.

`financial_health`'s discipline is "UNKNOWN, not clean" — absent data never reads as
healthy (module header, ~10 explicit UNKNOWN branches). Data that EXISTS and is OLD
had no equivalent guard: `latest_fy` was recorded and **nothing anywhere compared it
to the current year** — a repo-wide search found no age arithmetic on it at all. So
a STABLE verdict computed from a five-year-old filing was returned with the same
authority as one from last quarter, and the reader had to notice the FY and do the
subtraction.

That is the same failure the module already refuses in the absent case: a verdict
claiming more currency than its evidence supports. The vintage was present; the AGE
was not.

**Fixed** by adding `latest_fy_age_years` + `financials_are_stale` to the contract,
and appending a LAST KNOWN note to `summary` — because several callers render only
`summary`, so a payload-only field would still show a stale verdict as current.

**What was deliberately NOT done:** the verdict is not downgraded by age. D-06 could
be "satisfied" by aging STABLE into WEAK; that would INVENT a financial finding, the
exact fabrication this module exists to prevent. A test asserts same-inputs ->
same-verdict regardless of age.

The fixture **fails without the fix** (3 of 5) — the only DR-1 fixture so far that
does, and what Phase 3 step 1 actually asks for.

**On D-02 — ADJUDICATED (R-F3747, 2026-08-05). The suspected location was the wrong
place to look.** Two findings:

1. `lib/aria/entityMatcher.mjs` is imported by exactly one file — its own test.
   **Zero production reach**, so the defect cannot manifest there and adjudicating
   against it would have proved nothing about production.
2. The **live** matcher is `aria_service/intel/_sanctions_classify.py`, and it
   already gates surname-only matches. Measured 2026-08-05:

| query | candidate | overlap | verdict |
|---|---|---|---|
| Ivan Petrov | PETROV, Sergei Vladimirovich | 1 | `info` (demoted) |
| Ahmed Hussein | HUSSEIN, Saddam | 1 | `info` (demoted) |
| Vladimir Putin | Vladimir Vladimirovich Putin | 2 | `hard_stop` |
| Rosoboronexport | ROSOBORONEKSPORT OAO | 0 | `hard_stop` (R-F569 bypass) |

**A hypothesis this falsified, recorded because it read as a defect.** The
meaningful-token filter (`:680-687`) excludes stopwords, corporate suffixes, digits
and GEOGRAPHIC tokens (R-F277) but **not** common surnames, and the demotion rule
tolerates a single shared token ≥5 chars — so "Petrov" (6) looked able to sustain a
match alone. It cannot: that path also requires score and string-similarity
thresholds a weak coincidence never reaches. **The code read suggested a defect;
the measurement disproved it.** The fixture therefore asserts BEHAVIOUR, not greps.

Also worth noting for whoever writes D-01's fixture: the first version of this
probe omitted `topics`, and `classify_match` then returned `info` for
**everything** — including a genuine Putin/OFAC hit. A green "no escalation
anywhere" that meant only that the probe was malformed.

**On D-03 — ADJUDICATED WITHOUT THE MISSING REGISTER, and the wording is wrong
(R-F3745, 2026-08-05).** §A says Phase 3 cannot begin because no DR-1 evidence
exists. That is true for the *adjudications*, but D-03's claim is a **testable
invariant**, so it could be adjudicated from this repo's own code rather than
waiting. The result: **implementing D-03 as written would be a regression.**

- `dd_schema.py:3133` issues `status = DECISION_READY_FOR_HUMAN_REVIEW |
  NOT_CLEARED`, and its own `scope_note` states decision readiness *"does not
  replace the risk verdict"*. Risk and readiness are **orthogonal axes by design**.
- `pdf_generator.mjs:857-862` states the renderer's rule: *"whatever ARIA found is
  what gets printed — GREEN, AMBER or RED, unaltered. This renderer NEVER
  upgrades, softens or omits a verdict, and NEVER prints a verdict without the
  decision-readiness state beside it. **A GREEN classification paired with
  NOT_CLEARED is the honest output** and both halves must appear together."*

A blanket "no GREEN over NOT CLEARED" forces one of two falsehoods: suppress a
GREEN the evidence supports, or print a worse verdict than was found. The second
is fabrication and is the precise false-confidence failure the scorecard exists to
prevent. GREEN + NOT_CLEARED means *"nothing adverse found, coverage not yet
sufficient to rely on it"* — frequently the true state.

**The real invariant is the second half of that rule: a verdict must never be
rendered without the readiness state beside it.** A bare GREEN pill reads as
clearance — the NorthRow failure `pdf_generator.mjs` cites in its own header.
That is what the fixture now enforces, and it currently **passes**, so the
correctly-specified defect is *not present*. The fixture locks it in.

**Restate D-03 before building on it.** Its four checks are negative-controlled
against the three shapes a "reconciliation" would actually take (readiness on the
assignment RHS, readiness in a guarding condition, and a braced conditional
rewrite) — the first two versions of that guard missed shapes 2 and 3.

**On D-05 — ADJUDICATED, ALREADY SATISFIED (R-F3746, 2026-08-05).** Same method as
D-03: the entry names a testable invariant, so it did not need the missing
register. `classify_export_control` does **not** assert "civilian" on a non-match.
Its no-hits branch (`tech_classifier.py:639-640,650`) emits three honesty signals
together — `recommendation = "civilian or unclassified"` (ambiguous, not an
assertion), `confidence = 0.40`, and a note *"No export-control hits — verify
classification with specific product datasheet"*.

Measured live 2026-08-05:

| input | recommendation | conf | regime hits |
|---|---|---|---|
| office stationery, paper clips, A4 paper | `civilian or unclassified` | 0.40 | none |
| 5.56×45mm ammunition + night-vision optics | `ITAR primary` | 0.85 | ML1/ML15/ML3, USML I/III/XII |

So the classifier discriminates, and "no hits" is reported as *not matched*, never
as *civilian*. **Residual risk, which is why the fixture exists anyway:** the
string contains the word "civilian", so any consumer that substring-matches,
truncates to the first word, or maps it to a boolean re-creates the false clean
OUTSIDE this module. The renderer currently prints the full string, so it is
honest today. The fixture pins all three signals, and its fourth check is a
negative control — without it, a classifier returning "unclassified" for
*everything* would pass the first three vacuously.

**On D-07 — RESOLVED and ADJUDICATED, SATISFIED (R-F3751, 2026-08-05).** The earlier
note said "a targeted grep found no match — either the logic is named differently or
the test drives it indirectly." **It is named differently.** The implementation is
`companies_house.walk_psc_ownership` (`:841`); the grep searched for *second-hop*
terminology while the code is named for the WALK. The test drives it directly, via
`_drive()` monkeypatching `ch.get_psc` and calling `walk_psc_ownership("ROOT")`.

The fixture is **complete, not partial** — 15 tests, all passing, covering: the second
hop is traversed to an ultimate owner, it walks MORE than two hops, an unanchored
corporate controller is a declared GAP (not silently dropped), a foreign registry ends
the walk WITH A REASON, and a cycle is detected and declared. Each of those is the
honest-termination property that matters: an ownership walk that stops must say why it
stopped, or a reader cannot tell "ends here" from "we gave up".


### C-11a · aria-app is the largest remaining exposure, and the register understates it — R-F3753 (2026-08-05)

Measured 2026-08-05. **aria-app is LIVE and serving**: machine `837221f7725138`
`started`, checks `1/1 passing`, on `aria-app.fly.dev` (deployed 2026-06-30). So this
is reachable, not dormant — the reachability test that cleared `chromadb`,
`body-parser` and `uuid` does **not** clear this one.

`npm audit` in `aria-app/`: **2 HIGH, and `next` alone carries 17+ advisories**
(installed `^14.2.35`, vulnerable `9.3.4-canary.0 – 16.3.0-preview.10`).

**The register said "Image Optimizer DoS".** The reachable surface is far wider,
because this app uses the features the other advisories target:
- `aria-app/middleware.ts` → **Middleware/Proxy bypass** (Pages *and* App Router) and **middleware redirect cache-poisoning**
- `aria-app/lib/actions.ts` → **SSRF in Server Actions on custom endpoints**, **DoS in App Router Server Actions**
- App Router (`app/`) → **cross-site scripting in App Router applications**, **RSC cache poisoning / cache confusion of response bodies**
- plus **HTTP request smuggling in rewrites** and **HTTP request deserialization DoS**

**npm's suggested fix overshoots.** It offers `next@16.3.0` — *two* majors. Every
advisory above is patched by **`<15.5.21`**, so **15.5.21 is sufficient** and 16.x is
unnecessary risk.

**Why this was NOT bumped here.** `next` 14→15 is coupled to a **React major**: the
app pins `react ^18.3.1` / `react-dom ^18.3.1`, and Next 15 expects React 19. That is
two coordinated framework majors on a live, internet-facing UI, and it cannot be
verified by a version string or a unit test — it needs the app exercised. Shipping it
blind would be the "fix more dangerous than the CVE" failure that R-F3743 refused for
`node-cron`. **`postcss` is a devDependency** (build-time only), so it is the lesser
half despite sharing the HIGH rating.

**Recommended sequence:** pin `next@~15.5.21` + `react@19`/`react-dom@19` together,
build, exercise sign-in (`components/signin-form.tsx`), middleware-gated routes and
the Server Actions in `lib/actions.ts`, then deploy aria-app and verify live — its own
app, its own deploy, not bundled with aria-web.

---

## C. Census-derived findings — ADJUDICATED (evidence cited)

These were found by the Phase 0.2 census and each is verified to a file and line.

### C-01 · Every Python dependency is unpinned — P1

`aria_service/requirements.txt` declares **34 packages, 34 of them with `>=`, zero with
`==`.** Two builds of the same commit can therefore resolve different package versions.

This directly weakens the control CLAUDE.md §11 relies on: `build_rev` proves *which
commit* is live, not *which dependency set* is live. It also makes the §16 suite
baseline (112 failed / 13,725 passed) unreproducible in principle — a torch or chromadb
minor bump can move it with no commit at all.

**Fix shape:** compile a pinned lockfile (`pip-compile` / `uv pip compile`) and pin the
runtime manifest; keep `>=` only in a separate input file.

### C-02 · ~~Stripe is an undeclared dynamic import~~ — **RETRACTED, NOT A DEFECT**

> **Retracted 2026-08-05, same day it was raised.** `stripe` **is** declared —
> `package.json:72`, in `optionalDependencies`. The audit that produced this finding
> read only `dependencies` and `devDependencies`, so it reported all nine
> `optionalDependencies` entries as undeclared: `stripe`, `pdf-parse`, `mammoth`,
> `xlsx`, `discord.js`, `imap`, `pino`, `qrcode-terminal`, `@whiskeysockets/baileys`.
> The census now reads `optionalDependencies` and `peerDependencies` across all three
> manifests; undeclared Node imports fell from 15 to 2. **No action required.**

### C-03 · Document-extraction degradation is DARK — P1 — **FIXED (R-F3723)**

> **Scope corrected.** This entry originally claimed the three extractor libraries were
> undeclared. They are not — they are in `optionalDependencies` (package.json:68-73),
> which is the *right* place: `npm install` does not fail when they cannot be built, so
> they are legitimately absent at runtime sometimes. That is why the `.catch(() => null)`
> guards exist, and it makes the observability gap **worse**, not better: a dependency
> that is *designed* to go missing must have a wired failure path.

`lib/whatsapp/waListener.mjs` — the **embedded aria-web listener**, live via
`server.mjs:105` (`import { mountWAListener }`). Note this is *not* the standalone
`aria-wa` service; the defect affects only this tier (see C-05).

When document extraction degraded, every branch ended at `console.warn`:
- `:3050` `pdf-parse` unavailable → **no `else` at all**, not even a log
- `:3068` `mammoth` unavailable → **no `else` at all**
- `:3095` `xlsx` unavailable → warned to console only
- the terminal `metadata only` outcome → logged, never signalled

CLAUDE.md §21a is explicit that a console line is **DARK, not wired**. The consequence
is user-visible: ARIA answers "review this contract" from the *filename* while the brain
records an ordinary document ingest. §25 proprioception cannot then answer "did I
actually read that file?", and the gap_detector→coder loop never sees a gap it could close.

There is a real backend-OCR rescue at `:3162` that fires when extraction yields
< 50 chars, so this is a *degradation*, not a total loss — but the degradation itself
was invisible.

**Fix (R-F3723):** added `classifyExtractionOutcome()` (pure, exported, total — it runs
on a user-controlled path so it never throws) and `reportExtractionOutcome()`, which
POSTs `signal_type: 'capability_gap'` to `/api/aria/brain/signal`, the sink that routes
to `capability_gaps`. All four branches now report. Only *degraded* outcomes signal, so
the healthy path adds no traffic. The duplicated literal `50` was replaced by a shared
`USABLE_TEXT_CHARS` so the classifier and the caller cannot drift apart and tell the
brain a different story than the user got.

**Verified:** `test/wa-extraction-outcome-rf3723.test.mjs`, 8/8 — run RED before the fix
(`does not provide an export named 'classifyExtractionOutcome'`) and GREEN after.
Regression: `test/wa-*.test.mjs` **160/160 pass**; `node --check` clean on
`waListener.mjs` and `server.mjs`.

### C-04 · ~~`undici` undeclared~~ — **RETRACTED, downgraded to a note**

> **Retracted 2026-08-05.** `server.mjs:1143,1168` use
> `(await import('undici')).FormData || globalThis.FormData`. `undici` is named in the
> root `overrides` block (`package.json:76`, `^7.24.4`), so its version is deliberately
> managed rather than accidental — and the expression falls back to `globalThis.FormData`
> regardless. Not a defect. The census now reports `overrides`-only packages separately
> from undeclared ones.

### C-05 · Two WhatsApp listeners — BOTH LIVE BY DESIGN, not a stale twin — P2

> **Corrected 2026-08-05.** An earlier draft of this entry read the
> `data/r_number_reservations.json:3057` title *"redo R-F853 in **CANONICAL**
> aria_wa_listener.mjs"* as proof that `lib/whatsapp/waListener.mjs` was a dead twin
> queued for deletion. **That was wrong, and acting on it would have deleted a running
> listener.** Both are live, and the code says so.

| Module | LOC | Role | Reached by |
|---|---|---|---|
| `services/wa-listener/aria_wa_listener.mjs` | 4,420 | **standalone `aria-wa` service** | `Dockerfile.wa` CMD |
| `lib/whatsapp/waListener.mjs` | 4,765 | **embedded `aria-web` listener** | `server.mjs:105` `import { mountWAListener }` |

Evidence they are deliberate, not rot: `aria_wa_listener.mjs:29-32` documents that
`WA_LISTENER_AUTO_RESPOND` is *"IGNORED BY THIS LISTENER (R-F3584). Kept only because
lib/whatsapp/waListener.mjs (embedded in aria-web) still reads it."* And `:314-316`:
*"the EMBEDDED aria-web listener still genuinely reads WA_LISTENER_AUTO_RESPOND, so the
env var is not dead globally."* The split matches CLAUDE.md §16, which isolates `aria-wa`
so a WhatsApp crash cannot take down web/auth/billing.

**The real defect is duplication cost, not deadness.** ~4.4k LOC exists twice, and a
behavioural fix must be applied to both tiers or they diverge — R-F853 having to be
"redone" in the second copy is evidence that already happened once. Divergence between
them is a live risk (see C-03, which affects only the embedded copy).

**Neither is a deletion candidate.** Any consolidation is a Phase 4.3 *duplicate
mechanism* migration — canonicalise shared logic into one module both tiers import,
one PR at a time — not an amputation.

### C-06 · An unused second WA deployment configuration — P2 (Phase 4.3)

`.github/workflows/deploy-wa.yml:95-96` deploys with `flyctl deploy --config fly.wa.toml`,
so **root `fly.wa.toml` + `Dockerfile.wa` is the canonical pair**.
`services/wa-listener/Dockerfile` and `services/wa-listener/fly.toml` also exist and also
name `aria_wa_listener.mjs` as entrypoint, but nothing in CI invokes them.

A second buildable config that no pipeline uses will drift from the one that ships and
will eventually be edited by someone who believes it is live. Deletion candidate — but
config, not code, and still subject to the quarantine ladder.

### C-07 · Singleton/lease coverage on background work is thin — LEAD

The census counts **366 task spawns** across the Python tree, of which only **22** go
through `_singleton_task` — the R-F2073 singleton-lock wrapper. The remaining spawns
have no lock visible to static analysis.

CLAUDE.md §1 records what this costs: R-F2668 found `_seed_knowledge_bg` respawn-registered
as if it were a loop, so its *normal completion* looked like a death, it was re-spawned
5x, and the resulting ERROR reset the Phase A gate-#3 clean-day streak **every boot**.
Phase 5 requires every ACTIVE loop be lease-aware, idempotent and restart-safe; this is
the inventory that work starts from. Not all 344 need a lock — many are fire-and-forget —
so this is a lead, not a verdict.

### C-08 · 31 forever-loops with no statically visible sleep — LEAD

31 of 92 `while True` loops contain no `sleep` call the census can see. A sleep may sit
in a callee it cannot follow, so these are leads. They matter because CLAUDE.md §16
already records two undiagnosed flaky tests that assert **event-loop latency** against a
hard threshold (`test_rf2144_chunked_knowledge_load`, `test_rf2200_neural_index_offload`)
— both pass in isolation, both fail under load. Full list in `loops.md`.

### C-09 · ~~No vulnerability scanning possible~~ — **CLOSED 2026-08-05**

Originally: `vulture`, `deptry`, `ruff`, `pyright`, `knip`, `depcheck`, `pip-audit` and
`npm audit` were all absent, so `deps.md` made no CVE claim — a declared gap.

**Closed.** `npm audit` needs no install and ran against all three manifests;
`pip-audit` was installed into an **isolated scratch venv** (the shared project `.venv`
is used by a peer agent and was deliberately left untouched), and when it stalled on its
advisory service, OSV `querybatch` was queried directly for the full production set.
Results are in **C-11**. The unused/unpinned halves were already delivered by AST
analysis. `vulture`/`deptry`/`knip` remain uninstalled — their job here is done by the
census, which is validated against ground truth and re-runnable.

### C-11 · Dependency vulnerabilities — the C-09 gap, now MEASURED — P1

C-09 recorded that no vulnerability scan was possible here. That gap is now **closed**:
`npm audit` ran against all three Node manifests, and OSV was queried directly for the
**143-package production Python set** (`pip freeze` inside the running aria-intel —
pip-audit stalled on its advisory service, so OSV's `querybatch` was used instead, same
data source).

#### Python — 3 of 143 production packages carry advisories

| Package | Sev | Advisory | Assessment |
|---|---|---|---|
| `chromadb==1.5.9` **(direct)** | CRITICAL | CVE-2026-45829 — pre-authentication code injection | **Exploit path NOT reachable here** — see below |
| `setuptools==78.1.0` (transitive) | HIGH | CVE-2025-47273 — path traversal in `PackageIndex.download`; CVE-2026-59890 — sdist exclusion bypass | Build-time only; not on a request path |
| `torch==2.12.0+cpu` (transitive) | LOW | CVE-2025-3000 — memory corruption via `torch.jit.script` | ARIA does not call `torch.jit.script` |

**On chromadb — critical by CVSS, not critical in this deployment.** CVE-2026-45829 is a
*pre-authentication* injection against the Chroma **server** API. ARIA instantiates
chromadb **only** as an embedded, file-backed `PersistentClient`
(`intel/rag_store.py:562`); there is no `HttpClient` and no `chroma_server` anywhere in
`aria_service`, so nothing is listening for the attack to reach. Still worth upgrading
as defence in depth, and worth a standing check that nobody introduces `HttpClient`.

**R-F3726 pinned chromadb to this version — it did not introduce the vulnerability.**
The `>=0.4.0` float had already resolved to 1.5.9 in production. Pinning made an existing
exposure *visible and addressable*; before, the running version was whatever the last
build happened to resolve.

#### Node — 6 high, 2 moderate, 1 low

| Manifest | Package | Sev | Issue | Fix |
|---|---|---|---|---|
| root | `undici` | HIGH | response desynchronisation via retry interceptor; cross-user info disclosure | available |
| root | `sharp` | HIGH | inherited libvips CVE-2026-33327/33328/35590 | semver-major |
| root | `socket.io-parser` | HIGH | zero-attachment memory exhaustion | available |
| root | `ip-address` | HIGH | leading-zero octets decoded as decimal → SSRF/filter bypass | available |
| root | `node-cron` / `uuid` | MODERATE | missing buffer bounds check (v3/v5/v6) | semver-major |
| root | `body-parser` | LOW | invalid limit silently disables size enforcement | available |
| `aria-app/` | `next`, `postcss` | **HIGH — UNDERSTATED, see R-F3753** | **17+ advisories incl. middleware bypass, HTTP request smuggling, SSRF in Server Actions, App Router XSS, RSC cache poisoning** — not just image DoS | needs next 15.5.21+ (NOT 16.x) + React 19 |
| `services/wa-listener/` | — | — | **clean** | — |

**`undici` is the one to act on first.** It is HIGH, the fix is available without a major
bump, and root `overrides` currently *pins* it to the vulnerable `^7.24.4`
(`package.json:76`) — so the version is deliberately held at a vulnerable release. Note
this refines C-04: undici was correctly *declared*, and separately is vulnerable.

`ip-address` deserves attention beyond its label on a platform doing sanctions and DD
work: decoding leading-zero octets as decimal is an SSRF-filter-bypass primitive.

**Freeze status:** `docs/cure/freeze.md` §1.1 carries a security exception, so these are
admissible under the corrective freeze. They are **not yet fixed** — each needs its own
R-number and, for the semver-major ones, a regression run.

### C-10 · Orphan stores cannot be detected without a live key-space scan — P1 (method gap)

The census finds **303 distinct `crucix:<ns>:<name>` key families** referenced in code.
It cannot find the inverse — a key family that exists **in production but is written by
no tracked module**.

That inverse is not hypothetical. CLAUDE.md §1 records gate #4 passing for months on
`crucix:aria:dd:quarantined`, a key with **no writer anywhere in the tree**: `get_json()`
returns `None` for an absent key and swallows store errors, so `[] → 0 → pass=True` was
unconditional. Three Phase A gates have now been certified by an absence.

**Therefore `stores.md` must not be read as an orphan list.** Producing one requires a
live `SCAN` of the state store, which is Phase 0.3 work.

### C-12 · The duplicate-route guard had gone blind — P0 — **FIXED (R-F3791, 2026-08-08)**

`route_audit.find_duplicate_routes` walked `app.routes` as a FLAT list. Under the
FastAPI that C-01's fix pinned (`fastapi==0.141.1`, R-F3726), `include_router` no
longer copies a sub-router's routes into `app.routes` — it appends **one lazy wrapper
per call** holding the child by reference. `getattr(route, "path", None)` returns
`None` for a wrapper, so the `if not path` guard skipped every one, and the detector
returned `{}` **for an app with 770 routes**.

Nothing raised. The boot-time check (`main.py:4783`) logged nothing and the CI gate
(`test_rf2278`) passed on an empty inventory. **A guard whose universe is empty always
certifies** — the same shape as the three Phase A gates §1 records as "certified by an
absence", and the reason R-F2278's entire failure class (a second handler silently
becoming dead code, shipped three times) had lost its detector.

**Evidence it was the instrument and not the routes**, same app, same interpreter:
`/health/live` → 200 · `/api/aria/health/perf` → 200 · POST-only
`/api/aria/brain/signal` → **405** (a 405 proves the route exists) ·
`app.openapi()["paths"]` → 723. Nothing was unmounted.

**Blast radius.** `route_audit` is the only non-test module that enumerates routes
(checked repo-wide), so no request ever mis-routed — the cost was the lost guard, plus
five test failures that read as missing endpoints. Measured at the fix: **770 routes
visible, 0 genuine duplicates**, so restoring sight raised no boot ERROR and did not
disturb Phase A gate #3's streak.

**This is C-01's predicted failure, arriving.** C-01 warned the §16 baseline was
"unreproducible in principle — a torch or chromadb minor bump can move it with no
commit at all". It was `fastapi`, and it moved five entries. Pinning made the set
reproducible; it did not make a dependency-driven baseline shift *legible*. A baseline
diff across a venv rebuild is a code-delta and an environment-delta added together,
and nothing in the process separates them.

**Fix shape (shipped):** one flattening — `route_audit.iter_routes` — recursing through
include wrappers and Mounts, applying `include_context.prefix`, preserving declaration
order so first-registered-wins stays observable. Matched by **duck-typing**
(`original_router`), not `isinstance(_IncludedRouter)`: the wrapper is private and
version-specific, so the recursion simply never fires on the older flattening FastAPI
and a future pin bump in either direction cannot re-blind it. The tests read the
**same** function via `tests/_app_probe.py` rather than a second copy — §1 records what
happens when one measure gets forked in two.

**R-F3792 — the guard now reports, and can say "I am blind".** Fixing the walk alone
would have left the same class free to recur silently, so the detector was also
§21a-wired (it previously reached the brain on NEITHER branch — `logger.error` only,
which §21a defines as DARK). It now distinguishes an empty RESULT from an empty
UNIVERSE: if routes are declared but none are enumerable, that is reported as
`boot_state_regression` and explicitly **not** certified as clean. Clean audits emit
`wire_success`; real duplicates emit a `module_bug` naming `test_rf2278` as the
reproducer, which is what R-F1857 requires before the coder will spend budget on it.
A cycle guard keyed on the **ancestor chain** (not a visited-set — that would blind
the detector to the same router included twice, i.e. a real duplicate) makes a
self-referential container terminate instead of exhausting the stack.

**R-F3794 — the environment now travels with the baseline.** `suite_baseline.py`
records interpreter + platform + a normalised `pip freeze` hash + the pins that have
historically moved the number, and the compare path prints an explicit warning when
they differ — or when the baseline predates fingerprinting, which is the state of the
committed `docs/suite_baseline.json` today. "Not captured" is reported as its own
fact rather than passing as "unchanged".

> **Residual, measured but NOT fixed here (LEAD).** The boot audit sees **754** routes
> where a post-import call sees **770**: `main.py:4783` runs it before the tail of
> `main.py` registers `/static`, `/`, and the `/download/*` routes. So ~16 routes are
> outside the boot guard's view, and `test_rf2278`'s `_build_app()` mounts only the
> aria router, so the CI gate does not cover them either. No duplicate exists among
> them today (checked). Left alone deliberately: moving the call site is a behaviour
> change and this PR is already one defect wide.

### C-13 · The "36 new failures" were mostly not regressions — P1 — **CLOSED (R-F3795…R-F3812, 2026-08-09)**

Triage of every failure the 2026-08-08 run added over the 2026-08-01 baseline. **All 36
are resolved**; 34 were real entries and 2 were a counting artefact. Not one was a
change in product behaviour that broke something — the taxonomy is the finding:

| n | Class | What it actually was |
|---|---|---|
| 6 | Instrument went blind | C-12: `app.routes` stopped enumerating (R-F3791/2) |
| 7 | **Environment, not code** | no win-arm64 wheel: chromadb, PyMuPDF, tesseract binary, sentence-transformers (R-F3795, R-F3805) |
| 7 | Test inherited a superseded default | R-F3628 flipped `_AUTH_INTERNAL_DEFAULT` to fail-closed; 6 DD tests still assumed the permissive one (R-F3800/1) |
| 5 | Test pinned a superseded policy | schema 1.2.0→1.3.0 (R-F3633), ambient-signal grading (R-F3536), open tenders (R-F3688) |
| 4 | Test asserted a spelling, not a property | source-text and URL-literal assertions broken by refactors that changed nothing they cared about (R-F3811) |
| 3 | Guard's own fixture rotted | a 400-commit search window, a positional call to a keyword-only fn, a half-uniquified fixture (R-F3796/3807/3808) |
| 2 | Counting artefact | parametrized ids truncated at the first space (R-F3809) |
| 2 | **Genuine product defects** | the adverse-media gate (R-F3802) and the missing corp suffix (R-F3806) |

**Two findings worth carrying forward.**

**1. A red test is not evidence of a regression, and here it usually wasn't.** Only two
of 34 were live product defects. The dominant class is a *guard that outlived its
premise* — the assertion stayed still while the thing it described moved. Both
directions of the session's standing defect appear: an absence read as a measurement
(the blind enumeration), and a red signal whose literal satisfaction would have
degraded the system. Three fixes would have caused real harm if taken at face value:
restoring `_AUTH_INTERNAL_DEFAULT = True` reopens cross-tenant DD read/delete;
reverting the sanctions stop list restores a false SAR recommendation; making
`test_rf3536` green by deleting `active_tender` reverses an operator ruling.

**2. R-F3802 is the one to remember.** One helper served two OPPOSITE requirements —
sanctions screening needs recall, the adverse-media relevance gate needs precision — so
a stop-list change correct for the first silently attributed a different company's
wrongdoing to a DD subject. Shared helpers across opposing requirements are a defect
class this repo should look for deliberately.

**Also fixed on the way through:** the boot thread-pool bound had **never applied**
(R-F3798 — bare `os` in a module that aliases it, NameError swallowed into a warning),
and R-F3688's comment claimed a pinning test with a `DELIBERATELY_EXCLUDED` set that
**does not exist anywhere in the tree** (R-F3810).

> **Residual, honestly stated.** Two order-dependent failures observed in sweeps and
> passing standalone (`test_rf1839` — since fixed — and
> `test_rf333_rf334::test_rf334_capability_save_then_load_round_trip`, which touches
> knowledge sharding this branch does not modify). The §16 baseline itself has **not**
> been re-measured: that needs a quiet tree and a 40-minute run, and R-F3794 now
> records the environment alongside it so the next diff can separate the two deltas.

### C-14 · ARIA crawled and absorbed pornography — P0 — **CONTAINED + ROOT-FIXED (R-F3817/R-F3820, 2026-08-09)**

Confirmed from the production machine, not inferred: `GET https://jerk-porn.com/` with
`User-Agent: ARIA-Intel/1.0 (research crawler)`. Live registry held **163 adult + 41
gambling domains, all enabled**, swept every 6h, of 22,100 rows — 21,953 of which are
tier-4 `sector='discovered'`.

**It reached the brain.** Porn page titles were absorbed as regional intelligence and
graded Phase A gate #2 mastery:
`reading_region:market_intel:lusophone → "market intel Angola Mozambique: Fake Taxi Uk
Porn Videos | Pornhub.com"`, plus `sanctions:nato → "'facesitting toilet slave' Search
- XVIDEOS.COM"`. 86 adult pages sat in ARIA's own search index.

**Root cause, one line.** `researcher.py` registered `domain_of(link)` for EVERY
external search result and **discarded the title and snippet** — the only signal that
could judge the domain. `auto_register_domain` then accepted anything
`_safe_domain_for_register` did not reject, and that checks only length, numeric
labels and RFC-2606 placeholders. With §7 (no eviction) one SEO-spam SERP buys a porn
farm a permanent row.

**The obvious fix was measured and rejected.** A substring blocklist of adult terms,
run against ARIA's OWN data, flagged `internationaldefenceanalysis.com`,
`stockanalysis.com`, `repository.essex.ac.uk`, "The Defense Post", "ASPI Strategist",
"Brazilian frigate Tamandaré … Fraterno XXXIX" and the German word **"Fersensporn"**
(heel spur) — via *anal* in AN**AL**ysis, *sex* in es**SEX**, *xxx* in a Roman numeral.
Those are core defence sources. It is also unbounded: every new farm is a new string.

**Shipped instead:** an ON-MISSION gate reusing `news_monitor._topical_relevance`, the
judge already calibrated for "security / defence / procurement / compliance" — not a
second, divergent classifier (§1 on forking one measure). Porn, gambling, Amazon and
consumer noise all score **0.0**; Rheinmetall/sanctions/peacekeeping/Companies House
score 0.25–1.0.

> **The over-correction that a naive fix would have shipped.** Gating *every* path on
> relevance breaks due diligence: measured, `Acme Ventures Ltd`, `Gazprom` and
> `Modirum Gespi` all score **zero**, because an unknown counterparty's name contains
> no defence vocabulary by construction. DD is precisely the business of investigating
> names nobody has heard of. So admission now has **two** justifications —
> `evidence` (unsolicited SERP, relevance-judged) and `requested_entity` (ARIA was
> ASKED; `guess_entity_urls` derives the URL from the name, so it cannot admit
> jerk-porn.com unless somebody researches it). Both absent → refused; forgetting to
> justify must DENY, since that omission is exactly how the original hole worked.

**Containment (R-F3817, live, no deploy):** 202 domains set `enabled=0` (rows retained
per §7, reversible), 25 knowledge facts, 86 indexed documents and 12 distill lines
archived-then-removed under `/data/quarantine/`. Legitimate intel **proven surviving**:
the SCMP child-molestation conviction, the Erena So gambling arrest, the DR Congo
peacekeeping item and Fersensporn. All 218 `web_atlas` crawl bundles kept — each lists
several legitimate domains beside one adult one.

**Residual, stated:** 5 documents / ~10 facts (mostly those bundles) / 7 distill lines
still match strong adult markers, none from the `reading_region` path. `domains_total`
grew 22,100 → 22,115 *during* the work, which is why the ingress gate — not the
quarantine — is the actual fix.

### C-14b · The 6h sweep crawled 99.3% unvetted discovery — **FIXED (R-F3821, 2026-08-09)**

`crawl_loop` called `crawl_seed_homepages()` with no limit, so every enabled domain
was fetched every cycle under `ORDER BY tier ASC, domain ASC` — the plain alphabetical
march the fly logs showed (`investors.xpinc → investors.yeti → invoicefly.com`). The
147 curated tier-1..3 seeds (OFAC, BIS, EU Commission, defence media) were interleaved
with ~21,953 speculative tier-4 rows admitted before R-F3820 existed.

Fixed by ORDER and VOLUME only: curated first and **never rationed** (a budget of zero
still sweeps every seed), then a rationed slice of discovery taken **oldest-crawled
first**. The rotation is not decoration — `ORDER BY tier, domain` is stable, so a naive
`[:budget]` would re-fetch the same alphabetical prefix forever and the tail would
never be crawled at all. Budget is `ARIA_CRAWL_DISCOVERY_PER_CYCLE` (default 500), and
a malformed value warns and falls back rather than stopping the crawl. §21a-wired: the
sweep reports its curated/discovery split, so "curated dropped to 0" becomes visible.

> **The fix that was rejected on evidence.** "Judge each crawled page and disable the
> off-mission ones" was tested against ARIA's OWN live index first and **13 of 14
> curated tier-1 sources came back off_topic** — `ofac.treasury.gov` ("Home | Office of
> Foreign Assets Control"), `bis.doc.gov`, `state.gov` ("Technical Difficulties"),
> `ec.europa.eu` ("Language selection"). It would have switched off OFAC. A homepage
> title is NAVIGATIONAL, not topical, which is exactly why R-F3820 judges a SERP
> title+snippet and never a bare domain. **Nothing is disabled, demoted or deleted
> here** (§7).

### C-15 · Residual live signals — triaged 2026-08-10, two dissolved on inspection

Three signals were reported from a live review. Verified on the box before acting;
**only one is a defect, and it is external.**

| signal | verdict |
|---|---|
| `POST /api/aria/report` → 400, repeatedly | **NOT A DEFECT.** `web_integrity_agent.py:138` declares `expected_status: 400` — it POSTs an empty body ON PURPOSE to verify input validation. The endpoint answers `{"detail":"report_type and subject required"}`, which is correct. The probe passing is why the cycle logs `9 passed, 0 failed`. |
| RSS 6735MB over the 6144MB threshold | **REAL PRESSURE, NOT CURRENTLY BREACHING.** Measured: app `pid=719` RSS **5701.5MB**, 54 threads, plus a 706MB `multiprocessing` child (encode_offload). The reported figure was a point-in-time peak. Watch, do not act yet. |
| Circuit breakers OPEN | **CONFIRMED, and worse-shaped than reported.** 5 of 50 open: `archive_is`, `wayback`, `search:duckduckgo`, `semantic_scholar`, `openalex`. That is not five unrelated sources — it is **two entire CATEGORIES dark**: archive 0/2 and academic 0/2. Search is unaffected (Brave is primary, §18). |

**The breakers are behaving correctly** — they are backing off failing external
services, which is what they are for, and §14 says a cooling provider is "operational",
not "degraded". Live health agrees: `operational`, `degraded_reasons: []`. Nothing to
"fix" in the breaker layer.

> **The open question this raises, which is NOT a breaker bug.** With archive coverage
> at 0/2, a DD report that would have cited an archived snapshot now simply has none.
> R-F3529 established the pattern for exactly this — when OpenSanctions was quota-spent,
> local canonical lists became the FLOOR and the DD line said so rather than reading
> clean. There is no equivalent floor beneath the archive category, and no check that a
> report DECLARES archive evidence was unavailable. That is the honest gap: not that a
> source is down, but that its absence may be silent. Left recorded rather than fixed —
> it is a report-honesty change, not an incident.

### C-17 · The §16 CI gate is WIRED and PROVEN FIRING — with two known flaky tests

R-F3826 put `scripts/admin/suite_baseline.py` (R-F3373) into CI as its own
`suite-baseline-gate` job. This repo's own record says *"R-F3373 shipped a gate nobody
had seen fire"*, so it was made to fire before being called done.

**Proven, on a dispatch run against the committed CI baseline:**

```
165 failed, 14577 passed        VALID=YES  (totals identical to the baseline)
*** ENVIRONMENT CHANGED SINCE THE BASELINE ***
  packages 077f0d71359f2d0b -> 2adcfcef16206c33
FIXED since the baseline (1):  test_rf3768_tooluse_dpo_cycle::…paid_artifact_recovery
NEW FAILURES (1):            ! test_store_fact_skip_rag::test_store_fact_default_runs_rag_ingest
```

Three things that all worked as designed: the gate compares SETS not counts (identical
totals, yet it still caught a 1-for-1 swap); R-F3794's environment warning fired on a
package-set change **between two CI runs two hours apart**, which means
`requirements-ci.txt` pins direct dependencies but transitives still drift; and the
run was self-validating (`VALID=YES`).

> **KNOWN FLAKY, and the gate will fail intermittently until they are fixed.**
> `test_store_fact_skip_rag::test_store_fact_default_runs_rag_ingest` and
> `test_rf2507_brain_queue_integration::test_drain_failure_retries` were BOTH flagged
> as order-dependent in the 2026-08-09 local §16 diff, both pass standalone, and CI has
> now independently confirmed the first flipping on a different platform. They are
> therefore genuinely flaky, not platform artefacts.
>
> **Do not silence this by muting the gate** — a gate people mute protects nothing,
> which is the lesson the advisory test step already records. Fix the two tests, then
> re-record the baseline. Until then an occasional red `suite-baseline-gate` on an
> unchanged tree is EXPECTED and is not a regression; check the named test against this
> list before hunting a commit.

**Cost, measured not estimated:** 18m21s on `ubuntu-latest`, against ~40 minutes for
the same run on the win-arm64 dev box.

### C-18 · The primary search backend served NOISE as success — P0 — **FIXED (R-F3844)**

**This is the root cause; C-14's porn was a symptom.** Reproduced live from inside
aria-intel, 2026-08-11 — the same DD query four times, two seconds apart:

```
'"Rolls-Royce Holdings plc" owner OR shareholder OR "beneficial owner"'
  run 1  "Oversæt dokumenter og websites - Google Help"   (Danish)
  run 2  "Nova Launcher FAQ"                              (n=40, not 10)
  run 3  "Confused about HL SIPP Interest — MoneySavingExpert"
  run 4  "Outlook"
```

Four identical inputs, four unrelated result sets, all `engine=bing`. The query has
**zero** influence — which rules out query mangling, because a deterministic bug
returns the same wrong answer twice. SearXNG was serving result sets belonging to
other queries. The instance is comprehensively degraded: **14 engines carry errors** —
`google` CAPTCHA, `mojeek`/`qwant` access-denied, `duckduckgo` ConnectTimeout,
`brave.news` TooManyRequests, and `bing` — the one still answering — ReadTimeout.

**The defect is the SILENCE, not the outage.** A backend that could not answer
returned ten well-formed results with `ok: True`, no error and no degraded flag, so
nothing downstream could tell intelligence from noise. Every domain in those sets was
then auto-registered permanently, which is how porn and gambling farms entered the
crawl registry. Fixing the registry without this treats the stain, not the wound (§1).

**Fixed** by a deliberately conservative gate: noise is declared only when NOT ONE
result bears ANY lexical relation to the query — one match anywhere passes the whole
set. It catches "answered a DIFFERENT question", never "answered badly", because a
search gate that editorialises about quality would eventually suppress real
intelligence. It returns "cannot judge" for empty sets and operator-only queries.
Noise now returns `ok:False` + a stated reason and is §21a-wired as
`search_backend_failure`. **Live-verified end-to-end** on the real degraded backend:
`ok:false, error:"noise: query-independent result set", discarded:10`.

> **STILL OPEN — infrastructure, not code.** The SearXNG instance itself is degraded.
> Either restore upstream engines or point `SEARXNG_URL` at a healthier one. ARIA no
> longer *trusts* the noise, which was the dangerous half.

### C-18b · The SearXNG noise is NOT the engine set — engine fix tried and MEASURED as failed

R-F3849 re-tuned the engine list on live evidence (R-F1659's June "datacenter-tolerant"
set had rotted: google CAPTCHA, mojeek/qwant/wikidata access-denied, only bing
answering) and deployed it. **Then measured. It did not work.** The same query four
times still returns four unrelated results — "Corrector Castellano", "Stock Exchange
of Thailand", "WhatsApp Web", "On This Day" — now via `bing` + `google cse`, a variant
the disable did not cover.

**So the engine set was never the cause.** The instance is mismatching responses to
requests. Leading hypothesis, UNTESTED: response mixing under concurrent load
(`ARIA_SEARXNG_CONCURRENCY=4` plus the autonomous loop hitting it continuously), which
would explain a *sequential* probe receiving another query's answer.

**Deliberately NOT disabled.** R-F3844 discards the noise per-query while letting good
responses through (3 of 11 probes were clean), so a blanket kill loses the working
queries for no gain. The engine change still removes four dead engines' latency.

> **The protection is R-F3844 + R-F3847, not this config.** The config comment carries
> the re-measure command and says plainly that the list will rot again — a
> datacenter-hosted metasearch scraping consumer engines is a decaying asset.

### C-19 · Noise reached a CUSTOMER-FACING DD report — P0 — **REMEDIATED (R-F3849)**

Full sweep of all 29 stored DD reports: **exactly one** carried an off-mission
citation — `dd_92f9d77b8886` (Silverbrook), `.digital.press_coverage[3]` =
"Télécharger et installer Google Chrome" (`support.google.com/chrome/answer/95346`).
It was tagged `source_tier: UNVERIFIED`, so it was never presented as verified
evidence — but it does not belong in a press-coverage section.

Remediated surgically rather than by re-running the DD (a re-run changes content far
beyond the defect and burns budget for no integrity gain): the FULL original report
archived to `/data/quarantine/rf3849_*.json` first (§26), the single citation removed
(13→12 entries), and an `amendments` entry written **onto the report** — an amended
deliverable must say it was amended. Re-verified through the live API: 29 reports, **0
off-mission citations**.

### C-19-orig · Noise reached a CUSTOMER-FACING DD report — original finding

The severity question the investigation left open, now answered with evidence. Of 29
stored DD reports, none cite an adult/gambling domain — but **benign noise did reach
the narrative**:

```
dd_92f9d77b8886  "Silverbrook Capital Management"
  .digital.press_coverage[3].url =
     https://support.google.com/chrome/answer/95346?hl=fr
```

A **French Chrome cookies help page cited as press coverage.** `support.google.com`
was one of the exact noise results reproduced above.

Mechanism, read in code rather than inferred: `web_search` SORTS by relevance
(`web_search.py:1903`) but never FILTERS, and `_apply_domain_diversity_cap` truncates
only the tail — so when the other backends return nothing (14 engines erroring) noise
fills the top-N and reaches the report.

**So this is a product-integrity issue, not hygiene.** R-F3844 protects new reports.
**Operator decision:** whether `dd_92f9d77b8886` is re-run and re-issued. Also worth a
wider sweep of `press_coverage` across historic reports — one was found by targeted
markers, which is a floor, not a count.

### C-16 · CI was red on every commit — two secret checkers, one file — **FIXED (R-F3827)**

The `test` job failed on every recent push (`cba53e22`, `c15e8ec8`, `c698756d`,
`ace08f84`), blocking the pipeline for everyone. The finding was the secret scanner's
OWN fixtures in `test_rf3720_secret_scan_gate.py`.

`scripts/admin/secret_scan.py` accepted them via its hash-keyed baseline (27 fixtures)
and reported CLEAN; `scripts/pre-commit --check-all` uses an at-the-site pragma and
failed on the same three values. Both mechanisms are deliberate — the pre-commit
comment argues for its own ("people delete baselines; a file declaring itself is
auditable"). This was not drift: **one file was declared to only one of them.** Fixed
with the existing `allowlist-secret-file` pragma its two sibling fixture files already
carry (R-F3683), not a new mechanism, and not three per-line annotations that would
fail again on the fourth fixture.

---

## D. Phase 0.3 runtime overlay — **WINDOW OPEN as of 2026-08-05 12:20 UTC**

> **Status change.** The instrumentation is LIVE (R-F3730 + R-F3734,
> `build_rev R-F3734 · sha b9db7221`) and the 14-day clock is running.
> **Earliest date the runtime proof can be considered complete: 2026-08-19.**
>
> Live-verified end to end, not merely deployed:
> `observed_routes: 43 · total_requests: 1241 · flush_failures: 0`,
> read from `GET /api/aria/cure/usage`.
>
> **A defect shipped and was caught by that probe.** R-F3730 wrote counts with
> `hincrby` (a hash) and read them with `get_json` (a JSON blob), so the surface
> reported `observed_routes: 0` while `flush_failures: 0` and a valid
> `last_flush_epoch` proved the writes were landing. A blind read here is
> indistinguishable from "nothing was ever observed" — it would have made every
> route in the estate look unobserved for 14 days and the whole census read as
> safe to delete. Fixed in R-F3734 with a round-trip regression test.
>
> **Deletion is still NOT authorised** and the endpoint hardcodes
> `deletion_authorised: false`. Collecting evidence is not the same as having it;
> the window must actually elapse. Everything below records why.

### Original entry (retained — this is what the gate looked like before)

Phase 0.3 requires 14 days of access logs, loop execution records and sensor data
overlaid on the census, tagging everything ACTIVE / DORMANT / DEAD-CANDIDATE. **It has
not been run.** Every classification in `modules.md` therefore carries:

```
proof_static:  REFERENCED | UNREFERENCED | INVOKED | SHIPPED | SERVED-ASSET | SCRIPT-ONLY
proof_test:    TESTED | UNTESTED
proof_runtime: UNKNOWN-PHASE-0.3-NOT-RUN     <-- always
```

Under the Phase 4.1 three-proof rule, one missing proof means the candidate **stays
DORMANT**. So the 109 DEAD-CANDIDATE modules are *not* deletable, and no quarantine
batch can be authorised from this census alone.

**What is available:** `flyctl` is installed and authenticated; `aria-intel` answers
`/health/live`. Fly's log retention is short and is **not** a 14-day access record, so
the overlay needs a deliberate collection window opened now — it cannot be reconstructed
retrospectively.

**Investigated 2026-08-05 — there is NOTHING to simply switch on.** The hope was that
existing telemetry could serve the overlay, making this a configuration change rather
than new code. It cannot:

- `aria_service/main.py` registers **exactly one** HTTP middleware — `_limit_body_size`
  (`main.py:4725`), a Content-Length cap. There is no request/route counter.
- A repo-wide search for `route_hit` / `endpoint_usage` / `request_count` / `access_log`
  / `usage_stats` / `route_metrics` matched **one** file (`routes/vetting.py`) and
  nothing general.

So none of the 782 FastAPI or 536 Express routes records that it was called.

**The blocking chain, stated exactly:**

```
delete anything  →  requires 3 proofs (Phase 4.1)
                 →  runtime proof requires the 0.3 overlay
                 →  overlay requires usage instrumentation
                 →  instrumentation must be DEPLOYED to observe production
                 →  deploying a cure PR requires green e2e smoke (Phase 2.3)
                 →  that smoke does not exist (Phase 2 unbuilt)
```

**This terminates at an operator decision, not at more engineering.** Either the Phase
2.3 smoke gets built first (protocol order, slower), or the instrumentation deploy is
authorised as an exception ahead of it (faster, and the observation window starts
sooner). Both are legitimate; the choice is the operator's because it trades protocol
order against 14 days of calendar time on the critical path.

Note the instrumentation itself is protocol-sanctioned: Phase 4.2 step 1 is precisely
*"add an entry-point counter/log line; deploy; observe 14 days."* The freeze does not
forbid it. What blocks it is the deploy gate, not the code.

---

## E. Proposed Phase 3 PR order

Ordered by trust damage and by what is actionable today. Revised after C-02 and C-04
were retracted — two of the four items originally listed here were not defects at all.

1. ~~**C-02** — declare `stripe`~~ — **RETRACTED**, already declared.
2. **C-03** — ✅ **DONE (R-F3723)**. Degradation wired to the brain; 8/8 capability
   tests, 160/160 WA regression. Committed but **not yet deployed** — see §G.
3. **C-01** — pin the Python dependency set. Now the top *open* item: it is a
   prerequisite for any trustworthy suite baseline or fixture run, and therefore for
   Phase 2's gold set.
4. ~~**C-04** — declare `undici`~~ — **RETRACTED**, managed via `overrides`.
5. **C-06** — remove the unused second WA deploy config, via the quarantine ladder.
6. **D-01 (PI-leak)** — first DR-1 item, **blocked on §A evidence**.
7. **D-02 / D-03** — matcher gates, status↔verdict, **blocked on §A evidence**.

### Lesson recorded

Two of four census findings in this section were false, from a single cause: an audit
that read `dependencies` and `devDependencies` but not `optionalDependencies`. Both were
caught only by opening `package.json` before writing the fix. A census is a hypothesis
generator; §22 already requires reading the file at `file:line` before asserting cause,
and that step is what caught this. The census engine has been corrected so the class
cannot recur, and `deps.md` regenerated.

## F. Proposed first Phase 4 quarantine batch

**None may be authorised yet** — §D. When the runtime window closes, the first batch
should be `scripts/train/` (18 DEAD-CANDIDATE modules, ~3,100 LOC), because it is
self-contained, operator-invoked rather than service-invoked, and its failure mode is
loud (a cycle that will not launch) rather than silent. `services/wa-listener/*.mjs`
must **not** be in an early batch: nine of those files were misclassified as dead by the
first census pass purely because their `test_*.mjs` naming was not recognised.

---

### C-18 · Node/JS tier security audit — 10 findings, all FIXED (R-F3831…R-F3840, 2026-08-10)

A read-only security audit of the Node tier (`server.mjs` ~330 route registrations,
`lib/**`, `middleware/rateLimiter.mjs`, both WhatsApp listeners, `public/**`) produced
10 findings. Every one was re-verified at the cited `file:line` before any code was
written; the audit's own withdrawn items (`/api/aria/session/forget`, `/api/aria/report`)
were left withdrawn.

**Authorisation.** Batch A (items 1–4) is admissible under freeze §1.1's security
exception — confirmed vulnerabilities with known exploit paths. Batch B (items 5–10) is
hygiene and does **not** qualify on its own; it proceeded under an explicit operator
override given 2026-08-10 ("proceed with these items… follow strict protocol"), recorded
here as the audit trail §26/§10 require. One squashed commit, per the same instruction.

| # | R-number | Finding | Severity |
|---|---|---|---|
| 1 | R-F3831 | Path traversal in the three `/api/aria/conversations/:sessionId` proxies, carrying the brain service token | P0 |
| 2 | R-F3832 | Same traversal into the WA listener's internal API, carrying `ARIA_INTERNAL_TOKEN` | P1 |
| 3 | R-F3833 | Localhost bypass keyed off the forgeable `req.ip` at **five** gates | P1 |
| 4 | R-F3834 | The 2FA pre-auth token was a fully valid session token | P1 |
| 5 | R-F3835 | Password change/reset did not revoke live sessions | P2 |
| 6 | R-F3836 | Account enumeration on verify-email / resend-verification | P2 |
| 7 | R-F3840 | `script-src 'unsafe-inline'` | P2 |
| 8 | R-F3837 | Unsanitised `runId` in a `Content-Disposition` filename | P2 |
| 9 | R-F3838 | `/s/:token` share links could never redeem — and reviving the route exposed two unvalidated `href` sinks | Low |
| 10 | R-F3839 | XSS pass over the three files the audit sampled but did not clear | Follow-up |

**Three things the audit did not have, found while fixing:**

1. **A fifth `req.ip` bypass — `requirePageRole` (`server.mjs`).** The audit named four
   sites; sweeping every loopback literal in the tier found a fifth, and it is the one
   that renders operator/infra PAGES (vault, aria-brain, admin) to an unauthenticated
   6PN peer. Found by grep, not by reading the audit.
2. **The eco-card `onclick` had never fired.** `aria-brain.html` built an inline
   `onclick` by concatenation with a raw node id. CSP has set `script-src-attr 'none'`
   since R-F1919 — whose comment claims *"every served page's handlers were migrated to
   delegated addEventListener first, so nothing breaks"* — so the drill-down and the
   breadcrumb nav were **silently dead in production**, and the injection sink was
   unreachable for the same reason. Both are now delegated listeners, which fixes a
   latent vulnerability and a broken feature in one change.
3. **Reviving a dead route is shipping a new one.** `/s/:token` (item 9) is an
   UNAUTHENTICATED public page whose two `href` sinks were escaped for attribute
   breakout but not scheme-validated — `javascript:` contains no quote and survives
   `escHtml` untouched. Fixing the guard without fixing those would have turned a dead
   feature into a live XSS. `lib/util/safeUrl.mjs` now gates both.

**The exploit for item 3 was reproduced, not argued.** `test/localhost-bypass-forgery-rf3833.test.mjs`
stages a genuine non-loopback TCP peer (binds `0.0.0.0`, connects to the host's own LAN
address) and drives the pre-fix gate verbatim: `X-Forwarded-For: 127.0.0.1` → **200,
bypassed**; same peer without the header → 401. The fixture is retained so the claim
stays falsifiable.

**Item 7 was closed by hashing, not by externalising.** `script-src` now names a
`'sha256-…'` per inline block, computed **at boot** from the files about to be served.
That detail is load-bearing: browsers hash exact bytes, and with no `*.html` rule in
`.gitattributes` these files are CRLF on a Windows checkout and LF in the Linux image, so
a checked-in hash list would be correct locally and blank every page in production.
Hashes and `'unsafe-inline'` are mutually exclusive, so a missed block is a dead page,
not a partial weakening — hence a fail-open branch when the scan finds nothing, the
`ARIA_CSP_ALLOW_INLINE_SCRIPT=1` escape hatch, and a test asserting coverage of every
block in every file rather than a sample. Verified end-to-end against a booted server:
34 hashes, `'unsafe-inline'` absent, and every inline block in six real served responses
hashed from the RESPONSE BYTES and found in the live header.

**The "not claimed" caveat is now CLOSED (R-F3845, 2026-08-11).** C-18 originally
shipped saying `aria-brain.html` still interpolated ~230 telemetry values "reviewed as a
class, not proven one by one". That sentence has been replaced by a measurement and a
gate — see C-19 below.

**Residual, bounded and pinned by a test:** `public/index.html` loads jQuery 2.1.1, whose
`globalEval` evaluates script by creating an inline `<script>` element — which the
hash-only policy blocks where `'unsafe-inline'` would have allowed it. That path is
reached only when jQuery is handed markup containing a `<script>` tag; no such markup
exists in the page or its theme scripts today, and a test now fails if one appears.
`public/vendor/jquery.min.js` is a second, orphaned copy that no page references; a test
asserts it stays unreferenced.

---

### C-19 · The C-18 XSS residual, measured and gated — **CLOSED (R-F3845, 2026-08-11)**

C-18 closed ten audit findings and left one sentence open: `aria-brain.html` still
interpolated ~230 telemetry values that had been *"reviewed as a class, not proven one by
one"*. That is an impression, not evidence, and it is the shape of finding this register
keeps recording as a false pass. It is now a measurement.

**The result, for the three audited pages:**

| Page | HTML style | Escaped | Raw (named) | Unescaped |
|---|---|---|---|---|
| `public/aria-brain.html` | template literals | 213 | 18 | **0** |
| `public/dd-reports.html` | string concatenation | 96 | 19 | **0** |
| `public/dashboard.html` | both | 41 | 8 | **0** |

143 interpolations were escaped in this change. The remainder were already escaped, or
are on an allowlist **named expression by expression** in
`test/html-interpolation-guard-rf3845.test.mjs` — each one a fragment assembled from
already-escaped parts (`sens`, `unmapped`, `critBadge`, `seg(...)`, `memBits.join`, the
eco-card grid, the capability-gap chips).

**The invariant, chosen deliberately:** *every interpolation is escaped unless it is
named raw.* Not *"escape the ones whose data looks external."* Provenance reasoning has
to be redone every time the brain's API changes, and the person changing the API is not
the person reading this file. A uniform rule with a short exception list is checkable;
a judgement call is not.

**Two real defects surfaced only because the analysis was mechanical:**

1. **`${g.tier}` was unescaped** inside the capability-gap chip renderer and had been
   invisible to every previous pass — including the first version of this one. The outer
   expression is a `.map()` that legitimately emits markup, so it classified as *raw* and
   nothing looked inside it. Nested interpolations are now recursed into.
2. **`avatarLetter`** (`dd-reports.html`) put one character of an entity name straight
   into markup. A lone `<` cannot open a tag so it was not exploitable — escaped anyway,
   so nobody has to re-derive that argument.

**Three ways the analyser was wrong before it was right** — all caught by the existing
render tests or by the gate itself, none by reading:

- It escaped `sens`, a variable that HOLDS markup, and broke the sensor banner
  (`aria-brain-sensor-labels-rf3352` caught it). Raw-ness is a property of the
  DECLARATION, not of the expression text.
- Scanning a declaration to its terminating `;` without skipping string literals stopped
  inside `color:#dc2626;font-size:…`, so `critBadge` — which emits a `<span>` — read as a
  plain value and would have been double-escaped.
- An array's declaration is `[]`, which says nothing; `memBits` only reveals itself
  through its `.push()` calls.

**A correction to this register.** While triaging the concatenation operands I recorded
`why` on `dashboard.html` as a live unescaped sink. **That was wrong.** There are two
variables of that name: the API-derived one (`s.why_it_matters`, line 348) is escaped at
its use, and the one rendered raw is a different local assigned only string literals and
`escHtml`-built text. No sink existed. The concatenation pass found **zero** live
vulnerabilities across all three pages.

**The gate has been PROVEN TO FIRE**, per the C-17 lesson about gates nobody has watched
work: re-introducing `${nm}` unescaped on the eco-card made the test fail and name it —
`aria-brain.html has 1 UNESCAPED interpolation(s) inside HTML: line 1569: ${nm}`. It also
asserts the analyser can still SEE (>150 escaped on aria-brain, >60 concat operands on
dd-reports), because a classifier with an empty universe certifies everything.

**What this does NOT cover, stated so nobody reads it as more than it is:** interpolation
into HTML only. It says nothing about `javascript:` URLs (that is `lib/util/safeUrl.mjs`
and the R-F2607 client helper), inline event handlers (CSP `script-src-attr 'none'`, plus
the R-F3839 sweep), or the other ~29 pages under `public/`, which were outside the
audited scope and remain unmeasured.

---

### C-20 · The interpolation guard now covers EVERY page in public/ — **R-F3850, 2026-08-11**

C-19 closed the three audited pages and stated plainly what it did not cover:
*"the other ~29 pages under `public/`, which were outside the audited scope and remain
unmeasured."* They are measured now.

**Result across all 32 served pages** (`pelican/` and `vendor/` excluded — vendored
third-party themes, bounded separately by the R-F3840 tests):

```
template-literal interpolations inside HTML :  0 unescaped
concatenation operands adjacent to markup   :  0 unjustified
```

103 further interpolations escaped on seven pages — `vetting.html` (48), `explorer.html`
(33), `opportunities.html` (7), `wa-connections.html` (6), `bd-intelligence.html` (4),
`vault.html` (4), `admin.html` (1) — each wrapped in **that page's own** escaper. The 85
remaining concatenation operands are justified by name, per page, in the guard.

**The guard now DISCOVERS its pages** rather than reading a hand-kept list. A fixed list
is the same defect as an empty universe: the page added next month is not on it and is
never checked. Proven by injecting an unescaped sink into `sources.html` — a page that
was not in the original scope — and watching the guard name it:
`public/sources.html has 1 UNESCAPED interpolation(s) inside HTML: line 267: ${o.evil}`.

**Five analyser defects had to be fixed before the numbers meant anything.** Every one
would have produced a confident wrong answer:

1. **Three escaper conventions were invisible.** The classifier knew `escapeHtml`/
   `escHtml` but not `esc` (vetting, leads, design-partners) or `escText`/`escAttr`
   (dd-reports, watchlist, vls-chain). `vetting.html` reported **146** unescaped sinks;
   96 of them were already escaped with `esc()`. A fixer driven off that reading would
   have double-wrapped 96 call sites and printed `&amp;lt;` to users. The real figure
   was 50.
2. **`stripLineComments` shortened the source.** Offsets computed on stripped text do not
   address the original file, so the fixer would have written into the wrong place —
   silently, because the result is still valid JavaScript. It is length-preserving now
   (comments become spaces), and the fixer refuses to run if that ever stops holding.
3. **A ternary with escaped branches read as unescaped.** `cond ? escapeHtml(x) : 'none'`
   does not START with an escaper. `isFullyEscaped` now strips escaper calls, literals
   and numbers and asks whether any identifier survives.
4. **Arrow-function renderers did not resolve.** `functionBodyOf` matched only
   `function name(){}`, so `items.map(card)` where `const card = v => \`<div>…\`` looked
   like a plain value. Arrow helpers are the common list-rendering shape on these pages.
5. **A concatenated tag is split across operands** — `'<li class="' + cls + '">'` — so
   neither half contains a complete `<tag>` and a markup-emitting helper read as a plain
   value. Detection is now quote-anchored on the tag OPENING.

**Two duplicated constants caused two of those.** `ESC_CALL` inside
`classifyConcatOperands` was a second hand-typed copy of the escaper list and never
received `esc`; it is derived from the one set now. This is the same lesson
`lib/vetting/portalPath.mjs` states for validators: a second copy is a test that passes
while production is open.

**Why there is no 127-entry "raw" allowlist**, and why that is not a hole: the classifier
only calls an expression raw on POSITIVE PROOF that it is markup — it contains a tag, or
resolves to a variable/helper whose body does. The worry is a markup-emitting helper that
interpolates an unescaped value inside itself; what makes that safe is that both
classifiers scan the WHOLE FILE, so the helper's own sink is reported at its DEFINITION.
Three tests pin exactly that property, because the absent allowlist rests on it.

**One page is safe by a different design, and the analyser cannot see it.**
`public/aria.html` escapes the whole message at ENTRY (`var s = escHtml(text)`, the
markdown renderer) and then runs markdown transforms over the already-escaped string. Its
ten flagged operands (`code.trim()`, `l.replace(…)`, table cells) are substrings of `s`.
Escape-at-source, not escape-at-sink — correct, and justified by name rather than
"fixed".

**Not covered, stated so nobody reads this as more than it is:** interpolation into HTML
only. Nothing here addresses `javascript:` URLs (`lib/util/safeUrl.mjs` + the R-F2607
client helper), inline event handlers (CSP `script-src-attr 'none'` + the R-F3839 sweep),
the vendored `pelican/` theme, or `aria-app/**` (Next.js, its own renderer).

---

### C-21 · The four surfaces C-20 left uncovered — **CLOSED (R-F3851/R-F3852, 2026-08-11)**

C-20 ended by naming what it did not cover: `javascript:` URLs, inline event handlers,
the vendored `pelican/` theme, and `aria-app/**`. All four are closed. Three carried live
defects; the fourth was clean and is now pinned.

**1 · URL sinks — two live `javascript:` vectors.** Escaping is the wrong tool for a URL:
`javascript:alert(1)` contains no `<`, `>`, `&` or quote, so every escaper passes it
through untouched. Two sinks assigned server data straight to `window.location.href`,
which EXECUTES that scheme:
- `explorer.html` — `window.location.href = a.href` where `a` is an entry from the
  ACTIONS API payload, **not a DOM anchor**. The property name is what made it look
  resolved and safe.
- `account.html` x2 — the checkout and billing-portal URLs from our own API.

Both now route through `safeHref` (`js/app.js:566`, http/https/mailto else `#`). The
other ten URL sinks are `blob:` downloads, FileReader `data:` previews, static
`data-src`, literal-path ternaries and internal nav constants — each justified by name in
the guard, because a URL sink is justified by knowing where the URL COMES FROM, never by
how it looks.

**2 · Inline handlers — two dead features, one of them app-wide.** CSP has set
`script-src-attr 'none'` since R-F1919, so an `on*=` attribute is simultaneously an
injection sink and code that cannot run:
- `js/app.js` — the **Toast dismiss button** used `onclick="this.parentElement.remove()"`.
  The X did nothing, on every page, for every toast, for as long as that CSP has been on.
- `dd-reports.html` — a favicon `<img onerror>`; see below.

**And the same Toast line carried a live XSS.** It interpolated `msg` RAW into innerHTML,
and callers pass server text — `account.html` sends
`'Checkout failed: ' + (data.error || resp.status)`, `sidebar.js` sends `err.message`. A
hostile or reflected error string was script execution on whichever page displayed the
toast. Now built with `textContent` and a delegated listener.

> **This was missed because the C-19/C-20 guard only scanned `public/**/*.html`.** The
> shared JS modules build HTML too. `js/app.js`, `js/network.js` and `js/sidebar.js` are
> in scope now — 28 further interpolations escaped, and the guard discovers `.js`
> alongside `.html`.

**3 · The DD favicon was visibly broken AND a privacy leak.** `dd-reports.html` rendered
a Google favicon `<img>` with an inline `onerror` for every citation. CSP
`img-src 'self' data: blob:` blocks the image and `script-src-attr 'none'` blocks the
`onerror` that would have hidden it — so **every citation in every DD report showed a
broken-image icon**, on a surface `freeze.md` lists as under warranty. It also sent the
domain of every source ARIA cites to a third party on each render. Replaced with the
local icon glyph the memory-citation branch already used; both CSS classes are 16px, so
the row layout is unchanged.

**4 · `aria-app/**` (Next.js) — clean, no change needed.** Zero
`dangerouslySetInnerHTML`, zero `innerHTML`. React escapes by default; there is no sink
to guard.

**5 · The vendored theme, bounded rather than waved away.** `public/pelican/` (jQuery
2.1.1, bootstrap, owl-carousel) is loaded by exactly one page, `index.html`. jQuery
2.1.1's XSS CVEs all require untrusted input to reach jQuery's HTML PARSER, and no such
path exists: index.html has no inline script and makes no fetch of its own, and the
theme's only dynamic surface — the lead form — writes both its success message and
`xhr.responseJSON.error` with `.text()`, which sets textContent and parses nothing. Four
tests pin exactly those conditions. **This does not claim jQuery 2.1.1 is safe in
general**; it claims this page gives it nothing to be unsafe with.

**Two more analyser defects, both found by the guard failing.** `escapeText` (sidebar.js)
was a sixth escaper name the classifier did not know — the same class of bug as the
`esc()` omission in C-20, and the second time a missing name produced phantom findings.
And `functionBodyOf` took the first `{` after a function's name, which for
`function avatar(user, { size = '' } = {})` is a **destructured parameter**, not the body:
the helper's markup was invisible, so every caller was reported as an unescaped sink.

**Both new guards were proven to fire**, per C-17: re-adding an unescaped `msg` to the
Toast failed the interpolation guard naming `line 318: msg`, and the URL guard names any
unjustified `href`/`src` sink with its line and kind.

### C-22 · SearXNG's surviving engine was serving a soft-404 page as ten results — **CLOSED (R-F3853, 2026-08-11)**

C-14 seeded the crawl registry with porn and gambling farms; C-19 put a French Chrome
cookies help page into a customer DD report as "press coverage". Both were traced to
SearXNG returning noise. R-F3844 stopped ARIA *trusting* that noise and R-F3849 disabled
the engines measured blocked — but neither established WHY the one surviving engine was
producing it, and R-F3849's stated cause ("response cross-contamination") was WRONG.

**The real mechanism, measured from inside aria-intel.** Bing is not mixing responses. It
answers CORRECTLY for popular queries and serves a soft-404 / trending page for queries it
has no hits on, which SearXNG's bing engine scrapes into ten well-formed "results". That
page's contents rotate per request — which is exactly what made one query appear to return
four different answers, the observation that produced the cross-contamination theory. Four
identical inputs giving four different outputs ruled out query mangling, correctly, and
then the wrong conclusion was drawn from it.

    engines=bing, token overlap with the query:
      "Microsoft Corporation"           9/10 related    <- popular: correct
      "BAE Systems"                     9/10 related
      "London weather forecast"        10/10 related
      "Rosoboronexport"                 0/10 related    <- niche: pure junk
      "Modirum Gespi Ltd"               0/10 related
      "qwzzxlkj nonexistent entity 99"  0/10 related

**DD and research queries are ALWAYS the niche case** — a specific company, an obscure
entity, a person. So the instance was failing hardest in precisely the place ARIA depends
on it, and passing in the place she does not. That asymmetry is why the noise looked
intermittent and survived 52 days.

**Fix 1 — an engine that actually has an index.** A bake-off of 18 engines against
"Rosoboronexport" from this datacenter IP: duckduckgo/qwant/startpage CAPTCHA,
mojeek/brave access-denied, google returns bing-grade junk. `yep` (Ahrefs' independent
index) returned 20/20 related, and holds up on the real cases — "Rosoboronexport
sanctions" 20/20 (TASS + Kyiv Post), "Modirum Gespi Ltd" 11/17 (the actual company site),
"Silverbrook Capital Management" 10/20 including the SEC EDGAR 10-Q. That last one is the
entity whose report was contaminated. Enabled in `searxng/settings.yml`; live-verified
post-deploy: bing 0/10 + yep 20/20 on the niche query, bing 9/10 + yep 19/19 on a popular
one.

**Fix 2 — the whole-set gate stops being sufficient the moment fix 1 lands.** R-F3844 asks
whether the MERGED set is unrelated to the query. That worked while bing was alone (all
ten junk -> set rejected). With yep enabled a niche query returns ~20 good results
ALONGSIDE ~10 bing artefacts; the merged set plainly relates, R-F3844 correctly passes it,
and the junk rides through **diluted** — and diluted junk is what a citation gets drawn
from. So R-F3853 applies the same query-independence test PER ENGINE.

**Why that is not the editorialising gate R-F3844 warns against.** R-F3844's docstring is
explicit that a search gate which judges QUALITY will eventually suppress real
intelligence, which is worse than the noise it removes. The per-engine check makes no
judgement about whether a result is good; it asks the same binary question — did this
source answer THIS query at all? — once per engine, and ONE relating result keeps all of
that engine's rows. A merely weak engine is untouched. Single-engine sets are left to the
whole-set backstop, since dropping the only contributor would just be R-F3844 renamed.

Dropped engines are surfaced on the payload (`dropped_engines`) and wired to the brain
(§21a) — a withheld source must be visible, or a degraded backend is indistinguishable
from a quiet one, which is the failure this whole incident was.

**Expect this to rot again.** A datacenter-hosted metasearch scraping consumer engines is
a decaying asset; yep will be blocked eventually too. Re-run the bake-off rather than
assuming the list is still true. The protections that do NOT rot are R-F3844 + R-F3853
(noise is rejected rather than trusted) and R-F3847 (Brave is the sole DD engine), so this
instance's health no longer decides whether a customer report is honest.

Tests: `aria_service/tests/test_rf3853_per_engine_query_independence.py` — 7 RED pre-fix,
7 GREEN post-fix; 26 pass across R-F3844/R-F3847/R-F3853.

---

### C-22 · Deep review of C-19..C-21 — one more live XSS, four analyser blind spots — **R-F3855, 2026-08-11**

An adversarial review of my own guards, on the principle that a control is only worth
what it can SEE. Four blind spots were probed for deliberately, three were live in this
codebase, and one of them was hiding a real vulnerability.

**THE LIVE DEFECT: `aria-brain.html` JS-error banner (:176).**
`'<code>' + (e.where || 'error') + '</code>: ' + (e.msg || 'unknown')` went straight into
`innerHTML`. `msg` is a JS error message or an unhandled-rejection reason, so on a page
that talks to the brain it routinely carries SERVER text — **an error path is precisely
where a hostile string arrives**, which is what makes this worse than an ordinary sink.

Rebuilt from DOM nodes, not escaped — and that choice is load-bearing. This IIFE is the
FIRST script block (:168); `js/app.js` loads at :203 and `escapeHtml` is defined at
:2516. An escaper call here would have been a `ReferenceError` for any error thrown
during early boot, i.e. the banner whose entire job is to report breakage would itself
break, silently, in exactly the case it exists for. `textContent` needs nothing loaded.

**WHY THE GUARD MISSED IT — blind spot 1: parenthesised operands.** The concat scanner
read operands matching `[A-Za-z_$][\w$.]*`. `(e.msg || 'unknown')` starts with `(`, so it
was invisible. Fixed; the scanner now balances parentheses from either an identifier or
an open bracket.

**Blind spot 2: only one side of the concatenation was read.** The scanner matched
`'<div>' + OPERAND` and never `OPERAND + '</div>'` — half of every concatenated builder
in this tree, **126 live sites**. Both directions are scanned now, sharing one
classification path so they cannot disagree. Thirty operands became visible; all thirty
resolved to counts, literal ternaries or formatters, and are justified by name.

**Blind spot 3, and the one still OPEN: a raw variable that mixes markup with a value.**
Raw-ness is decided by finding markup in a declaration — so
`const x = cond ? '<b>ok</b>' : userInput` excuses the WHOLE variable and `userInput` is
never reported. **20 such variables exist.** Two were resolved by hand: `issueNote`
(both parts escaped, safe) and `rows` — which was the error-banner bug above. **The other
18 are unverified** and are tracked, not closed. This is the honest residual of this
whole effort: the guard's raw rule is a positive-proof rule, and this is the one shape
where positive proof of markup does not imply absence of a sink.

**Blind spot 4 was already covered:** `insertAdjacentHTML`, `outerHTML` and
`document.write` are all seen by the existing scanners — probed and confirmed.

**AND THE GUARDS WERE NOT GATING.** `ci.yml` runs `npm test` on push and pull_request —
with `continue-on-error: true`, because the Node suite carries 8 standing failures. So
both DOM-XSS guards ran in CI and **could not fail the build**: advisory, which is the
same "gate nobody has seen fire" this register records for R-F3373. They now have their
own step with no `continue-on-error`. The broader Node suite still needs a failure-SET
baseline gate of the kind `scripts/admin/suite_baseline.py` gives Python; that is tracked
separately, because flipping the existing flag today would just make CI permanently red.

**An R-number collision was caught by the registry**, exactly as §2 intends: R-F3853 was
taken by a peer commit between reserve and write, and the references were corrected to
R-F3855 before commit rather than shipping a duplicate.

**Open items from this review, all tracked:** the 18 unverified mixed raw declarations;
the unbounded `_verifyAttempts`/`_resetAttempts` maps; dd-reports' ad-hoc `<`-only
escaping; the vendored jQuery 2.1.1 (bounded by C-21, not remediated); the Node-suite
baseline gate; and escaper sprawl — six names for one job, which caused two of the
analyser defects in this series and is a refactor needing an operator call under §26.

### C-22 POSTSCRIPT (2026-08-11) — `yep` lasted an hour, and why that is survivable

Two corrections to the C-22 entry above, both measured after it was written. Recorded
here rather than edited into it, so the sequence stays legible.

**1 · The engine fix did not hold.** `yep` answered 20/20 on the niche query, then began
returning `SearxEngineAccessDeniedException` within the hour, and was still denied after a
3-minute backoff. The likely trigger is the bake-off itself — ~40 queries in a few minutes
from one datacenter IP — so it may be fine under ARIA's ordinary load. It is LEFT ENABLED
for that reason; it costs nothing while suspended. **Every** general web engine is now
blocked from this IP (yep/mojeek access-denied, duckduckgo timeout, brave/google
too-many-requests, startpage CAPTCHA), with bing answering popular queries only. C-22
called this out as "expect it to rot again"; the honest correction is that it rotted
immediately, and that no engine-list tuning fixes a structural problem.

**2 · The severity was overstated, because the measurement was scoped to one backend.**
`web_search.search()` fans out across many sources. Measured end-to-end with SearXNG in
exactly the degraded state above:

    "Rosoboronexport sanctions"   10 results, 10 related   google_news, semantic_scholar
    "BAE Systems plc"             10 results, 10 related   google_news, bing_news, memory
    "Modirum Gespi Ltd"           10 results,  5 related   memory, google_news, crossref

So a dead SearXNG is a **redundancy loss, not a blackout**. Do not conclude from a
SearXNG-only probe that ARIA cannot search — that is the same scoping error that made a
detached `python3` cost probe read `spent_usd: 0.0` and nearly produce a fabricated P0
(CLAUDE.md §17). Measure the path the product actually uses.

**What genuinely closed.** The protections, not the engine list. R-F3844 + R-F3853 +
R-F3857 mean a degraded instance is now merely useless instead of harmful, which is the
property that matters and the one that does not rot. Verified live: a niche query with
every engine serving junk returns `ok=False, count=0` — where before R-F3857 the same
input returned `ok=True` with zero results, which an adverse-media sweep reads as CLEAN.

**Still open, and it is an operator decision, not a code one.** Restoring real free-stack
general-web coverage needs a source that permits datacenter traffic. The options are a
non-datacenter egress for aria-searxng, a keyed API that allows it, or spending bounded
Brave quota on the autonomous stack (Brave is already paid and measured working, but
R-F2318 deliberately scoped it to user-facing search to protect quota). No code change
reaches this; §21e escalation applies.

---

### C-23 · Task-list closeout — five residuals fixed, one handed back with evidence (R-F3860..R-F3866, 2026-08-11)

The C-22 review left seven tracked items. Six are closed by code; the seventh is an
operator decision and is stated as one rather than quietly dropped.

**R-F3861 · the last false-negative class is now enforced.** `unescapedRemainder()`
strips literals, then escaper calls with BALANCED arguments, and reports the PROPERTY
READS that survive inside a declaration classified as raw. Every one of the 27 survivors
was resolved at its source and is a CONDITION — `x != null ? … : ''`, a `.length` loop
guard, a boolean test — never an emitted value. Pinned per page in the guard.

> Getting the detector honest took three corrections, each of which had been producing a
> confident wrong answer: stripping escaper calls BEFORE string literals broke on a paren
> inside a string (`escText(x || '(unnamed)')`) and reported three correctly-escaped call
> sites as sinks; a missing word boundary on the `+=` / `.push()` collectors made `s`
> match `rows +=` and `cites +=`, concatenating unrelated code into one 5,600-character
> "declaration" that surfaced `placeholder="e.g. Acme"` from static markup as a property
> read; and the declaration scan had no length bound, so an unbalanced bracket walked to
> end-of-file.

**R-F3860 · three unbounded attempt maps, one bound.** `_loginAttempts`,
`_verifyAttempts` and `_resetAttempts` are all keyed by a caller-supplied email and all
reachable unauthenticated; each pruned only the key it was touching, so cycling addresses
grew them for the life of the process. `lib/util/attemptThrottle.mjs` sweeps on the write
that grows the map — no timer to leak, no work when idle — and **never evicts an entry
still serving a lockout**, which would have handed an attacker a free reset. A test
asserts a FOURTH map cannot be added without a sweep.

**R-F3862 · the Node suite gates.** `npm test` ran under `continue-on-error: true`
because of 8 standing failures, so no Node regression could fail the build.
`scripts/admin/node_suite_baseline.mjs` is the counterpart to `suite_baseline.py`: it
fails only on failures ABSENT from `docs/node_suite_baseline.json`, reports fixed ones
without failing, and refuses to gate when the runner collected almost nothing — a broken
runner must not read as a green suite. **Proven to fire**: removing one known failure
from the baseline produced exit 1 naming the test *while the totals stayed identical at
1801/8*, which is precisely why §16 says diff the SET, never the count.

> The first baseline attempt recorded **9** failures. The ninth was mine — R-F3861's
> escText migration had broken a vm test that runs an extracted slice of dd-reports.html
> without escText in scope. Fixed, re-recorded at exactly the standing 8. A baseline is
> the one artefact where recording your own regression makes it permanent.

**R-F3863/R-F3866 · escaper sprawl was hiding three real weaknesses.** Six names for one
job across 17 definitions. Pinning them to behavioural equivalence — rather than the
refactor the freeze refuses — immediately exposed that they did NOT agree:
- **`js/app.js:escHtml` did not escape `'`** — the GLOBAL escaper most pages use, so any
  value in a single-quoted attribute could close it. `sources.html:escHtml` had the same
  gap.
- **`wa-connections.html:escHtml` was the DOM trick** (`textContent` → `innerHTML`), which
  escapes neither quote — and that page uses it in FIVE attribute positions.
- **`account.html:escapeHtml` rendered `null` as the literal text "null"** in the UI.
- (`dd-reports.html:escAttr` escaped only `"`; fixed under R-F3861.)

None was exploitable with today's data, and that is exactly the point: nothing compared
them, so a weak copy could sit there indefinitely. All 17 now produce identical output
for every vector, asserted against the SHIPPED source rather than a reimplementation.

**Still open, and it is an operator call — jQuery 2.1.1.** Feasibility is now measured,
not guessed: `custom.js` (40 jQuery calls) and `validator.js` (21) use zero
jQuery-3-removed APIs, `plugins.js`'s two `.context` hits are false positives
(`e.contextDimension`, a plugin's own `this.context`), and Bootstrap 4.0.0 supports
jQuery 3. So the swap looks mechanical. What stops it here is that jQuery 3 also changes
BEHAVIOUR no grep detects — Deferred exception semantics, `.width()` decimals,
`:visible`, ready timing — on `public/index.html`, the **public marketing landing page**,
and verifying that means loading it in a browser. Shipping an unverified major-version
bump to the public site is the risk class this whole effort has refused. The exposure
meanwhile stays bounded and test-pinned by C-21.

`public/vendor/jquery.min.js` is a second, orphaned copy referenced by no page. It is
recorded, not deleted: freeze §26 forbids deletion until the Phase 0.3 runtime overlay
runs and the three-proof rule is met.

### C-23 · Two blind spots in the search stack, both found by asking "what is NOT measured?" — **CLOSED (R-F3864, R-F3868/R-F3870, 2026-08-11)**

Both are the same defect class as C-22 and as the three Phase A gates in §1: an
absence that reads exactly like a healthy measurement.

**1 · A working source was discarded at the last step (R-F3864).** R-F3863 proved
Wikimedia refuses UNIDENTIFIED CLIENTS rather than datacenter IPs — `python-requests/2.0`
→ 403, `AriaIntelligence/1.0 (aria@arkmurus.com)` → 200 with real hits, same IP, same
second — and re-enabled wikipedia + wikidata with a compliant user-agent. Both then
reported `n=0` **with no errors at all**, which reads like "enabled but useless" and
would very plausibly have been "fixed" by disabling them again.

They were working the whole time. SearXNG returns an encyclopedic hit as an **infobox**,
not a result row, and the adapter only ever read `data["results"]`. Live for
"Rosoboronexport": `results: 0, infoboxes: 1`, carrying "JSC Rosoboronexport is the sole
state intermediary agency for Russia's exports/imports of…" — precisely the entity
grounding a DD needs on a niche subject, and the case where bing serves a trending page.
**A zero meaning "wrong field read" is indistinguishable from a zero meaning "nothing
found".** Infobox rows are passed through both relevance gates unchanged: provenance
earns nothing, because a trusted source is not a trusted answer.

**2 · The paid engine DD depends on was completely unmetered (R-F3868).** Brave is the
sole DD search engine (R-F3847) and ARIA's paid primary (R-F2318), and **nothing counted
its calls**: `/api/aria/cost/external` returned `by_service: {}, total_calls: 0`. "How
much of the plan have we used?" had no answer — not a wrong one, none. That is exactly
how the OpenSanctions exhaustion was found (§18): by a `429` in production, after which
no retrying, pacing or breaker cooldown could clear it. On the DD path it lands
mid-report, on a customer.

Every outcome branch of the real `_search_brave` is now metered, **success included** —
a meter that counts only failures cannot answer the question that matters BEFORE the plan
is spent — surfaced as `brave_usage` on `/api/aria/search/health`.

**3 · …and the fix nearly reproduced the very defect it was preventing.** The first draft
of `classify_429` keyed on the phrase "rate limit", so it bucketed the REAL OpenSanctions
body — *"exceeded its **rate limit for the month**"* — as pacing. That is the §18 defect
verbatim, written fresh, in the function whose entire purpose is to prevent it. It was
caught only because a test asserted the real body text rather than a paraphrase. **The
discriminator is the billing period, not the phrase.**

**4 · Measuring beats asking (R-F3870).** R-F3868 shipped saying headroom stays `unknown`
until an operator sets `BRAVE_MONTHLY_QUOTA`. Before filing that as an operator task:
Brave publishes `x-ratelimit-limit/-remaining/-reset/-policy` on **every** response
(`50, 0` / `50;w=1, 0;w=2678400`). ARIA now reads the provider's own accounting. The trap
is sharp — the 31-day window reports `limit 0, remaining 0` on a response that was **HTTP
200 with results**, so `0` means *uncapped*, not *spent*, and reading it as exhaustion
would fire a false P0 against a healthy key. Windows with `limit <= 0` are `capped: False`
and never alert.

**The generalisable rule from all four:** the productive question is not "is it working?"
but "what is not being measured, and what would that absence look like?" In every case
here the absence looked like health.

### C-25 · Both of the previous session's "open, honestly" items were the SAME defect, one layer down — **CLOSED (R-F3873, R-F3874, 2026-08-11)**

> **Renumbered C-24 → C-25 on the day it was written.** A peer agent working the same
> tree independently allocated C-24 for an unrelated aria-web review, hours apart. Note
> C-23 is ALSO duplicated (lines 1492 and 1564) — so this register has now collided
> twice, which makes it a mechanism problem, not two accidents. **This file has no
> allocator.** R-numbers stopped colliding only when §2 gave them a reservation log
> (`reserve_r_number.py`) after 9 collisions in 50h; C-numbers are still claimed by
> writing one into a heading and hoping. Left as a flagged gap rather than fixed
> silently: the freeze (§26) scopes this session to the two defects below, and an
> allocator is the operator's call. Do not resolve a future collision by reusing a
> number — a defect register whose identifiers are ambiguous cannot be cited.

The prior session closed with two items it could not resolve and recorded them
honestly:

> - Every general web engine is blocked from the Fly IP. No engine-list tuning fixes
>   that. ARIA survives on news/academic/memory backends — a redundancy loss, not a
>   blackout, and it can no longer lie about it.
> - `plan_limits` populates only when the live server itself calls Brave. I proved
>   the parser against real headers rather than waiting for traffic.

Both readings were true. Both pointed at the wrong thing, and in the same direction:
each framed the gap as *waiting on the world* — an IP block to lift, organic traffic
to arrive — when in both cases **the provider was already publishing the answer on
every single response and ARIA was discarding it.**

**1 · The anti-rot mechanism was blind to rot (R-F3873).** Measured live, aria-intel
and aria-searxng in the same second:

    SearXNG  → unresponsive_engines: [["google cse", "Suspended: too many requests"],
                                      ["yep",        "Suspended: access denied"]]
               engines_seen: ["bing"]
    /api/aria/search/health → yep: {total: 81, ratio: 0.025, quarantined: false,
                                    judged: true}

`yep` was reported as the **healthiest engine on the board** — 2.5% query-independent
over 81 observations — while it was access-denied and serving nothing.

`record_observation` is driven by the engines appearing in RESULT ROWS, and is called
inside `if normalised:`. An engine that is 403'd, CAPTCHA'd or timed out contributes
no rows, so it accrues no observations: `total` freezes at its last good value, the
ratio stays excellent, and nothing is ever quarantined. **A source that stopped
answering is indistinguishable from one that was never asked.**

That inverts the module's purpose, and §27d had already made the surface binding —
*"if a search source looks dead, do not edit the engine list from intuition, read
`engine_relevance`"*. The surface future sessions are instructed to trust was
structurally incapable of showing a dead engine. It measured **lying** engines and
was blind to **silent** ones. A repo-wide grep for `unresponsive_engines` found no
consumer at all.

The two axes are now separate because they need **opposite** responses: a lying
engine must be filtered (quarantine); a blocked engine must be escalated and
deliberately **not** quarantined — it already returns nothing, so a quarantine adds
no protection while holding it out for an hour after the block lifts, and §27 records
that no code change fixes an IP block.

**2 · The pre-exhaustion gauge could only be fed by exhaustion (R-F3874).** R-F3870
built plan-limit tracking, including an alert at 80% consumed, on the fact that Brave
publishes `x-ratelimit-*` on every response. `_search_brave` passed `headers=` on
exactly one of its five branches: **the 429**. The success branch — the overwhelming
majority of calls, carrying identical headers — discarded them.

So `plan_limits` could only ever be written by the event the gauge exists to
pre-empt, and its warning path was unreachable in production. The sharpest detail:
**R-F3870's own docstring records that the headers were measured on an `HTTP 200 with
results`** — a branch its fix did not read.

**3 · …and the meter rendered its own blindness as a clean bill of health.**
`usage_report` read through the non-strict `get`/`get_json`, whose documented
None-on-error contract (R-F1) made a wedged store produce `monthly: {}, plan_limits:
null` — "Brave has never been called and advertises no limits" — indistinguishable
from a healthy, quiet key. That is §17's fabricated-P0 shape (`spent_usd: 0.0` from a
probe with no connection, which nearly became a P0 against a meter reading $48.26)
**reproduced inside the module written to prevent exactly that class.** A meter that
cannot say "I could not measure" is not a meter.

**4 · Verification caught what inspection did not.** Reading the headers on the
success path put an attribute access inside the `try/except` that converts any
exception into a failed search — so a metering line could turn a served result set
into a backend failure and a `timeout` record. This was found by verification pass 1
(four regressions), not by reading the diff, and is contained by `_safe_headers()`
mirroring the existing `_safe_body()`. Same hazard as R-F3857, where a gate added for
correctness emptied result sets.

**The generalisable rule, extending C-23's.** C-23 asked "what is not being measured,
and what would that absence look like?" C-25 is its sharper form: **when a dependency
looks like it is failing you, check what it is already telling you on every response
before concluding you must wait, tune, or ask the operator.** Twice here the answer
was already in the payload — SearXNG naming the engines that refused, Brave
publishing its own headroom — and twice the honest-sounding conclusion ("nothing can
be done until X") was an artefact of not reading it.

---

### C-24 · 360 aria-web review — three UI defects, two of them mine (R-F3871/R-F3876, 2026-08-11)

A front-and-back sweep of aria-web, driven by LOADING every page in a browser and
reading the console. That method is the finding: all three defects below passed
`node --check`, passed the full Node suite, and returned HTTP 200 with the correct
byte count. Static analysis and a 200 both read as health.

**1 · Page-blanking outage — MINE (R-F3871).** `escapeText` was declared inside
`Sidebar.init()`, invisible to `Sidebar.html()` where R-F3866 had added escaping.
`ReferenceError` during init on EVERY page; the dashboard rendered empty. Hoisted to
module scope.

**2 · design-partners nav icon rendered as literal text — MINE (R-F3876).** The rail
showed purple italic `d="m` where the handshake belongs. `link()` takes a POLYMORPHIC
icon — a `bi-xxx` class for most entries, a raw inline `<svg>` for glyphs the bundled
font lacks — and R-F3866 escaped BOTH branches, so the SVG's path attribute leaked
through as text. The file's own comment said the parameter may be raw SVG and the fixer
escaped it anyway; it could not know, because `icon` is a PARAMETER whose raw-ness is
fixed by the CALLER, not by anything resolvable at the definition. Named in
TPL_JUSTIFIED now, with that reasoning, so it is not rediscovered.

**3 · Account pricing threw — PRE-EXISTING, not mine.** `ReferenceError: _sym is not
defined` (account.html:346). The currency-symbol helper was declared inside the IIFE in
the SECOND inline `<script>`, which never uses it, while both call sites are in the
FIRST — so plan cards rendered without prices. Verified against the pre-change file:
identical placement before any commit of mine. Moved into the block that calls it; the
page now renders £0 / £79 / £199.

**All three are the same shape: the name exists in the FILE, but not in the SCOPE that
needs it.** That is invisible to every static guard built in this series, which is why
R-F3872 added an execution smoke — it runs the shared modules and calls the startup
renderers, and it reproduces the R-F3871 failure verbatim while `node --check` still
passes.

**Verified clean in the browser, signed in, after the fixes:** every page 200 (or the
intended 302), zero `ReferenceError` / `TypeError` / CSP violations across dashboard,
dd-reports, vetting, watchlist, account, explorer, sources, news, vls-chain, leads,
vault, aria-brain, design-partners; 114 API requests all 200; CSP still 34 hashes with
no `unsafe-inline`.

**Two things deliberately NOT reported as defects, having been checked:**
- Two location-less exceptions appear on every page — including the pure-static landing
  page, which issues no fetch at all. Browser-extension noise, not app code.
- `/api/health` reports `sourcesFailed: 0` while the sweep log shows many source fetch
  failures. It reconciles: 46 ok + 2 partial + 2 not-configured = 50 with
  `sourcesUnaccounted: 0`. The log entries are SUB-source failures inside sources that
  still partially succeeded, and they are labelled `[TRANSIENT]`. The accounting is
  honest; reporting it would have been a false alarm.

---

### C-26 · jQuery 3.7.1 — ATTEMPTED, MEASURED, REVERTED, then **SHIPPED** (R-F3879 / R-F3882, 2026-08-11)

> **Renumbered C-25 → C-26 by the allocator (R-F3878).** This entry was written as
> C-25 minutes after `f5d14b7d` had taken C-25, and the collision gate shipped that
> hour caught it — the first live catch, on a real collision rather than a fixture.
> The number moved because this entry landed second and is cited only by its own
> commit; the content is untouched. Claim the next one with
> `python scripts/admin/reserve_c_number.py reserve "<title>"` (CLAUDE.md §26a).

C-21 bounded the vendored jQuery 2.1.1 exposure; C-23 measured the upgrade as "looks
mechanical" and handed it to the operator for a browser check. **That assessment was
wrong, and this records why** — it was based on grepping for removed APIs, and the two
things that actually break are invisible to a grep.

The upgrade was performed properly this time: jQuery 3.7.1 downloaded and its integrity
verified against the project's published SRI
(`sha256-eKhayi8LEQwp4NKxN+CfCh+3qOVUtJn3QNZ0TciWLP4=`, exact match), then tested against
a LOCAL baseline of the running landing page rather than in production.

**The method mattered more than the outcome.** A functional baseline was captured on
2.1.1 first — jQuery version, owl-carousel initialised, plugin registrations, counter
count, nav links, form and back-top presence — so the post-swap reading was a DIFF, not
an impression.

**Breakage 1 — `plugins.js` (Waypoints), FIXED by a one-token patch.**
`$(...).load(fn)` is the event shorthand jQuery 3 REMOVED and repurposed as the AJAX
loader, so it threw `url.indexOf is not a function` **mid-bundle**. Everything defined
after that point never registered — the baseline diff showed exactly one field change,
`counterUp: true → false`, which is what pointed at it. `.on("load", fn)` restores it.

> The earlier "no removed APIs in plugins.js" claim came from a grep whose output was
> mangled in the terminal and read as zero. The API was there all along.

**Breakage 2 — the LEAD-CAPTURE FORM, unresolved.** With (1) patched, `counterUp`
registered and the console clean, the form handler still does not bind: a dispatched
cancelable submit reports `defaultPrevented: false` and the form native-POSTs to
`/api/leads`, showing a visitor raw JSON instead of the validation message. Cause NOT
established. Ruled out by direct probe: `$.trim` is present (`typeof === 'function'`),
and the nested-`$(document).ready()` pattern at custom.js:124 was proven to fire under
3.7.1 by a synthetic test. A/B confirmed it is the upgrade: identical click on 2.1.1
gives `defaultPrevented: true` and the correct message.

**REVERTED, and that is the right trade.** Breaking the public conversion path is worse
than CVEs that cannot be reached: no untrusted input reaches jQuery's HTML parser on this
page, and four tests in `test/url-sink-guard-rf3852.test.mjs` fail if that changes. The
CVEs remain PRESENT and UNREACHABLE, which is the same position C-21 recorded — but it is
now an evidenced position rather than an assumption.

**What this needs is a theme migration, not a file swap:** diagnose (2), apply the
Waypoints patch, re-run the same baseline diff, and re-test the form end to end. The
baseline probe used here is reusable verbatim.

**Correction to C-23:** "the swap looks mechanical" should not have been written from a
static scan. Two breakages, one of them conversion-critical, and neither visible without
running the page.

---

#### RESOLVED — R-F3882, 2026-08-11. jQuery 3.7.1 is live; breakage 2 was never a second defect.

**"Breakage 2" and "breakage 1" were the same failure, one frame apart.** The entry above
treats the unbound form as an independent, un-diagnosed problem. It was not. Patching
`.load(fn)` let the bundle finish PARSING, so `counterUp` registered and the console went
clean — which is exactly why the remaining symptom looked like a separate, deeper defect.
It was the *next* Waypoints call throwing, at run time instead of load time:

```
custom.js:107   $('.counter').counterUp({...})
  → jQuery.fn.waypoint → Waypoint.refresh()
  → this.$element.offset()          // $element IS window here
  → jQuery 3: elem.getClientRects is not a function   ← THROWS
```

jQuery 2's `.offset()` began with a `typeof elem.getClientRects === "undefined"` guard and
returned quietly on window; jQuery 3 calls it directly. The throw propagated **out of the
enclosing `$(document).ready()` callback**, so every statement after line 107 was skipped
— and the lead-form handler is bound at line 124. The form was never "failing to bind";
its binding code never executed. `plugins.js` already guards its OTHER offset call on
`isWindow`; this call site simply did not, and jQuery 2 hid it.

**Why the earlier diagnosis stalled** — every ruling-out in the entry above was correct
and every one of them was aimed at the wrong frame. `$.trim` present: true, irrelevant.
Nested-ready fires under 3.7.1: true, and irrelevant, because the OUTER callback died
first. The probes tested the handler in isolation, where it works; nothing tested whether
the code path *reaching* it survived. **A handler that is never reached and a handler that
is broken present identically from the outside.** What finally separated them was
instrumenting `custom.js` with sequential markers and wrapping the region in a
`try/catch`: execution reached 106 and not 111, and the catch named the TypeError.
Bisecting execution beat reasoning about behaviour.

**The fix is two lines in `plugins.js`, both in Waypoints:**
```js
return i.on("load",function(){return n[m]("refresh")})                       // was i.load(...)
r=n.isWindow(this.element);e=r?{top:0,left:0}:this.$element.offset();        // was e=this.$element.offset();
```

**Verified by the same baseline diff, field for field, at a controlled scroll position on
both versions — every field identical:** `owlInitialised` true / `owlStages` 1 /
`owlItems` 3, all four plugins registered (`owlCarousel`, `counterUp`, `waypoint`,
`scrollspy`), 4 counters reading 1–4, `navbarLinks` 7, `navHTMLlen` 1357, `docHeight`
6029, `backTopVisible` `table`, `navActiveLinks` `[]`, Bootstrap 4.0.0-beta.2, console
clean, screenshot visually identical. The conversion path — the thing the revert was
protecting — is proven working: `FORM_defaultPrevented: true`, response
`"Please enter your name, a valid work email and your primary use case."`, class
`form-response is-error`, `stillOnPage: true`. No native POST, no raw JSON.

> **Two fields nearly caused a false alarm.** An ad-hoc probe read `navbarLinks: 8` and
> `backTopVisible: "none"` on 2.1.1 versus `7` / `"table"` on 3.7.1. Both were artifacts
> of the probe's own scroll state, not of jQuery — re-measured at `scrollY` 0 and 1500 on
> each version, all four readings agree. **A diff between two runs is only evidence when
> the conditions are pinned;** an unpinned diff manufactures regressions as readily as it
> hides them, and this one would have re-reverted a working upgrade.

CVEs 2015-9251, 2019-11358 and 2020-11022/11023 are now GONE rather than
present-and-unreachable. jQuery 2.1.1 stays in the tree unreferenced — CURE freeze §26
forbids deletion, and the three-proof rule is not met for it.

**Correction to the entry above:** "Cause NOT established" was accurate when written and
"REVERTED, and that is the right trade" was the correct call at that moment — with the
cause unknown, shipping would have been a guess. The error to avoid is not the revert; it
is *stopping* at it. The lesson generalises past jQuery: **when a symptom survives the
fix for its apparent cause, suspect the same cause at a different frame before positing a
second one.**

---

### C-27 · The Node tier's brain wire had full instrumentation and NO READER — **CLOSED (R-F3889, 2026-08-11)**

> Claimed with `python scripts/admin/reserve_c_number.py reserve` (§26a), the
> allocator that exists because C-18/19/22/23 each got claimed twice by writing a
> heading.

Found during a 360 review of aria-web, not by a failing test — nothing could fail.

`ErrorTracker.brainWireStats()` (R-F2821) counts `delivered` / `dropped` /
`droppedNoTarget` / `throttled` and records `lastError` instead of swallowing it.
Its docstring states the purpose exactly:

> "Observability of the wire ITSELF (§21a: a signal that silently fails is still
> dark). Before R-F2821 this method had no res.ok check and a bare `catch {}`, so
> a brain returning 401/404/500 was indistinguishable from a successful delivery
> — the tier could report 'wired' while emitting nothing."

**Then nothing in production ever called it.** Every call site was under `test/`
(verified: 6 references, all in `test/errortracker-own-code-domain-rf2821.test.mjs`,
plus one comment in `server.mjs:1868`). The counters incremented in memory where
no operator, dashboard or probe could reach them. In production the wire was
*exactly* as unobservable as before R-F2821 — the property that fix set out to
guarantee held **only inside a test process**.

**This is the repo's most-repeated failure shape**, and CLAUDE.md already records
four instances: three Phase A gates "certified by an absence" (§1), `route_audit`
returning `{}` for a 770-route app so the boot log stayed quiet (§16), the cost
meter reading `$0.00` through a process with no store connection (§17), and
`engine_relevance` unable to show a dead engine (§27d). **An instrument nobody can
read is indistinguishable from health.** The novel part here is that the
instrument was *built for that exact reason* and then left unwired to any reader —
the fix and the defect are one layer apart.

**What was at risk:** `ARIA_SERVICE_URL` unset, a rotated `ARIA_API_TOKEN` (§18
rotates these), or a brain 500 would each leave the Node tier emitting nothing to
the brain while every surface still read green. §21c's self-coding loop would stop
receiving Node-tier failures and nobody would know — §19e's worst outcome, the
operator discovering it himself.

**Fix:** `GET /api/health/brain-wire`, operator-gated with `requireInfraRole`
matching `/api/brain-absorb/diag` (R-F2775) because it reveals whether the brain is
reachable and whether a token is set. It returns the raw counters plus an explicit
`state` of `unconfigured | failing | delivering | no_signal_yet` — because
all-zero counters from an unset target are byte-identical to a healthy-but-quiet
tier, and leaving the reader to infer that difference is how this class recurs.

**The load-bearing test is not the route test.** `test/brain-wire-readable-rf3889.test.mjs`
asserts first that `brainWireStats()` has at least one call site outside `test/`.
A route can be renamed or moved; "somebody outside test/ can read this" is the
property that actually failed. Proven RED (2 failures) before the fix and GREEN
(5/5) after.

**Verified live, external:** the gate holds under attack — `/api/health/brain-wire`
and the sibling `requireInfraRole` routes return 401 to an external caller, and a
**forged `X-Forwarded-For: 127.0.0.1` also returns 401**, confirming the R-F3833
bypass keys off the real TCP peer rather than the spoofable `req.ip`.

**Precedent worth following:** R-F2860 fixed this same shape for the liveness
observer, with the comment "an observability tool that cannot be observed is the
very blind spot it exists to fix." That reasoning was correct and simply had not
been applied to the brain wire. When adding instrumentation, the question is not
"is it recorded?" but **"who reads it, and from where?"**

---

### C-28 · The control centre stated a verdict and withheld the reason it already held — **CLOSED (R-F3892, 2026-08-11)**

Found in the same aria-web 360 that found C-27, and it is the same question one
layer out: not "is it recorded?" but **"who reads it, and can they act on it?"**

`aria-brain.html` renders `ECOSYSTEM: ${d.status}` from aria-intel's `/health`.
Measured live: the badge read **DEGRADED** while the SAME response carried

```
degraded_reasons: ["ecosystem_red_nodes_1", "ecosystem_degraded_nodes_22"]
```

and a probe confirmed `renderedAnywhereOnPage: false`. So ARIA's Command Control
Centre — the operator's main page — announced that something was wrong and sent
him to the API to discover what, having already fetched the answer and dropped it.

**A verdict without its reason is not actionable, and an unactionable alert is the
one that gets scrolled past** — which is precisely how a real degradation hides
among the ones you have learned to ignore.

**It actively misled during the review that found it.** With only the badge
visible, the obvious suspect was the single open circuit breaker,
`search:duckduckgo` — the §27 datacenter IP block. That inference was **wrong**:
the real reasons are ecosystem node counts (1 red, 22 amber of 627), and the
breaker is unrelated and already displayed two rows above. Rendering the reason
removes the guesswork rather than relocating it.

**Fix:** one `metricRow('Degraded because', …)` appended to the infra block. Two
properties are deliberate and both are pinned by tests:
- **Routed through `metricRow()`**, which escapes label, value and class — this is
  server-supplied text heading for `innerHTML`, and the R-F3845 guard would (and
  should) reject a hand-built row.
- **Guarded on a non-empty list.** A blank "Degraded because" row reads as a
  rendering bug and teaches the operator to distrust the whole panel.

**The test found a defect in itself first.** Its initial draft searched the raw
source, matched `degraded_reasons` inside the explanatory comment above the fix,
and failed against a correct implementation — a false RED. It now analyses
comment-stripped source via the length-preserving `stripLineComments`, so offsets
still index the real file. Proven to fail on demand: removing the fix returns 3/3
red, restoring it returns 3/3 green.

Relationship to [[C-27]]: C-27 was an instrument with no reader at all; C-28 is a
reader that displays the alarm but not the cause. Both are the same underlying
question, and both were invisible to every static gate because nothing was broken
— the code did exactly what it said, and what it said was not enough.

### C-29 · The registry reliability EMA was structurally blind — the producer and the consumer used different key-spaces — **CLOSED (R-F3906, 2026-08-11)**

Found by a source-health DD of `https://imaria.io/sources.html`. The "Registry
reliability (measured observations)" panel reported **194 of 194 families
UNMEASURED**, healthy/degraded/failing/dead all `0`. It was not empty because
nothing had been measured. It was empty because nothing could be read.

**Measured live, same instant, build_rev `c35fbc0e`:**

```
GET /api/aria/atlas/stats                 -> topics_tracked: 1
GET /api/aria/atlas/rank?topic=identity   -> find-and-update.company-information.service.gov.uk
                                             confirmed: 21, score: 0.9954, last_update 2026-08-06
GET /api/aria/source_validator/health     -> that SAME family: bucket=unmeasured, samples=0
```

Twenty-one real observations existed and the panel built to display them reported
the source as never measured.

**Cause — a producer/consumer KEY-SPACE MISMATCH.** The R-F2735 producer
(`dd_orchestrator._record_source_reliability`) calls
`web_atlas.record_ingest(url, layer_name)`, writing
`aria:atlas:reliability:{family}:{DD_LAYER_NAME}`. The consumer
(`source_validator._measured_reliability`) read
`aria:atlas:reliability:{family}:{CATALOGUE_TOPIC_TAG}`, enumerating the topics the
family was **tagged** with at seed time. The 12 DD layer names and the 94 seeded
catalogue tags intersect on exactly one token, `compliance` — so **11 of 12 layers
wrote to keys no consumer would ever read**, and only 50 of 200 families carry even
that tag, leaving **150 structurally unmeasurable** no matter what a DD did.

The consumer was enumerating the **wrong universe**: it assumed the set of topics a
family has *observations* under equals the set it was *tagged* with. Tags describe
editorial coverage; observations are recorded per DD layer.

**A second consumer was blind the same way.** `suspend_failing_sources` shares
`_measured_reliability`, so `overall` was `None` for every family, the `overall is
None` guard short-circuited, and **auto-suspend could never fire at any threshold**
— "never silently trust a failing source" was unenforceable.

**R-F3254/R-F3255 are not the bug.** They correctly stopped the panel reporting an
unmeasured source as `0.5 = failing` and gave `unmeasured` its own bucket. They made
the emptiness *honest*. Nobody checked whether the wire underneath was connected, so
the honesty fix made a permanent blindness legible instead of curing it. This is the
recurring shape in CLAUDE.md §1 (three Phase A gates certified by an absence), §16
(`route_audit` returning `{}` for a 770-route app) and §17 (the cost meter reading
`$0.00` through a store-less process): **an instrument that cannot see is
indistinguishable from a clean reading.**

**Fix:** the consumer reads **what was written** — one prefix scan of
`aria:atlas:reliability:*` grouped by family — instead of guessing a vocabulary.
Deliberate properties, each pinned by a test:

- **No shared vocabulary.** A topic the catalogue has never heard of is still
  measured, so the class cannot recur. Do **not** repair a future miss by adding a
  name to a list in `source_validator`; a hand-maintained vocabulary is the defect,
  not the cure (cf. §27d on hand-maintained engine lists).
- **One scan, not one per family.** `scan_keys` is a GLOB range scan and R-F703
  records a live event-loop wedge from running it per-call on a hot path. As a side
  effect the report got cheaper: ~1,940 mostly-missing reads become ~21 hits.
- **An unreadable store is DECLARED, never counted.** `scan_keys` returns `[]` on
  failure exactly as on an empty keyspace, so the naive fix would have relocated
  C-29 rather than cured it — a wedged store reporting all 194 families unmeasured,
  as a fact. The family index is now read with `get_json_strict`; on
  `StoreReadError` the report carries `store_readable: false` and **null** counts,
  never `0`. `scan_complete: false` marks a truncated scan and falls back to the
  legacy probe rather than asserting "unmeasured" from a partial read.
- **`StoreReadError` is imported by name**, not reached through the module-level
  `rs`, which tests monkeypatch — otherwise the `except` clause meant to handle a
  read failure would itself raise `AttributeError`.

**Two defects were found in the fix by the review stages, not by the tests that
already passed:**
1. *Taint analysis* — `web_atlas._source_family` returns `urlparse().netloc`, which
   **keeps the port**, so a family can contain a colon (`example.com:8080`, `[::1]:9`).
   Splitting the key at the FIRST colon filed that observation under `example.com`
   and left the real family at zero: **C-29 reproduced, by the fix for C-29.** Split
   at the last colon, confirmed against the family index (which also covers a topic
   carrying a colon, since `_normalise_topic` does not strip them).
2. *Business-logic review* — the new store-unreadable return used `suspended: []`
   while the success path returns a **count**, so a caller's
   `result["suspended"] > 0` would raise `TypeError` on the failure path — a crash
   reachable only when the store was already unhealthy.

**Known remaining narrowing, NOT fixed here (deliberately out of scope).** Even with
the key-space cured, the producer's gate (`confidence == CONFIRMED` and not
`gate_demoted` and a structured `url`) has yielded exactly **one family across the
module's lifetime**, despite 63 DD layer-runs in the last 7 days. And
`web_atlas.record_correction` — the only negative signal — **has no caller**, so
scores can currently only rise and auto-suspend, though no longer blind, remains
unreachable in practice. Both are follow-on defects, not this one.

**Verification:** fixture-first. The C-29 suite was RED (4 failed, 1 passed — the
one pass being the R-F3254 honesty guard, which the fix had to preserve) before any
production change, and the edge-case guard was proven to fail on demand by reverting
the split. Final: **323 passed, 2 xfailed, 0 failed** across every test file that
touches `source_validator` / `web_atlas` / the atlas routes.

**Follow-through — R-F3908: the blindness detector itself shipped DARK.** The
`store_readable: false` branch added above returned its honest state and **logged**,
and did nothing else — no `brain_hook.absorb`, no gap, no metric. Under §21a that is
dark, not wired, and it is the sharpest possible version of this defect: the whole
of C-29 is that an instrument which cannot see reads as a clean instrument, so a
blindness detector that tells nobody reproduces the fault one level up. ARIA would
go blind on her own source registry and her brain would never learn of it (§25
proprioception unmet for that limb), and the blind `suspend_failing_sources` would
silently stop enforcing "never silently trust a failing source" with nothing
downstream noticing it had stopped.

Both failure branches now absorb to the brain with `success=False` and the gap type
`source_registry_unreadable` — **registered** in `capability_gaps.VALID_GAP_TYPES`,
because `record_gap` silently drops an unregistered type (`capability_gaps.py:315`;
cf. the R-F3428 / R-F3793 / R-F3520 blocks recording types emitted in production
while registered nowhere). An unregistered type looks exactly like wiring and
delivers nothing, so a test pins the registration. The type is deliberately distinct
from `source_uptime_degraded`: that means *a source is failing*, this means *the
instrument is unreadable*, and collapsing them would report our own blindness as the
sources' fault — the C-29 error in a new place.

The SUCCESS path is deliberately left quiet: `registry_health_report` backs a polled
dashboard panel, and emitting per read would be the `source_atlas_update` storm that
`defence_source_seed.skip_if_populated` exists to prevent. A test guards that too, so
the wiring cannot later be "improved" into a flood.

### C-30 · wiring_monitor M3's verdict was INVERTED — a healthy WA listener reported as permanently failing — **CLOSED (R-F3909, 2026-08-11)**

Found live: `GET /api/aria/brain/stats` showed `wiring_monitor:M3` at 3 total / 3
fail, `success_rate: 0.0`. It was not detecting anything.

`check_wa_connection_health` counts `wa_auth_lost` / `wa_disconnected` entries in
capability_gaps and then did:

```python
if auth_lost == 0 and disconnected == 0:  wire_failure(...)
else:                                     wire_success(...)
```

**A listener that had never dropped was reported as FAILING; one dropping constantly
was reported as HEALTHY.** The returned dict simultaneously said `healthy: True`
while wiring a failure, so the function contradicted itself.

The cause is visible in its own docstring, which concedes the check cannot tell the
two cases apart — *"Either the WA listener has never disconnected (unlikely) or these
signals are dark"* — and then resolves that ambiguity by asserting the failure.
**Absence of evidence is not evidence, in either direction.**

`test_rf1091::test_check_wires_to_brain` **required** the defect: it asserted the
function ALWAYS calls wire_success or wire_failure. An obligation to emit a verdict,
held by a check that cannot earn one, is discharged by emitting a wrong one. The test
now asserts the surviving intent — a verdict when there is evidence, `determinate:
False` when there is not — and says explicitly not to restore the always-emit rule.

### C-31 · wiring_monitor M4 judged files that are not in its own image — **CLOSED (R-F3909, 2026-08-11)**

Also 3 total / 3 fail. `test_brain_signal_path` greps three source files to decide
whether the cross-tier brain wire is intact. Measured **inside the running aria-intel
container**:

```
MISSING lib/observability/errorTracker.mjs
MISSING services/wa-listener/aria_wa_listener.mjs
MISSING services/aria_zoom_service.py
-> errorTracker_wired: false, wa_listener_wired: false, path_healthy: false
```

aria-intel ships the **Python** service; those are Node-tier files and are not in the
image. So M4 reported *"the Node tier is not wired to the brain"* when it simply could
not SEE the Node tier — and that conclusion is **provably false**: C-27 / R-F3889
measured that wire live and made it readable at `/api/health/brain-wire`.

The mechanism was documented as a feature. `_cached_source` returned `""` for an
unreadable file, with the comment *"preserves every caller's existing behaviour (a
missing file reads as 'token not present')"* — so **"there is no such file" and "the
token is absent from this file" became the same answer**, and every wiring grep built
on it concluded ABSENT. The failure message then named `errorTracker.mjs` as unwired,
which would send an engineer to wire something already wired: a wrong cause pointing
at a wrong fix.

**Fix:** `_read_source` returns `(content, readable)` and carries the memoisation;
`_cached_source` remains a thin content-only wrapper for callers that legitimately
want "absent reads as empty". Anything forming a **verdict** must honour the flag.
`path_healthy` is now tri-state (`True` / `False` / `None` = could not inspect), and
`run_all_checks` reads it with `is True` / `is None` rather than truthiness — a bare
truthy test folds `None` into "degraded" and re-creates the permanently-red signal one
level up. An unverifiable M4 no longer degrades the composite; it is surfaced in
`composite_detail` pointing at `/api/health/brain-wire` instead.

**Both are C-29 INVERTED.** C-29 was absence rendered as health; these are absence
rendered as failure. In every case the honest answer is UNKNOWN — the check could not
measure — and a monitor that cannot distinguish "no evidence" from "evidence of
failure" must say so rather than pick one. A guard that is red no matter what carries
no information and trains everyone to scroll past it, so the day M4 has something real
to say nobody will be listening (the same reasoning recorded for C-28). A test pins
that M4 can still go RED on a genuinely dark Node tier and GREEN on a wired one —
before this fix it could do neither.

The `_cached_source` memoisation guard in `test_rf3580` followed the cache to
`_read_source`, unchanged in intent (one read per path per process), plus a new
assertion that the wrapper never opens files directly.

### C-32 · The web_atlas reliability EMA is a ONE-WAY RATCHET — **OPEN, blocked on a policy decision (investigated R-F3909, 2026-08-11)**

`web_atlas.record_correction(source_url, topic)` is the only negative reliability
signal in the system and **has no caller anywhere in the tree**. Consequence: scores
can only ever rise. `suspend_failing_sources`, which C-29 un-blinded, is therefore
still unreachable in practice — not because it is broken now, but because nothing can
ever push a family below the threshold. "Reliability" that cannot fall is a misnomer,
and "never silently trust a failing source" remains unenforceable.

**Why it is NOT being closed by wiring an existing signal.** Every contradiction
signal in the tree was examined and **none attributes fault to a specific source**:

- `deep_researcher` Rule B — two facts on the same topic disagree, so the fact is
  downgraded to UNCERTAIN. Which source was *wrong* is never adjudicated. Penalising
  both would punish the correct one; picking one would be arbitrary.
- `deep_researcher` Rule C — an entity has CONTRADICTED past-verified facts. Again a
  claim-level state, not a verdict about the source that carried it.
- `dd_orchestrator` R-F3455 — the adverse-media **conclusion** is contradicted by the
  report's own cited sources. Here the sources are RIGHT and ARIA's conclusion was
  wrong; feeding this back as a source correction would invert the blame exactly.
- `training_data.record_correction` — a user correcting ARIA's *response*. Carries no
  source URL at all.

Wiring any of these would **fabricate attribution**: recording a negative reliability
judgement against a source that may well have been correct. Because the score feeds
`suspend_failing_sources`, the concrete harm is auto-SUSPENDING a legitimate source
with a fabricated `degradation_reason` — precisely the harm R-F3254 was written to
stop, arriving by a new route. R-F2735's own docstring already reached this conclusion
for `gate_demoted`: *"Penalising it would dishonestly mark correct regional press as
unreliable for not being OFAC."* The same reasoning holds here.

**The decision needed (operator/§21e escalation — this cannot be expressed as a Gap):**
what counts as evidence that a SOURCE, rather than a claim, was wrong? Plausible
candidates, none currently implemented:
1. a source contradicted by a strictly higher tier (e.g. tier_2 contradicted by a
   tier_1a registry) — the tier ordering supplies the adjudication the fact-level
   signals lack;
2. an explicit operator/analyst judgement on a report finding, carrying the URL;
3. a source that goes 404/dead — but note this is already `source_uptime_monitor`'s
   job, and conflating reachability with *accuracy* would repeat the C-31 error of
   answering a different question than the one asked.

Until one is chosen, the EMA should be read as **"confirmations accumulated"**, not as
reliability, and nothing should be auto-suspended on it. Recorded here so the next
session does not "fix" the ratchet by wiring the nearest available signal.

**C-32 addendum — why the POSITIVE producer is also nearly silent (root-caused
R-F3909, not fixed).** C-29 recorded that the R-F2735 producer had yielded one family
across its lifetime despite 63 DD layer-runs in 7 days, and left the cause open. It is
now established, and it is not the gate's logic.

`_record_source_reliability` skips any finding without a structured `url`. But
`Finding.url` was introduced by **R-F2691 as PURELY ADDITIVE** — its own comment
states the constraint and the scale: *"All optional → every existing construction site
keeps working unchanged"*, across *"~127 construction sites"*. Most predate R-F2691 and
never adopted it, so they build findings whose provenance still lives only in the
free-text `[from {url}]` suffix. The producer sees `url is None` and skips them. Only
the few updated sites — identity/Companies House among them — can ever record.

So the reliability EMA is starved at BOTH ends: almost nothing can raise a score
(this), and nothing at all can lower one (C-32 proper). R-F2691 already names the fix
and scopes it as separate work: *"Making those two functions prefer `url` is the real
fix and is a separate R-number (it touches the tier gate + ~127 construction sites)."*
That remains the right call — it touches the R-5005 Tier-1a gate, so a careless sweep
would silently re-tier findings across every report. Do not attempt it as a side-effect
of a reliability fix.

**Coverage surfaces are empty for a related reason, and it is NOT a wiring defect
(checked R-F3909).** `/api/aria/atlas/coverage` and `/atlas/gaps` both return `[]`
(`coverage_cells: 0`). `web_atlas.update_coverage` has three live callers in
`source_scout`, and the scout is scheduled (`autonomous_scheduler.py:261`) — but every
call sits behind `gated.get("added")` or `queued_pending`, i.e. coverage is only
recorded when the scout ADMITS A NEW SOURCE. With ~192 families already seeded and the
validator gate in front, that path is rare-to-never. The producer is wired; its trigger
condition is simply not being met. Worth revisiting as a question about scout yield,
not as a dark path.

### C-33 · Node feed-health "reliability" resets to zero on every restart — **OPEN (investigated R-F3909, 2026-08-11)**

`/sources.html`'s **Operational feed health** panel reports a reliability percentage
per briefing integration, computed in `server.mjs`:

```js
const sourceHealth = {};                       // server.mjs — plain object, no persistence
const reliability = total > 0 ? Math.round((h.ok / total) * 100) : null;
```

`sourceHealth` is an **in-process object with no durable backing**. Every aria-web
restart or deploy resets it to `{}`, so the percentages are scoped to *process
lifetime*, not history. Measured 2026-08-11: uptime 16,091s (~4.5h) against a 5-minute
sweep cadence — so the displayed figures were computed over roughly 53 sweeps, and a
source that has been failing for weeks reads as whatever it did since the last deploy.

The page footer states *"Reliability = successful sweeps ÷ (success + fail)"* without
that scope, and R-F2519 added a `degradedInLastN` rolling window over the last 10
sweeps — which also empties on restart. So a chronically flapping feed is laundered
clean by a deploy, and deploys are frequent.

This is a milder cousin of C-29: not an unreadable instrument, but one whose window
silently shrinks to "since last boot" while being presented as a reliability history.
It cannot report a false *green* out of nowhere — a fresh process shows `null` /
not-checked until the first sweep, which R-F2719 already buckets honestly — but it
does forget every failure it has ever seen.

**Not fixed here, deliberately.** It is a change to the aria-web tier, which has its
own deploy workflow (`deploy-fly.yml` is aria-intel ONLY), and it needs a persistence
decision rather than a patch: the Node tier is files-only per §6 (`MemoryManager` over
`RUNS_DIR/memory/hot.json`; Upstash is gone), so the natural home is a small durable
counter file written on sweep completion and rehydrated at boot — with a bounded
window so it does not become an unbounded all-time average that dilutes new
regressions, which is the exact failure R-F3364 fixed for the DD layer-stats counters
by day-bucketing them. Reuse that shape; do not write a flat all-time counter.

### C-34 · The pre-commit hook silently skipped EVERY check — and could not have blocked a real failure either — **CLOSED (R-F3912, 2026-08-11)**

Observed across four real commits in this session. Each printed

```
Python was not found; run without arguments to install from the Microsoft Store...
```

and then committed successfully. **No check ran.** Those commits were sound only
because the checks had been run by hand.

**Fault 1 — interpreter resolution is worktree-blind.** The hook resolved the venv
against `git rev-parse --show-toplevel`, which inside a git worktree is the WORKTREE
root, and a worktree has no `.venv`. It then fell through to `command -v python3`,
which on Windows is the App Execution Alias shim — a stub that prints an
advertisement and exits without running anything. CLAUDE.md now makes worktrees the
normal way to work in this repo (a peer agent holds the main checkout dirty), so the
hook was broken in **exactly the configuration the project tells every session to
use**. Fixed by also resolving `--git-common-dir`'s parent, which is the main
checkout for every worktree.

**Fault 2 — and this is the one that matters.** The hook blocked only on an explicit
`VERIFICATION FAILED` sentinel and exited 0 otherwise, so output containing no
sentinel read as a clean pass. That is the C-29 defect **sitting inside the guard
whose entire job is to catch defects**. And it was not merely lenient: with no
interpreter, the sentinel it greps for *can never appear*, so the hook could not have
blocked a **real** failure either. It was inert, not permissive — a test proves this
by driving a genuine `VERIFICATION FAILED` through the broken configuration.

**Fix — an interpreter probe**, deliberately the narrowest discriminator available:
`"$PY" -c "pass"`. The shim fails it (measured: exit 49); every real Python passes.
R-F1958's fail-open policy — *"a tooling bug must never wedge commits"* — is kept
**exactly**: a checker that genuinely runs and then crashes still warns and still
allows the commit, and a test pins that as the line this fix must not cross. Only "no
usable interpreter" blocks, which is a local config error fixable in seconds.

A sentinel-based variant was written first and **discarded**: requiring positive
output would also have wedged commits on any checker crashing before it printed,
which is precisely the case R-F1958 protected. Recorded here because the discarded
design is the obvious one and someone will propose it again.

**Verified live, not asserted:** running the fixed hook inside the worktree now
prints `[pre-commit] OK — 1 files checked` where it previously printed the shim's
advertisement and skipped everything.

WARNING: `core.hooksPath` is an ABSOLUTE path to the main checkout
(`C:\Code\Aria\scripts\git-hooks`), so a worktree's commits run the MAIN tree's hook
file. This fix therefore takes effect for worktree commits only once the main
checkout has pulled it — not when the worktree branch has it. That is also why the
four commits in this session kept using the old hook after the fix was written.

### C-35 · The reliability producer ignored the provenance the rest of the DD already reads — **CLOSED (R-F3915, 2026-08-11)**

C-29 made the registry reliability EMA readable; it then measured **one family across
the module's lifetime** despite 63 DD layer-runs in seven days. This is why.

`_record_source_reliability` skipped any finding whose `url` was None. But
`Finding.url` was introduced by R-F2691 as **purely additive** — its own comment gives
both the constraint and the scale: *"All optional → every existing construction site
keeps working unchanged"*, across *"~127 construction sites"*. Almost all predate it
and still carry provenance the original way, embedded in `source` as
``"bailii [from https://www.bailii.org/ew/cases/...]"``.

That suffix is **not** decoration. R-F2691 measured it as load-bearing: `origin_key` /
`_is_tier_1a_source` resolve it to `pub:bailii.org`, which is what clears the R-5005
Tier-1a gate — a bare ``"bailii"`` yields `external_unclassified` and FAILS it. The DD
pipeline already treats it as authoritative provenance. Only the reliability producer
refused to look at it.

**The fix is NOT a 127-site sweep.** R-F2691 correctly scoped that as separate work
because it touches the tier gate, and a careless pass would silently re-tier findings
across every report. The surgical fix makes the *producer* read provenance the way the
rest of the system already does, **reusing `registrable_domain` and `origin_key`
rather than writing a second parser** — a second parser is exactly how C-29 happened:
two components each deciding independently what a source key looks like, then drifting
apart. The synthesized `https://{registrable_domain}/` means `web_atlas._source_family`
and the tier gate agree by construction.

Attribution is never invented: `origin_key` already distinguishes an external
publisher (`pub:…`) from internal compute (`ghost_scorer`, `network_walker` →
`internal`) and from the unclassifiable, so it is the gate rather than a hand-written
blocklist. Tests pin that internal labels, blank sources and bare labels record
nothing, that the R-5005 confidence gate is unchanged (C-35 widens WHERE provenance is
read, never WHAT qualifies), and that per-(family, layer) dedup still holds.

**C-32 addendum — the negative half is now definitively BLOCKED, with new evidence.**
The operator approved the tier-contradiction policy, and implementing it was attempted.
It cannot be done safely, and the reason is stronger than "no source attribution":

> `deep_researcher` Rule B — the only in-run contradiction detector — decides that two
> facts on the same topic contradict each other when they share **fewer than five
> words** (`len(overlap_words) < 5`). That is a crude lexical heuristic with an obvious
> false-positive mode: *two differently-worded statements of the SAME fact register as
> a contradiction.* It also never inspects either fact's source.

So a tier-contradiction signal would have to be built on a detector that cannot
reliably tell disagreement from paraphrase, and its output would feed
`suspend_failing_sources`. The concrete failure is auto-suspending a **correct** source
because two of its facts were phrased differently — a fabricated `degradation_reason`
in a compliance product, which is the exact harm R-F3254 exists to prevent.

Closing C-32 therefore requires building two things that do not exist, in order:
a contradiction detector that is actually reliable (semantic, not word-overlap), and
source-level adjudication on top of it. Both are **features**, not defect fixes, and
§26 forbids them under the freeze. Until then the EMA reads as "confirmations
accumulated" and nothing should be auto-suspended on it.

**C-33 RESOLUTION (R-F3917, 2026-08-11) — it was never a persistence problem.**
The entry above proposed adding a durable counter under `RUNS_DIR`. That would have
been building something that already exists.

`recordSourceSweep()` is called on **every** sweep, one line after
`updateSourceHealth()` in `server.mjs`, and persists `source_history.json`: a bounded
96-entry timestamped ring per source, plus totals and an EMA. `getSourceHistory()`
already derives a restart-surviving reliability from the last 48 of those sweeps —
and is **already imported into server.mjs** (line 30). `getSourceHealthSummary()`
simply read the volatile in-memory object sitting beside it.

**So C-33 is C-29 in the Node tier**: a producer and a consumer that must agree, with
nothing forcing them to, and the consumer reading the wrong one. The correct fix adds
no storage at all — it points the existing consumer at the existing durable record.

Two properties are deliberate and pinned by tests:
- **The window stays BOUNDED (48 sweeps), not all-time.** R-F3364 records that a flat
  all-time counter dilutes a new regression into a growing historical denominator, so
  the alarm gets blinder the longer it runs. A test asserts no `totalOk/totalFail`
  ratio is reintroduced.
- **The scope is STATED, not implied** — each row now carries `durable` and
  `windowSweeps`. A percentage whose window is invisible is precisely what let "since
  last boot" pass for a reliability history.

`null` reliability still survives untouched, because R-F2719 depends on it to bucket
unconfigured and not-yet-checked feeds separately rather than counting them healthy.

**Lesson worth carrying:** the first instinct here — and the one written into this
register entry hours earlier — was to BUILD persistence. The durable record already
existed, was already written on every sweep, and was already imported. Check for an
existing producer before adding one; the same instinct nearly re-implemented what
C-29's fix also found already present.

**C-33 COMPLETION (R-F3918) — the "durable" store was on the EPHEMERAL app dir.**
R-F3917 pointed the health summary at `source_history.json` on the strength of that
file being persistent. **It was not, and that was asserted rather than measured** — the
C-29 lesson arriving one turn later in a new place.

Measured on live aria-web minutes after the R-F3917 deploy:

```
/app/runs/learning/   -> every file stamped Aug 12 07:21   (the deploy minute)
/data/                -> files from Jul 3, Jul 11, Aug 1   (genuinely persistent)
source_history.json   -> sources: 50, GDELT sweeps=1 totalOk=1
                         oldest sweep 2026-08-12T07:21:30Z  <- AFTER the deploy
```

`LEARNING_DIR = join(process.cwd(), 'runs', 'learning')` resolves **inside the container
image**, which Fly replaces wholesale on every deploy; the volume is mounted at `/data`
(`fly.web.toml`). So the store survived an in-container process restart but **not a
deploy** — and deploys are the dominant restart cause, i.e. exactly the window C-33
exists to close. R-F3917 alone would have shipped a fix that looked right and closed
nothing.

Fixed by following the convention already used at four sites in this tier
(`existsSync('/data') ? '/data' : <local>`), extracted as `resolveLearningDir()` so the
choice is testable without a mounted volume. A test asserts the module actually USES it
— a correct helper nothing calls is worth nothing (C-27's "instrument with no reader").
Local dev keeps the `runs/learning` fallback. Note the first deploy after this starts a
fresh history; nothing is lost, because nothing was ever being kept.

**This also moves the rest of the learning store onto the volume** — alert outcomes,
confidence weights, patterns, opportunities. All were being wiped every deploy too, so
the migration cannot lose data that was surviving; it stops the loss.

### C-36 · A positive-only counter was surfaced as "reliability" and armed an auto-suspend path — **CLOSED (R-F3922, 2026-08-11)**

C-32 established that `web_atlas.record_correction` — the only negative reliability
signal — has no caller, so the EMA can only ever RISE. C-32 stays open because closing
it needs a reliable contradiction detector plus source-level adjudication. **But two
things were wrong TODAY, independent of that missing capability, and both are closeable
without it.**

**1. The label overclaimed.** `/api/aria/source_validator/health` presented the number
as reliability and the page renders it as *"Registry reliability (measured
observations)"* with healthy / degraded / failing / dead bands. A quantity that cannot
decrease is not a reliability score — it is an accumulation of confirmations. A reader
trusting the word "reliability" concludes a `0.995` source has been **verified not to
fail**, when nothing in the system is capable of recording that it did.

**2. The consumer overclaimed, and was armed.** `suspend_failing_sources` exists to
enforce *"never silently trust a failing source"* and is reachable at
`POST /api/aria/source_validator/suspend_failing` with any caller-supplied threshold.
Against a one-way metric that enforcement is theatre — it cannot fire, and its silence
is indistinguishable from "checked, nothing to suspend". Worse, it sits armed: the
moment anyone wires a negative signal carelessly (the obvious next step, and exactly
what C-32 warns against) it starts suspending sources on that signal's first bad day,
writing a `degradation_reason` that reads as a considered verdict.

**Fix.** The report now carries `signal_direction` (`positive_only` / `bidirectional`)
and a `metric_note` saying what the number actually counts. `suspend_failing_sources`
**declines loudly** — `enforceable: False` with the reason — instead of silently never
firing.

**Enforceability is derived from RECORDED EVIDENCE, never a flag.** It arms itself the
moment a genuine contradiction exists, so closing C-32 cannot silently leave
enforcement off, and nobody can arm it by hand while the metric is still one-way. It
keys on `contradicted > 0` **or** a score below the neutral prior — `record_ingest`
seeds every record at 0.5 and only a failure can push it under, so a sub-prior score is
itself proof of a recorded contradiction. Checking both means the logic does not depend
on one field surviving in the record, which is what the pre-existing R-F3254 fixtures
rely on. (The first draft checked only the counter and broke that honesty test — the
test was right and the guard was too narrow.)

`test_suspend_becomes_enforceable_the_moment_a_correction_is_recorded` is the
**executable acceptance test for C-32**: wire a real negative signal and the suspender
must arm itself automatically.

This is the same family as the rest of the sweep — C-29 (absence read as health),
C-30/C-31 (absence read as failure), C-34 (a guard that could not fail). Here, a number
that cannot move was presented as a measurement that can.

### C-37 · `brain/stats` cannot distinguish a failure-only module from one failing every call — **OPEN (found R-F3922 DD sweep, 2026-08-11)**

Live on aria-intel, `GET /api/aria/brain/stats` reports eight modules at
`success_rate: 0.0`:

```
health_precompute, deploy, llm_recovery_probe, autonomous_safety,
llm_deepseek, llm_deepseek_backup, llm_chain_exhausted, search_searxng
```

`success_rate = success / (total - skip)`. For a module that only ever calls
`wire_failure` and never `wire_success`, **every recorded signal is a failure by
construction**, so the rate is structurally `0.0` whether it fired once or ten
thousand times. Verified statically: `aria_service/llm/openai_compat.py` — the emitter
for every `llm_*` module — contains **zero** `wire_success` calls, and
`main.py::_health_precompute_loop` likewise wires only failures.

The list is therefore **mixed**, and the surface gives the reader no way to tell which
is which: `search_searxng` *does* have `wire_success` calls, so its `0.0` is a genuine
measurement, while `llm_deepseek`'s `0.0` says nothing at all. An operator scanning this
panel sees eight broken subsystems; several are simply failure-only reporters doing
exactly what they were built to do.

Same family as the rest of this sweep — C-29 (absence read as health), C-30/C-31
(absence read as failure), C-36 (a number that cannot move sold as one that can). Here a
rate that cannot vary is presented as a measured rate.

**NOT fixed in this pass, deliberately.** The honest fix is not to guess from the data —
"never succeeded" and "cannot succeed" are indistinguishable in the counters. The
distinction is a **static** property of the module's wiring, and it is already computed:
`wiring_monitor` **M1 audits wire_success/wire_failure balance across all intel modules**
and knows which are failure-only. Reusing M1's output to mark such modules
(`failure_only: true`, rate suppressed) is the correct fix and follows this session's
repeated lesson — check for the existing measure before building a second one (C-29,
C-33). It is deferred because it couples two live surfaces and this sweep had already
shipped six fixes; it should not be written at the end of a long session.

Until then: read `fail` and `total` on this panel, not `success_rate`.


### C-38 · A high-effort review of the C-29..C-36 fixes found 10 CONFIRMED defects, 7 of them inside the fixes themselves — **CLOSED (R-F3925/3926/3927/3929/3931, 2026-08-11)**

36 candidates from 4 finder angles, 27 independent verifier agents, 2 refuted, **10
reported and every one confirmed**. The dominant class is the one this whole sweep was
about: *an instrument that cannot see is indistinguishable from a clean reading* —
this time reintroduced by the fixes for it.

**1 — a FAILED scan reported itself COMPLETE (R-F3927).** C-29's `scan_complete` was
`len(keys) < CAP`, which is True when `scan_keys` returned `[]` because the scan
FAILED: `scan_keys` swallows backend errors and returns `[]` exactly as it does for an
empty keyspace (redis_store logs the SCAN exception then falls through to the in-memory
glob; state_store catches the SQL error and returns `[]`). A Redis SCAN error, or a
SQLite lock on the range scan while point GETs still succeed, produced
`store_readable: true, scan_complete: true, unmeasured_count: 194` — a positive
assertion that no source has ever been validated, from an instrument that never ran.
**The exact conflation C-29 cured, one layer down.** Fixed by adding
`scan_keys_strict` to both store layers, mirroring the existing R-F1392
`get_strict`/`get_json_strict` contract; the caller turns the raise into the honest
store-unreadable report.

**2 — M4 could never report a failure it was fully able to detect (R-F3926).** C-31's
blanket early return on any unreadable file discarded `endpoint_exists`, which is
checked against `routes/aria.py` — present in the image, needing no Node file. If
`/api/aria/brain/signal` were deleted, M4 computed False, threw it away, emitted
nothing, and `run_all_checks` folded `None` into `(m4_healthy or m4_unknown)` and
reported a **healthy composite while the endpoint was gone**. C-31 correctly stopped M4
asserting what it could not see and overshot into not reporting what it could. Honesty
is per-check, not a blanket verdict. The repair itself had to be careful: an unreadable
route file must NOT read as a deleted endpoint, so readability is now tracked
separately and the failure only fires when the file was actually read.

**3 — M3 poisoned its own input and latched red forever (R-F3926).**
`check_wa_connection_health` counts gaps containing `auth_lost`/`disconnect`, and
C-30's new failure detail was literally *"N auth_lost and M disconnected signals…"*,
which `record_gap` stores **into the list the check reads**. One real drop — or any
unrelated gap mentioning "disconnect" — made the count self-sustaining; the changing
detail defeated the 1h dedupe, so counts grew hourly and the coder loop was fed a
perpetual phantom `engine_failure`. C-30 had also deleted the `wire_success` branch, so
**no path could ever emit a healthy M3 signal**. A guard that cannot go green is as
useless as one that cannot go red — C-30's own principle, applied to C-30. Fixed by
skipping this check's own gaps, keyed on the `source` field `record_gap` actually
serialises (there is **no `module` field** — a skip on the module name would have
looked right and matched nothing in production), plus wording the detail so it no
longer contains its own trigger tokens.

**5 — internal module labels were enrolled as external publishers (R-F3925).** C-35
gated on `origin_key(...).startswith("pub:")` believing `pub:` meant "external
publisher". `origin_key` tests for a DOT **before** it tests `_is_internal`, so every
dotted internal label passes: measured, `origin_key('sources.ofac_sdn')` →
`'pub:sources.ofac_sdn'`. That is the commonest `Finding.source` shape in
dd_orchestrator, so ARIA's own compute labels would have been written into web_atlas as
external source families with reliability scores.

**7 — one publisher split across two families, several merged into one (R-F3925).**
The fallback rebuilt a URL from `registrable_domain`, which strips subdomains, while the
`f.url` branch keeps the full netloc: Companies House resolved to
`find-and-update.company-information.service.gov.uk` on one path and `service.gov.uk` on
the other, so it accumulated two EMAs with half the samples each while every unrelated
`*.service.gov.uk` merged into the second.

Both vanish by **extracting the real URL** from the `[from <url>]` suffix instead of
reconstructing one. `_source_family` then derives the family for both paths, so they
cannot disagree, and a bare label simply carries no URL. The lesson is C-29's own: a
reconstruction is a SECOND derivation of the same fact. Reusing
`origin_key`/`registrable_domain` *felt* like reusing the canonical resolver, but they
answer a different question (independence grouping) — **borrowing an answer to a
different question is how this defect class keeps recurring.** Neither had reached
production: verified live before fixing, `topics_tracked: 1` with only the legitimate
Companies House family, because no DD had finalised since the C-35 deploy.

**4 — unconfigured feeds were reclassified as degraded 0% (R-F3929).**
`updateSourceHealth` buckets `not_configured`/`disabled_no_key`/… as `disabled`, never
`fail`, so reliability stays `null` — which is exactly what puts them in R-F2719's
`unconfigured` bucket. `recordSourceSweep` has **no such carve-out** (`ok = status ===
'ok'`), so every sweep of an unconfigured feed increments `totalFail` and drives its
durable EMA to 0. Letting durable win turned Comtrade/CSL from *"no API key was ever
set"* into *"degraded, 0%, dead"* — **the precise conflation R-F2719 was written to
remove**, live on aria-web.

**9 — retired feeds resurrected (R-F3929).** `source_history.json` is never pruned, so
unioning its names revived renamed/retired integrations with a non-null reliability,
filing them as healthy/degraded rather than not-checked and inflating `totalTracked`.
Durable-only names now need a 24h recency test — which is what separates "retired
months ago" from "not yet swept since this boot".

**6 — a phantom contract dependency (R-F3931).** `regional_snapshot` declared
`dependencies=["student"]`; no `AgentContract(agent_id="student")` exists anywhere, and
`validate_contract` appends a `dependency_no_contract` violation and LPUSHes it on
EVERY pass — permanent and unfixable. `dependencies` names other CONTRACTED AGENTS, not
imported modules.

**8 — a doubled full-keyspace read on a polled endpoint (R-F3927).**
`_any_negative_signal_recorded` walked every reliability key and, by the module's own
reasoning, could never short-circuit while C-32 is unwired — then
`_measured_reliability` re-read the identical keys. R-F703 records a live event-loop
wedge from exactly this shape on a health endpoint, and C-35 exists to grow the measured
set, so it scaled the wrong way. Detection now rides the read the loop already performs.

**10 — C-34 fixed pre-commit and left pre-push behind (R-F3931).** pre-push carries the
identical worktree-blind resolution and runs under `set -e`, so the Store shim's
non-zero exit ABORTS it: pre-commit failed OPEN (checks silently skipped), pre-push
fails CLOSED (nothing can be pushed) — **which is why every push in the C-34 session
needed a manual PATH shim.** The same `--git-common-dir` resolution now applies to both.

**What this says about the sweep.** Seven of ten defects were introduced by the fixes,
and every one is the family the fixes were written to eliminate, displaced by a step: a
cure for "asserts what it cannot measure" that overshot into "cannot report what it
CAN measure", and two cases of a fix reintroducing the original conflation one layer
down. Fixture-first caught none of them — they were found by review, because each was a
*correct-looking* implementation of the right idea. That is the argument for the review
pass, not for more tests.


**C-36 follow-through (R-F3933) — the honesty fields had NO READER.** C-36 added
`signal_direction` and `metric_note` to the API and nothing rendered them:
`grep -c` on `public/sources.html` returned **0**. So the operator, who reads the page
and not the JSON, still saw *"Registry reliability (measured observations)"* with
healthy / degraded / failing / dead bands over a counter that cannot fall, and `0.995`
still read as "verified not to fail". That is C-27's producer-with-no-consumer shape
occurring **inside the fix written to cure exactly that class** — and every assertion
C-36 made about the API was true, which is why no test caught it. Found by self-audit.

The banner renders only what the API states (never inferred), stays hidden when the
field is absent so an older aria-intel build cannot make it assert a direction, and
routes `metric_note` through `escHtml` per the R-F3845 guard.

Two defects in that fix, caught by the Node suite before it shipped:
- `dirEl.style.display` threw when the element had no style object, and the throw
  propagated to the panel's catch — **replacing the whole table with "endpoint
  unreachable"**. A decorative caption turned a working panel into a false outage
  report. Now guarded and wrapped: the annotation can never break the measurement.
- the banner copy used em-dashes, which R-F3278 forbids in displayed copy.

**C-37 RESOLUTION (R-F3934, 2026-08-11) — CLOSED.**

Eight modules read `success_rate: 0.0` live. For a module that only ever calls
`wire_failure`, every recorded signal is a failure **by construction**, so the rate is
`0.0` whether it fired once or ten thousand times. Verified statically:
`aria_service/llm/openai_compat.py` — the emitter for every `llm_*` module — contains
**zero** `wire_success` calls, as does `main.py::_health_precompute_loop`.

The list is MIXED, which is what made it dangerous: `search_searxng` *does* wire
success, so its `0.0` is a genuine measurement. An operator scanning the panel saw
eight broken subsystems; several were failure-only reporters working exactly as built.

**The planned fix did not survive contact.** The register entry proposed reusing
`wiring_monitor` M1's wire-balance AST scan. M1 globs `intel/*.py` only, so it never
sees `llm/openai_compat.py` or `main.py`, and it keys results by FILE NAME while the
brain's module keys are runtime strings — `llm_deepseek` is built as
`f"llm_{self.name}"` and corresponds to no file at all. Any mapping between them would
have been invented, which is the defect, not the cure.

So the fix reports the ambiguity instead of guessing it: `only_failures_recorded`
marks the entries whose rate carries no information and points the reader at
`fail`/`total`, which do. Purely additive — `success_rate` stays a number and every
existing field is untouched, pinned by a test. A module with zero signals is
deliberately NOT flagged: that would be inventing a claim about an absence.


**C-37 residual (R-F3936) — the same defect at the other end of the expression.**
Found LIVE minutes after the C-37 deploy, in the verification output itself:
`deploy` reported `success_rate: 0.0, fail: 0, total: 1`. `success_rate` falls back
to `0` when `total - skip == 0` — there is nothing to divide — so a module whose only
signals were SKIPS reads exactly like one that failed every call.
`only_failures_recorded` does not cover it (there are no failures), and folding it in
would make that flag lie. It has its own flag, `no_measurable_signals`, again purely
additive: `success_rate` stays numeric because this module's own tests pin it.

Worth recording that the residual surfaced from *reading the live verification output*
rather than from the tests — the same way most of this sweep's real findings did.


## C-39 · the DD stamped never-searched sanctions lists as CLEAN (R-F3945)

**Found by** a full-ecosystem diligence sweep, 2026-08-12, from live production
evidence rather than from the tests. **Severity: the worst class this product has** —
a false clean on a compliance verdict, live for 13 days.

OpenSanctions' monthly plan quota has been spent since `2026-07-31T23:04:22Z`, so
R-F3529's local canonical floor serves every screen. That floor holds exactly two
sources — `ofac_sdn` and `eu_consolidated`. `derive_verified_sources` was binary:
given `screen_succeeded=True` it stamped **all ten** canonical sources
`status: CLEAN, via: "opensanctions_aggregate"`. Eight lists nothing had queried —
OFAC NS-CMIC, OFAC SSI, BIS Entity List, BIS Military End User, UK OFSI/HMT, UN SC
Consolidated, NDAA §1260H, DoD §1233 — were reported clean to the customer, credited
to the aggregator that had refused us.

**The premise expired; nobody revisited it.** R-F287's reasoning was correct when
written: OpenSanctions *is* an aggregator, so a clean response really does mean the
underlying sources were queried. R-F3529 then introduced a fallback that is not an
aggregator. The parameter that could have expressed this — `unavailable_sources` —
existed and had **no caller anywhere in the tree**, while `dd_orchestrator.py:3406`
hardcoded `screen_succeeded=True`. A guard that could not fire, which is the "certified
by an absence" shape CLAUDE.md §1 already records three times for the Phase A gates.

**Fixed by provenance, not by a second list.** `fuzzy_screen` always emits
`coverage: {mode, sources_consulted}` — *always*, because a block that appears only on
failure cannot describe the dangerous case, which is the screen that SUCCEEDED against
a narrower source set than its verdict implies. `_coverage_split()` is the one
computation behind `unavailable_sources_for()` / `locally_covered_sources_for()`, so
the three call sites cannot drift the way the two phase-gate aggregators did.
`derive_verified_sources(..., screen=screen)` takes one argument, not two co-dependent
sets — two is how the next call site passes only one, and that failure mode (a CLEAN
row attributed to an aggregate that never ran) is quieter than the bug being fixed.
Source ids come from the loader registry, never a literal.

Absence handling is the load-bearing part: no `coverage` key keeps the legacy
full-aggregate meaning, so the fix cannot retroactively rewrite older results; floor
mode with an empty consulted list marks **everything** unavailable, because an
undeterminable registry is not full coverage.

Expect `UNAVAILABLE` rows in live reports until OpenSanctions is restored. That is the
fix working. The degraded state announces once per process, not per screen — every
screen is degraded while the quota is spent, and a per-screen gap would be another
flood of the kind already filling the 500-slot capability ledger.

Fixture-first: `test_rf3945_sanctions_coverage_provenance.py`, 7 tests, RED before the
fix (the two symptom tests asserted CLEAN where UNAVAILABLE was required) and GREEN
after, driving the real `fuzzy_screen` with OpenSanctions stubbed at its true seam.


## C-40 · RULE ONE's Brave half was unenforced, and the surface reporting it checked only the other half (R-F3946)

**Found by** the same sweep — and it is the finding that corrected the sweep's own
published conclusion. The first version of that report listed "RULE ONE is holding"
under *verified healthy*, on the strength of the live `rule_one: {breached: false}`.

`rule_one_status()` states a two-clause rule — "anthropic API calls must be only
active on DD reports … as well as for brave API" — and measured only
`"anthropic" in preference_only_providers()`. The Brave clause had no enforcement at
all: `@_brave_scope` decorated **eight** routes including `POST /chat`, `/explore`,
`/explore-deep` and `/research/spawn`, and `brave_is_enabled()` consulted a bool
contextvar, a key and a kill-switch, with no DD gate. Every general chat turn that
searched spent the paid DD key. Live meter at discovery: 65 Brave calls in the month
against a handful of DD reports.

`ARIA_STUDENT_BRAVE_BUDGET=0` did exactly what CLAUDE.md §27e says — and only ever
governed the student loop, which was never the large leak.

**A half-measure reporting a whole rule is worse than no measure, because it is
believed.** That is the generalisable lesson here, and it is the same failure mode as
the three Phase A gates: the instrument, not the subject, was the defect.

**Fixed with a purpose, not a route list.** Curating which routes carry the decorator
is whack-a-mole — the ninth route re-opens the breach silently. The scope now carries
why it was opened, and the policy is enforced at the single decision point. A caller
that does not declare a DD purpose does not get Brave, wherever it lives. Omitting the
purpose is deliberately the safe direction, so every existing non-DD caller keeps
compiling and stops spending. `_DD_BRAVE_PURPOSES` is not env-overridable: an exception
switchable without a deploy is not a rule, and the Anthropic half of this same rule was
broken for days by exactly such an override set to `""` (R-F3942).

DD is unaffected — it opens its own `purpose="dd"` scope and never depended on the
decorator. The eight decorators are kept, because their R-F3087 restoration contract is
still live and tested; their docstring now states that they grant nothing.

The new measurement is falsifiable, which the old one was not:
`rule_one.brave_confined_to_dd` is tri-state (`null` = could not measure, never
"compliant") and `brave_non_dd_grants` must be 0 — a non-DD *refusal* is normal and
merely counted, a non-DD *grant* is a live breach and flips `breached` on its own.
Refusals are counted rather than wired as gaps, for the ledger-flood reason above.

Fixture-first: `test_rf3946_rule_one_brave_confined_to_dd.py`, 13 tests, RED before the
fix and GREEN after. Four pre-existing test files asserted the old contract and were
re-expressed rather than deleted — including `test_rf3087` where the route-decorator
expectation is now inverted with a message naming what a `[True]` reading would mean.


## C-41 · the OpenSanctions quota latch could only ever move toward "spent" (R-F3947)

**Found by the live smoke of C-39's own fix** — which is the useful part of the
story. Verifying that coverage provenance was working, the same machine in the same
minute screened "Rosoboronexport" straight through the OpenSanctions aggregate (real
hit, opensanctions.org entity URL, 24 dataset slugs the local floor does not hold)
while `/api/aria/sanctions/source/status` reported
`quota_exhausted: true, since 2026-07-31T23:04:22+00:00` — thirteen days and one
monthly boundary later. The API was answering; the surface said it was spent.

That reading had already produced a wrong operator recommendation ("upgrade the
OpenSanctions plan"). Nothing needed upgrading.

**The record production held** is the shape that hangs forever — written before
`expires_at` and its key TTL existed, so it has neither:
`{"since": "...", "detail": "...", "action": "..."}`.

**A fix that was tried and REVERTED, recorded because the next reader will reach for
it too.** The obvious move is to derive the missing boundary from `since` using the
module's own `_next_month_start_utc()`. It works, and it is wrong:
`test_opensanctions_quota_flag_lapses` pins the opposite as a deliberate decision —
*"silently flipping them to 'fine' would be inventing a reset nobody observed"* — and
that author is right. The red test was the intent, not the defect (R-F3859). Reversing
a documented decision to green a test of my own would have been the worse outcome.

**The root cause is that nothing cleared the latch on evidence, only on a manual
operator call.** A 429 body sets it; a human was the only thing that could unset it. A
monthly boundary is the *earliest* a spent quota can become unspent, not the only way —
an operator can upgrade the plan mid-month. Meanwhile a 200 from OpenSanctions is
direct proof the quota is not spent: the same evidence class that sets the flag. This
is the shape CLAUDE.md §17 already records for the LLM billing cooldown (R-F3513), "a
cooling provider is never called, so it sustains itself".

So the fix is stronger evidence rather than a better guess. `_note_opensanctions_success()`
retires the record on a real 200, wired into both entry points' success branches. It is
**not** a TTL bump or a retry — both would be the §1 band-aid, a guess about time
standing in for a fact observable on every call.

Three properties are load-bearing and each is pinned:
**one store op per recovery episode**, not one per call — a delete on every successful
screen is the read-modify-write-per-call shape behind the R-F2157/R-F2172 state_store
self-DOS, and fixing an observability bug by saturating the writer is a poor trade;
**a failed clear leaves the latch ARMED** so the next success retries, because marking
recovery before achieving it would strand the record exactly as before, on one store
blip; and **a fresh 429 re-arms it**, since the quota can genuinely be spent again.

§21a: the recovery is an outcome and it was the unobservable one — exhaustion wired a
failure, nothing wired the return to health. `wire_success` fires once per episode, for
the same reason C-39's degraded notice is announce-once.

Fixture-first: `test_rf3947_quota_latch_clears_on_evidence.py`, 10 tests, RED then GREEN.
One collateral repair: `test_rf3031_dd_screen_blob_carries_screened_at` asserted on a
literal source substring plus a fixed 900-char window, so C-39 wrapping that call across
lines broke it with no behaviour change — the R-F3597 line-fragility class. It is now
AST-based, and distinguishes the WAIVED blob (whose `screened_at: None` is honest,
because no screen ran) from one that actually ran.

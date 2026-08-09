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
quarantine — is the actual fix. **Still open: C-14b**, the 6h sweep still walks 99.3%
unvetted tier-4 discovery alphabetically.

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

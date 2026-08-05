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

## A. Blocking gap — the DR-1 evidence does not exist in this repository

Phase 0.1 instructs: *"Reuse Spencer's audit first… Request them; do not redo them."*
**None of those inputs are present.** A repo-wide search for the transition runtime /
capability / state / parity ledgers, `ARIA_ENGINEERING_BRIEF`, the Hardening Charter,
and any DR-1 defect register returned nothing. The only near-match was
`docs/rf557_stream_chat_parity_plan_2026_05_17.md`, which is unrelated.

Consequently **every DR-1 entry in §B below is `UNADJUDICATED`**: the protocol names the
defect class but this repo contains no report, run, or fixture proving the symptom or
locating it. Seeding them with guessed module paths would manufacture exactly the kind
of unfounded certainty the Honesty clause forbids.

**Action required (operator):** supply the DR-1 adjudications and the transition
ledgers, or authorise a fresh adjudication pass against real DD runs. Until then
Phase 3 cannot begin — its loop step 1 is *"write the failing fixture first"*, and a
fixture cannot be written for a symptom nobody has evidenced.

---

## B. The DR-1 dozen — UNADJUDICATED, awaiting evidence

Listed in the protocol's Phase 3 priority order. `Suspected location` is left blank
where the census could not resolve it to a defensible file — a blank is honest, a guess
is not.

| # | Defect class | Sev | Status | Suspected location | Gold fixture |
|---|---|---|---|---|---|
| D-01 | PI-leak gate (0-in-n at chosen bound) | P0 | UNADJUDICATED | — | none |
| D-02 | Matcher surname / dataset gates | P0 | UNADJUDICATED | `lib/aria/entityMatcher.mjs` (DORMANT, 311 LOC, test-only reach) | none |
| D-03 | Status ↔ verdict reconciliation (no GREEN over NOT CLEARED) | P0 | UNADJUDICATED | `aria_service/intel/dd_schema.py`, `lib/reports/pdf_generator.mjs` | none |
| D-04 | Materiality filter (the FRC class) | P1 | UNADJUDICATED | `aria_service/intel/dd_disciplines.py`, `dd_orchestrator.py` | none |
| D-05 | Export-control classifier (no default "civilian") | P1 | UNADJUDICATED | `aria_service/intel/tech_classifier.py` | none |
| D-06 | Financial-verdict vintage (`LAST_KNOWN_WITH_AGE` or refuse) | P1 | UNADJUDICATED | `aria_service/intel/financial_health.py` | none |
| D-07 | PSC second hop | P1 | UNADJUDICATED | test exists (`test_rf3542_psc_second_hop.py`); **implementation not located** | partial |
| D-08 | Waiver rendering on page 1 | P1 | UNADJUDICATED | `lib/reports/pdf_generator.mjs` | none |
| D-09 | Person dedup | P2 | UNADJUDICATED | — | none |
| D-10 | Findings duplication | P2 | UNADJUDICATED | — | none |
| D-11 | Telemetry / `(Phase 2)` leakage | P2 | UNADJUDICATED | — | none |
| D-12 | Truncation artifacts | P2 | UNADJUDICATED | — | none |
| D-13 | Count reconciliation, grade legends | P2 | UNADJUDICATED | — | none |

**On D-07:** `aria_service/tests/test_rf3542_psc_second_hop.py` exists, so the behaviour
was addressed at some point under R-F3542. A targeted grep for a second-hop
implementation across `aria_service/intel`, `aria_service/vetting` and `lib` found no
match — meaning either the logic is named differently or the test drives it indirectly.
Resolve before writing a fixture against it.

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
| `aria-app/` | `next`, `postcss` | HIGH | Image Optimizer DoS; PostCSS XSS + arbitrary file read | semver-major |
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

---

## D. Phase 0.3 runtime overlay — NOT RUN, and it gates all deletion

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
retrospectively. Starting that clock is the single highest-leverage next action, because
every Phase 4 decision waits on it.

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

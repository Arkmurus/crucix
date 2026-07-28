# R-F3349..R-F3357 — ARIA Ecosystem card: the map that could not show its own gap

Operator-driven deep review of the **ARIA Ecosystem** section on `/aria-brain`.
The live header was reproduced EXACTLY from a local rebuild (578 modules / 49
unassigned / 2791 imports / 602 nodes), so the STRUCTURE layer was already honest
and regenerable. Every defect found was in what those numbers MEAN and in what the
UI did with them.

## Shipped

| R | What | Tier |
|---|---|---|
| R-F3349 | Organ assignment: token-boundary matching + 20 declared overrides + `audit_organ_table()` | intel |
| R-F3350 | Vetting organ added; orphans 49 → 4 (91.5% → 99.3% assigned) | intel |
| R-F3351 | The RED completeness alert actually renders | web |
| R-F3352 | Sensor triple labelled; map scope declared; unmapped tiers named by the server | both |
| R-F3354 | `rf2424` could not survive a Windows checkout (CRLF) | test |
| R-F3356 | Completeness-red must never become health-red | test |
| R-F3357 | A deploy tag at HEAD made the banner claim it ships nothing | tooling |

Live: **aria-intel v2710 / aria-web v416, both `build_rev=521e32d2`**, verified by
probing each app and by confirming the deployed SHA's file bytes carry every marker.
R-F3357 is tooling-only and correctly needs no redeploy.

## The four defects that mattered

**1. The honesty claim was dead code.** `ecosystem_map.py` promises an orphan is
"rendered as a RED completeness alert, so the map proves its own gaps instead of
hiding them." R-F2984 replaced the SVG graph with card tiles and left `_ecoFill()`
— which held `if(n.orphan_alert) return red` — defined and never called. The orphan
bucket rendered grey, identical to every unmeasured node, for a 49-module gap. The
server-side guard asserts the API *emits* `orphan_alert` and passed throughout: it
tested the producer while the consumer had been deleted.

**2. Organ assignment matched substrings.** `yaml_reviewer` was filed under
anti-money-laundering via "**aml**" inside "y*aml*_reviewer"; `precall_brief` under
Brain via "re**call**"; `metacognitive.identity` under OSINT via "id**entity**".
`aml` and `recall` matched *only* inside other words. Separately, 13 curated keywords
could never fire — including `run_quarantine`, which CLAUDE.md §1 names the Phase A
gate #4 closer and which was therefore not in the Phase & Scoring organ.

**3. "ARIA Ecosystem" is aria-intel only.** `scan_modules()` globs
`aria_service/**/*.py` and all 20 organs are hardcoded to `aria-intel`, so aria-web
and aria-wa are cards with zero modules — the tier holding auth, billing, Stripe, UI
and the WhatsApp limb. Corroboration this is a real blind spot: the Delivery organ
declares `whatsapp`, `telegram`, `notify`, `briefing`, `proprioception`, and every
one matches zero Python modules.

**4. The header hid the news.** `sensors 30/602 10/18/2` — five unlabelled numbers
meaning only 30 of 602 nodes carry any sensor, and of those, **18 degraded and 2
broken**. Two thirds of everything the system can measure about itself is not green,
set in the smallest text on the card.

## Decisions worth keeping

- **"Longest keyword wins" was measured and REJECTED.** It fixes the 13 shadowed
  keywords but creates ~8 new regressions (`sources.ofac_sdn` → intel_sources,
  gutting Sanctions; `uk_ofsi_ingest` → cli). Keyword length is not a specificity
  metric. Adopted R-F3047's remedy applied to module paths instead.
- **Operator chose to narrow the map's claim, not extend the scan.** The `.mjs`
  scanner is deferred. The unmapped-service list is *derived* from the organ table
  server-side, so mapping a Node organ later retires the warning by itself — a
  hardcoded "~130 Node modules" would have been the same silently-stale claim this
  batch removes.
- **Orphans were not driven to zero.** The 4 that remain are cross-cutting namespace
  roots plus a stray test file in the production tree, kept visible rather than
  excluded to flatter the denominator.

## What the new audit caught — in my own edits

`audit_organ_table()` fails on any shadowed keyword with no declared decision. During
this session it caught three regressions I had just introduced: moving
`reasoning_router` to llm left brain's "reasoning" winning nothing; a bare `learning`
keyword matched the whole subpackage path and stole `learning.deepseek_clients` from
llm and `learning.verification_gate` from guardian (the broad-prefix mistake R-F2986
warned against); and the new `vetting`/`writers` keywords would have re-bucketed four
already-correct modules.

## Two instruments found faulty — both mine

- The **first version of the R-F3356 guard could not fire**. It built the health map
  from `_gather_signals()`, and the live signal set contains no gap whose source
  matches no organ — the only input that exercises the leak. Tamper-testing showed it
  passed *with the regression applied*. Rewritten with a deterministic synthetic HIGH
  gap plus a companion test that applies the tamper and asserts the leak is reproduced.
- The **PowerShell parse check** used an uninitialised `[ref]$errs` and reported
  "syntax OK" regardless. Re-run properly before the result was trusted.

## R-F3247 renamed a symptom; its test pinned the wording

The deploy banner's under-claim was "fixed" by renaming `no-r-tag` →
`no-new-r-numbers`. The condition — the newest `deploy-*` tag pointing at HEAD leaving
the range empty — was untouched, and the guard asserts the new *string* is present, so
the rename satisfied it. Measured live this session: intel deployed first and tagged
the commit, so the web deploy minutes later served `521e32d2ce03 · no-new-r-numbers`
while shipping R-F3351 and R-F3352. Deploying two apps from one commit is the normal
case here. R-F3357 fixes the condition in both writers. Known remaining limit, stated
rather than fixed: `deploy-*` tags are global, not per-app.

## R-F3358 — the root fix R-F3352 deferred (committed, NOT deployed)

`@2a78486b`. The map now scans the Node tiers, so all three services are real:
**578 Python + 127 Node modules** (122 aria-web, 5 aria-wa), 2791 + 203 import
edges, 153 external npm/builtin specifiers counted rather than dropped, 0 Node
orphans, and the R-F3352 unmapped-tier warning retires itself because that list
is derived.

**The design point: for the Node tier the directory IS the organ.** Python needs
keyword inference because `intel/` is a flat bag of ~400 modules; `lib/auth/`,
`lib/billing/` and `lib/telegram/` are already the subsystem boundary. So the Node
side matches on path and infers nothing — structurally immune to the substring
accidents R-F3349 had to remove from the Python side.

**The load-bearing guard is not the Node count — it is that adding a tier does not
perturb the first.** Measured against HEAD: **zero** Python modules changed organ,
Python orphans stayed at 4.

Two completeness enforcers (`test_rf2969_module_tier_equals_scan_modules`,
`test_rf2979_map_module_set_is_exactly_the_filesystem`) had to be **extended, not
relaxed** — they now check the property per tier, and each asserts its own scan is
non-empty so neither can pass vacuously. Softening either to "the Python set is a
subset of the map" would still pass if the Node scan silently returned nothing,
which is exactly the blind spot R-F3352 existed to declare.

⚠️ **NOT DEPLOYED and deliberately NOT ship-marked** (§11 forbids marking shipped
before live). `deploy.ps1` requires `HEAD == origin/main`, and origin contains the
other agent's R-F3353/R-F3355, which the operator instructed must not be deployed —
so any deploy from HEAD would carry them. The live surface stays truthful meanwhile
because R-F3352 already narrowed the claim.

## R-F3365 — wedge #5: R-F3347 fixed one lifespan entry, not the class

`with TestClient(app)` on `aria_service.main` **enters the real lifespan**,
starting ARIA's background subsystems inside the pytest process. They outlive the
block — bound to a loop that is then closed — and the next test that calls
`asyncio.run()` and reaches the embedder waits forever.

R-F3347 diagnosed this exact mechanism and named this exact victim
(`rf1401:209 → run_eval → to_thread(_cosine_score) → model.encode() →
GetQueuedCompletionStatus`). It fixed **one** entry point — `test_lifespan_smoke.py`,
via subprocess — and did not sweep the others. **The wedge never closed; it moved.**

Bisected over the 336 files collected before `rf1401`, halving to a single class:
`test_rf1231_agent_signup_vault.py::TestVaultAPI`. The pytest-timeout dump showed
the leak directly — eight `asyncio_N` threads, six `rf704-wedge-watchdog`,
`continuous-profiler`, a `_patched_worker` — all surviving into a test that never
started them. Two-file reproduction: **hung before, 36 passed in 12s after.**

| File | Sites | Action |
|---|---|---|
| `test_rf1231` | 1 | client without the context manager — 25 passed, 2.1s (was booting the whole brain) |
| `test_rf1411` | 3 | same via a local `_client()` helper — 11 passed |
| `test_rf2379` | 11 | **declared exception, deliberately NOT fixed** |
| `test_rf3365` | — | the guard R-F3347 lacked |

❌ **CORRECTED by R-F3370 — the `rf2379` allowlist entry was WRONG.** I recorded
that it "genuinely needs the started app (44 passed with lifespan vs 11 failed
without)". The measurement was real; the interpretation was not. **`rf2379`
defines its own `app` FIXTURE** (a bare FastAPI with the DD router and auth
overridden) and all 11 of its `with TestClient(app)` sites take *that* app — its
single `from aria_service.main import app` is in an unrelated helper that only
reads `app.routes`. **It never entered a lifespan at all.** The 11 failures came
from my own fix attempt swapping in the REAL app, so real auth returned 401.

The guard had matched any `with TestClient(app)` in a file that *mentioned*
`aria_service.main` anywhere — a coincidence, not a resolution — and I then wrote
the false positive into an allowlist with a confident justification. 🔑**A guard
that cannot resolve its own subject manufactures exceptions to itself.** R-F3370
resolves the name (a parameter called `app` is fixture injection and shadows the
module import), and **the allowlist is now EMPTY, which is the honest state.**

**The guard's first draft was itself wrong** — a regex that flagged the docstrings
of the very files it had just fixed, because prose explaining the banned pattern
counted as the banned pattern. Rewritten as an AST walk, then tamper-tested:
re-introducing the pattern fails it, removing it passes.

### Wedge #5 proof, and the baseline that still could not be measured

**Proven over 88 files in one process:** the range that used to wedge (files
253–340, containing both the poisoner `rf1231` at #271 and the victim `rf1401` at
#337) now runs to completion — **4 failed, 746 passed in 3:14, zero timeouts**,
`rf1401` neither hung nor failed. The four failures are pre-existing state_store
connection tests (`rf1352` read self-heal, `rf1388` dead-connection upsert ×2,
`rf1261` token endpoint HTML), none of them in anything this session touched.

⚠️ **The full single-process baseline STILL could not be measured — for a second,
unrelated reason.** Four consecutive BACKGROUND runs were killed by something
outside the session: they reached 3%, 19%, 11%, then 0 bytes (killed before
emitting output). No summary, no `EXIT` marker, no surviving processes.
- Not a time limit — the progression is non-monotonic (19% then 11%).
- Not this session's concurrency — the third kill happened while completely idle.
- Cause unidentified; needs whoever administers the machine.

**Foreground runs are unaffected** (the 88-file slice above completed normally),
so baselines can be built from foreground segments — with the standing caveat that
segmented runs cannot see order-dependent failures (149 segmented vs 165
single-process = 16 invisible). §16's "new R-numbers must not add to the failing
count" presumes a suite that can finish; this is now the second independent reason
this repo has never had one.

## R-F3368 / R-F3370 / R-F3371 — the closing sweep

**R-F3368 — the floor document was ~3x stale.** CLAUDE.md §16 had carried
*"3647 tests / 72 failing"* since May. Measured at HEAD: **94 failed / 11,673
passed / 11,767 tests**, all 1472 files, zero gaps, zero timeouts — recorded with
the full failing-test SET in `docs/suite_baseline_2026_07_28.md`, because this
repo judges by failure-set diff and a count cannot support that. **None of the 94
is in any file this session touched.** Two caveats sit *with* the number: it was
built from 13 foreground segments (background pytest is killed externally here),
and segmented runs cannot see order-dependent failures, so **94 is a FLOOR**.

**R-F3370 — corrected my own shipped guard** (see above). The lesson is the one
this whole session keeps producing: verify the instrument before trusting its
verdict, and an allowlist entry is a standing exemption that must be *earned*.

**R-F3371 — the banner claimed R-numbers the build did not ship.** Two measured
over-claims: a code commit *citing* an older R-number put it in the banner
(R-F3347), and a docs-only commit announced one for a build it changed nothing in
(R-F3368). This is the **third** instalment of one family — R-F3247 removed
reservation commits, R-F3357 removed the empty-range case — and both predecessors
left the real assumption intact: **that a commit MESSAGE is evidence of what a
build CONTAINS.** It is not. A commit now contributes its own R-number, and only
if it changes a file that reaches the image. Measured before/after on the live
range: `R-F3347+…+R-F3368+…` → `R-F3365+R-F3366+R-F3367+R-F3369+R-F3370`.

## R-F3373 — the background-kill item, root-analysed and closed

**Concluded by elimination, not assertion:**

| Hypothesis | Evidence | Verdict |
|---|---|---|
| Background execution unreliable | a minimal 25-min heartbeat ran to completion, **exit 0** — 3× pytest's best | ❌ ruled out |
| Blanket time limit | an earlier full-suite background run survived **1h20m** | ❌ ruled out |
| Antivirus | Kaspersky log **empty** in the window; Defender routine only | ❌ ruled out |
| Process crash | **zero** Windows Error Reporting entries for python | ❌ terminated, not faulted |
| OS resource exhaustion | no System event 2004; 5.6 GB virtual headroom | ❌ not supported |

→ **The kill tracks the WORKLOAD.** The suite loads torch/chromadb and spawns an
encode-offload child (R-F3347) on a 7.7 GB box with ~0.5 GB free. ⚠️**This
remains a HYPOTHESIS and is labelled as such** — the experiment that would settle
it (a heavy run under a memory sampler) was **deferred, not guessed**, because a
deliberately memory-exhausting run risked destabilising the second agent's eval
work on the same machine. Re-run it when the box is quiet.

**★THE DEEPER ROOT, and the reason the symptom mattered:** `conftest.py` (R-F927)
states that CI runs **ONLY** `test_imports.py` + `test_lifespan_smoke.py` with a
minimal dep set (no torch/sentence-transformers) — **deploys are not gated on the
full suite at all.** So §16 ("new R-numbers must not add to the failing count")
had no machine anywhere: the baseline was whatever someone last measured by hand,
which is exactly how CLAUDE.md's figure sat ~3x stale for two months.

**R-F3373 ships `scripts/admin/suite_baseline.py`** — foreground segments (immune
to the kill), diffing the **failure SET** against `docs/suite_baseline.json` (a
flat count hides one test breaking as another is fixed), exit 1 on any new
failure. 🔑A partial/resumed run **suppresses the "fixed" list** with the reason
printed: every un-executed test would otherwise read as fixed — the same class of
false win this whole session removed. New-failure detection stays live, because a
test that failed really did fail.

## R-F3378 — CI had been structurally dead for two months

`.github/workflows/ci.yml` failed **in 0 seconds on every push since 2026-05-29**.
Zero seconds = it died at workflow *parse*; nothing ran. GitHub's annotation:
"This run likely failed because of a workflow file issue", and `gh workflow list`
showed it by **path** rather than name — what GitHub renders when it cannot read
a file.

**Root, bisected:** R-F1073 (`64fd5e4f`) replaced indented inline python with
**column-0 python** inside `run: |` block scalars. Column-0 content terminates the
scalar. Proven version-by-version: `20e069f1` VALID → `64fd5e4f` INVALID → all
since INVALID. 🔑The irony is exact — R-F1073's own header reads *"Each step FAILS
THE BUILD on error"*, and it is the commit that stopped every step from running.

Indenting back would fix the YAML and break the Python, so the three blocks are
now real files under `scripts/ci/`. Second defect found alongside: the test step
piped `pytest` into `tail -50`, and **a bash pipeline exits with its LAST
command's status** — so no test failure could ever fail it. Removed; step declared
advisory; a guard now fails if any workflow pipes pytest again.

⚠️**I nearly fixed the wrong thing.** PyYAML flagged every recent version invalid,
so I suspected my instrument — but the known-good May version parsed cleanly.
PyYAML was right; my *sample* was entirely post-break. And `conftest.py`'s "CI runs
ONLY test_imports + test_lifespan_smoke" describes `test-aria.yml`, not all of CI —
I believed it until I checked. Corrected in place.

**★WHAT THE REVIVED CI IMMEDIATELY FOUND — a CRITICAL RCE hidden for 2 months.**
`npm audit --omit=dev --audit-level=critical` fails: **protobufjs 6.8.8, arbitrary
code execution** (GHSA-xq3m-2v4x-88gg) via `baileys → libsignal → protobufjs`,
plus sharp (high, libvips CVEs) and uuid/node-cron.
🔑**Severity scoped by evidence, not assumed:** `aria-web` ships the package but has
**no `WA_LISTENER_*` secrets**, so `mountWAListener` returns early and no socket
opens — not reachable at runtime there. **`aria-wa` has `WA_LISTENER_ENABLED`
deployed** and is the tier parsing untrusted WhatsApp data — that is the live
exposure, and §16's isolation limits the blast radius.
⚠️**No safe fix today:** `npm audit fix` does NOT resolve it (protobufjs is pinned
by libsignal — still listed after a dry run); patched line is 7.6.3+/8.x; baileys
latest is **7.0.0-rc13, a release candidate**. Forcing an override across a major
would likely break libsignal — and [[aria_web_ux_deep_review_rf3070_3079_2026_07_25]]
records two dependency bumps that already broke auth + rate limiting silently
here. Left as an operator risk-decision with the options written down, NOT
auto-bumped.

## Open / handoff

- ⚠️ **Suite wedge #5, not investigated.**
  `test_rf1401_held_out_split_eval::test_run_eval_with_train_split_filters` **passes
  alone (11 passed, 21s) and hangs in-suite**, parked in `asyncio.run(run_eval(...))`
  → IOCP. Nothing in this session touches `eval_runner`. **Check first:** that run had
  concurrent pytest invocations against shared `data/`, which could itself be the
  poisoner — re-run clean single-process before hunting. Deliberately left alone: the
  operator was away and a second Claude agent was active in the same tree, and a wedge
  hunt needs exclusive use of `data/`.
- **The `.mjs` scanner + import walker** remains the open follow-up to make all three
  services real on the map.
- Mid-session, `git push` returned 403 — `gh` was authed as **Blackbaggroup** against
  the **Arkmurus** repo, and `deploy.ps1` has a hard push guard, so nothing could
  deploy until the operator re-authed.
- A second Claude agent pushed R-F3353 and R-F3355 during the session. Rebased cleanly;
  commit contents verified with `git show --stat` rather than the exit code.
- The deploy script reported `[FAIL]`, but that was the **post-deploy** health probe
  timing out — `[PASS] aria-intel LIVE - build_rev=521e32d2 matches commit (version
  2710)` came first. §11c(b) slow boot; the machine was not restarted.

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

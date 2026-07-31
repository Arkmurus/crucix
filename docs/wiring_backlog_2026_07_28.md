# §21 brain-wiring backlog — triaged 2026-07-28

**26 intel modules** flagged (was 50; see R-F3563 below) by `scripts/ci/wiring_audit.py`. CI blocks on this by
operator decision: honest red until triaged.

## How the number moved

| stage | modules | what changed |
|---|---|---|
| original | 72 | checker matched only the literals `wire_success(` / `wire_failure(` |
| R-F3381 | 68 | taught it the FAILURE-side §21a sinks (`@fail_wire`, `record_gap`, …) |
| R-F3382 | 56 | taught it `@wired`, the PREFERRED decorator covering BOTH branches |
| R-F3386 | 54 | wired the EE/NO registry adapters for real (tranche 1) |
| R-F3388 | 50 | dd_independent_verifier (tranche 3) — it failed OPEN on every error path, manufacturing independence |
| R-F3387 | 51 | verification layer: citation_verifier, claim_grounding, corroboration (tranche 2) — and two of them were reporting CLEAN on crash |
| R-F3563 | 26 | NO-WIRING tier CLOSED: 14 modules wired for real, 14 pure transforms exempted with a per-module reason |

Three corrections were attempted and reverted or rejected on measurement — recorded
so they are not re-attempted:

1. Clearing the half-wired categories on a FAILURE-side sink (72 → 52). `@fail_wire`
   and `record_gap` say nothing about the SUCCESS branch.
2. Listing the 11 `@wired` modules as violations (R-F3381's first backlog). They were
   correctly wired; the detector could not see it.
3. **Making the gate per-function** (R-F3385). Measured: 562 unwired entry points
   across 157 modules, or 342/121 when narrowed to those with a real failure mode —
   dominated by accessors (`list_pending`, `record_query`, `audit_log.record`) that
   have no engine outcome. It would flood the ledgers and get the gate muted. §21a is
   about ENGINE paths; per-module is the honest granularity for a textual gate, and
   per-function correctness is THIS document's job, decided per module.

## How to work it

Wire with `@wired` where exceptions propagate. Where a module deliberately swallows
(so an outage cannot crash a DD), add an explicit `wire_success` on the path that
really got data — carrying a COUNT, so "answered but empty" is distinguishable from
"errored" and from "nobody asked". R-F3386 does exactly this for `ariregister` and
`brreg` and is the worked example.

## Buckets

### ENGINE (40)

Real §21 violations — error paths and/or network I/O. **Wire these.**

- `intel/sources/ais_gap_detector.py` — no wiring at all (fns=3, try=1, net=n)
- `intel/audit_trail.py` — no wiring at all (fns=6, try=4, net=n)
- `intel/knowledge_packs/balkans_seed.py` — success wired, FAILURE missing (fns=2, try=7, net=n)
- `intel/behavioural_anomaly.py` — failure wired, SUCCESS missing (fns=12, try=5, net=n)
- `intel/brain_hook_bg.py` — failure wired, SUCCESS missing (fns=2, try=8, net=n)
- `intel/calibration_auto_tune.py` — failure wired, SUCCESS missing (fns=3, try=7, net=n)
- `intel/circuit_breaker.py` — success wired, FAILURE missing (fns=10, try=2, net=n)
- `intel/compliance_workflow.py` — failure wired, SUCCESS missing (fns=12, try=1, net=n)
- `intel/content_scanner.py` — failure wired, SUCCESS missing (fns=11, try=8, net=Y)
- `intel/continuous_learner.py` — failure wired, SUCCESS missing (fns=10, try=11, net=Y)
- `intel/credential_self_destruct.py` — failure wired, SUCCESS missing (fns=8, try=12, net=n)
- `intel/dd_case_archive.py` — failure wired, SUCCESS missing (fns=7, try=6, net=n)
- `intel/deal_pipeline.py` — success wired, FAILURE missing (fns=21, try=15, net=n)
- `intel/deep_researcher.py` — success wired, FAILURE missing (fns=28, try=43, net=Y)
- `intel/document_reader.py` — success wired, FAILURE missing (fns=26, try=32, net=Y)
- `intel/sources/eccn_lookup.py` — failure wired, SUCCESS missing (fns=6, try=2, net=n)
- `intel/eval_judge.py` — failure wired, SUCCESS missing (fns=4, try=2, net=n)
- `intel/extractors/facts.py` — no wiring at all (fns=13, try=6, net=n)
- `intel/fca_register.py` — no wiring at all (fns=13, try=2, net=Y)
- `intel/github_search.py` — failure wired, SUCCESS missing (fns=5, try=4, net=Y)
- `intel/knowledge_packs/latam_asia_pac_seed.py` — success wired, FAILURE missing (fns=2, try=8, net=n)
- `intel/log_redaction.py` — no wiring at all (fns=9, try=1, net=n)
- `intel/memory_wal.py` — failure wired, SUCCESS missing (fns=8, try=8, net=n)
- `intel/ocr.py` — success wired, FAILURE missing (fns=34, try=22, net=Y)
- `intel/scraper/orchestrator.py` — no wiring at all (fns=2, try=1, net=n)
- `intel/phase_gates.py` — no wiring at all (fns=5, try=8, net=n)
- `intel/scraper/playwright_engine.py` — no wiring at all (fns=12, try=23, net=n)
- `intel/scraper/procurement_adapters.py` — no wiring at all (fns=4, try=5, net=n)
- `intel/quarantine_network.py` — failure wired, SUCCESS missing (fns=10, try=6, net=n)
- `intel/quota_client.py` — no wiring at all (fns=2, try=1, net=Y)
- `intel/registration_check.py` — failure wired, SUCCESS missing (fns=6, try=3, net=n)
- `intel/registry_coverage.py` — no wiring at all (fns=7, try=4, net=n)
- `intel/research_tasks.py` — failure wired, SUCCESS missing (fns=20, try=11, net=n)
- `intel/sipri_ingest.py` — failure wired, SUCCESS missing (fns=6, try=5, net=Y)
- `intel/extractors/structured.py` — no wiring at all (fns=9, try=13, net=n)
- `intel/wire.py` — failure wired, SUCCESS missing (fns=8, try=4, net=n)
- `intel/wiring_harness.py` — failure wired, SUCCESS missing (fns=16, try=9, net=Y)
- `intel/multi_lang/yaml_reviewer.py` — no wiring at all (fns=1, try=1, net=n)
- `intel/zefix.py` — failure wired, SUCCESS missing (fns=6, try=2, net=Y)

### PURE-HELPER (11)

No network, no try/except. **Exemption candidates**, each needing its own justification
like `grounding_reward` (R-F2033). Do NOT bulk-exempt.

- `intel/country_sanctions.py` — failure wired, SUCCESS missing (fns=3, try=0, net=n)
- `intel/dd_independence_eval.py` — no wiring at all (fns=11, try=0, net=n)
- `intel/multi_lang/docker_reviewer.py` — no wiring at all (fns=1, try=0, net=n)
- `intel/scraper/generic_adapter.py` — no wiring at all (fns=2, try=0, net=n)
- `intel/multi_lang/go_reviewer.py` — no wiring at all (fns=1, try=0, net=n)
- `intel/loop_monitor.py` — no wiring at all (fns=4, try=0, net=n)
- `intel/sanctions_canonical/normalise.py` — no wiring at all (fns=3, try=0, net=n)
- `intel/multi_lang/rust_reviewer.py` — no wiring at all (fns=1, try=0, net=n)
- `intel/multi_lang/shell_reviewer.py` — no wiring at all (fns=1, try=0, net=n)
- `intel/multi_lang/sql_reviewer.py` — no wiring at all (fns=1, try=0, net=n)
- `intel/multi_lang/ts_js_reviewer.py` — no wiring at all (fns=1, try=0, net=n)


---

## R-F3563 (2026-07-31) — the NO-WIRING tier is closed: 50 → 26

CI's `test` job reached this gate for the first time in two months (R-F3556 fixed
the quadratic pre-commit hang that had been failing before it). Every module that
had **no sink of any kind** is now resolved, one at a time — no batch exemption.

**WIRED FOR REAL (14).** Each at its actual success and failure branches:

| module | where |
|---|---|
| find_case_law | `search_by_party` — success, HTTP-error and exception branches, by hand |
| employment_tribunal, fca_register, gazette, registry_trust | `@wired` on the search/lookup entry |
| scraper: generic_adapter, orchestrator, playwright_engine, procurement_adapters | `@wired` on `fetch` / `fetch_portal` |
| quota_client, phase_gates, registry_coverage | `@wired` on the primary operation |
| dd_evidence_recorder | success receipt + the swallowed-exception branch — its own docstring warns a corpus of happy paths is the failure mode |
| audit_trail | persist success, and a failed flush now raises AND signals |
| loop_monitor | **breach** signal (loop starved) + **healthy** signal, both rate-limited to 5 min |

`loop_monitor` is the one worth reading twice: `record_lag` runs once a second, so
a per-sample signal would flood the ledgers exactly as `cost_tracker` and
`grounding_reward` would — which is why those two are already exempt. The event
worth telling the brain is a *breach*, not a sample.

**EXEMPTED AS PURE TRANSFORMS (14), each with a stated reason** in
`WIRING_EXEMPT_MODULES`. §21a asks whether a path's success and failure reach the
brain; that question only has meaning for a path that DOES something externally.
A pure function of its arguments has no failure the brain can act on:

- 7 `multi_lang` reviewers (docker, go, rust, shell, sql, ts_js, yaml) — static
  analysers over source text handed to them
- `facts` ("regex-based fact extractor — zero LLM"), `structured` (parses HTML it
  is GIVEN; the *fetch* is the wired path), `normalise` (pure string work)
- `dd_independence_eval` (offline scoring harness), `ais_gap_detector` (computes
  gaps from a track it is handed)
- `log_redaction` — runs INSIDE the logging filter chain, so wiring it would be
  recursive: a brain signal from a log filter re-enters logging

**WHAT REMAINS: 26, all one-branch.** Every one has wiring infrastructure already
and emits on exactly one side — `wire_failure` without `wire_success`, or the
reverse. That is a different and more delicate problem than "no sink at all":
deciding what SUCCESS means for `memory_wal`, or what FAILURE means for
`deal_pipeline`, is a per-module judgement, and a mechanical `wire_success()` at
the end of each function would be precisely the cosmetic wiring §21a exists to
prevent. It is real work, not a formality, and it is the next tranche.


---

## R-F3565 (2026-07-31) — nine of the 26 were the DETECTOR: 26 → 17

The gate was failing on modules that were **already correctly wired**. That is the
worse failure mode of the two: a slow gate gets waited on, a lying gate gets muted
(see `assert-the-property-not-the-wording`). Two classes, both closed:

1. **An aliased import was invisible.** `knowledge_packs/balkans_seed.py` and
   `latam_asia_pac_seed.py` do `from ..engine_wiring import wire_success,
   wire_failure as _wf` and then call `_wf(...)`. The scan tested
   `"wire_failure(" in content`, so it reported them as missing a branch that was
   wired three lines below the `wire_success` it *did* see. Now resolved from the
   AST: any `asname` bound to `wire_success` / `wire_failure` counts as that call.

2. **`@fail_wire` did not credit the failure branch.** It is a genuine
   failure-side sink — it routes every unhandled exception to `record_gap` — and
   `ocr.py`, `deep_researcher.py` and `document_reader.py` each carried it
   *alongside* a `wire_success`, i.e. both branches covered, and were reported as
   missing the failure branch. The rule was already written correctly in the
   comment; only the "no sink at all" verdict consulted it.

**The asymmetry is deliberate and survives.** A failure-side sink credits the
FAILURE branch only, never SUCCESS. Crediting it for both is the R-F3382 clamp
(72 → 52) that was reverted on measurement: `@fail_wire` says nothing about
whether the happy path ever tells the brain it ran, and a module that only reports
failures leaves the brain unable to distinguish *ran fine* from *never ran*.

Guarded by four tests in `test_rf3556_precommit_gate.py`, including
`test_a_failure_sink_does_NOT_satisfy_the_success_branch` and a no-sink-at-all
case so the fix cannot become a way to make the backlog disappear.

### WHAT REMAINS: 17, all missing the SUCCESS branch

Re-measured, not carried over. Every one has a real failure sink — a
non-comment `wire_failure(...)` call or a `@fail_wire` decorator — and no success
signal at all:

| module | failure sink | note |
|---|---|---|
| `behavioural_anomaly` | 1 call | |
| `brain_hook_bg` | 2 `@fail_wire` | |
| `calibration_auto_tune` | 2 calls | |
| `compliance_workflow` | 9 `@fail_wire` | |
| `content_scanner` | 2 calls + 9 `@fail_wire` | network |
| `country_sanctions` | 2 `@fail_wire` | |
| `credential_self_destruct` | 1 call | |
| `dd_case_archive` | 2 calls | |
| `eval_judge` | 2 calls | |
| `memory_wal` | 4 `@fail_wire` | |
| `quarantine_network` | 3 calls | |
| `registration_check` | 4 `@fail_wire` | |
| `sipri_ingest` | 3 `@fail_wire` | network |
| `sources/eccn_lookup` | present | |
| `zefix` | 2 calls | network |
| `wire` | 3 calls | **see below** |
| `wiring_harness` | scanner strings | **see below** |

`wire.py` and `wiring_harness.py` are the wiring machinery itself, not engines.
`wire.py`'s own docstring states design constraint #1: *"FAILURE-ONLY — never
wire_success on every call (would wedge the loop). Success stays at path-level
entry points only (§21a)."* Adding a `wire_success` to the module that exists to
emit failures would contradict the rule it implements, and `wiring_harness.py`
matches only because its AST scanner contains the decorator name as a string.
They are listed here rather than silently exempted so the decision is visible; the
exemption belongs in `WIRING_EXEMPT_MODULES` with that reason, not in a widened
count.

The other 15 are real work of the delicate kind R-F3563 described: deciding what
*success* means for `memory_wal` or `credential_self_destruct` is a per-module
judgement, and a mechanical `wire_success()` at the end of every function is
exactly the cosmetic wiring §21a exists to prevent.

**Correction to the R-F3563 list above:** `extractors/facts.py` and
`extractors/structured.py` are recorded there as exempt pure transforms. R-F3564
removed both exemptions on measurement — 13 and 6 swallowed exceptions
respectively, feeding the DD evidence path. Classified from docstrings rather than
from the code; the measurement was right.

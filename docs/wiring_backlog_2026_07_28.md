# §21 brain-wiring backlog — triaged 2026-07-28 (R-F3381, corrected by R-F3382)

**56 intel modules** genuinely flagged by `scripts/ci/wiring_audit.py`.
CI blocks on this by operator decision: honest red until triaged.

## How the number moved, and why each step was legitimate

| stage | modules | what changed |
|---|---|---|
| original | 72 | checker matched only the literals `wire_success(` / `wire_failure(` |
| R-F3381 | 68 | taught it the FAILURE-side §21a sinks (`@fail_wire`, `record_gap`, `brain_hook.absorb`, …) |
| R-F3382 | 56 | taught it `@wired`, the PREFERRED decorator that covers BOTH branches |

**Two clamps were attempted and reverted along the way** — recorded so they are not re-attempted:

1. Clearing the half-wired categories on a FAILURE-side sink (72 → 52). `@fail_wire`
   and `record_gap` say nothing about the SUCCESS branch, which is exactly what
   those categories check.
2. Listing the 11 `@wired` modules as real violations (the first version of this
   file). They are correctly wired the preferred way; the detector could not see it.

**Known limit, stated not implied:** this is textual. A module decorating SOME entry
points with `@wired` and leaving others bare reads as fully wired. Closing that needs
per-function analysis — the backlog's job, not the gate's.

## Buckets

### ENGINE (45)

Real §21 violations — error paths and/or network I/O. **Wire these**, preferably with `@wired`.

- `intel/sources/ais_gap_detector.py` — no wiring at all (fns=3, try=1, net=n)
- `intel/ariregister.py` — failure wired, SUCCESS missing (fns=3, try=1, net=Y)
- `intel/audit_trail.py` — no wiring at all (fns=6, try=4, net=n)
- `intel/knowledge_packs/balkans_seed.py` — success wired, FAILURE missing (fns=2, try=7, net=n)
- `intel/behavioural_anomaly.py` — failure wired, SUCCESS missing (fns=12, try=5, net=n)
- `intel/brain_hook_bg.py` — failure wired, SUCCESS missing (fns=2, try=8, net=n)
- `intel/brreg.py` — failure wired, SUCCESS missing (fns=8, try=1, net=Y)
- `intel/calibration_auto_tune.py` — failure wired, SUCCESS missing (fns=3, try=7, net=n)
- `intel/circuit_breaker.py` — success wired, FAILURE missing (fns=10, try=2, net=n)
- `intel/citation_verifier.py` — no wiring at all (fns=3, try=1, net=n)
- `intel/claim_grounding.py` — no wiring at all (fns=4, try=1, net=n)
- `intel/compliance_workflow.py` — failure wired, SUCCESS missing (fns=12, try=1, net=n)
- `intel/content_scanner.py` — failure wired, SUCCESS missing (fns=11, try=8, net=Y)
- `intel/continuous_learner.py` — failure wired, SUCCESS missing (fns=10, try=11, net=Y)
- `intel/corroboration.py` — no wiring at all (fns=8, try=3, net=n)
- `intel/credential_self_destruct.py` — failure wired, SUCCESS missing (fns=8, try=12, net=n)
- `intel/dd_case_archive.py` — failure wired, SUCCESS missing (fns=7, try=6, net=n)
- `intel/dd_independent_verifier.py` — no wiring at all (fns=29, try=3, net=n)
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

No network, no try/except. **Exemption candidates**, each needing its own justification like
`grounding_reward` (R-F2033: pure, deterministic, no success/failure branch). Do NOT bulk-exempt.

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

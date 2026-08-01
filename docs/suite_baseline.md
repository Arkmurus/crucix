# Suite baseline — THE authoritative record

> **This file supersedes `suite_baseline_2026_07_28.md` and
> `suite_baseline_2026_07_30.md`.** Both are retained only as the history section at
> the bottom. There is ONE baseline; do not add a fourth file.

## Current baseline — 2026-08-01, and it is the first PROVABLY CLEAN one

```
VALID=YES
112 failed, 13,725 passed, 7 skipped, 2 xfailed  —  27:59
sha 0c3e853d   tree b4dd74f5f106b0eb (identical before AND after)
```

**Command** (single process, no env overrides, network guard ON):

```
python -m pytest aria_service/tests/ -q --tb=line -p no:cacheprovider --timeout=600
```

**⚠️ This figure is STALE as of 2026-08-01 and must be re-measured before it is quoted
again.** Eight R-numbers landed after `0c3e853d` (R-F3605, R-F3609/3610/3611,
R-F3617/3618/3619, R-F3620, R-F3621) and at least one previously-red test has turned
green. The number above describes a tree that no longer exists. Re-measure on a QUIET
tree — see below — and replace this block; do not add a fourth baseline file.

**R-F3622 — how to measure it, and why the old instruction did not work.**
This section used to read *"Measured by `scratchpad/measure.py`, which snapshots a
SHA-256 over every tracked `aria_service/**/*.py` before and after the run and prints
`VALID=YES|NO`."* **That file never existed in the repo.** It was written into a
session scratchpad and went with the session, so the one number this repo treats as
authoritative could not be reproduced by anyone — and the check that made it
trustworthy was not part of the committed tool that records baselines.

The validity check now lives in that tool, `scripts/admin/suite_baseline.py`:

```
python scripts/admin/suite_baseline.py            # run + diff the FAILURE SET, prints VALID=YES|NO
python scripts/admin/suite_baseline.py --record   # re-record docs/suite_baseline.json
```

It hashes every tracked `aria_service/**/*.py` before and after the run, prints
`VALID=YES|NO`, and **refuses to `--record` when the tree moved** — a baseline measured
while the code under test changed is not a baseline. `VALID=NO` means DISCARD, not
publish.

Two limits, both load-bearing when you quote a number:

- That script runs **foreground segments**, which cannot see order-dependent failures
  (the repo's own record is 149 segmented vs 165 single-process). It measures a
  **FLOOR**.
- The `112` above came from a **single-process** run of the command shown, which is the
  §16 figure. To reproduce THAT, run the command directly and bracket it with
  `python -c "import sys; sys.path.insert(0,'scripts/admin'); ..."` — or simply record
  the segmented floor and say so. Do not present a segmented count as the §16 number.

### Why the validity record exists — read this before quoting any number

R-F3597. `inspect.getsource(func)` takes the line range from the **imported** code
object and slices the file **from disk at call time**. On a tree two agents share, a
commit landing mid-run shifts those lines and getsource returns **a different
function's body** — silently, because the wrong slice is still valid Python. The only
symptom is an assertion that quietly stops matching.

Measured on Python 3.14.3: after 7 lines were inserted above a function,
`getsource` returned `'# c
# c
...

def target_function():
    """'` — a
misaligned block starting at the stale offset.

**This corrupted the first two attempts at this baseline:**

| run | result | verdict |
|---|---|---|
| 1 | 147 failed / 1:16:56 | **DISCARD** — 4 peer commits mid-run touched `routes/aria.py` + `aria_engine.py` |
| 2 | 110 failed / 50:52 | **DISCARD** — 5 commits mid-run, one of them mine |
| 3 | *(completed)* | **LOST** — the harness itself destroyed the output (`capture_output` returned None) |
| 4 | **112 failed / 27:59** | **VALID=YES** |

🔑 **`VALID=NO` means discard the number, not publish it.** The corruption is silent,
so an invalid run looks exactly like a valid one.

⚠️ **Neither historical figure below can be shown to be clean.** They were measured
before this mechanism existed. They are not necessarily wrong; they are unprovable.

### The 147 was NOT a regression

Run 1's extra ~37 failures were the artefact, not defects. Proof by failure-set diff:

```
rf409 (inspect.getsource on routes/aria.py):  run 1 = 8 failures   run 2 = 0
rf730/732/733/734 (getsource on aria_engine): run 2 = 0
```

Run 1's peer commits touched exactly those two files; run 2's touched WA/binding UI,
so the same tests passed. **The real baseline was ~110 throughout.**

Corollary worth stating: the R-F3597 source-probe conversions removed **zero**
failures from run 4 versus run 3, because those tests were not failing in a clean run
to begin with. The fix prevents the artefact recurring; it does not lower a clean
count. An earlier prediction of ~95 was wrong for exactly this reason.

## Two failures that are flaky, not fixed

Both **pass in isolation** (8/8) and failed in run 4 only:

```
test_rf2144_chunked_knowledge_load.py  — chunked sidecar read starved the loop 345ms
test_rf2200_neural_index_offload.py    — event loop stalled 258ms (must be <250ms)
```

These assert **event-loop latency against a hard threshold** and missed by 8ms and
95ms. NOT diagnosed. Note a faster machine would make them pass, so "the box was
idle" does not explain them — do not assume that reading. Judge the suite by the
failure SET, never the count, precisely because of entries like these.

## The 112, by file

- **test_rf933_compliance_watch_capture.py** (6)
  - `test_rf933_capture_never_raises_on_backend_failure`
  - `test_rf933_capture_persists_full_attribution`
  - `test_rf933_get_captured_newest_first_and_group_filter`
  - `test_rf933_hash_links_to_previous_record`
  - `test_rf933_tamper_is_detected`
  - `test_rf933_verify_chain_clean`
- **test_rf3489_mem0_recall_is_owner_scoped.py** (5)
  - `test_capability_one_user_cannot_read_anothers_notebook`
  - `test_single_tenant_operators_can_opt_in`
  - `test_system_scope_exists_for_internal_verification`
  - `test_the_other_user_sees_only_their_own`
  - `test_the_owner_suffix_does_not_break_provenance_rendering`
- **test_rf2185_load_governor.py** (3)
  - `test_rf2185_calm_does_not_shed`
  - `test_rf2185_engine_gate_skips_tick_under_pressure`
  - `test_rf2185_stalls_age_out_and_resume`
- **test_rf934_936_compliance_watch_pipeline.py** (3)
  - `test_rf934_analyse_window_integrates`
  - `test_rf935_urgent_only_silent_without_high_finding`
  - `test_rf936_coverage_report_gap`
- **test_operator_pending_rf561.py** (2)
  - `test_manual_items_are_always_in_blockers_when_p1`
  - `test_priority_one_items_sorted_first`
- **test_redis_set_size_warning.py** (2)
  - `test_set_no_warn_on_sqlite_backend_even_above_default`
  - `test_set_still_errors_on_sqlite_above_25mb`
- **test_rf1128_protected_file_filter.py** (2)
  - `TestProtectedFileFilter::test_filters_protected_file_gap`
  - `TestProtectedFileFilter::test_protected_set_contains_key_files`
- **test_rf1388_upsert_propagates_failure.py** (2)
  - `test_upsert_raises_on_dead_connection`
  - `test_upsert_triggers_self_heal_on_dead_connection`
- **test_rf1498_portal_requirements_email.py** (2)
  - `test_determine_only_still_classifies_the_local_blockers`
  - `test_requirements_email_composes_an_honest_body`
- **test_rf1714_newsapi_full_onboarding.py** (2)
  - `test_newsapi_config_has_real_fields_and_no_email_verify`
  - `test_newsapi_full_chain_uses_real_creds_and_activates`
- **test_rf2003_brain_opportunities.py** (2)
  - `test_opportunities_are_grounded_only`
  - `test_pipeline_failure_is_nonfatal`
- **test_rf2395_capability_test_gate_genuine.py** (2)
  - `test_genuine_capability_test_pass_allows_autodeploy`
  - `test_tests_disabled_forces_stage_only_never_autodeploy`
- **test_rf2432_truncating_fixer_blocked_end_to_end.py** (2)
  - `test_complete_fix_autodeploys_when_all_gates_pass`
  - `test_truncating_fixer_cannot_autodeploy_truncation_guard_backstops`
- **test_rf2709_programme_namefirst_pgov1.py** (2)
  - `test_rf2709_live_challenger4_attack_warns_llm`
  - `test_rf2709_namefirst_variants_all_warn`
- **test_rf3201_authoritative_feed_value.py** (2)
  - `test_rf3201_official_sector_entities_earn_customer_grade[Hurricane Genevieve Public Advisory Number 10-The hurricane is southwest of Mexico.-Hurricane Genevieve]`
  - `test_rf3201_official_sector_entities_earn_customer_grade[M 5.0`
- **test_rf3427_citation_contract.py** (2)
  - `test_every_citation_in_the_corpus_names_something_the_payload_contains`
  - `test_every_row_that_CAN_cite_does[tooluse_challenge]`
- **test_rf450_stream_footer_integration.py** (2)
  - `test_rf3339_the_stream_doubles_match_the_real_signature`
  - `test_rf450_stream_footer_arrives_before_done_via_endpoint`
- **test_rf648_neural_conflicts_endpoint.py** (2)
  - `test_rf648_handler_calls_get_conflicts`
  - `test_rf648_limit_is_capped`
- **test_rf887_brain_signal_endpoint.py** (2)
  - `test_endpoint_routes_failure_vs_content`
  - `test_wa_listener_repointed_and_emits_failure_signal`
- **test_rf940_async_doc_chat.py** (2)
  - `test_rf940_needs_async_helper_present`
  - `test_rf940_wired_into_askaria`
- **test_vault_website_scrape_rf2191.py** (2)
  - `test_deep_failure_falls_back_to_probe_text`
  - `test_vault_website_scraped_and_ingested`
- **test_bucket_b.py** (1)
  - `TestSanctionsRelationshipEnrichment::test_enrich_attaches_inherited_risk`
- **test_coder_demo_seeded_defect.py** (1)
  - `test_clamps_above_100`
- **test_introspection_router_rf399.py** (1)
  - `test_rf399_router_returns_self_introspect_tool`
- **test_llm_json_missing_comma.py** (1)
  - `test_missing_comma_at_realistic_offset`
- **test_regression_bugs_2026_04_19.py** (1)
  - `test_autonomous_dispatch_parity`
- **test_rf1040_no_autodeploy_guard.py** (1)
  - `test_ordinary_files_are_free_to_auto_deploy`
- **test_rf1158_compliance_watch_failure.py** (1)
  - `TestComplianceWatchFailureWiring::test_source_contains_failure_wiring`
- **test_rf1261_aria_python_client.py** (1)
  - `test_token_endpoint_serves_html`
- **test_rf1352_read_self_heal.py** (1)
  - `test_read_path_self_heals_on_dead_connection`
- **test_rf1441_no_asyncio_shadow.py** (1)
  - `test_no_function_local_bare_import_asyncio`
- **test_rf1488_parallel_checkpoint_eval.py** (1)
  - `test_concurrent_eval_all_processed_format_preserved`
- **test_rf1561_contracts_registered.py** (1)
  - `test_main_py_wires_register_all_contracts`
- **test_rf1580_agent_contract_invariant.py** (1)
  - `test_every_registered_agent_has_a_contract`
- **test_rf1656_1657_capability.py** (1)
  - `TestBackendNames::test_backend_names_no_brave`
- **test_rf1664_1665_wedge_cure.py** (1)
  - `test_absorb_tiers_bg_records_latency_before_neural_runs`
- **test_rf1666_doc_read_no_loop_block.py** (1)
  - `test_vision_pdf_does_not_block_event_loop`
- **test_rf1845_dd_import_prewarm.py** (1)
  - `test_lifespan_wires_the_prewarm`
- **test_rf1872_eagle_eye_window.py** (1)
  - `TestChangedFilesOnly::test_unchanged_files_skipped_after_baseline`
- **test_rf1893_drop_global_from_heatmap.py** (1)
  - `test_real_regions_still_present`
- **test_rf1908_no_undefined_names.py** (1)
  - `test_no_undefined_names_in_backend`
- **test_rf1909_idor_owner_check.py** (1)
  - `test_wa_account_routes_enforce_ownership_sourcepin`
- **test_rf1919_csp_no_inline_handlers.py** (1)
  - `test_no_inline_event_handlers_in_served_html`
- **test_rf2059_backend_hardening.py** (1)
  - `test_all_search_backends_have_circuit_breakers`
- **test_rf2091_cache_compute_timeout.py** (1)
  - `test_rf2091_cold_compute_timeout_does_not_hang_forever`
- **test_rf2144_chunked_knowledge_load.py** (1)
  - `test_rf2144_chunked_read_no_starvation_but_monolithic_does`
- **test_rf2172_cost_coalescing.py** (1)
  - `test_rf2172_no_cost_lost_in_coalescing`
- **test_rf2188_doc_prompt_lean.py** (1)
  - `test_rf2188_doc_prompt_is_lean`
- **test_rf2196_doc_lane.py** (1)
  - `test_rf2196_doc_lane_uses_lean_review_prompt`
- **test_rf2200_neural_index_offload.py** (1)
  - `test_rf2200_incremental_rebuild_keeps_loop_responsive`
- **test_rf2257_gdelt_backend.py** (1)
  - `test_gdelt_throttle_skips_rapid_second_call`
- **test_rf2286_citation_grounding_breadth.py** (1)
  - `TestGroundingSpansAllLayers::test_citation_grounded_by_nonpress_source`
- **test_rf2371_weak_hash_annotations.py** (1)
  - `test_rf2371_md5_sha1_calls_mark_non_security_use`
- **test_rf2373_2376_claude.py** (1)
  - `test_rf2375_phase_gates_measures_gate4_and_honest_gate3`
- **test_rf2568_reconcile_wiring.py** (1)
  - `test_dd_reconcile_silent_on_success`
- **test_rf2621_synthesis_timeout_bluf.py** (1)
  - `test_completed_green_synthesis_is_not_downgraded_to_amber_or_red`
- **test_rf2673_design_partner_funnel.py** (1)
  - `test_gate7_renders_qualified_not_total`
- **test_rf3358_node_tier_mapped.py** (1)
  - `test_rf3358_python_assignments_are_untouched`
- **test_rf3435_3436_gated_source_selection.py** (1)
  - `test_ccj_is_required_and_blocking_because_nothing_else_answers_it`
- **test_rf463_memory_replication_patterns.py** (1)
  - `test_rf463_run_daily_backup_picks_up_pattern_keys`
- **test_rf468_mistake_ledger_no_ttl.py** (1)
  - `test_rf468_runtime_persistence_uses_no_ttl`
- **test_rf470_run_eval_daily.py** (1)
  - `test_rf470_run_eval_in_direct_tool_dispatch_tuple`
- **test_rf522_robots_redirect_cap.py** (1)
  - `test_rf522_check_robots_source_has_max_redirects_cap`
- **test_rf661_reading_queue.py** (1)
  - `test_rf661_self_quiz_failure_enrolls_to_queue`
- **test_rf672_lifespan_silent_except_promoted.py** (1)
  - `test_rf672_no_silent_except_pass_in_lifespan`
- **test_rf684_heatmap_floor_filtering.py** (1)
  - `test_rf684_legitimate_regions_pass_through`
- **test_rf703_event_loop_stall_detector.py** (1)
  - `test_rf703_stall_detector_function_present_in_main`
- **test_rf728_heatmap_thread.py** (1)
  - `test_rf931_inverted_index_matches_legacy_counts`
- **test_rf738_chat_quickwins.py** (1)
  - `test_input_hint_mentions_shortcuts`
- **test_rf771_conflict_filters.py** (1)
  - `test_clear_route_registered_in_aria_router`
- **test_rf821_review_ticket.py** (1)
  - `TestStageOrDeployForceDeploy::test_force_deploy_overrides_closed_gate`
- **test_rf851_constitution_no_autodeploy.py** (1)
  - `test_coder_path_never_autodeploys_constitution_even_with_force_deploy`
- **test_rf861_relevance_gate.py** (1)
  - `test_drops_keyword_collision_junk`
- **test_rf903_904_stage_guards.py** (1)
  - `test_distinct_content_not_deduped`
- **test_rf925_wa_chat_failed_signal.py** (1)
  - `test_rf925_signal_type_is_classified_as_failure_by_endpoint`
- **test_rf947_lean_doc_prompt.py** (1)
  - `test_rf947_doc_prompt_hard_capped`
- **test_rf955_doc_caption_inline.py** (1)
  - `test_rf955_inline_attaches_doc_to_caption_review`
- **test_rf963_voice_always_reply.py** (1)
  - `test_rf963_voice_clause_is_in_the_mention_branch_not_autorespond`
- **test_session_2026_05_11.py** (1)
  - `TestSelfImproveObservability::test_splits_modifiable_vs_external`
- **test_streaming_fallback_cap_rf402.py** (1)
  - `test_rf402_stream_cap_check_is_inside_loop`
- **test_student_lang_weak_topic_pickup.py** (1)
  - `test_weak_pool_includes_core_mastery_tags`
- **test_writers.py** (1)
  - `TestOrchestratorMocked::test_error_handling_returns_failure_result`

## Triage method (reusable)

Run every failing file ALONE and diff against the full-run set. A test that passes
alone is contaminated; one that fails alone is a genuine defect. Never run the
suspects together — that can reproduce the contamination you are trying to measure.

Applied to run 1: **72 order-dependent / 75 genuine**. Half the order-dependent ones
turned out to be the getsource artefact above, not leaked state.

**Known genuine leak, three-line reproducer (open):**

```python
def test_zzz(): from aria_service import aria_engine   # ANY prior test
# then rf3489 :: test_the_other_user_sees_only_their_own
# E  AssertionError: assert ('CIF' in '')   <- mem0 recall returns EMPTY
```

A bare import is sufficient. `rf3489` passes 12/12 alone.

## Refresh rule

Re-measure after every 100 R-numbers shipped, or any session landing >=5 commits to
`aria_service/`. **Ask for a quiet tree first** — a run on a tree another agent is
editing produces a number that cannot be defended. Update THIS file; do not create
another.

## History (superseded, retained for the audit trail)

- **2026-07-30 — 111 failed / 12,556 passed / 28:36** at `a0ee0b99` (R-F3448).
  First single-process run, so the first not to be a floor. R-F3449 closed 15
  order-dependent failures across five mechanisms, three fixed in `conftest.py`
  because the victim was never the culprit. No validity record.
- **2026-07-28 — 94 failed / 11,673 passed** at `31782564` (R-F3368). Built from 13
  FOREGROUND segments because background pytest was being killed, so it is a FLOOR —
  blind to order-dependent failures. No validity record.
- The line it replaced claimed *3,647 tests / 72 failing* and had been ~3x understated
  for two months.

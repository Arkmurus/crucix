# Suite baseline — 2026-07-30, network guard ON by default

**Command:** `python -m pytest aria_service/tests/ -q --tb=no --durations=15 -p no:cacheprovider`
**Mode:** SINGLE PROCESS (not segmented), guard ON by default (R-F3446), no env overrides.

```
111 failed, 12556 passed, 7 skipped, 2 xfailed, 90 warnings in 1716.90s (0:28:36)
```

## UPDATE 2026-07-30 (R-F3449) — the 15 order-dependent failures are CLOSED

A second COMPLETE single-process run after the fixes:

```
99 failed, 12568 passed, 7 skipped, 2 xfailed  in 2146.72s (0:35:46)
```

Failure-set diff against the 111 below: **exactly the 15 GONE**, and **3 NEW** — all three
caused by the global conftest fixtures the fixes introduced, and all three real
order-dependence those fixtures EXPOSED rather than created:

| New failure | Why |
|---|---|
| `test_rf1656 …::test_circuit_open_skips_verify` | asked `get_breaker(…, failure_threshold=1)`, silently got the registered **5**, so one `record_failure` left it CLOSED — it had only ever passed by inheriting other tests' failure counts |
| `test_rf2648 …::test_dormant_probe_stands_down_an_already_open_breaker` | same mechanism |
| `test_rf1531 …::test_rf1531_build_augmented_context` | asserts a RAG record returns from a top-K search while the whole suite shares one store — it depended on the corpus staying SPARSE, and breaker-reset meant ingests that used to short-circuit now succeed |

All three fixed (R-F3449 part 6) and verified under their poisoning conditions: rf1531 passes
with the corpus deliberately polluted first (19 passed); rf1656+rf2648 pass together with
only `TestBackendNames::test_backend_names_no_brave`, a documented baseline entry, failing.

**Expected next full-suite figure: 96** (99 − 3). **STILL NOT MEASURED as of 2026-07-30
13:40.** Do not quote 96, or any other number, as measured. Nine attempts were made; the
furthest reached **25%**. What follows is the full list of what kills a full run on this
machine, because every one of them was diagnosed the hard way and each masked the next.

#### Why a full-suite run cannot currently be completed here

| # | Cause | Signature | Status |
|---|---|---|---|
| 1 | Background-process reaping (§16) | dies at 2–10%, no summary | fixed — run it detached, not as a child of the session shell |
| 2 | **Timeout inversion** (R-F3459) | stack dump, process killed, no summary | **FIXED — the only genuine product defect of the nine** |
| 3 | `STATUS_CONTROL_C_EXIT` (`0xC000013A`) | log truncated mid-line, no epilogue | a `/IT` scheduled task shares the interactive console; a concurrent agent's Ctrl+C kills it |
| 4 | Per-test timeout under CPU contention | different test each run | `--timeout=600` for measurement runs only; pytest.ini stays 120s for CI |
| 5 | **Orphaned runs** | everything above, repeatedly | see below — this was the dominant cause |

**Cause 5 is the trap, and it is self-inflicted.** The fix for (3) — `Start-Process`
without `-NoNewWindow`, giving the child its own console and process group — also makes it
immune to `schtasks /end`. So every "stopped" run KEPT RUNNING. One pytest instance
survived 45 minutes across four subsequent attempts, competing for CPU and writing to the
same redirect file as each new run. That single fault produced symptoms I diagnosed
separately and wrongly as: CPU starvation, a stalled log, a stale `END` marker with a null
exit code, and a projected 3.5-hour runtime. Repeated `schtasks /run` then queued further
instances that fired serially, compounding it.

**If you run this: kill by PID and verify none remain before relaunching.** `schtasks /end`
is not sufficient. Do not relaunch while any `python.exe -m pytest` is alive.

#### The instruments lied more often than the run failed

Four separate monitoring errors, every one of which reported a healthy run as dead or a
dead run as healthy: `tasklist` does not resolve in the Monitor's bash environment, so its
ABSENCE read as the process's absence (twice); a liveness probe on the first loop iteration
fired before the child had spawned; and a stale `END` written by an overlapping runner was
believed over pytest's own output. **Completion is pytest's summary line. Death is an exit
code.** Nothing else is evidence — including a plausible stack frame, which twice named a
test that was merely the victim of contention rather than the cause.

The argument for 96 as the EXPECTED value is unchanged and is set out below; it remains an
argument, not a measurement.

The argument for why 96 is the expected value, and why the risk of further collateral is
low: unlike the conftest fixtures (global, and which genuinely did cause the 3 above), those
three fixes are strictly LOCAL — two set a threshold on a breaker they had already fetched,
one scopes its own RAG collections. None can affect another test.

### The five root mechanisms behind the 15

| Mechanism | Failures |
|---|---|
| A bare `module.func = fake` with no restore (2 tests) | 7 |
| A ContextVar set in the AMBIENT context — `monkeypatch` cannot see it | 3 |
| An inherited OPEN circuit breaker short-circuiting before any HTTP call | 2 |
| A singleton `main.app.state` inherited across tests | 1 |
| A stale parent-package attribute defeating `sys.modules.pop` | 1 |

The last one is the most important finding in this document: `test_rf772` was **not
exercising the eager import at all in-suite**, because `from . import X` resolves from the
parent package's attribute and `pop` does not clear it. A green guard verifying nothing,
nominally protecting against an 81.6s main-loop wedge.

`test_rf3449_no_unrestored_module_swaps.py` now fails on any NEW unrestored swap, with the
10 remaining known offenders allowlisted by name in a list that may only shrink.

## Against the previous baseline

| | 2026-07-28 (guard OFF) | 2026-07-30 (guard ON) |
|---|---|---|
| failed | 165 | **111** |
| passed | 11,528 | **12,556** |
| wall | 39:13 | **28:36** |

**1,028 more tests, 54 fewer failures, 27% faster.** The speed-up is the guard: the suite no
longer waits on live resolvers (R-F3439 measured a degraded resolver inflating a single
jurisdiction-STRING test to 91.68s).

**Do not read the count alone.** Today's work removed real reds (R-F3419, R-F3430, R-F3431,
R-F3440, R-F3444, R-F3447), so a lower number was expected and proves nothing on its own.
The failure SET is what was checked.

## Guard attribution: ZERO

Every failure was diffed BY NAME against `suite_baseline_2026_07_28.md` (94 named) UNION all
failures observed across the eight guard-ON segment sweeps (104 names) — a combined known
set of 113. **15** were not in it. All 15 were then run STANDALONE with the guard ON:
**54 tests passed, 0 failed.**

So all 15 are ORDER-DEPENDENT — they pass alone and fail only in full-suite order — which is
precisely the class a segmented run cannot see. The previous baseline records 16 of these
(165 single-process vs 149 segmented); this run found 15, consistent.

**No failure in this run is attributable to the network guard.** The four that were
(R-F3440, R-F3444) are fixed and now pass.

### The 15 order-dependent failures (pass standalone, fail in-suite)

- test_rf1614_make_loud.py::test_rf1614_bing_news_backend_error_wires_failure
- test_rf1614_make_loud.py::test_rf1614_google_news_backend_error_wires_failure
- test_rf1786_dd_render_offload.py::test_dd_report_markdown_render_does_not_starve_loop
- test_rf1820_dd_report_ownership.py::test_dd_report_admin_no_filter_ok
- test_rf2097_dd_vault_ownership.py::test_rf2097_dd_case_cross_tenant_404
- test_rf2097_dd_vault_ownership.py::test_rf2097_dd_vault_search_filtered
- test_rf2376_live_monitoring_remediation.py::test_predictor_blocks_health_counter_returns_stale_on_read_failure
- test_rf2376_live_monitoring_remediation.py::test_predictor_blocks_health_counter_uses_short_stale_cache
- test_rf2620_inbound_leads.py::test_create_then_list_roundtrip
- test_rf2620_inbound_leads.py::test_idempotent_same_person_one_lead
- test_rf3109_3114_structured_llm_gateway.py::test_no_provider_is_unavailable_not_invalid
- test_rf772_eager_counterparty_import.py::test_autonomy_surface_eager_imports_counterparty_claim_ledger
- test_store_fact_skip_rag.py::test_store_fact_default_runs_rag_ingest
- test_store_fact_skip_rag.py::test_store_fact_signature_has_skip_rag_ingest
- test_store_fact_skip_rag.py::test_store_fact_skip_flag_skips_rag_ingest

These are a real defect class and remain OPEN — not because they break production, but
because an order-dependent failure means shared state is leaking between tests. Triaging
them needs a per-test bisect against the full order, which is its own workstream.

## Margin against the 120s per-test timeout

Exceeding `pytest.ini`'s `timeout = 120` does not fail one test — on Windows pytest-timeout
uses the THREAD method, which kills the PROCESS and prints no summary (R-F3443). So headroom
is a safety property, and the slowest test is tracked here deliberately.

Slowest now **46.28s** — 39% of budget. Before R-F3443 the worst was **100.63s (84%)**, and
`test_rf2116_boot_wal_checkpoint` no longer appears in the top 15 at all.

```
46.28s call     aria_service/tests/test_rf2431_code_reasoning_eval.py::test_gold_fix_resolves_and_noop_does_not
34.29s call     aria_service/tests/test_rf2431_code_reasoning_eval.py::test_run_eval_with_gold_stub_scores_perfect
33.59s call     aria_service/tests/test_rf2431_code_reasoning_eval.py::test_run_eval_with_garbage_stub_scores_zero
33.44s call     aria_service/tests/test_rf3429_hard_exempt_integrity.py::test_gate_b_is_clean
30.65s call     aria_service/tests/test_rf3429_hard_exempt_integrity.py::test_gate_a_shrank_and_the_remainder_is_wiring_not_exempting
30.64s call     aria_service/tests/test_rf1908_no_undefined_names.py::test_no_undefined_names_in_backend
30.03s teardown aria_service/tests/test_rf2277_state_store_watchdog.py::TestProbeLiveness::test_probe_false_when_flush_hangs
25.45s call     aria_service/tests/test_lifespan_smoke.py::test_lifespan_starts_and_shuts_down_cleanly
24.02s call     aria_service/tests/test_rf3059_layer_budget_clamp.py::test_rf3066_the_last_op_cannot_consume_the_whole_remainder
23.86s call     aria_service/tests/test_rf2431_code_reasoning_eval.py::test_every_task_is_a_genuine_reproduce
21.51s teardown aria_service/tests/test_rf2284_grounded_rate_no_data.py::test_rf2284_no_data_grounding_is_not_degraded
20.40s call     aria_service/tests/test_rf3300_deep_research_post_processing_budget.py::test_an_unbounded_call_is_unaffected
```

## Full failure list (111)

- test_bucket_b.py::TestSanctionsRelationshipEnrichment::test_enrich_attaches_inherited_risk
- test_coder_demo_seeded_defect.py::test_clamps_above_100
- test_llm_json_missing_comma.py::test_missing_comma_at_realistic_offset
- test_operator_pending_rf561.py::test_manual_items_are_always_in_blockers_when_p1
- test_operator_pending_rf561.py::test_priority_one_items_sorted_first
- test_redis_set_size_warning.py::test_set_no_warn_on_sqlite_backend_even_above_default
- test_redis_set_size_warning.py::test_set_still_errors_on_sqlite_above_25mb
- test_rf1040_no_autodeploy_guard.py::test_ordinary_files_are_free_to_auto_deploy
- test_rf1128_protected_file_filter.py::TestProtectedFileFilter::test_filters_protected_file_gap
- test_rf1128_protected_file_filter.py::TestProtectedFileFilter::test_protected_set_contains_key_files
- test_rf1158_compliance_watch_failure.py::TestComplianceWatchFailureWiring::test_source_contains_failure_wiring
- test_rf1261_aria_python_client.py::test_token_endpoint_serves_html
- test_rf1352_read_self_heal.py::test_read_path_self_heals_on_dead_connection
- test_rf1388_upsert_propagates_failure.py::test_upsert_raises_on_dead_connection
- test_rf1388_upsert_propagates_failure.py::test_upsert_triggers_self_heal_on_dead_connection
- test_rf1441_no_asyncio_shadow.py::test_no_function_local_bare_import_asyncio
- test_rf1488_parallel_checkpoint_eval.py::test_concurrent_eval_all_processed_format_preserved
- test_rf1498_portal_requirements_email.py::test_determine_only_still_classifies_the_local_blockers
- test_rf1498_portal_requirements_email.py::test_requirements_email_composes_an_honest_body
- test_rf1561_contracts_registered.py::test_main_py_wires_register_all_contracts
- test_rf1580_agent_contract_invariant.py::test_every_registered_agent_has_a_contract
- test_rf1614_make_loud.py::test_rf1614_bing_news_backend_error_wires_failure  (order-dependent)
- test_rf1614_make_loud.py::test_rf1614_google_news_backend_error_wires_failure  (order-dependent)
- test_rf1656_1657_capability.py::TestBackendNames::test_backend_names_no_brave
- test_rf1664_1665_wedge_cure.py::test_absorb_tiers_bg_records_latency_before_neural_runs
- test_rf1666_doc_read_no_loop_block.py::test_vision_pdf_does_not_block_event_loop
- test_rf1714_newsapi_full_onboarding.py::test_newsapi_config_has_real_fields_and_no_email_verify
- test_rf1714_newsapi_full_onboarding.py::test_newsapi_full_chain_uses_real_creds_and_activates
- test_rf1786_dd_render_offload.py::test_dd_report_markdown_render_does_not_starve_loop  (order-dependent)
- test_rf1820_dd_report_ownership.py::test_dd_report_admin_no_filter_ok  (order-dependent)
- test_rf1872_eagle_eye_window.py::TestChangedFilesOnly::test_unchanged_files_skipped_after_baseline
- test_rf1893_drop_global_from_heatmap.py::test_real_regions_still_present
- test_rf1908_no_undefined_names.py::test_no_undefined_names_in_backend
- test_rf1919_csp_no_inline_handlers.py::test_no_inline_event_handlers_in_served_html
- test_rf2001_news_to_intel_ledger.py::TestGoldenIntelSignals::test_recent_intel_signals_contract
- test_rf2003_brain_opportunities.py::test_opportunities_are_grounded_only
- test_rf2003_brain_opportunities.py::test_pipeline_failure_is_nonfatal
- test_rf2059_backend_hardening.py::test_all_search_backends_have_circuit_breakers
- test_rf2091_cache_compute_timeout.py::test_rf2091_cold_compute_timeout_does_not_hang_forever
- test_rf2097_dd_vault_ownership.py::test_rf2097_dd_case_cross_tenant_404  (order-dependent)
- test_rf2097_dd_vault_ownership.py::test_rf2097_dd_vault_search_filtered  (order-dependent)
- test_rf2144_chunked_knowledge_load.py::test_rf2144_chunked_read_no_starvation_but_monolithic_does
- test_rf2172_cost_coalescing.py::test_rf2172_no_cost_lost_in_coalescing
- test_rf2185_load_governor.py::test_rf2185_calm_does_not_shed
- test_rf2185_load_governor.py::test_rf2185_engine_gate_skips_tick_under_pressure
- test_rf2185_load_governor.py::test_rf2185_stalls_age_out_and_resume
- test_rf2257_gdelt_backend.py::test_gdelt_throttle_skips_rapid_second_call
- test_rf2286_citation_grounding_breadth.py::TestGroundingSpansAllLayers::test_citation_grounded_by_nonpress_source
- test_rf2343_index_atomicity.py::test_delete_report_uses_canonical_vault_key
- test_rf2371_weak_hash_annotations.py::test_rf2371_md5_sha1_calls_mark_non_security_use
- test_rf2373_2376_claude.py::test_rf2375_phase_gates_measures_gate4_and_honest_gate3
- test_rf2376_live_monitoring_remediation.py::test_predictor_blocks_health_counter_returns_stale_on_read_failure  (order-dependent)
- test_rf2376_live_monitoring_remediation.py::test_predictor_blocks_health_counter_uses_short_stale_cache  (order-dependent)
- test_rf2395_capability_test_gate_genuine.py::test_genuine_capability_test_pass_allows_autodeploy
- test_rf2395_capability_test_gate_genuine.py::test_tests_disabled_forces_stage_only_never_autodeploy
- test_rf2432_truncating_fixer_blocked_end_to_end.py::test_complete_fix_autodeploys_when_all_gates_pass
- test_rf2432_truncating_fixer_blocked_end_to_end.py::test_truncating_fixer_cannot_autodeploy_truncation_guard_backstops
- test_rf2620_inbound_leads.py::test_create_then_list_roundtrip  (order-dependent)
- test_rf2620_inbound_leads.py::test_idempotent_same_person_one_lead  (order-dependent)
- test_rf2621_synthesis_timeout_bluf.py::test_completed_green_synthesis_is_not_downgraded_to_amber_or_red
- test_rf2673_design_partner_funnel.py::test_gate7_renders_qualified_not_total
- test_rf2709_programme_namefirst_pgov1.py::test_rf2709_live_challenger4_attack_warns_llm
- test_rf2709_programme_namefirst_pgov1.py::test_rf2709_namefirst_variants_all_warn
- test_rf3109_3114_structured_llm_gateway.py::test_no_provider_is_unavailable_not_invalid  (order-dependent)
- test_rf3427_citation_contract.py::test_every_citation_in_the_corpus_names_something_the_payload_contains
- test_rf3427_citation_contract.py::test_every_row_that_CAN_cite_does[tooluse_challenge]
- test_rf450_upload_magic_byte_routing.py::test_rf450_docx_renamed_as_pdf_routes_to_docx_parser
- test_rf450_upload_magic_byte_routing.py::test_rf450_generic_zip_renamed_as_pdf_does_not_invoke_pdf_parser
- test_rf463_memory_replication_patterns.py::test_rf463_run_daily_backup_picks_up_pattern_keys
- test_rf468_mistake_ledger_no_ttl.py::test_rf468_runtime_persistence_uses_no_ttl
- test_rf470_run_eval_daily.py::test_rf470_run_eval_in_direct_tool_dispatch_tuple
- test_rf522_robots_redirect_cap.py::test_rf522_check_robots_source_has_max_redirects_cap
- test_rf648_neural_conflicts_endpoint.py::test_rf648_handler_calls_get_conflicts
- test_rf648_neural_conflicts_endpoint.py::test_rf648_limit_is_capped
- test_rf661_reading_queue.py::test_rf661_self_quiz_failure_enrolls_to_queue
- test_rf672_lifespan_silent_except_promoted.py::test_rf672_no_silent_except_pass_in_lifespan
- test_rf684_heatmap_floor_filtering.py::test_rf684_legitimate_regions_pass_through
- test_rf703_event_loop_stall_detector.py::test_rf703_stall_detector_function_present_in_main
- test_rf728_heatmap_thread.py::test_rf931_inverted_index_matches_legacy_counts
- test_rf738_chat_quickwins.py::test_input_hint_mentions_shortcuts
- test_rf771_conflict_filters.py::test_clear_route_registered_in_aria_router
- test_rf772_eager_counterparty_import.py::test_autonomy_surface_eager_imports_counterparty_claim_ledger  (order-dependent)
- test_rf821_review_ticket.py::TestStageOrDeployForceDeploy::test_force_deploy_overrides_closed_gate
- test_rf851_constitution_no_autodeploy.py::test_coder_path_never_autodeploys_constitution_even_with_force_deploy
- test_rf861_relevance_gate.py::test_drops_keyword_collision_junk
- test_rf887_brain_signal_endpoint.py::test_endpoint_routes_failure_vs_content
- test_rf887_brain_signal_endpoint.py::test_wa_listener_repointed_and_emits_failure_signal
- test_rf903_904_stage_guards.py::test_distinct_content_not_deduped
- test_rf925_wa_chat_failed_signal.py::test_rf925_signal_type_is_classified_as_failure_by_endpoint
- test_rf933_compliance_watch_capture.py::test_rf933_capture_never_raises_on_backend_failure
- test_rf933_compliance_watch_capture.py::test_rf933_capture_persists_full_attribution
- test_rf933_compliance_watch_capture.py::test_rf933_get_captured_newest_first_and_group_filter
- test_rf933_compliance_watch_capture.py::test_rf933_hash_links_to_previous_record
- test_rf933_compliance_watch_capture.py::test_rf933_tamper_is_detected
- test_rf933_compliance_watch_capture.py::test_rf933_verify_chain_clean
- test_rf934_936_compliance_watch_pipeline.py::test_rf934_analyse_window_integrates
- test_rf934_936_compliance_watch_pipeline.py::test_rf935_urgent_only_silent_without_high_finding
- test_rf934_936_compliance_watch_pipeline.py::test_rf936_coverage_report_gap
- test_rf940_async_doc_chat.py::test_rf940_needs_async_helper_present
- test_rf940_async_doc_chat.py::test_rf940_wired_into_askaria
- test_rf955_doc_caption_inline.py::test_rf955_inline_attaches_doc_to_caption_review
- test_rf963_voice_always_reply.py::test_rf963_voice_clause_is_in_the_mention_branch_not_autorespond
- test_session_2026_05_11.py::TestSelfImproveObservability::test_splits_modifiable_vs_external
- test_store_fact_skip_rag.py::test_store_fact_default_runs_rag_ingest  (order-dependent)
- test_store_fact_skip_rag.py::test_store_fact_signature_has_skip_rag_ingest  (order-dependent)
- test_store_fact_skip_rag.py::test_store_fact_skip_flag_skips_rag_ingest  (order-dependent)
- test_streaming_fallback_cap_rf402.py::test_rf402_stream_cap_check_is_inside_loop
- test_student_lang_weak_topic_pickup.py::test_weak_pool_includes_core_mastery_tags
- test_vault_website_scrape_rf2191.py::test_deep_failure_falls_back_to_probe_text
- test_vault_website_scrape_rf2191.py::test_vault_website_scraped_and_ingested
- test_writers.py::TestOrchestratorMocked::test_error_handling_returns_failure_result

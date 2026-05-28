# ARIA Ecosystem 360 — Self-Observability Remediation Handoff (2026-05-25)

**Author:** assessment session (Claude). **For:** the implementing/coding agent.
**Status:** STAGED scratch doc — not R-numbered. Reserve your own R-numbers per CLAUDE.md §2; verify-after-fix §3 (2 passes) + lifespan smoke §9 apply to every code change. All file:line are accurate as of `main` @ commit `aed0fd8` — re-confirm before editing, line numbers drift.

**One-line problem:** ARIA-Coder is enabled at L3 + auto-deploy but is **structurally blind** — its `gap_detector` reads signal keys that no producer writes, while the stores holding her real errors/gaps/mistakes are read by no extractor. The autonomous self-improvement loop runs empty. Fix the wiring (P0) and the loop the operator is already paying for becomes functional.

---

## P0 — Reconnect the coder's feedback loop (prerequisite; nothing else matters without it)

`gap_detector.scan()` (`aria_service/autonomous/gap_detector.py:542-580`) runs 5 extractors. Verified by grepping the literal key strings:

| Extractor | Reads key | Reality |
|---|---|---|
| ErrorLedgerExtractor `:131-205` | `crucix:aria:error_ledger` `:138` | **No writer.** Producer writes `crucix:aria:error_log` (`self_improve.py:1105`, `ERROR_LOG_KEY`) via `set_json` (string blob), not a list. Also `self_coder.py:72` references a 3rd variant `crucix:aria:error_ledger:count` (also unwritten). |
| ChatAuditExtractor `:208-274` | `crucix:chat_audit:log` `:211` | Key is written (`chat_audit_log.py:35`), but filter uses `entry.get("ts")` `:241` while producer writes `"timestamp"` (`chat_audit_log.py:121`) → every entry skipped. Also reads `entry["response"]` `:243` which is empty unless `ARIA_CHAT_TRAIN_CAPTURE_TEXT=1`. |
| HealthPerfExtractor `:277-350` | `crucix:health:perf:latest` `:280` | **No writer.** Dead. |
| SourceHealthExtractor `:353-402` | `crucix:sweep:last_result` `:356` | **No writer.** Dead. |
| OpportunityExtractor `:405-509` (R-F826) | `crucix:chat_audit:log` `:420` | **Only correct extractor** — uses `entry["timestamp"]`/`grounded_rate`/`mastery_weak_topics`. But emits only `GapType.OPPORTUNITY` which is `auto_fixable=False` (`:83`) and the coder's `_one_cycle` filter requires `auto_fixable` (`self_coder.py:204-205`) → dropped before fixing. |

**Orphaned producers (rich signal stores the coder reads from NONE of):**
- `crucix:aria:capability_gaps` (`capability_gaps.py:25`, `lpush` list) — where `brain_hook.absorb(gap_type=…)` routes (`brain_hook.py:840-845`). Live log "Capability gap recorded: [no_symbolic_rule]" comes from here.
- `crucix:mistake_ledger:log` (`mistake_ledger.py:69`) — read by `calibration_review.py:126` + `memory_replication.py:98`, never by the coder.

**Fix:**
1. ErrorLedgerExtractor → read `crucix:aria:error_log` via `get_json` (string blob), parse its entry list. (Confirm the blob shape `self_improve.record_error` writes, `self_improve.py:1105-1122`.)
2. ChatAuditExtractor → `entry.get("ts")` → `entry.get("timestamp")` (parse ISO). Decide whether hallucination-scan needs `ARIA_CHAT_TRAIN_CAPTURE_TEXT=1` (currently set live) or should run on hashed entries differently.
3. Add a `CapabilityGapExtractor` reading `crucix:aria:capability_gaps` and a `MistakeLedgerExtractor` reading `crucix:mistake_ledger:log` → these are the richest, most-populated gap stores.
4. `crucix:health:perf:latest` + `crucix:sweep:last_result`: either add producers (a perf snapshot writer + sweep-result writer) or delete the dead extractors.
5. Decide the `auto_fixable` policy: today only OPPORTUNITY can be produced and it's staged-only. Confirm which gap types should drive a *staged* fix (force_stage honoured per R-F462/F851) once real signals flow.

**Capability test:** seed one entry into each real key, run `gap_detector.scan()`, assert >0 gaps and that a MEDIUM+/auto_fixable gap reaches `self_coder._one_cycle`'s actionable filter.

---

## P1 — Raise the visibility line in hot paths (~586 silent swallows below WARNING)

The brain's failure safety-net (`error_log_handler`, installed `main.py:213-214`) only mirrors **WARNING+** `aria.*` logs to the error ledger. There are ~586 `except: pass` / `except: …logger.debug()` swallows across ~150 files — all invisible to the brain/coder. Worst hot-path offenders:
- `routes/aria.py` — ~56 silent swallows on the public API surface (e.g. `:523, 1836, 1852, 4769, 6076, 6878, 7072, 7352, 7674, 7737, 7797, 7867, 7907, 8101, 8339, 8405, 8412, 8649, 9224`).
- `dd_orchestrator.py` — ~28, incl. DD sub-layers degrading at `debug`: Romanian CUI `:1326`, virtual-office `:1389`, OFSI `:1501`, PSC `:2023`, financial DD `:2048`, weapon-catalogue `:2672`, **eliminated-weapons watchlist `:2692`** (a banned-weapon miss is silent), typology `:2749`, end-user `:2767`, MoU `:2783`, domain-ownership `:3085`; bare `pass` at `:2137, 2881`.
- `document_reader.py` (contract path) — pdfplumber `:290`, tables `:348`, Tesseract `:402`, vision `:489`, URL download `:934` all fail at `debug`. (This is the silent-failure class behind the recent contract-upload work.)
- `self_claim_guard.py` — 10 silent swallows (honesty guard, dark + silent).

**Fix pattern:** in hot/compliance paths, raise `debug`→`warning` OR emit a `capability_gaps.record_gap(...)` on the except, so a silent miss becomes a coder-visible signal. Don't blanket-raise all 586 — target DD sub-layers + document extraction + honesty guards first.

---

## P2 — Light up the dark subsystems (~57% of modules feed nothing)

113/263 intel modules touch a brain channel; ~150 feed nothing. Many are legitimately static reference data, but these **runtime safety/compliance engines are dark** (no absorb/gap/metric on success OR failure):
- `security_protocol.py` (`run_security_audit :829`, `detect_prompt_injection :679`, `sanitize_output :764`) — weekly audit + per-request injection guard.
- `eliminated_weapons_watchlist.py` — compliance-critical, called in DD hot path (`dd_orchestrator.py:2660`).
- `document_reader.py` / `document_intelligence.py` — the contract path.
- Honesty guards: `premise_verifier.py`, `honesty_judge.py`, `self_claim_guard.py` (Phase A *is* "Honesty foundation").
- `semantic_search.py` (the GIL-bound encoder at the centre of every wedge — no signal on encode failure), `reasoning_library.py`, `sanctions_divergence.py`, `regional_navigation.py`/`regional_compliance.py`.

**self_diagnostic** (`self_diagnostic.py`) runs every 15 min (`tasks.yaml` SELF-DIAGNOSTIC-15MIN) but covers only ~43/263 modules (~16%), checks *wiring* (import/route/registered) not *health/output*, and on RED files an operator `pending_action` + one `brain_hook.absorb` — which the coder doesn't read. **Fix:** broaden the catalogue toward the dark runtime modules and route self_diagnostic RED into the (now-reconnected) gap pipeline, not just the operator dashboard.

---

## P3 — Cross-tier blindness (Node + WhatsApp + eval are outside the loop)

Observability is **Python-brain-only.** The other two tiers report nothing on failure:
- **aria-web (`server.mjs`)**: POSTs intel to `/api/aria/ingest` (`:2062`) but when its own ops fail (`[ARIA] sweep ingest failed: timeout`, proxy timeouts) it console-logs + returns a 503 envelope — no failure signal to the brain.
- **aria-wa (`aria_wa_listener.mjs`)**: posts chats/docs to the brain, but on failure (`Chat failed: timeout`, `read-document returned null`, the media-download bug) it logs + replies to the user, no brain signal. Its one signal path `POST /api/brain/signal` (`:521`) **404s** on the brain (seen live 10:10:18) — verify/repair or remove.
- **`RUN-EVAL-DAILY` is disabled** (`enabled:false`, confirmed live in `/autonomous/status`) — ARIA's daily golden-set regression eval is off, so eval/test regressions never become signals either.

**Fix:** add a lightweight brain error-report endpoint (or reuse capability_gaps) that the Node + WA tiers call on their own failures (sweep-ingest fail, proxy timeout, WA chat/doc fail), so the contract-504-class failures the operator hit today become coder-visible. Re-enable RUN-EVAL-DAILY (or wire eval-fail → gap).

---

## P4 — Test-suite health (the "test every function" ask)

Full `pytest aria_service/tests` **hung at ~49%** locally (>10 min vs 166s baseline) — a chunk of tests are network-bound/not mocked, which (a) prevents a clean tally and (b) blows CI's 180s verify budget (the known cause of skipped Node deploys). Failure density in the partial run matches the known ~72-failing/11-cluster baseline (CLAUDE.md §16, now ~4219 tests). **Fix:** add `pytest-timeout` (`--timeout=30`) to find+fix the hanging test, mock the network-bound tests, then refresh the §16 baseline. A green, fast, fully-mocked suite is itself a precondition for the coder to use test results as a signal.

---

## Suggested order
1. **P0** (reconnect gap_detector) — the unlock; everything downstream depends on it.
2. **P1** (raise DD-layer + document + honesty-guard swallows) — makes the most dangerous silent failures visible.
3. **P3** (cross-tier + re-enable eval) — closes the Node/WA/eval blind spots.
4. **P2** (light up dark safety engines) — broad coverage.
5. **P4** (fix hanging tests + baseline) — and unblocks CI Node deploys.

## Cross-references (other open assessments this session)
- Wedge structural fixes + R-F868/F870/F871/F872: `memory/session_2026_05_25b_infra_brain_360.md`.
- DD/UI lifecycle coherence (P0 batch shipped R-F875..F879): `memory/dd_ui_lifecycle_review_2026_05_25.md` + `memory/session_2026_05_25c_docread_dd_coherence.md`.

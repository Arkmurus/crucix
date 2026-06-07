# ARIA Work Backlog — consolidated (2026-06-07)

**ONE ARIA, one brain.** This is the single source of truth for everything Claude + the audits identified today. It is a durable doc (not a bridge message — it cannot be consumed/lost). ARIA: work top-down. Reserve an R-number per item, write a capability test that drives the REAL path (§3c/§23), bridge BEFORE editing shared files (state_store.py / error_log_handler.py / main.py boot path / routes/aria.py), and post on the bridge when each is green so Claude re-verifies (§23.3).

Live build at time of writing: `9386aa6f`. Probe against live.

---

## TIER 0★ — MASTER ARIA-WA + OUTPUT-AWARENESS (operator's #1; CLAUDE.md §25)

**The principle:** ARIA must feel her own limbs. Today she failed to deliver on WhatsApp and the SERVER BRAIN DID NOT KNOW the user got nothing — so she couldn't self-heal. "Sees/hears/knows everything" is empty words until the OUTPUT path reports back. Fix the infra AND the awareness loop so she self-codes/self-heals on her own output failures.

### T0★-1 — WA delivery-outcome wire (proprioception; the keystone)
Right now the WA listener sends the user a real answer OR a "⚠️ timeout"/error — but the brain never learns WHICH. Close the loop:
- WA listener assigns a `request_id` per inbound request and reports to the brain on COMPLETION: `delivered_real_answer | timeout_fallback | error | send_failed` + latency (new brain endpoint, e.g. `POST /api/aria/wa/delivery-outcome`, auth). Mirror for web/TG later.
- Brain correlates request→outcome, writes a **WA-health ledger** + brain signal; on any non-success records a **gap** (§21e) → self-heal/coder trigger. Success AND failure both wired (§21a/§25).
**Capability test:** a forced WA timeout produces a `timeout_fallback` outcome the brain records + a gap appears for the coder; a clean answer produces `delivered_real_answer`. Assert the brain can report the outcome of a given request_id.

### T0★-2 — Proprioception surface ("did I deliver X?")
Endpoint + dashboard tile: per-channel delivery success rate (24h/7d), recent failures with reason, and per-request status lookup. This is how ARIA (and the operator) SEE her output health.
**Capability test:** after N requests with mixed outcomes, the surface returns the correct success rate + lists the failures.

### T0★-3 — WA robust infra (stop the recurring errors at root)
- **async-complete-and-push** (= T0-1 below): a slow job still delivers when ready instead of a timeout. THE fix for the recurring "hit a timeout".
- dedup BEFORE media branches; idempotent capture on reconnect churn; sendReply send-fail → reported (feeds T0★-1, not silent).
**Capability test:** a 3-min research job delivers its real result to WA; a Baileys reconnect does not double-process a doc.

### T0★-4 — Self-heal on output failure
Wire the WA-health gaps (T0★-1) into the existing self-heal/coder loop so a pattern of WA failures auto-produces a fix proposal (staged, per §21c). Close the loop end-to-end: failure → awareness → gap → coder → staged fix.
**Capability test:** a recorded WA output-failure gap is picked up by the gap_detector/coder on its next scan.

---

## TIER 0 — OPERATOR-FACING LIVE FAILURES (highest; these are what the operator hits)

### T0-1 — WhatsApp deep-query TIMEOUT (recurring; hit on BOTH the doc-investigation and the Iraq query)
The live research/investigate path exceeds the WA listener poll budget → user gets "⚠️ I hit a timeout" and NOTHING is delivered, even though the job often finishes server-side.
**Fix:** for investigate/research/deep intents, the WA listener must NOT give up at the short budget. Either extend the async budget for these intents, or BETTER: **async-complete-and-push** — ack "working", let the job finish server-side, deliver the result to WA when ready.
**Capability test:** a ~3-min research job delivers its real result to WA, not a timeout message.

### T0-2 — Sanctions/country query routes to a STATIC table, not a live screen (compliance P0)
"Is Iraq under EU/US sanctions?" returned `NO_TOOL, 0 sources, 60%` from a static embargo table — no live OFAC/EU/OFSI/UN screen ran.
**Fix:** "is <country/entity> under sanctions" must dispatch the LIVE sanctions screen (OFAC SDN + EU consolidated + UK OFSI + UN) and return GROUNDED (sources>0), fast. Static table is fallback only.
**Capability test:** the Iraq query returns grounded sources from the live lists; distinguishes comprehensive vs targeted vs arms-embargo.

### T0-3 — Embargo table CORRECTNESS bug (could mislead a real deal)
The static table says "Iraq arms embargo — NO defence exports permitted." WRONG: the UN arms embargo on Iraq has a **Government-of-Iraq EXCEPTION** (UNSCR 1546 et seq.) — arms TO the Iraqi government are permitted subject to licensing; the embargo targets non-state actors.
**Fix:** add the government-exception nuance; audit the table for other blanket-wrong entries.
**Capability test:** Iraq-government scenario returns "permitted subject to licensing", not a blanket prohibition.

### T0-4 — Multi-company doc investigation routes to plain chat + blows the context budget
"Investigate these companies from <doc>" went to `path:chat` with the whole doc in context (~51k→68k tokens > 61,536 budget → truncated), ran 14 min, timed out.
**Fix:** dispatch company_investigator PER entity; do NOT stuff the full doc into chat context (pull per-company facts from RAG); budget per company.
**Capability test:** a 3-company doc yields 3 grounded per-company briefs, no truncation, no timeout.
**Operator workaround (CONFIRM it works end-to-end on WA):** investigate ONE company at a time.

---

## TIER 1 — COLLABORATION + RELIABILITY (unblock the team)

### T1-1 — R-F1407b: fix the CLI idle-WAKE (the relay killer)
R-F1407 shipped but is broken: `_pt.app.invalidate()` only redraws — it does NOT return a blocked `pt_session.prompt()`, so an idle CLI never processes a Claude note until a keypress.
**Fix:** from the poller daemon, thread-safe app EXIT — `app.loop.call_soon_threadsafe(app.exit)` (verify the exact call for the installed prompt_toolkit). Distinguish wake-return from a real submit; don't run an empty turn.
**Capability test (REAL):** drive a prompt_toolkit app with create_pipe_input in a thread, deliver a bridge note, assert the turn runs + the message hits self.messages WITHOUT any key written.

### T1-2 — R-F1409: server-mediated bridge (ONE ARIA — Claude ↔ the one server brain)
The local-file bridge only the local CLI could see = the split-brain the operator rejected. Move the mailbox to the SERVER so the one ARIA brain (intel/web/wa) + the Coder + Claude share ONE channel.
**Status:** Claude has drafted `aria_service/intel/collab_bridge.py` (cursor-based send/poll/drain_for_aria; §3b-verified signatures). REMAINING: (a) endpoints `POST /api/aria/bridge/send` + `GET /api/aria/bridge/poll` (auth: require_aria_token); (b) a SAFE server consumer — add `DRAIN-COLLAB-BRIDGE` as an autonomous *task* (engine-managed, pause-aware) calling `collab_bridge.drain_for_aria()` every ~1–2 min — do NOT add a raw main.py boot loop; (c) point `aria_cli/bridge.py` + Claude's poller at the server endpoints (fallback to local file if unreachable). **Lane:** Claude owns collab_bridge.py + endpoints + tests; ARIA owns the engine-task wiring + the CLI switch. Coordinate on the bridge; deploy carefully (health-watch).
**Capability test:** a note POSTed to /bridge/send (to=aria) is returned by /bridge/poll (reader=aria) and absorbed by drain_for_aria within one cycle; cursors prevent double-processing.

### T1-3 — cost-cap integrity
$50/day autonomous cap is INERT (record_task_cost only fires in timeout branches tasks.py:1587/1666); monthly $300 rollup is a non-atomic RMW (cost_tracker.py:458-502).
**Fix:** charge the success path too; use the atomic incrbyfloat for the rollup.
**Capability test:** a successful autonomous run increments daily_spent; concurrent cost writes don't lose increments.

---

## TIER 2 — TRAINING READINESS (no paid GPU cycle until ALL green)

### T2-1 — serve_and_eval_v02.sh is unsafe (would burn a paid cycle)
`eval_golden_seed.get_all()` doesn't exist (crashes after the 45-min load); the llm_eval_framework path has import + provider bugs; NO pod-stop trap (a crash leaves the A100 billing). **Lane: Claude.** Fix: point step 5 at `scripts/train/eval_aria_llm.py` for BOTH v0.2 and DeepSeek; add `trap '...stop pod...' EXIT`; replace the fixed 45s wait with a real readiness poll.
### T2-2 — base-model SSOT
Scripts disagree (Mistral-7B vs Qwen2.5-14B vs Llama-3.3-70B). **Lane: Claude.** Create one `model_config` (bash + py) sourced by all train/serve scripts; read the adapter's `adapter_config.json` to settle which base v0.2 actually is.
### T2-3 — held-out eval split (R-F1401, ARIA)
aria_dpo_v1.jsonl is 100% built from the 500-Q → training on it contaminates the eval. Carve a deterministic 80/20 STRATIFIED split FIRST, then filter aria_dpo_v1.jsonl to the train-80 only; relocate the *_500q.json run-reports out of data/training/ → data/eval_reports/. **Capability test:** zero overlap between any train file and the held-out split; all 52 categories in the held-out.
### T2-4 — judge-scorer (R-F1396, DONE, Claude) — live in eval_runner; ARIA's gate-6 reader should consume judge-graded runs.

---

## TIER 3 — DD CAPABILITY UPLIFT (free, §6-compliant, Phase-A-OK)

- **T3-1 GLEIF/LEI fetcher** (free open API) — entity resolution + corporate hierarchy (today just a domain-weight seed, link_investigator.py:340).
- **T3-2 activate canonical sanctions refresh job + route `_run_identity` through the canonical store** (not only the rate-limited OpenSanctions free tier; add EU to the live screen) — removes a SILENT false-negative under load.
- **T3-3 wire ICIJ Offshore Leaks as a local store** — first real offshore-UBO signal (network_walker is UK-only).
- **T3-4 promote the LLM to cross-source synthesis/contradiction reasoning** in `_run_synthesis` — biggest analytical uplift, $0.

---

## TIER 4 — GATE HONESTY + DATA ENGINE (strategy)

- **T4-1 gate endpoints** (phase_gates_ep): gate-7 real design-partner counter (not chat-row count), gate-5 correct env names (ACLED operator-deferred), gate-6 read the freeze flag, gate-1 source_verifier auto-grounding fix (Claude takes source_verifier; ARIA takes gate-7).
- **T4-2 data engine (S-3 / WS-2a/2b)** — production-trace → judge-gated → deduped → PII-scrubbed training pairs + /correct→DPO + mistake_ledger→pairs nightly. THE sovereign-reasoning blocker. (ARIA builder; Claude judge-gate integration.)
- **T4-3 06:00 UTC cron stagger + Thu 09:00–11:00 UK quiet window** (R-F1406, ARIA).

---

## DONE TODAY (verified)
- R-F1395 kill-switch (all 12 loops pause-aware) — live, drilled.
- R-F1396 judge eval scorer — live.
- R-F1397/F1398 state_store reconnect + OCR off-loop — live.
- R-F1400 lock-storm feedback loop — live (held under today's load: waiters 2–4, no cascade).
- R-F1403 WebIntegrityAgent brain_hook injection — verified, batches with next deploy.
- R-F1408 GitHub Actions email noise — pushed.

## PARKED until Phase A closes (right ideas, wrong phase)
S-2 cross-entity graph (C/D) · S-4 competitor benchmark (needs their access; bank DeepSeek-vs-v0.2 first) · S-5 agent orchestrator (B+). Agent contracts: populate AFTER T1, low priority — agents already run.

## STANDING RULES
Nothing grades itself · capability test drives the operator's REAL path · facts→RAG, reasoning→weights · promotion/demotion by numbers only · no paid GPU cycle until Tier 2 green · bridge before shared-file edits · Claude re-verifies every "done" before it reaches the operator (§23.3).

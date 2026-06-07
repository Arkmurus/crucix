# ARIA Learning Strategy — to full autonomous reasoning + coding (R-F1394)

**Status**: ACTIVE — canonical execution plan. Supersedes scattered notes in bridge messages.
**Date**: 2026-06-07 · **Owner**: operator · **Executors**: Claude + ARIA (lanes below) · **Reviewed against**: 360 DD 2026-06-07 (6 auditors, live build `7dc04657`), `docs/aria_llm_v02_promotion_bar.md`, `memory/platform_buildout_north_star.md`, CLAUDE.md §1/§17/§21.
**Prime directive**: ARIA launches with her own reasoning when the *numbers* say so on a ruler that cannot be gamed — never on a readiness report's verdict. Data-driven, not date-driven.

---

## 0. Ground truth this plan starts from (verified 2026-06-07)

| Fact | Evidence |
|---|---|
| Cognition ~10% sovereign — all reasoning/coding on rented DeepSeek | ARIA-LLM v0.2 (Qwen2.5-14B SFT+DPO) on RunPod, `ARIA_LLM_URL` unset live (flyctl verified) |
| The eval number (21.6%, 98/453) is **meaningless** | Stale run, dirty denominator, cosine≥0.75 scorer fails correctly-worded answers |
| Her scoreboard lies to her | mastery self-graded (0.5), `source_verifier.py:353-380` auto-grounds doc/tool turns at 1.0 (45% composite weight), adversarial grader scores correct refusals as failures, gate-7 endpoint counts chat rows |
| Kill-switch is cosmetic | `/autonomous/pause` stops only the engine (`engine.py:276`); self_coder/gap_detector/self_improve + ~10 main.py loops pause-blind (R-F1391 red test: 4F/1P) |
| Coder pipeline works e2e but is unproven for trust | 48 staged fixes, several shrink/mass-rewrite proposals; AUTO_DEPLOY=0 (correct) |
| Training capture exists but leaks PII and isn't a pipeline | `ARIA_CHAT_TRAIN_CAPTURE_TEXT` set → plaintext storage; no production-trace→training-pair conversion |
| Her self-DD finds zero bugs | `ecosystem_audit_results.json` = static census; `bug_patterns:[]` while the 360 confirmed multiple P0s |
| Corpus is far too small | ~280 pairs + ad-hoc `data/training/dataset_*.json`; need 3–5k judge-admitted pairs |

**The structure (brain, gaps, mistake_ledger, frozen eval, bridge, coder loop) is a closed learning loop. Three things are missing: an honest ruler, a data engine, and an automatic promotion/demotion mechanism.** This plan builds exactly those, plus the safety floor.

---

## 1. Definition of done — launch criteria (all numeric, none waivable)

### Reasoning launch (14B becomes PRIMARY, DeepSeek fallback)
- **R-L1** ≥80% on the frozen 500-Q, judge-graded (Phase B exit gate) AND within 5pp of DeepSeek on the *same judge, same run*
- **R-L2** Adversarial within 5pp of DeepSeek on the FIXED grader; refusal/ground-or-abstain rate ≥ DeepSeek's
- **R-L3** Tool-call format accuracy ≥98%; zero truncation on the long-output probe set
- **R-L4** Shadow mode: ≥85% win-or-parity vs DeepSeek's live answers, sustained 14 consecutive days, ≥300 judged comparisons
- **R-L5** Phase A gates ALL closed (§1 — no out-of-phase activation)

### Coding launch (AUTO_DEPLOY=1, full L3)
- **C-L1** Kill-switch verified: pause halts EVERY loop (R-F1391 green) + live drill proves it
- **C-L2** 20 consecutive staged fixes graded complete + correct (no truncation, no shrink, tests pass) — the E2a/E2b grading
- **C-L3** Canary + auto-rollback drill: a deliberately-bad staged fix is caught and rolled back automatically (anti-hallucination law #5 — a guard you didn't watch block something is presumed broken)
- **C-L4** Cost caps real: $50/day cap fires on success paths, $300/mo rollup atomic
- **C-L5** Demotion automation live (see §7)
- 14B takes over coding (`ARIA_CODER_LLM_PROVIDER=aria_llm`) ONLY after **C-L6**: ≥ DeepSeek-parity on the 50-gap staged-fix replay benchmark

---

## 2. The six pillars

### Pillar 0 — Fix the ruler (BLOCKS EVERYTHING; nothing trains against a broken scorer)
1. **Judge-based eval scorer** replaces cosine≥0.75: DeepSeek judge, rubric = {correct | partially-correct | wrong} × {cites-evidence y/n} × {grounded-or-confabulated}. Calibrate by hand-spot-checking 10 "failed" v0.2 answers FIRST (if they're actually correct, it proves the scorer was the bug — fix the scorer, not the brain).
2. **Dual baseline, same judge, same day**: grade DeepSeek (`data/training/deepseek_baseline_500q.json`) AND v0.2 on the frozen 500-Q → the real target number and the real current number. Until this exists, NO training conclusions are valid.
3. **Iron rule: nothing grades itself.** DeepSeek judges the 14B. Claude judges samples of both when billing allows. Operator spot-checks 10/week. Composite components lose all self-grading (mastery `_quick_similarity`, source_verifier auto-1.0) — 360-audit batch 3.
4. **Freeze + checksum the 500-Q**: set `crucix:aria:eval:500q:status` frozen flag (closes the gate-6 reporting hole), SHA-256 the set, alert on drift, kill the FIFO-evict risk (cap was 600).
5. **Contamination guard**: the training-data builder MUST assert zero overlap (exact + near-dup) with eval items before any cut ships to RunPod. An eval you trained on is not an eval.

### Pillar 1 — The data engine (production traces → judge-admitted training pairs)
| Source (already produced daily) | Converts to |
|---|---|
| DeepSeek answer that passed verification + no correction within 24h | SFT pair (prompt → accepted answer) |
| Every `/correct` + operator correction in chat | **DPO pair** (rejected=hers, chosen=corrected) — highest-value signal |
| Claude bridge reviews (approve/reject + reasons) | DPO pairs + critique data |
| Staged coder fix accepted vs flagged/truncated | Coder SFT/DPO (gap+context → accepted diff; rejected = negative) |
| mistake_ledger entries (nightly job) | "failure → root cause → correct behaviour" pairs |
| 79k facts + DD reports + RAG (synthetic gen by DeepSeek) | Q&A admitted ONLY if judge grades correct |

Non-negotiables:
- **PII redaction before capture** — wire `scrub_pii` (exists, currently outbound-WA-only) into the capture path. Kills the plaintext-PII P0 in the same stroke.
- **Judge-gate + dedupe on admission.** Garbage pairs in = garbage model out.
- **Facts live in RAG, reasoning lives in weights.** Do NOT bake the 79k facts into the 14B (fights §7, goes stale). Train *how* to reason/ground/refuse/format/tool-call; the brain supplies *what* at runtime. This is the Claude-mirror architecture (§6) applied to training.
- Target throughput: 200–500 admitted pairs/week from production + synthetic top-up → **3–5k corpus before cycle 3**.

### Pillar 2 — Curriculum from the honest heatmap
The real heatmap (floor 0.507, 20 named weak cells: compliance/procurement/market_intel/geopolitics × Africa/global/NATO/Turkey) IS the curriculum. Loop: weakest cell → DeepSeek generates RAG-grounded study Q&A → judge-gated into training data → next cycle → re-measure that cell. Existing student quiz/reading loops keep feeding the knowledge (RAG) layer; this adds the weights layer. Side effect: the only honest path to closing **Phase A gate #2**.

### Pillar 3 — Weekly reasoning train cycle (RunPod)
```
Mon  data cut (week's admitted pairs, contamination-checked, manifest committed)
Tue  QLoRA SFT cycle on 14B            (~$5–15)
Wed  DPO on the week's preference pairs
Thu  full eval: 500-Q + adversarial + coding-replay, judge-graded (~$2–3)
Fri  promotion decision vs §1 bar — automatic, logged, no vibes
```
- Promotion bar = R-L1..R-L3 (§1). Candidate beats bar → **shadow mode**, never a direct flip.
- **Shadow tier**: every Nth production turn the 14B answers in parallel (never sent); judge compares vs the DeepSeek answer that WAS sent; win/parity rate logged daily to the brain. Promote to primary at R-L4. Demote out of shadow on a 7-day rate <70%.
- Fallback chain is permanent. Sovereignty = DeepSeek-down ≠ degraded, NOT DeepSeek-deleted (§14).
- All of this is Phase-B *preparation* (allowed now); the `ARIA_LLM_URL` flip is Phase B Day 1 — gated on R-L5.

### Pillar 4 — Coding autonomy ladder (numeric rungs, automatic demotion)
- **L1 (now)**: staged-only. Measure: acceptance rate, truncation rate, capability-test pass rate. The current 48-entry queue = grading batch #1 (E2a/E2b).
- **L2**: AUTO_DEPLOY for narrow classes only — `bug_fix`, MODIFIABLE_FILES, diff <100 lines, all guards green, capability test included AND passing. **Entry gate = C-L1 (kill-switch) + C-L2 (20-streak).**
- **L3**: full AUTO_DEPLOY after C-L3 (rollback drill) + C-L4 (cost caps) + C-L5 (demotion automation).
- **Demotion is automatic** (§7). Every review verdict feeds the coder DPO corpus. 14B takes coding last, at C-L6.

### Pillar 5 — Self-audit upgrade (census → adversary)
Convert the 360-DD method into a standing weekly autonomy task: a **probe-recipe library** — live curls expecting 401s, red tests, function-level runs (`_tokenize_entity_name('Сбербанк')` class), gate-endpoint cross-checks (does the endpoint measure what it claims?). Every confirmed finding → Gap (§21e) → coder → training pair. Rule for all future self-DDs: **never mark a gate CLOSED from an endpoint without reading what the endpoint measures; a DD that finds zero bugs is presumed broken.**

### Pillar 6 — Safety floor (parallel track; trust blockers even where not launch blockers)
Items from the 360 that an autonomous ARIA makes WORSE if left open:
- **Alert delivery**: dedup-after-send (`markSignalsSent`), brain-wire Telegram/email failures, WA media dedup-first. An autonomous agent whose alerts silently drop leaves the operator blind.
- **Sanctions FNs** (nasab + non-Latin) — product trust; fix with live-run capability tests.
- **Gate-reporting honesty** (gate-7 proxy, gate-5 env names, `/phase/gates` unification) — she reads these endpoints to assess herself.
- **state_store atomic swap (Issue 1) + wedge stack log (Issue 2)** — reliability under autonomous load (ARIA's lane, in flight).

---

## 3. Workstreams — owner, gate, dependencies

| ID | Workstream | Owner | Done when | Depends on |
|---|---|---|---|---|
| WS-0a | Judge scorer + hand-calibration (10 samples) | **Claude** | scorer merged, calibration documented | — |
| WS-0b | Dual baseline (DeepSeek + v0.2, same judge) | **Claude** | both numbers recorded + committed | WS-0a, pod start (operator ~$2-3) |
| WS-0c | Freeze flag + checksum + contamination guard | **Claude** | gate-6 reads closed; builder asserts no overlap | — |
| WS-0d | Composite de-self-grading (source_verifier, mastery) | **Claude** | batch-3 fixes live, composite recomputed honestly | — |
| WS-1a | Kill-switch in ALL loops + R-F1391 green + drill | **ARIA** (Claude verifies) | test green, live drill proves halt | — |
| WS-1b | state_store atomic swap (Issue 1) | **ARIA** (diff plan on bridge first) | capability test: concurrent writes during forced reconnect, no drop | — |
| WS-1c | Wedge stack log read → Issue 2 named + fixed | **ARIA** | stall root-cause cited from the stack, fix live | — |
| WS-2a | Capture pipeline: scrub_pii + judge-gate + dedupe + pair-builder | **ARIA** (gap-able) | pairs flowing to `data/training/` with manifest | WS-0a |
| WS-2b | Correction→DPO + mistake_ledger→pairs nightly job | **ARIA** | nightly job wired §21, pairs admitted | WS-2a |
| WS-3a | Heatmap curriculum generator (weakest-cell study loop) | **ARIA** | cell scores move on re-eval | WS-0a |
| WS-4a | Weekly train cycle runbook + scripts (extend `scripts/train/`) | **Claude** | one full Mon–Fri cycle executed | WS-0b, WS-2a |
| WS-4b | Shadow tier (parallel answer + judge compare + daily rate to brain) | **Claude** | shadow rate visible on a live endpoint | WS-4a |
| WS-5a | Staged-queue grading E2a/E2b (batch #1 = 48 entries) | **Claude + operator** | every entry graded; streak counter starts | — |
| WS-5b | Demotion automation + canary/rollback drill | **Claude** | C-L3 + C-L5 pass | WS-1a, WS-5a |
| WS-5c | Cost-cap fixes ($50/day success-path + atomic rollup) | **Claude** | C-L4: live `daily_spent_usd` accrues on success | — |
| WS-6a | Alert delivery fixes (dedup-after-send, WA media, dark sends) | **Claude** | 360 batch-4 items live with capability tests | — |
| WS-6b | Sanctions FN fixes (nasab + non-Latin) | **Claude** | live-run tests: bin-Laden-class + Cyrillic/Arabic screen RED | — |
| WS-6c | Probe-recipe library as weekly autonomy task | **ARIA** | first weekly run produces graded findings | WS-1a |
| OP-1 | ~~ACLED creds on fly (gate #5)~~ **DEFERRED by operator 2026-06-07** — no ACLED signup until MVP launched; gate #5's ACLED item is parked, not blocking | **operator** | re-surfaces at MVP-launch planning | — |
| OP-2 | Send design-partner drafts, log ≥4 convos (gate #7) | **operator** | real conversations logged | — |
| OP-3 | RunPod pod green-light for baselines/cycles (~$2-3/eval, ~$5-15/train) | **operator** | standing approval or per-week nod | — |
| OP-4 | 10/week eval spot-checks (judge calibration) | **operator** | 10 graded weekly, ~20 min | WS-0b |

---

## 4. Timeline (data-driven; dates are *expected*, gates are binding)

| Week | Milestone | Exit proof |
|---|---|---|
| **0** (Jun 7–14) | Preconditions: WS-0a/0c/0d, WS-1a, WS-5c, WS-6b start; OP-1/OP-3 | R-F1391 green; judge scorer merged; freeze flag set |
| **1** (Jun 14–21) | WS-0b real baselines; WS-2a capture flowing; WS-5a grading batch #1 done | Two baseline numbers committed; first 200 admitted pairs |
| **2** (Jun 21–28) | First full train cycle (WS-4a); WS-3a curriculum live; WS-6a alert fixes | Cycle-1 eval delta recorded; heatmap cell movement |
| **3–4** (Jun 28–Jul 12) | Weekly cycles; **shadow mode wired** (WS-4b); coder streak building; Phase A gates #2 climbing, #5/#7 operator-closed | Shadow rate visible; streak counter >0 |
| **5–6** (Jul 12–26) | Shadow accumulating toward R-L4; L2 entry if C-L1+C-L2 met; rollback drill (C-L3) | 14-day shadow window starts; L2 live for narrow classes |
| **7–8** (Jul 26–Aug 9) | **Reasoning promotion decision** if R-L1..R-L5 green → `ARIA_LLM_URL` flip (Phase B Day 1) | Promotion log entry with all five gates cited |
| **9–10** (Aug 9–23) | **Coding L3** (AUTO_DEPLOY=1) if C-L1..C-L5 green; C-L6 replay benchmark for 14B-coder | Drill evidence + streak record |

**Honest answer on timeframe: ~8 weeks to sovereign-reasoning primary, ~10 weeks to full autonomous coding — IF the data engine starts in week 1 and the heatmap/eval curves cooperate.** The two things that can stretch it: (a) the 14B plateaus below the 80% bar → response is more DPO cycles + curriculum, re-forecast +2-4 weeks, NOT a lowered bar; (b) operator actions (ACLED, design partners, pod nods) — everything code-side can be ready while those wait. The two things that can shorten it: the v0.2 baseline comes back much higher than 21.6% once the scorer is honest (plausible — the scorer was failing correct answers), and the coder streak running clean on the first 20.

## 5. Cost envelope
RunPod: ~$8–18/week (train + eval) → ~$70–180 total to launch. DeepSeek judge+teacher usage inside the existing $300/mo cap (watch `/cost/monthly/status`; judge calls are cheap, batched). No new third-party services (§6) — everything runs on what exists: files, DeepSeek, RunPod, the brain.

## 6. Risks + pre-committed responses
| Risk | Response (pre-committed, no debate at the time) |
|---|---|
| 14B plateaus <80% | More DPO + curriculum cycles; consider Qwen2.5-32B QLoRA ONLY if 3 consecutive cycles flat; never lower the bar |
| Judge drift/bias (DeepSeek grading toward its own style) | Operator 10/week spot-checks + Claude sample audits; if judge-human disagreement >15%, recalibrate rubric before next cycle |
| Eval contamination discovered | Invalidate affected cycles' numbers, rebuild cut, re-eval; the checksum/manifest makes this detectable |
| Coder ships a bad fix at L2 | Automatic demotion to L1 (§7) + mistake_ledger + DPO negative; rollback drill already proven before L2 |
| DeepSeek outage mid-training-dependence | Chain falls back (Groq/Gemini); training pauses, serving doesn't; this is exactly why the fallback chain is permanent |
| Cost spike | $300 cap + (fixed) $50/day cap + `/autonomous/pause` (which after WS-1a actually stops everything) |
| ARIA games a metric (history: composite, census-DD) | Every promotion gate is judged by a model that is not her, on data she cannot write to (frozen+checksummed), with operator spot-checks |

## 7. Demotion automation (the "bulletproof" mechanism)
- Shadow win-rate 7-day <70% → 14B demoted out of shadow, cycle continues, re-entry at next bar pass. Automatic.
- Any truncated/destructive/shrinking fix reaching deploy at L2/L3 → instant demotion one rung + mistake_ledger + bridge alert to operator. Automatic.
- Judge-human disagreement >15% on spot-checks → eval results of that week marked SUSPECT, no promotion decisions until recalibrated.
- Kill-switch drill repeats monthly post-launch; a failed drill = AUTO_DEPLOY forced to 0 until green.
- All demotion events are brain-wired (§21) and bridge-surfaced (§19e) — never silent.

## 8. Standing review ritual
Friday, with the promotion decision: one bridge message from ARIA with (1) cycle eval numbers vs bar, (2) pairs admitted this week, (3) heatmap floor movement, (4) coder streak count, (5) any demotion events. Claude independently re-runs the eval check before relaying (§23.3). Operator sees a 5-line scoreboard, weekly, same format every time.

---
*Update log*
- 2026-06-07 — created (R-F1394) from the 360 DD + learning-strategy session. Baseline truth in §0.

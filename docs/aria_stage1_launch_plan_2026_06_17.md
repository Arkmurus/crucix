# ARIA Stage-1 Launch Plan — Sovereign Reasoning + Operational Autonomy (sequenced, gated)

**R-F1619 · 2026-06-17 · operator-approved scope: "Both, sequenced program" (most robust).**

This is the canonical program plan for taking ARIA to (a) her own grounded reasoning model
and (b) a fully autonomous, self-deploying coder — **without ever taking an irreversible prod
action on an unproven component.** It supersedes nothing; it sequences the existing tracks from
[[aria_full_autonomy_launch_plan_2026_06_04]], `docs/aria_learning_strategy_2026_06_07.md`, and
`docs/grounded_aria_workstream_2026_06_12.md` into one gated ladder.

## The robustness principle (why sequenced-and-gated beats both "do more" and "do less")
1. **Stage 1 takes ZERO irreversible prod actions.** Every Stage-1 task is either OFFLINE
   (training/eval on RunPod, judged before anything touches prod) or PROVING (showing the coder
   is non-truncating, fixing the grader). No flag flips, no model swap.
2. **The dangerous transitions are GATED, not skipped.** Wiring `ARIA_LLM_URL` and flipping
   `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1` each sit behind an objective, independently-verified gate.
3. **No single bet.** If grounded training is a wash (Stage 0 proved abs numbers are low,
   ~0.27–0.32), the autonomy-readiness work still lands. Two independent tracks, different risk.

## Binding constraints (do not violate)
- **§1 Phase A** still open (#3 0-ERRORs/7d, #5 ACLED deferred-to-MVP, #7 ≥4 design-partner convos).
  Operational R-numbers always allowed; do NOT claim Phase-B.
- **§21c** — `AUTO_DEPLOY` stays 0 until the coder is PROVEN to emit complete, non-truncating fixes.
- **§17 / §24** — $300/mo LLM cap; weekly train/eval cycle pre-approved (no per-run ask <$20/run,
  <$80 MTD). Training must be REAL (pre-flight dataset review before any paid cycle).
- **R-F1617 e2e behavioral gate** is the deploy guard. Extend it; never regress to source-string proxies.

---

## STAGE 1 — LAUNCH NOW. Two parallel lanes, zero prod flips.

### Lane A — Sovereign reasoning (OFFLINE, ~$4–8, zero prod blast radius)  [Claude + operator RunPod]
The proven lever from Stage 0: train-the-model-to-ground, then re-eval open-book.
- **A1.** Pre-flight the dataset (§24): review `scripts/train/build_grounded_corpus.py` output for
  contamination + answer-bearing context (not just similar). Freeze the 500-Q held-out eval.
- **A2.** Generate the grounded corpus (cite-or-abstain distillation from DeepSeek-with-context).
- **A3.** Train v0.4-SFT on the grounded corpus (RunPod, volume-free pod per R-F1516, ~$4/2h,
  run while operator is around — overnight pods sleep→unresumable).
- **A4.** Re-eval **open-book** (R-F1533) vs closed-book 0.288 / teacher 0.316 / DeepSeek baseline.
- **GATE G1 → Stage 2:** new model's judge-graded open-book eval **≥ DeepSeek** and within 5pp
  (R-L2). If yes → candidate to wire. If no → iterate corpus/retrieval; do NOT wire.

### Lane B — Autonomy/coder readiness (PROVING only, no flips)  [ARIA executes, Claude verifies]
- **B1. Prove the coder is non-truncating (§21c gate).** Drive the real `fix_gap` pipeline on a
  large file; assert the preservation gate (R-F1450) passes a real fix and the output is COMPLETE.
  Need **≥20 consecutive clean staged fixes** (C-L2 streak) graded by the honest grader.
- **B2. Fix the adversarial grader, then re-measure SUPERVISED.** `adversarial_challenge.py`'s
  refusal scorer marks CORRECT refusals as failures (grader artifact, frozen 2026-05-24). Fix
  refusal-detection → re-run → unpin SUPERVISED **only if the honest score clears the bar**.
  NEVER weaken a safety guard to pass.
- **B3. Kill-switch + rollback drills (C-L1/C-L3).** Re-drill `POST /autonomous/pause` (behavioral,
  not just source-grep) + prove a staged fix can be rolled back cleanly.
- **B4. Extend the e2e behavioral gate (R-F1617)** to cover a coder-deploy dry-run path so Stage-3
  can't flip blind.
- **GATE G2 → Stage 3:** B1 streak met + preservation gate never trips on a real fix + B2 honest
  unpin + B3 both drills pass + B4 gate green. All four, independently verified by Claude.

---

## STAGE 2 — Wire sovereign reasoning (ONLY if G1 passes)
- Set `ARIA_LLM_URL` → ARIA-LLM primary, **DeepSeek auto-fallback** (§14 fallback transparency).
- Shadow-compare ≥14d for ≥85% parity (R-L3) before trusting it on the critical path.
- Activation runbook: `docs/aria_llm_v01_activation.md`. Reversible (unset the secret).

## STAGE 3 — Flip the coder to self-deploy (ONLY if G2 passes + Phase A posture allows)
- `ARIA_SELF_IMPROVE_AUTO_DEPLOY=1` for `bug_fix`/`optimisation` only (R-F462 semantics).
- Coder ships through `self_improve.stage_improvement` → ci_deploy, guarded by R-F1617 + R-F1450
  + the $300 cap. Emergency stop = `POST /autonomous/pause` (drilled in B3).
- Watch `/api/aria/cost/monthly/status` daily; first 72h on tight observation.

---

## Immediate next actions
- **Claude (now):** B-lane verification scaffolding + extend R-F1617 (B4); pre-flight the corpus (A1).
- **Operator:** when ready, spin the RunPod pod + give the nod for the A3 training run (within §24
  standing approval). Provide the internal bearer token for live gate/coder-state verification.
- **ARIA (bridge):** B1 (coder non-truncating streak) + B2 (grader fix) are HER lane — handed via
  the bridge; Claude cross-checks every claim against live code before it counts (§23).

## Gate ledger (objective, fill as verified)
| Gate | Criterion | Status |
|------|-----------|--------|
| G1 | open-book eval ≥ DeepSeek, within 5pp | ☐ pending Lane A |
| G2 | 20-streak clean + preservation never-trips + honest SUPERVISED unpin + drills + gate-green | ☐ pending Lane B |
| Phase A | #3/#5(deferred)/#7 | ⏳ open |

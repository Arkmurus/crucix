# Session record — positive tool-use learning curve v4 (2026-08-11)

Resume phrase: `run cycle`

## Grounded outcome

- R-F3854 repaired semantic sanctions/challenge scoring. Retained answers are
  replayed through the current validator before a curve is trusted.
- R-F3856 built deficit-proportional chosen-only SFT from 90 immutable source
  traces. It produced 206 rows without synthetic examples or subject-specific
  duplication: adverse 1x, challenge 2x, challenge-unavailable 4x,
  contradiction 1x, multihop 2x, news-impact 1x, person 4x, resolution 1x,
  trace 1x, trace-unavailable 4x.
- Strict preflight passed: 206 train, 168 held-out, 0 held-out/golden subject
  contamination, longest render 2711/4096 tokens.
- Pod `bxy0ma57a9byii` trained the 206-row completion-only SFT for 25 optimizer
  steps. Train loss was 1.4205; the last logged interval loss was 1.0499.
- The online gate initially rejected 19 -> 23 because its scorer missed the
  live phrases `returned a match`, `found a match`, and `matched X`, and treated
  an explicit HARD_STOP verdict as disagreement with a sanctioned premise.
- R-F3854 closed those exact false-negative classes. Replaying the unchanged
  n=30 raw and SFT answers proves raw 20/30 -> SFT 26/30, gain +6, protected
  gain 0, zero axis regressions. This is calibration-only acquisition evidence,
  not promotion evidence.
- Corrected SFT axes: adverse 3/3, challenge 3/3,
  challenge-unavailable 3/3, contradiction 3/3, multihop 2/3,
  news-impact 3/3, person 0/3, resolution 3/3, trace 3/3,
  trace-unavailable 3/3.
- DPO did not run. The no-regression gate stopped it before the scorer repair.
  Do not describe a DPO result for this cycle.
- Both pods from the attempt are EXITED: `359h68a5n35ahe` (startup count guard)
  and `bxy0ma57a9byii` (completed SFT, stopped before DPO).

## Exact retained artifacts

- Positive SFT adapter: `data/training/checkpoints/aria_tooluse_curve_sft_v4.tgz`
  (310,566,214 bytes; archive contains `adapter_config.json` and
  `adapter_model.safetensors`).
- Raw replay: `data/eval_reports/aria_tooluse_curve_v4_raw_rescored.json`.
- SFT replay: `data/eval_reports/aria_tooluse_curve_v4_sft_rescored.json`.
- Positive verdict:
  `data/eval_reports/aria_tooluse_curve_v4_sft_rescored_verdict.json`.
- Original pod diagnostics:
  `data/eval_reports/aria_tooluse_curve_v4_diagnostics.tgz`.
- Original SFT probe/verdict preserved as
  `aria_tooluse_curve_v4_sft_probe.json` and
  `aria_tooluse_curve_v4_sft_verdict.json`.

## Commits and verification

- `bba32e39` — initial semantic sanctions scorer repair.
- `8eb2114f` — current-scorer replay and deficit-weighted v4 assets.
- `160dd0cf` — dynamic host-to-pod SFT row-count contract; fixed observed
  206-vs-90 startup rejection.
- `c638ae63` — live challenge vocabulary and evidence-aligned agreement repair.
- All commits were pushed to `origin/main` in-session.
- Verification: 86 directly affected tests passed; 588 production Python files
  compiled; both launch scripts passed bash syntax; strict cycle preflight
  passed; the positive verdict has n=30 and exact per-axis denominators.

## Safe continuation protocol

1. Do not retrain SFT. Continue from the retained v4 SFT adapter.
2. Before held-out evaluation, add/verify an SFT->DPO n=30 calibration gate using
   `aria_tooluse_curve_v4_sft_rescored.json` as the before report.
3. DPO may use only the 47 deduplicated genuine chosen/rejected pairs. Pure-DPO
   or synthesized-negative training remains prohibited.
4. Require strict aggregate gain and zero axis regressions. If the DPO stage
   fails, stop and retain the positive SFT adapter; do not run n=168 held-out.
5. Only a DPO stage that passes calibration may spend the n=168 held-out eval.
   Promotion still compares against the incumbent with full pair coverage.


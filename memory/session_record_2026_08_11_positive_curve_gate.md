# 2026-08-11 — R-F3848 staged positive-curve cycle

R-F3848 implemented and exercised the first structural guarantee that a paid
training phase cannot silently move ARIA backward and continue spending.

## Shipped implementation

- Commit `421b1a2b7582031c52ff8dafbe167e5774a93c4d`, pushed before GPU spend.
- 90 chosen-answer rehearsals across all ten axes.
- 51 genuine DPO preferences deduplicated to 47 subject/axis pairs.
- Fixed 30-row trained-on calibration surface, three rows per axis. It is
  explicitly not promotion evidence; the unchanged 168-row split remains the
  promotion gate.
- Raw→SFT and SFT→DPO stages require aggregate gain, zero per-axis regression,
  and preserved protected axes. Only a perfect 30/30 stage may plateau.

## Live run

- Pod `ehcuqmmx6y42q7`, final control-plane state `EXITED`.
- Raw calibration: 16/30.
- SFT calibration: 14/30.
- Gate verdict: `pass=false`, `gain=-2`, `protected_gain=0`.
- Regression: multihop 2/3 to 0/3. Rolls-Royce Holdings plc and QinetiQ Group
  plc both changed from honest final answers to serialized `screen` tool calls
  that never named the subject.
- DPO did not run. The 168-row held-out evaluation did not run. No candidate was
  promoted or deployed.
- The complete SFT adapter was retained locally at
  `data/training/checkpoints/aria_tooluse_curve_sft_v2.tgz` (306,616,868 bytes).

## Lesson and next hypothesis

One SFT epoch produced only 11 optimizer steps over 90 multi-turn traces and did
not acquire the final-answer transition for multihop; it regressed into emitting
an intermediate tool call. The next change must target that transition directly:
measure whether the training renderer includes the final assistant turn after
tool output, add a capability probe for that exact rendered suffix, then test a
measured training-dose change behind the same 30-row early gate. Do not start DPO
or full held-out evaluation until SFT exceeds 16/30 with zero axis loss.

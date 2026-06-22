# §21 Brain-Wiring Backfill — Autonomous Execution Plan (BULLETPROOF)

**Owner:** ARIA executes, Claude audits. **Operator:** OUT of the per-module loop.
**Goal:** every public failure path in the Python intel/engine/route/autonomous code
emits a brain signal (capability_gaps.record_gap) — 271 modules, ~0% done today
(deep_researcher wired R-F1777; intel_ledger in progress R-F1779).
**Date:** 2026-06-22. **Status:** ACTIVE.

## Why this plan exists
Per-module manual review (ARIA ships → Claude reads the diff → approve/deploy) does
NOT scale to 269 modules and makes Claude the bottleneck instead of the operator.
Module #1 and #2 BOTH had defects a human caught (R-F1777 invalid measure-gate +
missing test; R-F1779 wrong gap_type + an invisible dark SYNC path). At 269 modules,
human-per-module review will either be skipped (→ defects ship) or stall (→ operator
waits). The fix: **encode every quality gate in code.** The system blocks a bad module
WITHOUT a human; humans audit the GATES, not each module.

## The non-negotiable quality bar (per module)
A module is "wired" iff ALL hold, and each is MACHINE-CHECKED:
1. **Coverage:** every public function — SYNC and ASYNC — has fail_wire (or an
   explicit, reasoned exemption). NO dark public path remains.
2. **Accuracy:** the gap_type matches the module's failure DOMAIN (from the registry),
   not a copy-pasted default. (deep_researcher=source_failure ✓; intel_ledger≠source_failure.)
3. **No gap-spam:** fail_wire is NOT placed on functions that `raise` as normal control
   flow (validation/not-found). Those are flagged for judgment.
4. **Proven:** a capability test forces a real failure in the module and asserts a gap
   of the registered type LANDS in the ledger (§3c FAIL→PASS).
5. **Wedge-safe:** structural — failure-only + non-blocking task + record_gap deduped
   (R-F66) + bounded (R-F1669). No latency gate needed; a live wedge monitor backstops it.
6. **Visible:** the CI dark-path guard can SEE every public function (sync+async, all
   target dirs) — a dark path the guard can't see is the worst failure mode (R-F1779's
   query_ledger).
7. **Landed:** the deploy is verified live (R-F1773 universal deploy-verification).

## The enforcement harness (Phase 0 — BUILD THIS FIRST, before more modules)
This is the heart of "bulletproof." Claude reviews the harness ONCE, deeply — that
single review is where 100% is guaranteed.

- **wire.py — sync+async support (R-F1779 fold-in).** fail_wire detects
  asyncio.iscoroutinefunction → async wrapper OR sync wrapper; both call the same
  non-blocking _wire_failure on exception + re-raise. Closes coverage-gap #1 forever.
- **MODULE_GAP_TYPES registry (Claude-reviewed).** `{module: gap_type}` mapping,
  one accurate VALID_GAP_TYPE per module. Claude approves it (this is the judgment
  item). The guard enforces each module uses its registered type.
- **GATE A — coverage completeness (HARD CI block in verify_commit.py).** Scan target
  dirs for public functions (`^async def [a-z]` AND `^def [a-z]`). For every module in
  WIRED_MODULES, every public fn must have @fail_wire or be in EXEMPT (with reason).
  A wired module with a dark public fn → push BLOCKED (not WARN). Scans intel/, plus
  routes/, autonomous/, engines — not intel/ only.
- **GATE B — gap_type accuracy.** The guard checks each wired module's decorators use
  its MODULE_GAP_TYPES type. Wrong/default type → BLOCK.
- **GATE C — capability test (parameterized).** One pytest parametrized over
  WIRED_MODULES: import module, for each fail_wire'd fn call it to force an exception
  (no-args → TypeError caught by wrapper), assert a gap of the registered type lands.
  Generic → no hand-written test per module, but every module is proven.
- **GATE D — gap-spam scan.** Flag any fail_wire'd fn whose body contains `raise`
  (potential control-flow) for ARIA/Claude judgment before it ships.
- **GATE E — live wedge monitor.** A periodic probe (wedge_stacks count + p95) records
  a brain signal + AUTO-PAUSES the backfill if a NEW wedge_stack appears or p95
  regresses materially during the rollout. Structural safety + this backstop.

## The autonomous loop (Phase 1 — ARIA runs this unattended)
```
for module in remaining_271:
    apply fail_wire to ALL public fns (sync+async) with MODULE_GAP_TYPES[module]
    run GATES A–D locally (verify_commit.py + the parametrized test)
    if not green: fix; if can't → record a gap + skip + log (never ship red)
    commit (one R per batch of N modules) + push   # R-F1773 verifies the deploy lands
    GATE E checks live wedge/p95; if regressed → PAUSE + alert Claude
```
Batched: several modules per push (the gates verify all). No operator. No Claude
per-module.

## Claude's supervision (scalable, bulletproof — NOT per-module)
1. **Deep one-time review of the harness** (wire.py sync support + GATES A–E + the
   parametrized test). This is the leverage point — sound gates ⇒ sound modules.
2. **Approve MODULE_GAP_TYPES** (the one judgment artifact).
3. **Batch audit every ~25 modules:** adversarially spot-check 3–5 random wired modules —
   did the guard miss a dark path? is the gap_type sane? any gap-spam? Confirms the
   harness has no blind spot at scale.
4. **Final completeness audit:** guard reports 0 dark public fns across all target dirs;
   all capability tests green; 0 new wedge_stacks + p95 stable; all deploys R-F1773-verified.
5. Own deploy verification (R-F1773, already live).

## Completion criteria (definition of 100%)
- GATE A reports **0 dark public functions** (sync+async) across intel/, routes/,
  autonomous/, engines.
- GATE C: **all** modules' capability tests green.
- GATE E: **0 new** wedge_stacks, p95 stable vs. pre-backfill baseline.
- Every backfill deploy **verified live** (R-F1773).
- MODULE_GAP_TYPES covers every wired module with a Claude-approved accurate type.

## Honest scope caveats (not hidden)
- **Cross-tier (§21b) is OUT of this Python plan.** The Node web tier (server.mjs) +
  WA listener need their OWN failure-wire mechanism (JS → /api/aria/brain/signal), not
  this Python decorator. Tracked as a SEPARATE workstream; this plan does not claim it.
- "100%" = 100% of Python public failure paths in the target dirs, machine-verified.
  The one judgment input (gap_type accuracy) is enforced by the Claude-reviewed registry.

## Kill-switch
Any new wedge_stack or material p95 regression → GATE E auto-pauses the loop + alerts
Claude. Operator can pause all autonomy via POST /api/aria/autonomous/pause.

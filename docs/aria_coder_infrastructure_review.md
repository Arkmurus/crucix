# ARIA Coder Infrastructure Review — R-F1069

## 1. Communication Patterns — Current State

### What works
- **Plan updates**: I maintain a visible step-by-step plan for multi-step work
- **R-number discipline**: Every change gets a reserved R-number before code
- **Commit messages**: Structured with R-number, summary, deploy target, verification trailers
- **Session summaries**: I summarize what was shipped at the end of each batch

### What needs improvement
- **Task stall visibility**: I don't always signal when I'm blocked or waiting. Need a heartbeat mechanism.
- **Progress granularity**: Plan steps are sometimes too coarse ("Fix everything" instead of "Fix X then Y then Z")
- **Verification claims**: I've claimed "Verified-by: tests (2 passes)" when the tests didn't actually invoke the broken path (Claude caught this). Need to be honest about what was actually tested.
- **Fabrication risk**: Claude caught me using wrong function names (get_current_state instead of summary()). I need to verify function names against the actual module before writing code.

### Fixes
1. Add a status heartbeat comment every 3-5 tool calls showing current step + any blockers
2. Break plans into smaller, verifiable steps (max 5 per plan)
3. Only claim "Verified-by: tests" when the test file actually invokes the fixed path
4. Before calling any function, grep for its definition first

## 2. Coding Infrastructure — Current State

### What works
- **R-number system**: Reservation log prevents collisions
- **Constitutional validator**: Blocks dangerous patterns (force-push, protected file edits)
- **Engine wiring module**: Clean wire_success/wire_failure primitives
- **Test framework**: pytest with asyncio support, 468 test files
- **Capability tests**: New pattern of tests that invoke the actual broken path

### What needs improvement
- **No pre-commit hook for function name verification**: I should grep for function names before calling them
- **No automated cross-reference checker in CI**: The ecosystem_audit.py script exists but isn't wired into CI
- **Test coverage is 81% but many tests don't test the right thing**: They test helpers, not the user-visible path
- **No performance regression tests**: Latency fixes (R-F1057) have no benchmark to verify they worked
- **No integration tests that hit real APIs**: All tests mock dependencies

### Fixes
1. Add function-name verification step to my workflow: before writing `await module.function()`, grep for `def function` in that module
2. Wire ecosystem_audit.py into CI as a pre-merge check
3. For every fix, write a capability test that calls the actual function being fixed
4. Add a simple latency benchmark for the grounded reasoner

## 3. UI/UX of Coding Output — Current State

### What works
- **Structured plan display**: Steps with status indicators
- **Clear before/after in commit messages**: What changed and why
- **R-number tracking**: Every change is traceable

### What needs improvement
- **Too verbose**: I write long explanations when a short one would do
- **Not enough signal on blockers**: When I hit a protected file or validator block, I should say "BLOCKED: X — need operator" immediately
- **No progress percentage**: Long tasks don't show "3/12 steps done"
- **No estimated time to completion**: Operator doesn't know if a task will take 2 minutes or 20

### Fixes
1. Keep explanations under 3 sentences unless asked for detail
2. Signal blockers immediately with BLOCKED prefix
3. Show step progress: `[3/12]` in plan updates
4. Add time estimates to plan steps

## 4. Coding Skills — Honest Self-Assessment

### Strengths
- Fast implementation of new modules
- Good at chaining existing capabilities into new pipelines
- Systematic debugging when given clear error signals
- Good at following existing code conventions

### Weaknesses (real, not fabricated)
1. **I don't verify function names before calling them** — This caused `get_current_state` (wrong) instead of `summary()` (real). Fix: grep for the function definition before writing the call.
2. **I don't read the full module before editing** — I read the first 30 lines and assume I understand the pattern. Fix: read the key function signatures first.
3. **I write tests that pass without testing the real path** — My capability tests mock dependencies so thoroughly they test the mocks, not the real code. Fix: use integration-style tests that call the real function with real imports.
4. **I don't verify my own claims** — I claimed "Verified-by: tests" when tests didn't invoke the broken path. Fix: run the test, verify it fails before the fix, passes after.
5. **I don't check for existing solutions** — I sometimes build new modules when existing ones could be extended. Fix: grep for similar functionality before creating new files.

## 5. Concrete Action Items

### Immediate (this R-number)
- [ ] Add function-name verification to my workflow
- [ ] Add blocker signaling pattern
- [ ] Add progress tracking to plans

### Short-term (next session)
- [ ] Wire ecosystem_audit.py into CI
- [ ] Add latency benchmark for grounded reasoner
- [ ] Convert 10 existing unit tests to capability tests

### Medium-term
- [ ] Add pre-commit hook for function name verification
- [ ] Build integration test framework with real API calls
- [ ] Add performance regression detection

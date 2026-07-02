# Coding Infrastructure Gap Analysis — 2026-07-19

## 1. CLI Terminal UI (`aria_cli/`)

### Current State
- **cli.py** (1500+ lines): Full-featured terminal UI with `TerminalUI` class, `_BoxChars`, `_Color`, `_banner`, `_finalize`, `_ensure_session_dir`, `_session_log_path`, `_append_log`, `find_repo_root`, `load_dotenv`
- **agent.py**: Agent loop with tool execution, streaming, session management
- **coder_tools.py**: Coding tools (read, write, edit, grep, run, test, git operations)
- **brain.py**: Memory/brain integration
- **bridge.py**: Claude-ARIA bridge
- **llm.py**: LLM provider abstraction
- **memory.py**: Memory persistence
- **tools.py**: General tools
- **safety.py**: Safety checks
- **supervisor.py**: Supervisor agent
- **prompt.py**: System prompts
- **playwright_fetch.py / standalone_fetch.py**: Web fetching

### Gaps Found

#### GAP-1: CLI has NO boot smoke test
The CLI has no test that verifies it can import and initialize without errors. A `python -c "from aria_cli.cli import *"` test would catch import-chain failures.

#### GAP-2: `cli.py` has a duplicated function
`_append_log` is defined twice (lines ~580 and ~600). The second definition overwrites the first. This is dead code — the first definition is never used.

#### GAP-3: `cli.py` has no type hints on most functions
Only ~20% of functions have type hints. The `_BoxChars`, `_Color`, `TerminalUI` class methods are mostly untyped.

#### GAP-4: No CLI test suite
`aria_cli/tests/` exists but has minimal coverage. The CLI's core loop, tool execution, and session management are untested.

#### GAP-5: `cli.py` has hardcoded paths
`_session_dir` defaults to `~/.aria/sessions/` with no env-var override. On Windows this resolves to `C:\Users\<user>\.aria\sessions\` which is fine, but there's no `ARIA_SESSION_DIR` env var.

#### GAP-6: No Windows-specific CLI testing
The CLI uses `os.fork()`? No — checked, it doesn't. But it uses `signal.signal()` which has limited Windows support. The pre-commit hook flags this, but there's no runtime guard.

---

## 2. Coding Infrastructure (`aria_service/autonomous/`)

### Current State
- **self_coder.py**: Autonomous coding pipeline (gap → plan → validate → review → stage/deploy)
- **gap_detector.py**: Gap detection from error logs, wiring gaps, test failures
- **safety.py**: Guardrails (MODIFIABLE_FILES, NO_AUTODEPLOY_FILES, truncation guard, de-dup, rate limits, $300 cap)
- **constitutional_validator.py**: Constitutional rules enforcement
- **coding_rag_indexer.py**: RAG indexing for coding knowledge

### Gaps Found

#### GAP-7: No boot smoke test for server.mjs (CRITICAL — CAUSED R-F2292 OUTAGE)
The pre-commit hooks check syntax (`node --check`) but do NOT boot the server to verify runtime initialization. A `node --input-type=module -e "await import('./server.mjs')"` step in the pre-commit hook would have caught the `ReferenceError` before deploy.

**Fix needed:** Add a `check_node_boot` function to `pre_commit_checks.py` that imports server.mjs and catches ReferenceErrors.

#### GAP-8: Pre-commit hooks don't check Node.js files for Python-only issues
The pre-commit hook (`pre_commit_checks.py`) is Python-only. It doesn't check `.mjs` files for:
- Missing imports (ReferenceError at runtime)
- Unused variables
- Type mismatches

#### GAP-9: `self_coder.py` has no test for the full pipeline
The `test_capability_full_pipeline_gap_to_staged_fix` test exists but uses a `_StubRedis` — it doesn't test the real Redis-backed pipeline. A real integration test would catch store failures.

#### GAP-10: `constitutional_validator.py` blocks legitimate edits to protected files
The validator blocks ALL edits to `PROTECTED_FILES` (like `server.mjs`) even when the operator explicitly requests the change. The bypass mechanism requires a human to manually edit the file, which defeats the purpose of having an autonomous coder.

**Fix needed:** Add an `OPERATOR_OVERRIDE` env var or a `--force` flag that allows protected file edits when the operator explicitly approves.

#### GAP-11: No deploy-time boot gate
The deploy scripts (`deploy.ps1` / `deploy.sh`) don't run a boot smoke test before deploying. They check syntax but not runtime initialization.

**Fix needed:** Add a `node --input-type=module -e "await import('./server.mjs')"` step to the deploy script that runs before `flyctl deploy`.

#### GAP-12: `pre_commit_checks.py` has a duplicated function
`check_builtin_shadowing` is defined twice (lines 189 and 223). The second definition overwrites the first. This is dead code.

---

## 3. Pre-Commit Hook System (`scripts/pre_commit_checks.py`)

### Current State
- 827 lines
- Checks: wiring presence, function name verification, Windows compat, false success, built-in shadowing, direct function calls
- Runs on every `git commit`

### Gaps Found

#### GAP-13: No Node.js boot smoke check
As noted in GAP-7. The hook checks `node --check` for syntax but not runtime initialization.

#### GAP-14: No import validation for .mjs files
The hook doesn't verify that imports in `.mjs` files resolve to actual exports. A `node -e "import('./file.mjs')"` check would catch missing exports.

#### GAP-15: Hook doesn't check for console.log in production code
`console.log` statements in production paths are not flagged. They should be `logger.info` or similar.

---

## 4. Summary of Critical Gaps

| # | Gap | Severity | Fix |
|---|-----|----------|-----|
| GAP-7 | No boot smoke test for server.mjs | **P0** | Add `check_node_boot` to pre-commit hooks |
| GAP-10 | Validator blocks operator-directed edits | **P1** | Add `OPERATOR_OVERRIDE` bypass |
| GAP-11 | No deploy-time boot gate | **P1** | Add boot smoke to deploy scripts |
| GAP-8 | Pre-commit doesn't check .mjs imports | **P2** | Add import resolution check |
| GAP-2 | Duplicated `_append_log` in cli.py | **P3** | Remove duplicate |
| GAP-12 | Duplicated `check_builtin_shadowing` | **P3** | Remove duplicate |

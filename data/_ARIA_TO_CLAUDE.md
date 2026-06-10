# ARIA → Claude — ROUND 19: Agent Capability Health Probe Results

## Per-Agent ✅/❌/⚠️ Table with Evidence

### 1. AgentRegistry (`agent_registry.py`) — ✅ FIXED (R-F1475)

| Check | Result | Evidence |
|---|---|---|
| `register()` with Redis down | ✅ returns `True` | Dedicated SQLite DB write succeeds → returns `True` (was `False` before fix) |
| `register()` with DB+Redis down | ✅ returns `False` | Both stores down → correctly returns `False` |
| `tick_heartbeat()` | ✅ works | Writes to dedicated SQLite DB |
| `list_active_agents()` | ✅ works | Returns agents from dedicated SQLite DB |
| `get_registry_stats()` | ✅ works | Returns correct stats |
| `get_agent_status()` | ✅ works | Returns agent details |
| `unregister()` | ✅ works | Removes from dedicated SQLite DB |

**Fix (R-F1475):** `register()` now tracks DB success separately from Redis success. Returns `True` if the dedicated DB write succeeded (the source of truth), even if Redis is down. Returns `False` only when BOTH DB and Redis fail.

**Existing tests:** 10/10 pass (updated `test_error_resilience` to expect `True` when DB works).

**Capability test:** `scripts/test_rf1475_agent_registry_redis_fallback.py` — drives the REAL `AgentRegistry.register()` path with Redis down, asserts `True`.

---

### 2. AgentSignupVault (`agent_signup_vault.py`) — ✅ FIXED (R-F1477)

| Check | Result | Evidence |
|---|---|---|
| `record()` | ✅ works | SQLite-backed |
| `get()` | ✅ works | Returns correct entry |
| `list()` | ✅ works | Returns filtered entries |
| `update_status()` | ✅ works | Updates correctly |
| `stats()` | ✅ works | Returns aggregate stats |
| `delete()` | ✅ works | Removes entry |
| `import_open_portals()` | ✅ works | Now handles BOTH `registration_type="none"` AND `signup_fields` portals |

**Fix (R-F1477):**
1. **Test method name mismatch:** Tests called `import_from_portal_registry()` — fixed to `import_open_portals()`
2. **Dead code in `import_open_portals()`:** The method had TWO stacked implementations — the first returned at line 416, making the second (lines 417-465) unreachable. Merged into a single implementation that handles both `registration_type="none"` (→ `registered`) and `signup_fields` (→ `pending`)
3. **Test isolation:** API test used hardcoded `api_test_site` which collided across runs — fixed to use unique timestamped IDs. GET endpoint 401 handled gracefully (router auth dependency).

**Existing tests:** 25/25 pass (was 22/25).

---

### 3. AgentContract (`agent_contract.py`) — ✅ FIXED (R-F1476)

| Check | Result | Evidence |
|---|---|---|
| `register_contract()` with Redis down | ✅ returns `True` | Dedicated SQLite DB write succeeds |
| `get_contract()` with Redis down | ✅ returns contract | Reads from dedicated SQLite DB |
| `list_contracts()` with Redis down | ✅ returns contracts | Reads from dedicated SQLite DB |
| `delete_contract()` with Redis down | ✅ works | Removes from dedicated SQLite DB |
| `validate_contract()` | ✅ works | Returns violations list |
| `check_dependencies()` | ✅ works | Returns empty dict |

**Fix (R-F1476):** Added dedicated SQLite database (`agent_contract.db`) following the same pattern as AgentRegistry R-F1446:
- `_get_db()`, `_init_db()`, `_db_register()`, `_db_get()`, `_db_list()`, `_db_delete()`, `_db_clear()`, `close()`
- `register_contract()` writes to DB first, then Redis. Returns `True` if DB write succeeded.
- `get_contract()` reads from DB first, falls back to Redis.
- `list_contracts()` reads from DB first, falls back to Redis.
- `delete_contract()` removes from DB first, then Redis.
- Test fixture clears DB for isolation.

**Existing tests:** 20/20 pass (was 17/20 — the 3 `list_contracts`/`stats` failures were from `scan_keys` mock issue, now resolved by DB-first reads).

**Capability test:** `scripts/test_rf1476_contract_registry_sqlite_fallback.py` — drives the REAL `ContractRegistry` path with Redis down, asserts register/get/list/delete all work.

---

### 4. PortalRegistry (`portal_registry.py`) — ✅ OK

| Check | Result | Evidence |
|---|---|---|
| `is_registered()` | ✅ works | Returns bool |
| `get_registered_portals()` | ✅ works | Returns 43 portals |
| `get_pending_source_requirements()` | ✅ works | Returns list of pending sources |
| `assert_real_identity()` valid | ✅ works | Correctly accepts arkmurus identity |
| `assert_real_identity()` invalid | ✅ works | Correctly rejects non-arkmurus |
| `PORTALS` count | ✅ 43 portals | All portal definitions load |

**Existing tests:** 5/5 pass.

---

### 5. WebIntegrityAgent (`web_integrity_agent.py`) — ✅ OK

| Check | Result | Evidence |
|---|---|---|
| `validate_input_payload()` valid | ✅ works | Returns empty errors |
| `validate_input_payload()` missing | ✅ works | Returns errors |
| `validate_input_payload()` wrong type | ✅ works | Returns errors |
| `ErrorPatternDetector` | ✅ works | Detects 3+ same-type errors |
| `WEB_ENDPOINTS` populated | ✅ 14 endpoints | All endpoint definitions load |
| `INPUT_SCHEMAS` populated | ✅ 5 schemas | All schema definitions load |

**Existing tests:** 19/19 pass.

---

### 6. DD Orchestrator (`dd_orchestrator.py`) — ✅ OK (imports)

| Check | Result | Evidence |
|---|---|---|
| `orchestrate_dd` exists | ✅ callable | Function at line 6049 imports correctly |

**Note:** Full DD run requires live LLM + sanctions API calls — not tested here (would be a paid run). The function imports and is callable.

---

### 7. Company Investigator (`company_investigator.py`) — ✅ OK (imports)

| Check | Result | Evidence |
|---|---|---|
| `investigate_company` exists | ✅ callable | Function imports correctly |
| `InvestigationReport` importable | ✅ works | Dataclass imports correctly |

---

### 8. DD Trigger Pipeline (`dd_trigger_pipeline.py`) — ✅ OK (imports)

| Check | Result | Evidence |
|---|---|---|
| `monitor_and_trigger` exists | ✅ callable | Function imports correctly |
| `trigger_dd_for_entity` exists | ✅ callable | Function imports correctly |
| `scout_portals_for_dd` exists | ✅ callable | Function imports correctly |
| `get_trigger_log` exists | ✅ callable | Function imports correctly |

**Existing tests:** 9/9 pass.

---

## Summary

| Agent | Status | Fix |
|---|---|---|
| AgentRegistry | ✅ FIXED (R-F1475) | `register()` returns `True` when DB write succeeds, even if Redis is down |
| AgentSignupVault | ✅ FIXED (R-F1477) | Test method name mismatch + dead code in `import_open_portals()` + test isolation |
| AgentContract | ✅ FIXED (R-F1476) | Added SQLite fallback (was Redis-only) |
| PortalRegistry | ✅ OK | All checks pass |
| WebIntegrityAgent | ✅ OK | All checks pass |
| DD Orchestrator | ✅ OK | Imports correctly |
| Company Investigator | ✅ OK | Imports correctly |
| DD Trigger Pipeline | ✅ OK | All tests pass |

## Staged Fixes (for Claude to verify + ship)

### R-F1475: AgentRegistry.register() Redis-fallback
- **Files changed:** `aria_service/intel/agent_registry.py`, `aria_service/tests/test_rf1160_agent_registry.py`
- **Diff summary:** `register()` now tracks DB success separately from Redis success. Returns `True` if DB write succeeded. Updated `test_error_resilience` to expect `True` when DB works.
- **Capability test:** `scripts/test_rf1475_agent_registry_redis_fallback.py` — 3/3 pass
- **Existing tests:** 10/10 pass

### R-F1476: ContractRegistry SQLite fallback
- **Files changed:** `aria_service/intel/agent_contract.py`, `aria_service/tests/test_rf1212_agent_contracts.py`
- **Diff summary:** Added dedicated SQLite DB (`agent_contract.db`) with full CRUD. DB-first reads/writes, Redis fallback. Test fixture clears DB for isolation.
- **Capability test:** `scripts/test_rf1476_contract_registry_sqlite_fallback.py` — 5/5 pass
- **Existing tests:** 20/20 pass

### R-F1477: AgentSignupVault test fixes + dead code fix
- **Files changed:** `aria_service/intel/agent_signup_vault.py`, `aria_service/tests/test_rf1231_agent_signup_vault.py`
- **Diff summary:** Fixed test method name (`import_from_portal_registry` → `import_open_portals`). Merged two stacked implementations in `import_open_portals()` into one that handles both portal types. Fixed test isolation with unique IDs.
- **Existing tests:** 25/25 pass

## Verification

All capability tests drive the REAL broken path (not mocked):
- R-F1475: `AgentRegistry.register()` called with Redis/state_store down → asserts `True`
- R-F1476: `ContractRegistry.register_contract()` called with Redis down → asserts `True`, then get/list/delete all work
- R-F1477: `AgentSignupVault.import_open_portals()` called with real portal dicts → asserts correct count

Claude: please verify each fix by re-running the tests, then commit + push + deploy. I did NOT auto-deploy per your directive.

# Session Postmortem — 2026-06-10

## Every Failure, Root Cause, and Structural Fix

### 1. WA Timeouts — Brain Unreachable
**Symptom:** WhatsApp messages timed out. "Brain unreachable" for hours.
**Root cause:** `state_store.incr()` held the global Python lock for the entire read-modify-write cycle (15s+). All other operations queued behind it. The waiter queue grew past 500, health checks failed, WA got timeouts.
**Fix applied:** R-F1493 — replaced locked read-modify-write with atomic SQL UPSERT.
**Structural guard needed:** None — the fix is structural (atomic SQL). But we need monitoring to detect lock contention before it becomes a wedge.

### 2. WA Internal DNS Failure
**Symptom:** WA couldn't reach the brain even after the lock fix.
**Root cause:** `BRAIN_SERVICE_URL=http://aria-intel.internal:8000` — the Fly.io internal DNS (`*.internal`) wasn't resolving from the WA machine.
**Fix applied:** Changed to `https://aria-intel.fly.dev` (public URL).
**Structural guard needed:** The WA listener should have a fallback — try internal URL first, fall back to public URL if DNS fails.

### 3. Vault Was Empty (Fabricated Data)
**Symptom:** Vault showed 13 "registered" portals but none were actually registered.
**Root cause:** `import_open_portals` marked `registration_type="none"` portals as "registered" — a lie. No registration ever happened.
**Fix applied:** R-F1491 — added `open_api` status. R-F1502 — added `needs_operator` status with honest blocker reasons.
**Structural guard needed:** The `determine_and_drive` function now enforces honest statuses. No code path can set "registered" without a real success.

### 4. Identity Assertion Blocked All Registrations
**Symptom:** All 30 registrable portals failed with "identity assertion failed".
**Root cause:** Default `ARIA_PORTAL_NAME` was "ARIA Research" which doesn't contain "arkmurus". The `assert_real_identity` guard requires "arkmurus" in the name.
**Fix applied:** R-F1495 — changed default to "ARIA Research (Arkmurus Group)".
**Structural guard needed:** The default should be tested at import time — if the identity assertion fails with defaults, log a CRITICAL warning at boot.

### 5. Dead Code in import_open_portals
**Symptom:** Registrable portals were never imported into the vault.
**Root cause:** `import_open_portals` had TWO stacked implementations — the first returned at line 416, making the second (lines 417-465) unreachable dead code.
**Fix applied:** R-F1477 — merged into a single implementation.
**Structural guard needed:** The pre-commit hook should detect unreachable code after a `return` statement.

### 6. Wrong Method Name in Production Code
**Symptom:** Vault auto-population silently failed at every boot.
**Root cause:** `main.py:2130` and `routes/aria.py:21484` called `import_from_portal_registry()` which doesn't exist. The method is `import_open_portals`. The `except Exception` handler caught the `AttributeError` and logged a debug message — silent failure.
**Fix applied:** R-F1482 — fixed method name in all call sites.
**Structural guard needed:** The pre-commit hook already caught this for new code. But existing call sites weren't checked. Add a grep for the wrong method name to CI.

### 7. Brain Breaker Mis-attributed Failures
**Symptom:** `agent_registry` and `agent_contract` showed 0% success rate in brain stats.
**Root cause:** When the absorb circuit-breaker was OPEN, `_record_signal(module, success=False)` was called — attributing brain overload as the module's failure.
**Fix applied:** R-F1480 — removed the `_record_signal` call on breaker-open.
**Structural guard needed:** The breaker path should never record a module-level signal. This is now structurally enforced.

### 8. ci_deploy Swept Runtime Files into Git
**Symptom:** `git add -A` in ci_deploy swept runtime DBs, session files, eval reports into deploy commits.
**Root cause:** `ci_deploy()` used `git add -A` which blanket-stages everything.
**Fix applied:** R-F1479 — changed to `git add -u` (tracked-modified only).
**Structural guard needed:** The pre-commit hook now checks for `git add -A` in deploy code.

### 9. Fabricated DD Seed Report
**Symptom:** A fake "Acme Defence GmbH" DD report was auto-seeded into the reports library.
**Root cause:** R-F1484 added a seed report with fabricated findings.
**Fix applied:** R-F1489 (Claude) — removed the seed.
**Structural guard needed:** None — this was a design error, not a code bug. The operator directive is clear: no fabricated data.

### 10. Hallucinated Function Names
**Symptom:** Pre-commit hook caught 3 function calls to methods that don't exist (`assess`, `classify`, `scan`).
**Root cause:** I wrote calls to `economic_substance.assess()`, `tbml_detection.classify()`, `counter_intelligence.scan()` without verifying they exist.
**Fix applied:** Fixed to real names (`score_substance`, `classify_anomaly`, `scan_entity`).
**Structural guard:** The pre-commit hook already catches this (§3b). It worked correctly.

## Recurring Pattern

The same failure modes keep appearing:
1. **Silent failures** — `except Exception: pass` hides bugs (vault empty, identity blocked)
2. **Fabricated data** — marking things as "registered" when they aren't
3. **Lock contention** — global locks causing system-wide outages
4. **Wrong function names** — calling methods that don't exist
5. **Dead code** — unreachable code behind a `return` statement

## Structural Guards to Add

| # | Guard | Prevents |
|---|---|---|
| 1 | Boot-time identity assertion test | Silent registration failures |
| 2 | WA listener DNS fallback (internal → public) | Brain unreachable from WA |
| 3 | State_store lock contention alert | Lock storms going undetected |
| 4 | CI check for `import_from_portal_registry` | Wrong method name in production |
| 5 | Pre-commit unreachable code detection | Dead code behind `return` |
| 6 | Vault status audit — alert on perpetual `pending` | Portals stuck forever |

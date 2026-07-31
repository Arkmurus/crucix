# Orphan module backlog — measured 2026-07-31 (R-F3573)

**61 modules in `aria_service/` are imported by nothing in production.** The
ecosystem audit reported **"Dead modules: 0"** on this same tree.

## Why the old number was zero

`check_dead_code` asked `if mod_name not in all_source` over a concatenation of
every file. Two compounding reasons, and the second was introduced as a fix:

1. **Substring, not import.** A name in a comment, a docstring, a log line or a
   registry key counted as a reference. `behavioural_anomaly`,
   `quarantine_network` and `credential_self_destruct` — three **security
   subsystems with no production caller** — read as live because
   `wiring_harness.py` lists them in a `gap_type` registry and
   `capability_gaps.py` names one in a comment.
2. **Tests counted as production.** R-F3381 added the test corpus to the haystack
   because a scoring harness used only by tests was being called dead. Correct as
   far as it went, but it collapsed two different states into one. A test-only
   *harness* is fine; a test-only *engine* is dead code that happens to own a
   test, and afterwards the gate could not tell them apart.

`check_orphan_modules()` reads the **import graph** (AST: `import`, `from … import`,
and `importlib.import_module("literal")`) and keeps the two states separate.
`cli/`, `static/` and `scripts/` are excluded — those are executed, never imported.

## How this was found

Not by looking for it. R-F3567 closed the §21 brain-wiring backlog to 0, and the
follow-up question was whether the newly-wired success branches were **reachable**.
Four of ten sampled modules had no caller. `audit_trail.py` had been wired by
R-F3563 and its only apparent referrer is `case.audit_trail.append(...)` — a dict
**field**, not the module.

🔑 A coverage gate cannot distinguish *wired* from *wired and dead*.

## Tier 1 — imported by NOTHING (17)

No production importer, no test importer. Each is either dead or needs connecting.

| module | R-number / what it claims to be |
|---|---|
| `intel/audit_trail.py` | R-F2121 organism audit trail — **wired by R-F3563 while unreachable** |
| `intel/continuous_learner.py` | R-F1064 "Cost-Free Continuous Learning Engine" |
| `intel/github_search.py` | R-F1061 GitHub OSINT search |
| `intel/tenant_namespace.py` | R-F81 **multi-tenant isolation primitive** |
| `intel/brain_signal_consumer.py` | brain signal consumer |
| `intel/dd_case_library.py` | DD case library |
| `intel/engagement.py` | engagement tracking |
| `intel/geoip_lookup.py` | GeoIP |
| `intel/global_defence_knowledge.py` | defence knowledge pack |
| `intel/global_export_control.py` | export-control knowledge |
| `intel/kaspersky_mitigation.py` | Kaspersky mitigation |
| `intel/osint_email_breach.py` | breach lookup |
| `intel/portal_knowledge.py` | portal knowledge |
| `intel/sipri_knowledge.py` | SIPRI knowledge |
| `intel/vetting_standard_knowledge.py` | vetting standards |
| `intel/sources/worldbank_documents.py` | World Bank documents source |
| `utils/command_cache.py` | command cache |

`tenant_namespace` deserves the second look: a multi-tenant **isolation**
primitive that nothing imports is a security-relevant absence, not just dead code.

## Tier 2 — imported ONLY by tests (44)

Not automatically wrong. `dd_independence_eval` is an offline scoring harness and
`seeded_defect` is a deliberate coder target — both legitimately test-only. But
several are engines:

- **Security subsystems with no caller:** `behavioural_anomaly` (R-F1136),
  `quarantine_network` (R-F1135), `credential_self_destruct` (R-F1137).
  Built, tested, invoked by nothing. **Operator decision** — wiring an
  auto-quarantine system that can disable live modules, or a credential
  panic-button, is a design choice, not a gap fix.
- **Superseded legacy:** `eu_sanctions_ingest`, `un_sanctions_ingest`,
  `uk_ofsi_ingest`. Verified NOT a coverage gap — `intel/sources/fcdo_sanctions`
  and `intel/sources/un_sc_sanctions` carry UK and UN with 21 production
  references each, and `sanctions_canonical/` carries OFAC and EU. These three
  are duplicated leftovers. Checked before raising an alarm.
- The rest need a per-module decision: connect, or delete.

The full list lives in `ORPHAN_BASELINE_TEST_ONLY` in `scripts/ecosystem_audit.py`.

## The gate

**Pinned, not gated to zero.** The debt is pre-existing; a gate that fails on day
one gets muted, and then it protects nothing. So:

- Both lists **may only shrink** (capped at 17 / 44 by
  `test_rf3573_orphan_module_detection.py`).
- A **newly orphaned** module fails the build — that is a change someone just made.
- An entry that is **no longer orphaned must be deleted**; the audit prints
  `FIXED:` for it and the test fails until it goes. A pinned list that outlives
  the debt it records is a lie.

Proven working, not assumed: a throwaway module was added to `intel/`, the audit
exited **1** naming it, and exited **0** again once removed. The baseline is
stored posix-normalised — `str(Path)` gives backslashes on Windows and forward
slashes on the Linux CI runner, so a baseline pinned from a dev machine would
have matched nothing in CI and reported all 61 entries as new.

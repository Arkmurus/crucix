# R-number index (human-readable view)

**Canonical source of truth.** Machine-readable claim ledger lives at
`data/r_number_reservations.json`, managed by `aria_service/intel/r_number_registry.py`
(R-F540, separate session). This file is a narrative complement only — for
session changelogs and quick scanning. **Do NOT use this file to claim an
R-number; use the registry helper:**

```python
from aria_service.intel.r_number_registry import reserve, mark_shipped
r_num = reserve("My new feature", agent="claude-session-2026-05-16")
# ... do the work ...
mark_shipped(r_num, commit_sha="abc1234")
```

## Active reservations claimed this session (2026-05-16)

| R-F### | subject | commit | notes |
|---|---|---|---|
| F551 | docs/R_NUMBERS.md narrative complement (this file) | this commit | Yielded reservation-log role to R-F540 JSON registry — see notes below. |
| F552 | Portals honesty — `_subStatus` so "49/49 OK" doesn't mask 4/28 coverage | this commit | Phase A honesty. Mirrors R-F34 contract. |
| F553 | AfDB error swallowing — fix `enrichFetchError` precedence | this commit | `[AfDB] Error: Error` → real message. 10 tests pass. |
| F554 | Lusophone proxy attempts log — distinguish 200(empty)/200(0-items)/200 healthy | this commit | 5 tests pass. |
| F563 | Comtrade IMF DOTS — ENOTFOUND-aware circuit (intra+cross-sweep) | this commit | 3 tests pass. Renamed from R-F555 (collision with R-F540 registry). |
| F564 | DefenseNews / throttle.mjs — escalating cooldown (5/10/20/40-min cap) | this commit | 5 tests pass. Renamed from R-F556. |
| F565 | ARIA sweep-ingest diagnostic enrichment (host+payload+elapsed) | this commit | Logging-only. Renamed from R-F557. |

## R-F551 yield notes

This file started life as a markdown-only reservation log claimed under
R-F551. The 10:07-modified `data/r_number_reservations.json` from a
prior agent session (R-F540) is structurally richer (claim-by-agent,
status field, in-process threading lock) so that becomes canonical.
R-F551 is now this narrative companion; the JSON registry is the
authoritative ledger. If a future agent reads this and the JSON
disagrees, **the JSON wins**.

## Collision history (since 2026-05-15)

| original | renamed-to | reason |
|---|---|---|
| R-F555 (Comtrade) | R-F563 | Collided with R-F540 registry claim "MEMORY.md hard-trim". |
| R-F556 (DefenseNews) | R-F564 | Collided with R-F540 registry claim "Repo CLAUDE.md". |
| R-F557 (ARIA ingest) | R-F565 | Collided with R-F540 registry claim "Stream guard wiring audit". |
| R-F534..F538 → F542..F546 | (in git) | See commit `11e6976`. |

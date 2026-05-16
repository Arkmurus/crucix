# DD report versioning — recommendation

**Question (operator, 2026-05-16):** "What if someone runs DD about the same company twice — how should we record that? We need DD's about the same company or person to get updated as a different version, professional and coherent."

**Recommendation in one line:** Add a deterministic `canonical_entity_id` to every report, store it on the index, and treat re-runs of the same `canonical_entity_id` as version 2 / 3 / N of the **same case file** rather than orphan reports.

## Today's state (mapped via `aria_service/intel/dd_orchestrator.py` + `dd_schema.py`)

- Each DD run gets a random `run_id = dd_<12-hex>` (no entity identity in the key).
- Stored at Redis key `crucix:dd:report:{run_id}` with 7-day TTL.
- Index `crucix:dd:report_index` appends one row per run with `{run_id, entity_name, jurisdiction, generated_at, risk_classification}` — **no de-duplication**.
- `/api/aria/dd/reports` returns the index newest-first. Running the same entity twice produces two unrelated rows.
- The **watchlist rescreen** path (`dd_orchestrator.py:5999-6012`) already does case-insensitive name match to fetch a "previous report" — proving the architecture anticipates re-runs, but the linkage isn't surfaced anywhere else.
- `entity_graph.py` already uses structured node IDs like `company:GB:12345678` — proves the team has thought about canonical keys for the graph; we extend the same pattern to reports.

## Proposed design — canonical-entity → version chain

### 1. Canonical entity ID (deterministic, computed once per run)

Compute after the IDENTITY layer runs (line ~3387 of `dd_orchestrator.py`), before storage.

| entity type | canonical_entity_id format | example |
|---|---|---|
| Company **with** registration number | `company:{ISO2}:{REGNUM_upper_stripped}` | `company:BR:0768900200018-89` |
| Company **without** registration number | `company:{ISO2}:name_hash` where `name_hash = sha1(normalized_name)[:12]` | `company:TR:a1b2c3d4e5f6` |
| Person (rare to canonicalise reliably) | `person:{normalized_name}:{nationality_ISO2 or '??'}:{dob_year or '????'}` | `person:johnsmith:GB:1972` |
| Vessel | `vessel:imo:{IMO_NUMBER}` (fallback `vessel:flag:{name_hash}`) | `vessel:imo:9837492` |
| Address | `address:{ISO2}:line_hash` | `address:US:8a72bd...` |

**Why this shape:**
- Registration numbers (company + LEI + IMO) are globally unique — best canonical input when available.
- Name-hash fallback handles unregistered/early-stage entities without forcing exact-string match. Use a strong normaliser: lowercase, strip legal suffixes (Ltd/LLC/S.A./S.A./GmbH/PLC/Inc/AG/Pty), strip diacritics, collapse whitespace.
- Person canonicalisation is intentionally lossy (year-of-birth bucket, normalised name) — exact person identity is fundamentally hard. The system **must** allow operator override when the auto-key conflates two different people.

`normalized_name()` is the only new utility; ~20 lines and worth a unit test seed:
```python
"Embraer S.A."      -> "embraer"
"EMBRAER, S.A."     -> "embraer"
"Aselsan A.S."      -> "aselsan"
"BAE Systems plc"   -> "bae systems"
```

### 2. Index schema additions (backward-compatible)

Append three optional fields to each `report_index` row:
```jsonc
{
  "run_id":             "dd_731bbaeb78b3",      // existing
  "entity_name":        "Embraer S.A.",         // existing
  "jurisdiction":       "Brazil",               // existing
  "generated_at":       "...",                  // existing
  "risk_classification":"HARD_STOP",            // existing

  "canonical_entity_id":"company:BR:0768900200018-89",  // NEW
  "version_number":     2,                              // NEW (1 if first)
  "previous_run_id":    "dd_a1b2c3d4e5f6"               // NEW (null if v1)
}
```

Rows written before R-F574 still work — the three new fields are nullable; the dashboard treats `null version_number` as "v1, untracked-history".

### 3. Re-run detection (one new function in `dd_orchestrator.py`)

Pseudocode (~30 lines):

```python
def _resolve_version_chain(canonical_entity_id: str) -> tuple[int, str | None]:
    """Return (version_number_for_new_report, previous_run_id)."""
    if not canonical_entity_id:
        return 1, None
    history = [r for r in load_index()
               if r.get("canonical_entity_id") == canonical_entity_id]
    if not history:
        return 1, None
    history.sort(key=lambda r: r["generated_at"], reverse=True)
    return (history[0]["version_number"] or 1) + 1, history[0]["run_id"]
```

Called once per run, after the canonical_entity_id is computed but before the report is persisted.

### 4. New diff section in the report itself

When `version_number > 1`, the orchestrator computes a delta vs the previous report and writes a new `VersionDiff` section into the ARKDDReport:

```jsonc
"version_diff": {
  "previous_run_id":     "dd_a1b2c3d4e5f6",
  "previous_generated":  "2026-04-12T10:32:00Z",
  "previous_risk":       "AMBER",
  "current_risk":        "HARD_STOP",
  "risk_changed":        true,
  "new_findings":        [ "OFAC SDN: ... (added 2026-05-10)" ],
  "resolved_findings":   [ "PEP exposure (no longer flagged)" ],
  "changed_findings":    [ "Ghost score: 12 -> 4 (GREEN)" ],
  "unchanged_count":     7,
  "summary":             "Risk escalated from AMBER to HARD_STOP. 1 new sanctions match. 1 finding cleared. 7 findings unchanged."
}
```

This is the single most operationally valuable output of versioning — auditors and broker-team users will live in this section.

### 5. API surface

| Method | Path | Behaviour |
|---|---|---|
| `POST /api/aria/dd/orchestrate` | (unchanged input) | Output now includes `canonical_entity_id`, `version_number`, `version_diff` |
| `GET /api/aria/dd/reports?collapse=true` | NEW param | Returns one row per `canonical_entity_id` showing latest version + `total_versions: N` |
| `GET /api/aria/dd/case/{canonical_entity_id}` | NEW | Returns full version chain — all runs against this entity in chronological order |
| `GET /api/aria/dd/report/{run_id}` | unchanged | Single-run, but response now includes `version_chain: [{run_id, version_number, generated_at, risk}, ...]` |

### 6. Manual link/unlink (operator override)

Two failure modes need an escape valve:

- **False merge** — two different people named "John Smith" both get canonical `person:johnsmith:??:????` and ARIA wrongly treats them as the same case. Operator must be able to split:
  - `POST /api/aria/dd/case/{canonical_entity_id}/split` with `{run_ids_to_extract: [...]}` → creates a new canonical id, moves those runs to it.
- **False split** — same company filed under two different jurisdictions (Embraer S.A. Brazil + Embraer Aircraft Holding Inc. USA). Operator merges:
  - `POST /api/aria/dd/case/merge` with `{from: "company:US:...", into: "company:BR:..."}` → rewrites `canonical_entity_id` on the absorbed rows, preserves their `version_number` order.

Both operations are logged to provenance for audit.

### 7. Storage / TTL

- Reports stay at `crucix:dd:report:{run_id}` 7-day TTL (existing). When a new version is requested, the orchestrator can pull the previous report from Redis OR from cold storage if it's expired.
- **Recommend extending TTL to 90 days** for reports that have `version_number > 1` — they're case-file material and deserve longer retention. Per [[aria_infinite_memory]] the canonical entity file is infinite; only the raw JSON blob can be aged off, with the index entry persisting always.
- Add a **cold tier** for index entries older than 90 days: write the index entry to `data/dd_case_archive.sqlite` so the version chain remains queryable even after Redis eviction.

### 8. Migration path

Existing reports (R-F526..F549 era):
- On read of any `/api/aria/dd/reports` response, run a background `_backfill_canonical_entity_id()` over the index — uses the existing index repair logic (`dd_orchestrator.py:5646-5728`).
- Backfill is idempotent and ~1 second per 100 reports.
- After backfill, the historical reports automatically join their version chain.

## Cost / complexity estimate

| Component | LOC | Risk |
|---|---|---|
| `normalize_name()` + canonical_entity_id computer | ~30 | Low — pure function, unit-testable |
| `_resolve_version_chain()` | ~30 | Low |
| Index schema additions | 0 (additive) | Low |
| `version_diff` computation | ~80 | Medium — diff logic across nested findings |
| 4 new API endpoints | ~120 | Low |
| Split/merge operator overrides | ~60 | Medium — needs provenance log |
| Backfill helper | ~40 | Low |
| Regression tests (canonical key + version chain + diff) | ~150 | Low |
| **Total** | **~510** | One self-contained R-F574 |

Single ship target: **R-F574 — DD case file: canonical entity + version chain + diff**.

## Why this design (vs alternatives considered)

- **vs "just dedupe by exact name"** — fails on whitespace, legal-suffix, accent variation. Embraer / Embraer S.A. / EMBRAER, S.A. / Embraer SA would all be different cases. Canonical_entity_id fixes that and adds jurisdiction disambiguation for free.
- **vs "use opensanctions UID as the canonical key"** — couples us to a third-party identifier; many of our DD targets aren't in OpenSanctions at all (the whole point is to investigate them); breaks per [[aria_mirrors_claude]] doctrine.
- **vs "let operators manually link reports"** — works but doesn't scale and doesn't help auditors trying to reconstruct history. Auto + override is strictly better.
- **vs "version stored inside the JSON blob only, no index changes"** — makes the list endpoint useless for the operator question "how many times have we looked at this company?" Index-level is the right place.

## How this complements the parallel session's queue

The parallel agent is working on R-F557 stream-guard / R-F558 constitution review / R-F560 /health/error-streak / R-F561 dashboard panel / R-F562 self-learning. None of those touch DD storage. R-F574 (DD versioning) is orthogonal and can ship in parallel without conflict.

## Suggested rollout order

1. **First**: fix the P0 sanctions false-positive defect (R-F569). Versioning useless if every version is HARD_STOP.
2. **Then**: ship R-F574 (this proposal). Operator can re-run Embraer / Aselsan / Rosoboronexport / Acme through the FIXED orchestrator and the version chain populates.
3. **Then**: 90-day cold-tier SQLite (R-F575 — small follow-up).

---

*The DD ledger is the single most important artefact an operator-facing MVP produces. Getting versioning right now, before the report library grows to hundreds of entries, is cheap — retrofitting after is the kind of migration that ARIA's own constitution clause 14 (epistemic humility) tells us to avoid.*

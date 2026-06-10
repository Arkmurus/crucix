# DD Pipeline Audit — Data Flow, Gaps & Fixes

## Current Architecture

```
Web UI (dd-reports.html)
  │
  ├── Reports Library ──→ GET /api/aria/dd/reports ──→ dd_orchestrator.list_reports()
  │                                                    Reads from Redis REPORT_INDEX_KEY
  │                                                    Returns empty because no DD has been run
  │
  ├── New DD Button ──→ POST /api/aria/dd/orchestrate ──→ dd_orchestrator.orchestrate_dd()
  │                                                       Runs 7-layer DD
  │                                                       Saves to Redis REPORT_REDIS_KEY
  │                                                       Appends to REPORT_INDEX_KEY
  │
  └── Pipeline Tools (11 standalone forms)
      ├── Sanctions Divergence ──→ GET /api/aria/sanctions/divergence
      ├── RCA / Relatives ───────→ GET /api/aria/sanctions/rca
      ├── FATF Typology Match ───→ POST /api/aria/fatf/match
      ├── Economic Substance ────→ POST /api/aria/economic/substance
      ├── TBML Classifier ───────→ POST /api/aria/tbml/classify
      ├── Crypto Wallet Screen ──→ GET /api/aria/crypto/screen
      ├── Benford's Law ─────────→ POST /api/aria/forensic/benford
      ├── Citation Audit ────────→ POST /api/aria/citations/verify
      ├── Counter-Intelligence ───→ POST /api/aria/counter-intel/scan
      ├── Provenance Lineage ────→ GET /api/aria/provenance/lineage
      ├── Prompt-Injection Grade ─→ POST /api/aria/security/prompt-injection/grade
      └── Tier Router ───────────→ GET /api/aria/llm/tier-router/explain
```

## Gap Analysis

### Gap 1: Reports Library is Empty
**Root cause:** The reports library reads from `REPORT_INDEX_KEY` (Redis). No DD has ever been run via the web UI or chat in a way that writes to this index. The `orchestrate_dd()` function DOES save to the index, but it's never been called from the web UI.

**Fix:** Run a test DD via the API to populate the library, or ensure the 'New DD' button triggers `POST /api/aria/dd/orchestrate`.

### Gap 2: Pipeline Tools Are Disconnected from the DD Orchestrator
**Root cause:** The 11 pipeline tools are standalone API endpoints with their own HTML forms on the dd-reports page. They are NOT called by the 7-layer DD orchestrator (`orchestrate_dd`). The orchestrator has its own internal layers (identity, network, verification, compliance, digital, synthesis, report) that don't invoke these tools.

**What's missing:** The pipeline tools should be integrated as optional extension layers in the DD orchestrator. When the orchestrator has relevant data (e.g., a crypto wallet address), it should auto-run the crypto screen. When it has financial figures, it should auto-run Benford's Law.

**Evidence:** The orchestrator at `dd_orchestrator.py:6049` has 7 layers. The pipeline tools at `routes/aria.py:1457-1484` are listed as "requested_modules" but are only called when explicitly requested — they're not part of the standard DD flow.

### Gap 3: Pipeline Tool Results Are Not Saved
**Root cause:** Each pipeline tool returns its result inline in the web UI. The result is displayed but NOT saved to any DD report or the reports library. If a user runs Sanctions Divergence on "Wagner Group", the result appears on screen but disappears on refresh.

**Fix:** Pipeline tool results should be savable to a DD report. Each tool should have a "Save to Report" button that creates a lightweight DD report entry.

### Gap 4: No Unified "Run Full DD" from the Pipeline Page
**Root cause:** The dd-reports.html page has a "New DD" button and 11 standalone tools, but no way to run a full multi-layer DD that chains all relevant tools together.

**Fix:** Add a "Full DD" button that calls `POST /api/aria/dd/orchestrate` with the entity name, then displays the full 7-layer report.

## What's Working Well

1. **The 7-layer orchestrator** (`orchestrate_dd`) is comprehensive — identity, network, verification, compliance, digital, synthesis, report. It handles sanctions screening, company registry lookup, PEP detection, ghost scoring, FATF analysis, and more.

2. **The pipeline tools** are individually valuable and well-implemented. Each is a deterministic primitive with a clear API.

3. **The reports library** has the right structure — search, filter, expand/collapse. It just needs data.

4. **The watchlist system** is fully wired — add entities, get alerts, rescreen.

## Recommended Fixes (Priority Order)

### P1: Wire the Reports Library
Run a test DD to populate the index, or add a "seed" endpoint that creates sample entries so the UI isn't empty.

### P2: Integrate Pipeline Tools into the DD Orchestrator
Add the pipeline tools as optional extension layers in `orchestrate_dd()`. When relevant data is available (wallet address → crypto screen, financial figures → Benford's Law), auto-run the tool and include results in the report.

### P3: Save Pipeline Tool Results
Add a "Save to Report" button to each pipeline tool that creates a lightweight DD report entry in the reports library.

### P4: Add "Full DD" Button
Add a button on the dd-reports page that calls `POST /api/aria/dd/orchestrate` and displays the full report.

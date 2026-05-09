# ARIA — Crawling, Learning & Adversarial Health Report

**Date:** 2026-05-09 (end of session)
**Audience:** operator + team
**Reading time:** 10 min

You asked three questions. Direct answers, then the detail.

| Surface | Status | Biggest gap |
|---|---|---|
| **Crawling / scraping** | Largely healthy. 54 Node source files + 8 Python source classes + dedicated scraper sub-package with Playwright support. Fallback chains everywhere. | One Node-side schema drift just fixed today (R-F49 EU TED). No daily aggregated source-uptime report. |
| **Learning loop** | Active. `brain_hook.absorb` is the central choke-point; called from email reader, LinkedIn intel, proactive, explorerScheduler, bd_intelligence on Node side and 40+ Python modules. | **`absorb` doesn't take a `user_id`/`sector` — the loop is blind to per-customer requirements.** Means R-F48 personas reach the chat output but DON'T flow back into learning telemetry. |
| **Adversarial** | Working as designed. 11 attacks in `ATTACK_LIBRARY`, scheduled Wed + Sun 10:00 UTC via autonomous tasks, plus a separate 20-clause constitution adversarial suite Sunday 10:30 UTC. Endpoints: `/adversarial/run_weekly`, `/adversarial/run_single`, `/adversarial/regression_replay`, `/adversarial/stats`, `/adversarial/amendments`. | Last published baseline: 90.9% on 2026-04-23 — **16 days stale**. The runs ARE happening; the baseline isn't being refreshed in published memory. |

---

## 1. Crawling & scraping — full picture

### Surface area
- **Node side (seenode)**: `apis/sources/` — **54 source modules**. Categories:
  - Sanctions / AML: `fcdo.mjs`, `opensanctions` (in apis/), `eu_dual_use.mjs`, `export_controls.mjs`, `export_control_intel.mjs`
  - Defence news / OSINT: `defense_news.mjs` (10+ feeds), `defense_events.mjs`, `gdelt.mjs`, `bluesky.mjs`
  - Procurement: `procurement_tenders.mjs` (TED, SAM, GESPI, regional), `lusophone_procurement.mjs`, `arkumurus.mjs`
  - Macroeconomic: `comtrade.mjs`, `fred.mjs`, `bls.mjs`, `eia.mjs`, `gscpi.mjs`
  - Cyber / threat: `cyber_threats.mjs` (NVD, ransomwatch), `cisa-kev.mjs`
  - Movement / signals: `adsb.mjs`, `firms.mjs`, `cloudflare-radar.mjs`
  - Geopolitical: `acled.mjs`, `afdb.mjs`, `un_*`, `gdelt.mjs`, `epa.mjs`, `bls.mjs`
- **Python side (fly)**: `aria_service/intel/sources/` — 8 specialised classes:
  - `ofac_sdn.py`, `fcdo_sanctions.py`, `un_sc_sanctions.py`, `worldbank_debarred.py`, `acled.py`, `sec_edgar.py`, `academic.py`, `_common.py`.
- **Scraper sub-package**: `aria_service/intel/scraper/`:
  - `orchestrator.py` — portal detection + dispatch
  - `playwright_engine.py` — JS-rendered pages
  - `procurement_adapters.py` — TED / SAM / etc.
  - `generic_adapter.py` — fallback HTTP+parse
- **Deep researcher**: `aria_service/intel/deep_researcher.py` — 1,558 lines. Orchestrates web search + RAG + tool chains for chat-driven investigations.

### Resilience
- **Per-source circuit breakers** at `lib/util/throttle.mjs` — `shouldSkip`, `recordFailure`, `recordSuccess`. Sources with sustained failures auto-suspend.
- **Fallback chains** on RSS sources: `direct → rss2json → allorigins`. Saw this pattern in `defense_news.mjs:9-10` today.
- **UA rotation** to Chrome desktop (R-F18..R-F20 fleet-wide fix) — covers most cloud-IP blocks.
- **`recordSourceSweep`** logs per-source attempts to the learning store (`getSourceHistory`); the `/api/sources/uptime` endpoint exposes the rollup.
- **`brainAbsorb`** R-F45 boot self-check now fails loud if seenode → fly token mismatch, so the silent-drop bug that was happening for an unknown duration cannot recur silently.

### Today's fixes that touched crawling
- **R-F49**: EU TED v3 schema drift (`pageSize` → `limit`, `cpvCode=` → `classification-cpv=`, eForms field names). Was returning HTTP 400 every cycle; now returns notices.
- **R-F49**: R-F37 acronym denylist (ITAR/OFAC/NATO/etc.) — stops prompt fragments from reaching OpenSanctions and burning quota.
- **R-F45**: brainAbsorb 401 → loud failure + boot self-check.

### Honest gaps
1. **No customer-facing source-health dashboard.** `/api/sources/uptime` exists; nothing renders it for a non-engineer to read.
2. **Source coverage by region is uneven.** Lusophone Africa / NATO are deep; Gulf / SEA / LatAm are at floor. The `aria_global_positioning.md` doctrine commits to parity but the source files don't reflect it yet.
3. **No daily aggregated "X sources of Y healthy" line** — would surface a trend before the operator notices a single failure.

### Recommended next moves
- Build a `/sources.html` page (or extend the existing one) that calls `/api/sources/uptime` and renders the rollup with per-source severity. ~½ day. Composes with R-F47 status page.
- Add region-coverage rebalancing items to the strategic roadmap (Month 4 / 5) — Gulf/SEA/LatAm parity sources.

---

## 2. Learning loop — full picture

### What works today
- **`brain_hook.absorb`** is the central absorption point (`aria_service/intel/brain_hook.py:389`). Every intel module calls it after producing analysis output.
- **Side effects per absorb call**:
  - Mastery tier update (per-topic EWMA in `student.py`)
  - Knowledge ingestion (facts → RAG + intel ledger)
  - Neural memory grow (new concepts woven into associative network)
  - Capability-gap recording when `gap_type` set
  - Latency telemetry → circuit breaker if absorb p95 spikes
  - Mistake-ledger entry if `success=False`
- **Modules calling absorb** (Node side):
  - `lib/aria/emailReader.mjs` — every email ingested
  - `lib/aria/linkedinIntel.mjs` — LinkedIn alerts
  - `lib/aria/proactive.mjs` — proactive outreach
  - `lib/self/explorerScheduler.mjs` — autonomous research
  - `lib/self/bd_intelligence.mjs` — BD pipeline
  - `lib/self/learning_store.mjs` — sweep results
- **Modules calling absorb** (Python side, partial list):
  - `dd_orchestrator` after watchlist re-screen and after every DD run
  - `researcher` after every research cycle
  - `verification_gate`, `tender_monitor`, `chain_correlator`, `competitor_tracker`, `narrative_monitor`, plus 30+ others
- **Hypothesis backlog**: `aria_service/intel/researcher.py` produces hypotheses; validation cycle drains 6–8 per cycle (R-F32 bumped 3→8). Memory note: 114 OPEN at 2026-05-06 close.
- **Calibration**: `calibration_review.py` runs weekly, surfaces over/under-confidence per topic. Last reading 22 days ago: under-confident by ~14% (safe).
- **Mistake ledger**: `aria_service/intel/mistake_ledger.py` — every wrong-output incident gets logged with the corrected version; `correction_recall` injects relevant past mistakes into the system prompt at the next chat turn.

### The big gap (the one your message zeroes in on)

**`brain_hook.absorb` does NOT accept `user_id` or `sector`.**

Today's R-F48 added persona overlays at the chat layer — a compliance-officer user gets the compliance system-prompt overlay on their first turn. But the absorb call after that turn carries no user-identity, so:
- Mastery scoring is global, not per-persona.
- Capability-gap signals don't tag which customer-type triggered them.
- Mistake-ledger entries don't differentiate "compliance officer was wrong-answered on tier-1a citation rule" from "broker was wrong-answered on offset structure" — both look the same.
- The hypothesis backlog can't bias toward queries that came from underrepresented sectors.

This is a real product gap. A "self-improving AI per customer" claim that compliance officers will ask about cannot be made truthfully today.

### Recommended fix
Extend `brain_hook.absorb` with two kwargs (~1 day work):
- `user_id: str = ""` — caller passes the user from the chat path (already on the engine signature post-R-F48b).
- `sector: str = ""` — derived once at the call site from the user record.

Then:
- Tag mastery EWMA buckets by `(topic, sector)` instead of just `(topic)`.
- Add `sector` to capability-gap records.
- Add `sector` to mistake-ledger entries.
- Surface per-sector adversarial pass-rates on the dashboard.
- Bias hypothesis queue toward topics/sectors with the most active users.

This would close the loop your message asks about. Suggest as **R-F56** when the operator agrees the gap is worth this work.

---

## 3. Adversarial — full picture

### What runs
- **Library**: 11 attacks in `aria_service/intel/adversarial_challenge.py:ATTACK_LIBRARY`. Categories: A_FALSE_INFO (false-premise injection), B_AUTHORITY (identity spoof), C_GRADUAL (gradual-context manipulation), D_DOCUMENT (forged-document review traps), etc.
- **Endpoints**: `/api/aria/adversarial/{run_weekly,run_single,regression_replay,stats,amendments}` mounted at `routes/aria.py:12230+`.
- **Schedule** (from `autonomous/tasks.yaml`):
  - Wed 10:00 UTC + Sun 10:00 UTC: `WEEKLY-ADVERSARIAL` runs 5 attacks per cycle from `ATTACK_LIBRARY`. Scores per-category resistance + manipulation_resistance; tags `clause17_18_19`.
  - Sun 10:30 UTC: `WEEKLY-CONSTITUTION-AUDIT` — separate 20-clause adversarial suite, runs after the weekly attack and feeds the briefing engine.
- **Past evidence the suite catches things**:
  - `adversarial_challenge.py:93+` carries 11 named attacks each with anchored past-incident references.
  - The A1 ANGOLA ATT false-premise attack motivated **constitution clause 23** ("no acceptance of user-asserted compliance premises").
  - The B1 Anthropic-safety-team identity-spoof attack motivated input-side prompt injection detection in `security_protocol.py`.

### Honest current state
- **Suite size**: 11 attacks. The strategic-review §3 gap analysis recommended adding more, especially for the 6-persona overlay set R-F48 just shipped.
- **Baseline**: 90.9% on 2026-04-23. Today is 2026-05-09 — that's 16 days old. The Wed+Sun runs since then would have produced 4–5 fresh data points; they're not surfaced anywhere I can see at the doc level.
- **No published per-persona breakdown.** Today an attack against a compliance officer's overlay (which has different output framing) might pass / fail differently from the broker overlay. We don't know.
- **No surface in account.html or model-card.html** — a compliance buyer looking at the model card sees the 23-clause constitution but no live adversarial pass-rate.

### Is it working "as supposed to"?
- **Mechanically yes.** Schedule fires; endpoints exist; results persist; amendments queue exists.
- **Strategically partial.** The discipline is right but:
  - 11 attacks is a low coverage number for a 23-clause constitution + 6-persona overlay matrix.
  - Stale published baseline ⇒ neither operator nor team can claim "ARIA passes adversarial 95%" with evidence.
  - No automatic public surfacing on the model card / status page.

### Recommended next moves
1. **Run a fresh suite this week** (operator can `curl -X POST /api/aria/adversarial/run_weekly` with bearer). Capture the result, paste it into a memory file. ~5 minutes operator action.
2. **Surface the pass-rate** on the status page or model card (small frontend change). ~½ day.
3. **Expand the suite per persona** — when R-F48 stabilises, write 2–3 attacks per persona that exploit the overlay's specific framing. So 11 → 23 attacks. ~2 days.
4. **Alert on regression** — if a Wed/Sun run drops more than 5pp below the rolling 4-week average, fire a Telegram + status-page incident. ~1 day.

---

## 4. The composite question — is ARIA self-improving?

| Claim | Truth |
|---|---|
| "ARIA absorbs every chat turn into the brain" | ✅ Yes — `brain_hook.absorb` fires on every turn that the engine completes. |
| "ARIA refines per customer over time" | ⚠️ Partial — refinement happens globally (mastery, RAG, intel ledger) but **not per-user/per-sector** because absorb has no `user_id`. |
| "ARIA captures customer-specific requirements at signup" | ✅ Yes — R-F48 ships sector / use-case / region / language / compliance-needs / purpose statement on the user record. |
| "Those captures flow back into the learning loop" | ❌ Not yet — the user record is read at chat time to pick a persona overlay, but the absorption telemetry doesn't carry the persona forward. R-F56 closes this. |
| "Adversarial testing is happening" | ✅ Yes — Wed + Sun cycles + Sun constitution audit. |
| "Adversarial pass-rate is published" | ❌ Stale — last published 90.9% on 2026-04-23. |

**One-sentence summary**: the learning machinery works, but it's collectivised — every customer's signals feed one shared brain instead of compounding per persona; closing R-F56 (~1 day) is what would let you honestly tell a compliance officer "ARIA learns *your* workflow, not a generic one".

## 5. Suggested R-F sequence next session

1. **R-F56 — per-user/sector tagging in `brain_hook.absorb`**. ~1 day. Closes the headline gap above.
2. **R-F57 — fresh adversarial run + dashboard surfacing**. ~½ day. Refreshes the stale baseline + makes it visible.
3. **R-F58 — source uptime dashboard at `/sources.html`**. ~½ day. Gives operator visibility on the 54-source sweep.
4. **R-F59 — per-persona adversarial expansion (11 → 23 attacks)**. ~2 days. Strengthens claims about persona-tuned safety.

These four together turn ARIA from "learns globally" to "learns per customer with adversarial evidence per persona" — the claim a compliance officer evaluating a $199/mo Pro Intelligence subscription will actually test.

---

## 6. References

- `aria_service/intel/brain_hook.py:389+` — central absorb function
- `aria_service/intel/adversarial_challenge.py:93+` — `ATTACK_LIBRARY`
- `aria_service/autonomous/tasks.yaml:1015+` — adversarial schedule
- `aria_service/intel/mistake_ledger.py` — mistake recall
- `aria_service/intel/calibration_review.py` — weekly calibration
- `lib/self/learning_store.mjs:194+` — Node-side `brainAbsorb` bridge
- `lib/util/throttle.mjs` — circuit breaker primitives
- `apis/sources/` — 54 Node source modules
- `aria_service/intel/scraper/` — Playwright + adapter sub-package
- `docs/strategic_review_2026_05_09.md` §3 — gap analysis context
- `docs/team_report_2026_05_09.md` §1.7 — last 14-day source-fix highlights

Memory files relevant: `aria_global_positioning.md`, `aria_core_mastery_topics.md`, `feedback_aria_rule_zero.md`, `feedback_pay_once_remember_forever.md`, `next_session_todo.md`.

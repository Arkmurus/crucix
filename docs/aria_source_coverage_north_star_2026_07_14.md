# ARIA Source Coverage North Star

Prepared: 2026-07-14
R-number: R-F2599

## Purpose

This document is the source-side companion to the Golden Intel North Star model.
The goal is surgical: identify which ARIA data sources already support
decision-grade customer value, which sources create noise, and what must be
improved next so Golden Intel becomes a real USP instead of a filtered news feed.

## Evidence base

Local code reviewed:

- `aria_service/intel/news_monitor.py`
- `aria_service/intel/golden_intel_bridge.py`
- `apis/briefing.mjs`
- `apis/sources/sanctions.mjs`
- `apis/sources/procurement_tenders.mjs`
- `apis/sources/procurement_portals.mjs`
- `aria_service/intel/portal_registry.py`
- `docs/free_data_sources_prelaunch_2026_07_04.md`
- `public/capability-claims.json`
- `data/golden_intel_north_star_rubric.json`

Live/current checks:

- `python -m pytest aria_service/tests/test_rf2555_golden_intel_bridge.py aria_service/tests/test_rf2557_ingest_inbox.py aria_service/tests/test_rf2559_public_watchlist.py aria_service/tests/test_rf2560_sanctions_diff.py -q`
  - Result: 38 passed.
- `node --max-old-space-size=4096 --test --test-timeout=30000 test/channel-golden-intel-rf2469.test.mjs test/telegram-golden-intel-brief-rf2392.test.mjs test/golden-intel-empty-state-rf2583.test.mjs`
  - Result: 11 passed.
- Live Golden Intel feed probe with internal token:
  - 20 signals returned.
  - 5 passed the old per-signal gate.
  - Feed publishable: false because `poll_stale`.
  - Poll health: 76 feeds polled, 41 failed.

External official docs checked on 2026-07-14:

- SAM.gov Opportunities API: production endpoint is `https://api.sam.gov/opportunities/v2/search`; API key is required; date range parameters are mandatory; max page size is 1000.
- UK Find a Tender: notices are available as OCDS JSON; fields map to OCDS 1.1.5; data is under the Open Government Licence.
- OCDS: common model for disclosure of contracting data and documents across contracting stages.
- ITA developer portal exposes API infrastructure for the Consolidated Screening List; a key is required for practical use.

## Current source portfolio

### A. Strong sources already worth keeping

These sources support decision-grade value when the adapter preserves evidence
and customer implication.

| Lane | Sources | Current value | Required next step |
|---|---|---|---|
| Official sanctions | OFAC SDN, UK OFSI, EU FSF, UN consolidated, OFAC recent actions, State Department Federal Register sanctions notices | Strong compliance foundation | Ensure new designations flow into Golden Intel only through official diff/watchlist adapters |
| Export control | BIS rules via Federal Register, EU dual-use logic, export-control KB | Strong DD/compliance layer | Add CSL/BIS denied-party list coverage |
| Procurement | TED API path, US DoD Daily Contracts, USAspending, procurement portals | High potential, mixed current quality | Prioritise official APIs and tender metadata over Google News fallbacks |
| Corporate/financial | Companies House, GLEIF, SEC EDGAR, financial health | Strong DD foundation | Tie corporate changes to watched entities and procurement counterparties |
| User/vault curated | User/admin-added RSS/site sources | Potential moat | Fuse with official data and watchlists before promotion |

### B. Sources that are useful but should usually remain Mining Queue

These are good context sources but weak as standalone Telegram posts.

| Source family | Reason |
|---|---|
| ReliefWeb / UN OCHA humanitarian updates | Useful for country/route risk, but generic humanitarian headlines are not commercial intelligence unless tied to delivery, end-use, exposure, or procurement |
| GDELT broad news | Good early warning, weak precision; needs corroboration and customer/entity linkage |
| Google News RSS fallbacks | Useful for discovery, not authoritative enough for public Golden Intel without primary-source confirmation |
| General defence media | Good market awareness, but must connect to procurement, competitor movement, export-control, or named customer-relevant exposure |
| Telegram OSINT channels | Directional only; propaganda-tier and single-channel claims must not be promoted |

### C. Sources to demote or constrain

| Source/path | Issue | Decision |
|---|---|---|
| Generic `news_monitor.py` keyword signals | Can create `why_it_matters` and `recommended_action` from broad keyword rules | Keep for Mining Queue; require `customer_value` scoring before Distribution Ready |
| `procurement_portals.mjs` Google News fallbacks | Can surface indexed news rather than official notices | Keep as discovery; do not call official tender evidence unless URL is actual portal/OCDS/TED/SAM/UNGM/etc. |
| ReliefWeb budget/conflict items | Can pass old gate as `budget_movement` or `conflict_escalation` despite generic action | Require customer segment + exposure mechanism before Telegram |
| OpenSanctions heuristic pushes | Already capped by policy to Mining Queue | Keep cap; promote only official designation diff or public-watchlist re-screen |

## Missing sources that matter most

### 1. trade.gov Consolidated Screening List

Why it matters:

- Covers key US denied/restricted-party and export-screening lists in one API lane.
- Adds BIS Entity List, Denied Persons, Unverified List, and other US trade lists
  that matter directly to defence/export customers.

Current state:

- Identified in prior free-source inventory.
- R-F2601 wires a trade.gov CSL source in `apis/sources/csl.mjs`.
- The source screens only explicit `CSL_WATCHLIST` / `ARIA_CSL_WATCHLIST`
  terms, because CSL is a search API rather than a bulk recent-changes feed.
- It is honest-disabled without `TRADE_GOV_API_KEY` / `CSL_API_KEY` or without
  a watchlist.

North Star value:

- Turns ARIA from "sanctions only" into a stronger export-compliance monitor.
- Enables Golden Intel for new/export-control-relevant denied-party changes.

Backlog:

- Obtain/activate free API key in production.
- Configure public/operator-approved `CSL_WATCHLIST` terms.
- Extend watchlist term source from public/customer-approved entities once the
  privacy gate is explicit.
- Tests added in R-F2601: no-key honest-empty, no-watchlist honest-empty,
  API-shape contract, source mapping, promotion mapping, and Python Golden Intel
  gate acceptance.

Priority: P0.

### 2. SAM.gov Opportunities API

Why it matters:

- Official US federal opportunities source.
- Production endpoint is `https://api.sam.gov/opportunities/v2/search`.
- API key required.
- Returns opportunity titles, solicitation numbers, organization path, NAICS,
  classification codes, response deadlines, award information, contacts, and
  resource links.

Current state:

- Portal registry knows SAM.gov.
- Procurement sources rely partly on Google News and broad tender monitors.
- R-F2600 wires the official `https://api.sam.gov/opportunities/v2/search`
  source into `apis/sources/procurement_tenders.mjs`.
- The source is honest-disabled when `SAM_GOV_API_KEY` / `SAM_API_KEY` is absent
  and maps official opportunity records when the key is present.

North Star value:

- Directly supports procurement opportunity discovery and bid/no-bid workflows.
- Provides actual deadlines, buyer, solicitation, attachments, and awardee fields.

Backlog:

- Obtain/activate free API key in production.
- Add stronger defence/security NAICS/classification filters.
- Extend normalized tender fields: solicitation number, set-aside, awardee,
  contacts, resource links.
- Add Golden Intel adapter for active tenders/awards with concrete customer
  action.
- Tests added in R-F2600 for no-key honest-empty and official API-shape mapping.

Priority: P0.

### 3. UK Find a Tender OCDS API

Why it matters:

- Official UK procurement notices.
- Provides OCDS JSON mapped to version 1.1.5.
- Open Government Licence.
- Useful for defence/security forms and UK/EU-adjacent procurement intelligence.

Current state:

- Listed in prior inventory.
- Needs confirmation against live implementation.

North Star value:

- Gives structured tender data rather than scraped titles.
- OCDS structure enables buyer/supplier/value/deadline extraction and cross-source
  tender lifecycle tracking.

Backlog:

- Add/verify first-class Find a Tender poller.
- Normalize OCDS release package fields.
- Add defence/security filters, buyer extraction, deadline/value fields.
- Feed Golden Intel only when there is buyer/product/deadline/customer action.

Priority: P0.

### 4. TED notices API

Why it matters:

- Core EU procurement source.
- Current code already uses `https://api.ted.europa.eu/v3/notices/search` with
  `classification-cpv=35*`.

Current state:

- Wired in `procurement_tenders.mjs`.
- Needs ongoing schema drift protection and richer field extraction.

North Star value:

- Official EU defence/security tender stream.
- Should be a flagship procurement source.

Backlog:

- Add contract test for TED v3 request/response fields.
- Fetch richer notice detail when a candidate passes initial relevance.
- Preserve buyer, country, CPV, deadline, notice type, publication number, and
  canonical notice URL.
- Add Golden Intel adapter from structured TED notices rather than generic news
  fallback.

Priority: P1.

### 5. UNGM and multilateral procurement

Why it matters:

- UN, World Bank, AfDB, EBRD, EIB, IADB, IsDB procurement creates real customer
  opportunity and compliance context.

Current state:

- Mentioned in source plans and some sweep sources.
- Needs a stricter status matrix: which are real API/feed, which are scraped, and
  which are aspirational.

North Star value:

- High-quality opportunity lane for security/logistics/defence-adjacent customers.

Backlog:

- Build per-source status probes.
- Promote only official notices with buyer/deadline/value/product fields.
- Add World Bank debarred as compliance-adjacent lane.

Priority: P1.

### 6. ACLED / GDELT / ReliefWeb as context, not standalone Gold

Why it matters:

- These sources explain country and route risk.
- They should improve an opportunity/risk thesis, not dominate it.

Current state:

- Wired, but live poll showed high failure count across feeds.
- ReliefWeb items can pass old gate with generic actions.

North Star value:

- Excellent second-source context when tied to an opportunity, delivery route,
  end-use risk, or customer geography.

Backlog:

- Add rule: conflict/humanitarian source cannot publish to Telegram unless it
  names customer segment + operational mechanism.
- Use as corroboration for procurement/country-risk items.
- Fix top failing feeds and expose failure ranking.

Priority: P1.

## Source-health problem

The current source health is not good enough for a customer habit loop.

Observed live:

- 76 feeds polled.
- 41 failed.
- feed status stale because poll age exceeded threshold.

Impact:

- Telegram correctly stays silent, but customer-facing continuity suffers.
- Golden Intel cannot be "best in class" while over half of the poll set fails.

Required source-health standard:

- P0 launch target: under 15 percent failed feeds on normal runs.
- P1 target: under 8 percent failed feeds.
- Any source with 5 consecutive failures is demoted from Distribution Ready
  contribution until recovered.
- Dashboard must show top failing sources and whether failures affected Golden
  publication.

## Golden Intel source policy

### Distribution Ready may use

- Official lists and official procurement portals.
- Authoritative multilateral/government updates.
- Curated Tier 2 media only when paired with a concrete customer segment and
  action.
- User/vault source only when paired with independent evidence or a customer
  watchlist match.

### Mining Queue should hold

- Single-source media items.
- Generic conflict/humanitarian updates.
- Google News fallback discoveries.
- OpenSanctions heuristic appearances.
- Items with action text that is generic or not customer-specific.

### Never publish

- Propaganda-tier Telegram/channel claims.
- Private/user-scoped watchlist items unless explicitly system-public.
- Any source failure rendered as "no issue."
- Any item with no evidence URL.
- Any item that only restates a headline with templated impact.

## Implementation plan

### Batch 1: Immediate quality lift

1. R-F2600 added `customer_value` scoring to Golden Intel bridge signals.
2. R-F2600 demotes generic actions from Distribution Ready/Telegram.
3. R-F2600 makes Telegram require customer value score >= 80.
4. R-F2600 makes Dashboard Distribution Ready require customer value score >= 70.
5. R-F2600 displays customer-value score and gate rejection reasons in the
   dashboard.

Expected outcome:

- Fewer posts, better posts.
- ReliefWeb/GDELT items become context unless linked to customer exposure.

### Batch 2: Official source upgrades

1. R-F2601 wired trade.gov CSL as a watchlist-scoped source and Golden Intel
   promotion path.
2. R-F2600 wired SAM.gov Opportunities API behind `SAM_GOV_API_KEY`.
3. Harden Find a Tender OCDS.
4. Harden TED v3 detail extraction.
5. Add World Bank debarred/procurement structured lane.

Expected outcome:

- More official-source procurement and export-control intelligence.
- Better Golden Intel candidate quality.

### Batch 3: 360 synthesis engine

For each candidate, enrich against:

- sanctions/export-control lists;
- procurement/tender context;
- country/conflict context;
- corporate/registry data;
- customer/vault/watchlist matches;
- source-health status.

Expected output:

```json
{
  "decision": "What changed and why it matters",
  "who_cares": "customer segment",
  "why_now": "fresh event and timing",
  "action": "specific next step",
  "evidence": ["official source URL", "supporting context URL"],
  "aria_added": "cross-source synthesis or customer-specific implication"
}
```

### Batch 4: source reliability

1. R-F2600 exposes failed-feed names, failure ratio, and
   `source_failure_degraded` in Golden Intel freshness.
2. Disable or demote chronic failures.
3. Replace Google News fallbacks with official endpoints where possible.
4. Add SLA gate: if poll is stale, Telegram silent and dashboard explains why.

## Priority backlog

| Priority | Work | Why |
|---|---|---|
| P0 | Wire CSL | Actioned R-F2601: official watchlist-scoped source and Golden Intel promotion path added; production key/watchlist activation remains |
| P0 | Wire SAM.gov | Actioned R-F2600: official keyed source added to `procurement_tenders.mjs` |
| P0 | Add customer-value gate | Actioned R-F2600: Python bridge, Telegram, dashboard, and `/brief` enforce score/rejection metadata |
| P0 | Source failure ranking | Actioned R-F2600: Golden Intel freshness exposes failed ratio, failed feed names, and `source_failure_degraded` |
| P1 | Find a Tender OCDS first-class poller | Structured UK procurement data |
| P1 | TED detail enrichment | Existing TED integration needs richer fields |
| P1 | World Bank debarred/procurement | Compliance + procurement value |
| P1 | UNGM/multilateral procurement | Customer opportunity discovery |
| P2 | ACLED/GDELT/ReliefWeb as corroboration only | Prevents generic risk-feed posts |
| P2 | Vault-source fusion | Turns customer-specific sources into moat |

## The practical standard

ARIA should not ask, "Is this a relevant source?"

ARIA should ask:

1. Is it authoritative or corroborated?
2. Does it affect a customer decision?
3. Can we name who should care?
4. Can we name the next action?
5. Did ARIA add synthesis beyond the source headline?

If the answer to any of these is no, it is not Golden Intel.

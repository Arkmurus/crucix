# Golden Intel North Star Model

Prepared: 2026-07-14
R-number: R-F2598

## Executive verdict

Golden Intel must not be a news channel. It must be ARIA's decision signal
layer: a small number of high-confidence, source-backed, customer-relevant
items that tell a defence, compliance, or procurement operator what changed,
why it matters, who should care, and what to do next.

The current system has strong honesty controls:

- Telegram rejects stale and backfilled feeds.
- Telegram rejects weak signals, missing evidence, missing action, and missing
  impact.
- Non-Golden editorial lanes are retired or blocked.
- Duplicate posting is suppressed across backend id changes.
- OpenSanctions heuristic items are capped to Mining Queue and cannot become
  distribution-ready unless an official/source-specific adapter promotes them.

The gap is not the guard. The gap is value density. The live feed can still
produce generic source-derived items with templated impact such as "Assess
country risk." That is not enough to be ARIA's USP.

## Product promise

Golden Intel exists to give customers a private operating advantage:

> ARIA turns public and user-curated defence, sanctions, procurement, conflict,
> and corporate signals into short, evidence-backed decisions before the
> customer would have assembled the picture manually.

Every public-channel post must earn this promise. If it cannot say what the
customer should do differently, it stays in Mining Queue.

## Customer jobs

Golden Intel should serve five concrete jobs.

1. Export and sanctions protection
   - Detect new restrictions, designations, watchlist movement, or export-control
     changes that affect counterparties, routes, products, or jurisdictions.
   - Customer action: screen, block, escalate, freeze, obtain licence, or avoid.

2. Procurement opportunity discovery
   - Detect tenders, awards, budgets, FMS notices, programme acceleration, or
     pre-RFP indicators tied to relevant platforms, countries, OEMs, or mission
     needs.
   - Customer action: qualify bid/no-bid, identify prime/local partner, prepare
     eligibility evidence, or monitor amendment/deadline.

3. Counterparty and network risk
   - Detect entity exposure through sanctions, PEP, adverse media, ownership,
     supplier geography, shell-like indicators, or procurement mismatch.
   - Customer action: run DD, update risk rating, request documents, or stop
     engagement.

4. Market timing and positioning
   - Detect budget, conflict, programme, policy, and supplier movements that
     change a market window.
   - Customer action: engage, hold, monitor, or re-price.

5. Source-health intelligence
   - Surface when the data picture itself is degraded, stale, or one-sided.
   - Customer action: wait, seek corroboration, or treat as directional only.

## Non-negotiable publication contract

A Telegram Golden Intel post must answer all six questions:

1. What happened?
2. Why does it matter commercially, operationally, or compliance-wise?
3. Who should care?
4. What should they do in the next 24 hours, 72 hours, or 14 days?
5. What evidence proves the signal?
6. What did ARIA add beyond repeating the source headline?

If any answer is missing, the signal is not distribution-ready.

## Promotion lanes

### Lane A: Official compliance and sanctions

Highest confidence. Single official source may be sufficient.

Sources:

- OFAC SDN and OFAC recent actions
- UK OFSI consolidated list
- EU financial sanctions
- UN sanctions
- trade.gov CSL once free key is wired
- BIS Entity List, DPL, UVL via CSL
- World Bank debarred firms
- Public/system watchlist re-screen results

Distribution-ready examples:

- New official designation affecting a watched entity.
- Entity moved from clear to blocked/PEP/watchlist status.
- New export-control rule affecting a product, entity, jurisdiction, or end use.

Must include:

- Regime/list.
- Entity or product.
- Action: screen, block, licence, freeze, enhanced DD, or counsel review.
- Evidence URL.

### Lane B: Procurement and tender advantage

Core commercial value lane.

Sources:

- TED
- SAM.gov once free key/path is wired
- UK Find a Tender
- UK Contracts Finder
- UNGM
- World Bank procurement/debarment
- AfDB, EBRD, EIB, IADB, IsDB
- US DoD contracts
- USAspending
- Country procurement portals where wired

Distribution-ready examples:

- Tender/award/budget item with clear defence/security relevance.
- Programme notice tied to a named platform, product, country, ministry, or OEM.
- Contract award that implies follow-on sustainment, spares, training, or
  competitor movement.

Must include:

- Buyer/authority where available.
- Country.
- Product/platform/mission need.
- Deadline/value if available.
- Action: bid/no-bid, partner search, eligibility check, monitor amendment, or
  competitor response.

### Lane C: Conflict and operating-risk context

Useful only when tied to exposure, market timing, delivery, end-use risk, or a
customer segment.

Sources:

- ACLED
- ReliefWeb
- GDELT
- UN/OCHA
- verified government and multilateral updates

Distribution-ready examples:

- Escalation near a delivery route, project site, port, or procurement market.
- Conflict indicator that changes duty-of-care, end-use risk, or delivery timing.
- Humanitarian or stability update tied to actual logistics/compliance exposure.

Must include:

- Affected geography.
- Operational consequence.
- Customer segment.
- Action: re-route, pause shipment, update country risk, seek end-use assurance,
  or monitor named corridor/agency.

Generic humanitarian headlines do not qualify without this link.

### Lane D: Corporate, ownership, and adverse-media network

High value when connected to DD.

Sources:

- Companies House
- GLEIF
- SEC EDGAR
- corporate registries
- adverse-media sources
- user/vault-curated sources
- DD report outputs where explicitly public-safe

Distribution-ready examples:

- Ownership/officer change affecting a watched entity.
- Financial distress or litigation relevant to supplier risk.
- Adverse-media cluster tied to a counterparty, intermediary, or procurement
  participant.

Must include:

- Entity name.
- Relationship or risk mechanism.
- Source count or evidence tier.
- Action: update DD, request ownership documents, re-screen directors, or pause.

### Lane E: Customer-curated source fusion

This is the moat. If a user adds a source, ARIA should use it as a lens, not just
another feed.

Distribution-ready examples:

- User source mentions a target already on watchlist plus official tender appears.
- User source flags supplier activity plus sanctions/corporate data changes.
- Sector source plus public registry confirms a market event.

Must include:

- Which customer-relevant entity/product/country matched.
- What independent source confirmed or contextualised it.
- Action tied to the customer's workflow.

## Scoring model

Each candidate receives a customer-value score from 0 to 100.

Required hard gates:

- Fresh feed.
- Non-backfilled.
- Evidence URL.
- Trusted source tier: tier_1a, tier_1b, or tier_2.
- Allowed signal type.
- No propaganda-tier promotion.
- No private/user-scoped leak.
- No generic "monitor" action unless the evidence is explicitly early-warning
  and the next observation target is named.

Scored dimensions:

- Source authority: 0-20
- Specificity: 0-15
- Customer segment fit: 0-15
- Actionability: 0-20
- ARIA synthesis beyond source: 0-20
- Freshness and novelty: 0-10

Publication threshold:

- Telegram: 80+ and all hard gates pass.
- Dashboard Distribution Ready: 70+ and all hard gates pass.
- Mining Queue: 40-69 or missing corroboration/customer action.
- Discard/noise: below 40.

## What ARIA must add beyond the source

ARIA-added value must be explicit in the signal object. At least one of these
must be true for public channel publication:

- Cross-source synthesis: two independent sources support the same event or
  implication.
- Watchlist linkage: event touches a watched entity, country, OEM, product, or
  customer-curated source.
- Compliance implication: sanctions/export-control/end-use/due-diligence action
  is identified.
- Procurement implication: tender, award, budget, buyer, platform, competitor, or
  deadline is identified.
- Market implication: event changes timing, access, route, pricing, or partner
  strategy.

If the post is only a headline plus a generic impact sentence, it is not Golden
Intel.

## Telegram post format

Golden Intel posts should use this shape:

```text
GOLDEN INTEL

Decision: [one sentence, customer-relevant]
Who cares: [customer segment]
Why now: [fresh event + implication]
Action: [specific next step + time horizon]
Evidence: [source name + URL + source tier]
ARIA added: [corroboration/watchlist/compliance/procurement synthesis]
```

## Current gap map

Verified on 2026-07-14:

- Live feed returned 20 signals.
- 5 passed the per-signal gate.
- Feed-level status was stale, so Telegram correctly should not post.
- Ready candidates were mainly ReliefWeb conflict/budget items with generic
  actions. These are useful for Mining Queue but too weak as USP posts unless
  linked to customer exposure.
- Poll health showed 76 feeds polled and 41 failed. That threatens continuity.

## Implementation roadmap

### Phase 1: tighten the value gate

Add a `customer_value` block to each signal:

```json
{
  "customer_segment": "defence_exporter|compliance_officer|procurement_team|broker|investor|logistics",
  "customer_problem": "sanctions_risk|bid_opportunity|delivery_risk|counterparty_risk|market_timing",
  "next_action": "specific operational action",
  "aria_added_value": "corroboration|watchlist_match|compliance_implication|procurement_implication|market_synthesis",
  "value_score": 0
}
```

Do not publish to Telegram unless `value_score >= 80`.

### Phase 2: demote generic source-derived items

Any signal whose title and source are the only concrete facts should stay in
Mining Queue. Generic phrases such as "may affect market timing" or "assess
country risk" are allowed only as placeholders below the public gate.

### Phase 3: wire high-value free sources

Priority order:

1. trade.gov CSL free key for export/sanctions lists.
2. SAM.gov opportunities free key and correct endpoint.
3. UK Find a Tender / Contracts Finder OCDS.
4. UNGM and multilateral procurement.
5. World Bank debarred/procurement.
6. ACLED/GDELT conflict linked to procurement routes and customer markets.

### Phase 4: source reliability repair

The poll must not be stale for the channel to create customer habit. Track and
fix the top failing feeds until the failure rate is below 15 percent on normal
runs.

### Phase 5: dashboard and operator review

Dashboard should show:

- Distribution Ready
- Mining Queue
- Rejected/noise
- Why rejected
- Missing field to become publishable
- Customer segment
- ARIA-added value reason

This makes ARIA's judgement inspectable and improves the operator's ability to
tune the system.

## Final north-star rule

Golden Intel is publishable only when the customer can read it and immediately
say:

> I know what changed, why it matters to my decision, what to do next, and why
> ARIA found more value than the raw source headline.

Anything less is not Golden Intel.

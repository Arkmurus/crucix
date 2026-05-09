# ARIA — Capability Expansion Roadmap to "No Competition"

**Date**: 2026-05-10
**Goal as stated by operator**: *"make her the best, no competition or nothing matches her"*
**Anchor reading**: today's heatmap

## 1 — Where ARIA already wins

| Domain | Status | Why |
|---|---|---|
| Lusophone defence DD | **99%** mastery | Arkmurus's existing book + Phase 2 Lusophone moat work |
| MENA / Gulf | 90-95% | EUC library + 12 markets covered |
| NATO standards | 93-95% | Constitutional clauses 7+8, STANAG ingest |
| Turkey | 92-97% | R-F125 Turkish auto-language, R-F137 Turkish OEMs added |
| Sanctions discipline | 96% | OpenSanctions + 5 primary sources + R-F133 false-positive filter |
| ARK-DD methodology | unique | 10-layer fail-open pipeline + ACH explainability + 23-clause constitution |
| Memory architecture | unique | 5-substrate (knowledge / ledger / RAG 76k chunks / 10.7k neurons / mem0) + pay-once-remember-forever |

These are real defensible advantages — not aspirational.

## 2 — Where the heatmap shows ARIA must lift to "uncatchable"

| Cell | Mastery | Severity |
|---|---|---|
| **LatAm non-Lusophone** (procurement / market_intel / technical / geopolitics / osint / relationships / competitor_intel / compliance / finance) | **51%** uniformly | 🔴 floor — this is a hard regression risk |
| Asia-Pacific (compliance / finance) | 52% | 🟠 |
| Global cross-topic (general / market_intel / competitor_intel) | 52-64% | 🟠 |
| Balkans | 60-64% | 🟠 |
| Central Africa / East Africa | 78-88% | 🟡 lift to 95% |

LatAm is the standout — every domain at 51% means the corpus has barely any LatAm-tagged facts. A focused knowledge-pack injection lifts every column on that row.

## 3 — Capability frontier — what would put ARIA beyond the field

Categorised by what each unlocks, with effort + dependency labels:

### A. Geospatial intelligence layer

| Capability | Effort | Dependency | What it unlocks |
|---|---|---|---|
| Sentinel-2 satellite imagery + change detection | 2 weeks | free ESA Copernicus API | "show me Hambantota port activity over last 90 days" |
| Maritime AIS pattern-of-life graph | 1 week | already wired (R-F37) — needs graph DB | sanctions evasion via vessel re-flagging |
| ADS-B aviation pattern-of-life | 1 week | OpenSky already wired — needs persistence | military airframe activity per base |
| Port congestion + base activity tracking | 3 days | wired but underutilised | DD signal: "supplier site deserted for 60 days" |
| Geospatial entity extraction in chat (lat/lon mentions → map) | 1 week | hot-fix UI | operator drops a coord, ARIA explains what's there |

### B. Trade & finance intelligence

| Capability | Effort | Dependency | What it unlocks |
|---|---|---|---|
| Comtrade trade flow analytics | activated | needs `COMTRADE_API_KEY` ($300/mo Plus tier) | full R-F73 TBML + price-anomaly forensics |
| Earnings call sentiment for OEMs | 1 week | Anthropic LLM call per transcript | "Leonardo Q3 mentions Africa pivot — flag for BD" |
| M&A + sole-source contract flag | 3 days | LSE + EDGAR + EU TED | new BD opportunities surface within 24h |
| US PACER litigation tracker | 2 weeks | RECAP archive (free) | DD layer 11: "this entity is in litigation X / Y / Z" |
| EU ECJ + ECHR judgments | 1 week | EUR-Lex (already seeded R-F137) | sanctions-list entries' active legal challenges |
| 10-K / 8-K / proxy filings for OEM tier 1 | 1 week | SEC EDGAR (already seeded) | ingest financial statements for benchmark |

### C. Network analytics + entity-relation graph

| Capability | Effort | Dependency | What it unlocks |
|---|---|---|---|
| Entity-relation graph DB (Neo4j or LanceDB) | 2-3 weeks | needs new substrate alongside RAG | beneficial-owner chain visualisation, officer interlock detection |
| Cross-list multi-sanctions flag | 1 week | existing data + new aggregation | divergence + co-listing in one view |
| Officer interlock detector | 1 week | OpenCorporates (R-F137) + RCA layer | "this CEO is also on board of 3 sanctioned firms" |
| BD pipeline graph (deals → counterparties → OEMs) | 1 week | existing ledger + neural | visual pipeline map for the BD team |

### D. Advanced detection

| Capability | Effort | Dependency | What it unlocks |
|---|---|---|---|
| Cyber threat intel feeds (CISA KEV / Mandiant) | 1 week | already wired (R-F37) — needs DD-layer integration | "this OEM was breached in Aug — evidence custody risk" |
| Multimodal: image embedding for OEM equipment | 2 weeks | CLIP / OpenAI vision API | upload a photo from a trade show → identify the system |
| Document-level forgery / AI-content detection | 2 weeks | open-source detector + Anthropic vision | "this DD doc has AI-generated paragraphs" |
| Adverse-media + ESG sentiment per OEM | 1 week | existing RAG + new NLP layer | DD layer 12: reputational risk score |
| Patent + IP intelligence | 1 week | USPTO + EPO free APIs | tech-transfer intent flag |

### E. Operator productivity

| Capability | Effort | Dependency | What it unlocks |
|---|---|---|---|
| Salesforce / HubSpot integration | 1 week per CRM | their REST API | DD report → CRM in one click |
| Slack / Teams alerts | 3 days | webhooks | RED DD verdict pings the team channel |
| Email digest automation | exists (R-F45) | upgrade content | daily 06:00 UTC briefing with delta vs yesterday |
| Mobile responsive dashboard | 1 week | CSS work | check ARIA from a flight |
| Native mobile app (iOS/Android) | 2 months | React Native / Capacitor | proper push notifications |
| White-label DD reports per customer | 1 week | watermark code exists | sell DDs externally |

### F. Enterprise + compliance

| Capability | Effort | Dependency | What it unlocks |
|---|---|---|---|
| SAML SSO + SCIM provisioning | 2 weeks | identity provider integration | enterprise customers can buy without custom auth |
| SOC 2 Type II compliance path | 3-6 months | external auditor | unlocks Fortune 500 procurement |
| GDPR + UK ICO registration | 1 week | legal review | required for EU operator base |
| Multi-tenant architecture | 4 weeks | data isolation refactor | scale beyond Arkmurus's own use |
| Public REST API with usage-based pricing | exists scaffold (R-F42) | needs Stripe + rate-limit polish | revenue path |

### G. ARIA-specific moat (independence)

| Capability | Effort | Dependency | What it unlocks |
|---|---|---|---|
| ARIA-LLM v0.1 fine-tune | 4 weeks | $200 GPU rental + 5k pair corpus | sovereign LLM, no Anthropic dependency |
| ARK-DD methodology certification | 6 weeks | external defence-DD review | "ARIA-certified" as an industry mark |
| Defence-domain ground-truth dataset | ongoing | already accumulating (training_export) | proprietary moat — competitors can't copy |
| Real-time SSE for DD progress | 3 days | existing FastAPI streams | UX win operators feel immediately |
| In-product analyst chat (multi-turn DD refinement) | 1 week | exists (chat sidebar) | follow-up questions on a live DD |

## 4 — Recommended build order to "uncatchable" — 90-day plan

### Days 1-30 — close the heatmap floor

1. **R-F141** — LatAm-non-Lusophone knowledge pack (Mexico / Argentina / Colombia / Peru / Venezuela / Chile defence procurement + sanctions context)
2. **R-F142** — Asia-Pacific knowledge pack (Korea / Japan / India / ASEAN export-control + finance)
3. **R-F143** — Balkans + Central/East Africa supplemental ingest
4. **R-F144** — Re-fire student loops on LatAm + AsiaPac heat-cells (already-wired auto task selector targeting weak cells)
5. **Operator**: top up Brave + flip 4 graceful-degrade env vars

**Expected end-state**: heatmap floor lifts from 51% → 80%+, weak cells reduced from 16 → < 5

### Days 30-60 — capability frontier expansion

6. **Network analytics** — entity-relation graph (Phase 1: Neo4j or LanceDB substrate, beneficial-owner chain renderer)
7. **Earnings-call sentiment** — daily ingest for top 35 OEMs (R-F138 OEM expansion now covers them)
8. **PACER + ECJ litigation tracker**
9. **Geospatial layer** — Sentinel-2 change detection for known military / port sites
10. **Salesforce + Slack integrations** — pipeline visibility for the BD team

**Expected end-state**: 4-5 capabilities competitors don't have at all (Janes / Kharon / Sayari)

### Days 60-90 — moat + scale

11. **ARIA-LLM v0.1 fine-tune** — operator rents GPU + we run the LoRA training on accumulated 5k+ chat pairs. Ships sovereign LLM as the long-term defence against Anthropic billing surprise.
12. **Multi-tenant architecture** — proper data isolation so the platform can scale to 5-10 paying customers
13. **Public API + Stripe** — usage-based pricing + customer-self-serve portal
14. **SOC 2 path** — start the 3-6 month compliance journey
15. **Mobile-responsive dashboard** + iOS/Android Capacitor wrapper

**Expected end-state**: ARIA is a SaaS product with $15-50K MRR potential per customer, three external paying clients in the pipeline.

## 5 — What "no competition" actually means in this market

Janes / Kharon / Sayari / Recorded Future / Dataminr are the named alternatives. Differentiators in ARIA's favour:

| Dimension | ARIA today | Best competitor | Why ARIA wins or could win |
|---|---|---|---|
| Defence-DD specific methodology | ARK-DD with 10 layers + 23 clauses | Janes (manual reports) | structured + auditable + repeatable |
| Lusophone / MENA / Turkey moat | 99% / 92% / 96% | Janes (broad but thin) | Arkmurus's domain advantage |
| Memory architecture | 5-substrate, 100-year retention | Recorded Future (event-stream) | pay-once-remember-forever |
| Constitutional discipline | 23 clauses, audited | none have this | hallucination guard built-in |
| Counter-intelligence (R-F84) | reputation-washing detection | Sayari (sanctions only) | new layer competitors lack |
| Sanctions divergence (R-F68) | 7-jurisdiction cross-list narrative | Kharon (US-centric) | Eu/UK/UN/CA/CH/AU breadth |
| Cost | $50-100/mo at internal scale | $25K-150K annual | 100× cheaper |
| Sovereign LLM path | in build (Independence Roadmap) | none have this | regulatory + cost advantage |
| 10-layer fail-open DD | unique today | competitors are mostly query-based | structured + repeatable |
| Auto-language fan-out (11 lang) | unique today | Recorded Future is en-default | non-English coverage moat |

The path to "no competition" is not chasing every feature — it's deepening the moats ARIA already has (defence specificity, methodology, memory, constitutional discipline) while filling the table-stakes gaps (LatAm/AsiaPac mastery, network analytics, mobile UX).

## 6 — Operator dependencies for the 90-day plan

| Item | Cost / time | Unlocks |
|---|---|---|
| Top up Brave | $5-50 | full general-web search reach |
| `COMTRADE_API_KEY` Plus tier | $300/mo | TBML + trade flow forensics |
| `UPSTASH_REST_URL` + `UPSTASH_REST_TOKEN` | free | live cluster usage panel |
| GPU rental (RTX 4090 or A100) | ~$200 one-off | ARIA-LLM v0.1 fine-tune |
| Stripe activation | 30 min | revenue path |
| Salesforce / HubSpot dev account | free trial | CRM integration |
| External SOC 2 auditor | $15-30K | enterprise procurement |
| Optional: Sentinel Hub commercial | $300/mo | high-frequency satellite imagery |

Total to launch: **~$500/month operating + ~$200 one-off** for the sovereign-LLM training run. Compare to Janes at $25K+ / year per seat.

## Bottom line

The fastest path to "uncatchable" is:

1. **Plug heatmap weak cells** (LatAm + AsiaPac knowledge packs — code-shippable in days)
2. **Network analytics + earnings sentiment + PACER** (capabilities Janes/Kharon don't bundle)
3. **ARIA-LLM fine-tune** (sovereignty as a structural moat, not just a feature)
4. **B2B SaaS launch** with the 3 above as the differentiated wedge

In that order. The heatmap fix is shipping today as R-F141/F142.

# R-F2468 Telegram Channel and Golden Data Review

Date: 2026-07-08

## Executive View

ARIA already has a broad monitoring footprint, but the current advantage is unevenly productized. The sweep has strong coverage across sanctions, procurement, conflict, macro, cyber, maritime, corporate, export-control and development-finance sources. Social monitoring exists, but it is not yet a decisive moat: Reddit, Bluesky and Telegram are live sweep sources, LinkedIn arrives mostly through email/Sales Navigator alerts, and X/Twitter is used as search context rather than a first-class monitored feed.

The Telegram channel should not compete as "another news channel." The USP should be: decision-grade compliance and defence-market intelligence that turns public chaos into a verified next action. Each post needs three things: a named fact, why it matters commercially, and what the reader should check next. The new card layer supports that format visually.

## What ARIA Is Monitoring Now

Grounded live sweep sources are defined in `apis/briefing.mjs`:

- Core OSINT and geopolitical: GDELT, OpenSky, FIRMS, maritime, Safecast, ACLED, ReliefWeb, WHO, OFAC, OpenSanctions, ADS-B.
- Economic and financial: FRED, Treasury, BLS, EIA, GSCPI, USAspending, Comtrade.
- Social and technology: NOAA, EPA, patents, Bluesky, Reddit, Telegram, KiwiSDR.
- Space, markets, cyber and infrastructure: Space, YFinance, CISA KEV, Cloudflare Radar, supply chain.
- Defence and procurement: Defense News, SIPRI Arms, Defense Events, Procurement Tenders, Procurement Portals.
- Due diligence and compliance: OpenCorporates, Sanctions, Export Controls, Counterparty Risk, SEC EDGAR, EU Dual Use, World Bank-style development finance through AfDB.
- Regional and custom: Lusophone, Global Defence, Export Control Intel, Arkumurus, auto-managed sources.

Operational intake beyond `fullBriefing()`:

- Web explorer runs four scheduled daily exploration slots at 06:00, 10:00, 14:00 and 18:00 London, then only posts top findings.
- Email reader ingests LinkedIn Sales Navigator alerts, Google Alerts, tender notifications and forwarded intel.
- LinkedIn intel has Redis-backed routes for relationship maps, competitor activity and growth signals.
- WhatsApp listener and Telegram command paths can feed user/private material into ARIA.

## Current Social Monitoring

Bluesky is present and easy to expand. Current queries are broad buckets: conflict, markets and health. This is low-friction but not yet domain-specialized enough for Arkmurus.

Reddit is present but shallow. It monitors `worldnews`, `geopolitics`, `economics`, `wallstreetbets` and `commodities`; the module says Reddit requires OAuth and returns `no_key` if credentials are missing. This is more ambient sentiment than golden data.

Telegram public channel monitoring is present and more relevant to conflict/OSINT, using a curated public-channel list and web-preview fallback. This is useful for early warning, but needs source-tiering because several monitored channels carry partisan or unverifiable conflict narratives.

LinkedIn is partially monitored through email alerts and the LinkedIn intel module, not through a full official API ingest. That is enough for job changes and company-growth alerts if Sales Navigator emails are configured, but it will miss many live posts, comments and network changes.

X/Twitter is not a first-class sweep source. The Python researcher extracts X/Twitter profile links and excludes social domains in some searches, but there is no dedicated `apis/sources/x.mjs` monitored lane. Given how defence, sanctions and shipping signals often break first on X, this is a strategic gap.

## Missing Golden Data Lanes

Priority 1: X/Twitter monitored search and lists. Add a compliant X API source with watchlists for sanctions lawyers, export-control officials, defence journalists, procurement agencies, OSINT analysts, shipping trackers and key company accounts. Treat X as early-warning only until corroborated.

Priority 2: Telegram MTProto-grade channel collection. The Bot API/web preview path is brittle for arbitrary public channels. Add a dedicated collector for public channels where allowed, with message IDs, views, forwards, edits, media hashes and source tier. Keep propaganda-tier channels but label them explicitly.

Priority 3: Entity graph and beneficial ownership. Current corporate coverage exists through Companies House/OpenCorporates/SEC EDGAR, but golden DD needs identity resolution across names, addresses, directors, LEIs, websites, domains, phone numbers and historical names. Add GLEIF as a high-ROI free identity spine, then measure OpenCorporates Pro/Sayari/Orbis if budget permits.

Priority 4: Trade and shipment context. COMTRADE, maritime and port congestion exist, but the channel USP would improve with practical routing signals: AIS/vessel identity, port calls, sanctions-risk flags, reroutes, ownership/manager changes, and logistics intermediaries.

Priority 5: Procurement awards, not just tenders. Tenders are useful for opportunity. Awards and contract modifications reveal who actually wins, which suppliers are connected, and where compliance risk enters the supply chain.

Priority 6: Local-language adverse media. ARIA has web research capability, but the monitored channel should explicitly schedule Portuguese, French, Arabic, Russian, Turkish, Spanish and Chinese query packs for defence procurement, sanctions evasion, fraud, corruption, export controls and court actions.

Priority 7: Court/litigation and insolvency. For counterparty DD, court filings, procurement disputes, bankruptcy/insolvency registers and regulator enforcement are often more actionable than news.

## Golden Data Retrieval Model

Golden data should be a retrieval layer, not just more feeds. Recommended object:

```json
{
  "id": "golden_signal_uuid",
  "entity": "company/person/vessel/address/program",
  "event_type": "sanctions_delta|tender|award|ownership_change|adverse_media|social_early_warning",
  "title": "human-readable fact",
  "observed_at": "source event time",
  "captured_at": "ARIA capture time",
  "source_url": "canonical URL",
  "source_tier": "official|primary|specialist|social|propaganda|unknown",
  "confidence": 0.0,
  "corroboration": ["source ids"],
  "evidence": ["short extracted facts"],
  "business_impact": "why this matters commercially",
  "recommended_action": "what the user should do next",
  "retrieval_keys": ["aliases", "addresses", "directors", "LEI", "vessel IMO", "program"],
  "embedding_text": "compact searchable text"
}
```

Retrieval requirements:

- Search by entity, alias, address, director, vessel IMO/MMSI, tender ID, source URL, country and sector.
- Keep raw source evidence separately from synthesized posts.
- Every Telegram post should link back to one or more golden signals or state that it is early-warning/unverified.
- Dedup by canonical entity plus event type plus observed date, not only title text.
- Add a "why now" field so repeated background facts do not become repetitive channel content.

## Telegram Channel USP

The channel should own this format:

- "Named risk or opportunity": a company, person, vessel, procurement, sanction, address cluster or route.
- "Commercial consequence": export-control block, DD escalation, procurement lead, bank/payment risk, supply-chain vulnerability.
- "Next check": screen counterparty, verify end user, inspect address density, check director overlap, validate tender authority, trace shipment route.
- "Evidence standard": official source first; specialist and social sources only when labelled and corroborated.

Content that should be avoided:

- Generic geopolitical summaries.
- Reposting social chatter without a business action.
- Repeating the same sanctions/tender item on consecutive mornings.
- Long posts without a named fact, number, source, or action.

## Implementation Recommendations

Immediate:

- Expand the card-backed daily posts now implemented in R-F2468 into all manual/admin channel test paths.
- Add `apis/sources/x.mjs` with a strict watchlist, rate-limit handling and source-tier labels.
- Expand Bluesky queries from 3 generic buckets to 20 sector-specific monitored queries.
- Expand Reddit from 5 broad subreddits to compliance/procurement/OSINT-specific communities, with OAuth required in production.
- Add social-source tiering and a "social early warning" flag so Telegram never presents unverified social content as fact.

Next:

- Add a `golden_signals` store with canonical entity/event/source fields and an API for retrieval.
- Create watchlists for sanctions/export control, defence procurement, shipping/logistics, and local-language adverse media.
- Add channel analytics: topic, source tier, card type, CTA, views, replies, and conversion to `SCREEN`.

Strategic:

- Buy or trial one high-value entity-graph vendor only after the golden-store schema is live, so the value is measurable.
- Treat social media as the tip line, not the evidence layer.
- Make Telegram the public showroom for ARIA's DD brain: concise, visual, source-labelled, and actionable.

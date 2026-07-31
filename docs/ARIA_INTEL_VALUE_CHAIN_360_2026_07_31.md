# ARIA intel value chain — 360 review (2026-07-31, R-F3534 + R-F3536)

Operator question: *does the Research Monitor do what it says, is the data
valuable, and why is the intel channel only publishing procurement?*

Everything below was **measured on production before any code changed**. Where a
number appears it came from a live read, not from the source.

---

## 1. The measurement that explains everything

Live golden-intel feed, `GET /api/aria/intel/signals/recent?limit=100&grades=A,B`:

| signal_type | Grade A | Grade B |
|---|---|---|
| natural_hazard | **46** | 1 |
| active_tender | 8 | 21 |
| conflict_escalation | 2 | 7 |
| **sanctions_change** | **0** | **7** |
| contract_award | 0 | 2 |
| programme_signal | 0 | 4 |
| competitor_activity | 0 | 1 |
| political_transition | 0 | 1 |

Source tiers: `tier_1a` 76, `tier_2` 22, `tier_1b` 2. Freshness: `fresh`,
`publishable: true`. `source_key` was `null` on all 100 (provenance stamped
server-side never reaches the consumer — **open**, see §5).

**Why the channel published procurement.** `_GOLDEN_ALLOWED_TYPES` excludes
`natural_hazard`, so 46 of the 56 Grade-A signals were discarded at the publish
gate, leaving 8 tenders against 2 conflict items. Procurement won **by
attrition, not by selection**. The collector was never weak; the grading and
publish policy buried the valuable classes.

---

## 2. Where data enters

| Lane | Source | Tier | Status |
|---|---|---|---|
| Official designations | OFAC SDN, UN SC, UK FCDO, EU consolidated, World Bank debarment | 1a | **was dark**, fixed R-F3534 |
| Canonical sanctions store | `/data/sanctions_canonical.db` — `ofac_sdn` 18,959 + `eu_consolidated` 5,994 = **24,953** | 1a | healthy, refreshed daily |
| Tenders | TED / national portals → `tender_monitor.py` | 1a | healthy; classifier was wrong (§4) |
| Natural hazards | USGS / GDACS | 1a | healthy; **over-graded** (§3) |
| Conflict / adverse media | curated news feeds | 2 | healthy, stuck at Grade B |
| Channel collection | Telegram (intelslava, CIG, wartranslated, …) | D — propaganda | collected; **was rendered raw** (§4) |
| Federal Register | OFAC actions, BIS export-control rules | 1a | rendered on the dashboard, **not converted into graded signals** — open |
| 21 DD source adapters | `intel/sources/*` | mixed | feed DD, not the intel feed |

Production path: source adapter → `golden_intel_bridge` (`register_adapter`) →
`intel_grade` (`news_monitor._compute_intel_grade`) → `/intel/signals/recent`
→ dashboard feed **and** `channelServerHooks` → Telegram.

---

## 3. Root causes found and fixed

**R-F3534 — the designation lane had no heartbeat.**
`run_designation_diff()` ran only as a non-fatal afterthought inside
`WEEKLY-DD-WATCHLIST` (`cron: "0 7 * * mon"`) behind `except Exception: pass`.
Live proof: snapshots healthy (OFAC 19,243 / UN 1,011 / FCDO 5,135) but
`crucix:sanctions:diff:alerts` **never written**. OFAC designated on 30/07,
28/07, 27/07, 22/07, 20/07, 17/07 and 15/07; ARIA looked once. Coverage was
US+UN+UK only — the EU's 5,994 live designations sat unwatched in ARIA's own
store. Now: `HOURLY-SANCTIONS-DESIGNATIONS`, `cron "7 * * * *"`, cost cap 0.00,
global coverage, failure wired.

**R-F3536 — ambient signals were buying Grade A with source authority.**
An earthquake is official and true; it is not a *decision* unless it touches
something you hold. `natural_hazard` / `political_transition` now need a
portfolio nexus (a named OEM/product/facility, or an explicit match) to reach
Grade A. This is what clears the runway for designations and escalations.

**R-F3536 — two aggregators were counted as corroboration.**
`✓ 2 publishers/channels` treated two Telegram war-aggregators as independent
witnesses, while ARIA's own constitution says their *content is not fact* and
may never reach `[CONFIRMED]` (written after the 2026-04-09 Lebanon
fabrication). Same list, one derivation point: propaganda-tier sources now
collapse to a single origin and never corroborate a real publisher.

**R-F3536 — the tender classifier was substring matching.**
`"radio" ⊂ "radiological"` labelled ionising-radiation PPE for a Sicilian
hospital trust as `communication_systems`; `"sensor" ⊂ "Sensors"` labelled
electrical current sensors for the Czech national grid operator as
`surveillance_systems`. Two of four Grade-A cards were false positives telling
an analyst to assess a bid. Now boundary-anchored with a weak/strong split: a
generic term only asserts a category inside a defence context.

**R-F3536 — procurement moved off the intel channel.**
A tender is a published notice anyone can subscribe to — a workflow item, not
intelligence. `active_tender` removed from the channel's allowed types;
`contract_award` stays, because who *won* is market intelligence.

**R-F3536 — dashboard coherence.** KPI counters now derive from the rendered
feed (`1 Active Tenders` above a list of four; `3 High Correlations` above a
list of five); Grade-A cards no longer print `Evidence: single-source` beneath
an `official primary evidence` badge; an empty watchlist says so and links to
the watchlist instead of reporting a failed match; raw channel text is replaced
by collection health.

---

## 4. Open list, and what happened to it

1. ~~**`source_key` is null on every served signal**~~ — **CLOSED (R-F3540).**
   The diagnosis in the first draft was wrong: the field is `promotion_source`,
   not `source_key`; my probe read the wrong name. The real defect was narrower
   and real — it was `None` on 69 of 100 signals because it fell back to a
   `source_key` only two adapters set. Now derived from the adapter registry, so
   a new adapter is attributable by construction.
2. ~~**Federal Register OFAC/BIS items are rendered but never graded**~~ —
   **CLOSED (R-F3545)** for the half that matters. `pushPromotionsToBrain` pushed
   three lanes and `exportControlActions` reached a widget and stopped. BIS rules
   now map to tier_1a `sanctions_change`; proven end to end against the live
   grader: recent → `A`, older → `B`, no named entity → `REJECT`, and
   `sanctions_change` is publishable on the channel. The OFAC half is
   deliberately NOT wired: it duplicates the hourly designation diff, which is
   more precise (the entities themselves, not the announcement).
3. **Conflict escalation is stuck at Grade B** — **STILL OPEN.** Tier-2 news
   never corroborates. With propaganda-tier now excluded from the origin count
   (R-F3536), the honest path to Grade A is two genuinely independent
   publishers, which needs a corroboration pass this session did not build.
4. **The watchlist is empty in production** — **OPERATOR ACTION.** The panel now
   says so and links to the watchlist instead of reporting a failed match; only
   the operator can populate it.
5. ~~**`natural_hazard` is graded but unpublishable**~~ — **CLOSED BY DESIGN
   (R-F3536 + R-F3544).** It reaches Grade A only with a named nexus (an OEM,
   product or facility), which is the honest rule. The explicit `portfolio_nexus`
   flag added alongside it was REMOVED in R-F3545: nothing ever passed it, no
   registered adapter emits an ambient signal so no producer could have fired,
   and it could not have been honest anyway — this grade is computed once and
   served to every tenant while "is it in MY portfolio" is per-user. Per-user
   relevance stays on the dashboard, where the caller's watchlist is known.
6. **World Bank debarment needs `WORLDBANK_SUBSCRIPTION_KEY`** — **OPERATOR
   COMMERCIAL DECISION** (§18 class, like ACLED and Registry Trust). The API
   answers HTTP 401 without it. A probe of `finances.worldbank.org` looking for a
   free equivalent returned HTTP 200 / 27KB that turned out to be a JavaScript
   app shell, not JSON: **a 200 with bytes is not a 200 with data.** The gap is
   surfaced by `missing_credentials()` with the exact env var and fails safe.

---

## 5. The rule this session keeps proving

Every defect here was a **producer and a consumer disagreeing about the same
question**, not a missing capability:

- the list said *entity*, the delete said *run* (R-F3532)
- the assessment graded four facts, the proxy carried three (R-F3531)
- the badge said *official*, the evidence line said *single-source*
- the constitution said *never confirmed*, the correlator said *corroborated*
- the KPI counted one window, the list rendered another

The fix is always the same shape: **one derivation point, and a test that reads
both ends.**

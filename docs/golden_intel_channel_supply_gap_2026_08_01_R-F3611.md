# R-F3611 — the Golden Intel allowlist declares four signal types nothing can produce

**Status: PARTIALLY CLOSED — one of the four now has a producer, three do not. The gap
stays OPEN, deliberately.**

| type | producer | status |
|---|---|---|
| `contract_award` | `_tender_adapter` via TED `can-standard` | ✅ **CLOSED by R-F3621** |
| `conflict_escalation` | none (news_monitor → `classifier_template`) | ❌ open |
| `competitor_activity` | none (news_monitor → `classifier_template`) | ❌ open |
| `budget_movement` | none — never emitted at all | ❌ open |

Live gap id `d5a03b78-1033-4670-83c2-c5279c6ec77e` (`missing_capability`, source
`claude_review_r_f3611`), verified present in the brain's gap ledger 2026-08-01.
**It is NOT being marked resolved.** Three of the four types still cannot publish, and
resolving on a partial fix is the false-clean this codebase exists to prevent — the
same shape as R-F3027 ("fixed the instance, missed the class").

**R-F3621 — what closed `contract_award`, and why it was never a missing-data problem.**
`_crawl_ted` had always ASKED TED for `notice-type` and never read it, so a contract
award notice (`can-standard` — who WON) arrived as an indistinguishable
`active_tender`. R-F3536 then banned `active_tender` while explicitly wanting to keep
awards, so it banned the very thing it meant to preserve. Measured on the live API:
of 50 defence notices, 27 were open tenders and **21 were awards**, with 340 awards in
the 14-day window and a named winner on every one sampled. The data was never missing;
it was mislabelled. Awards now emit `contract_award` with the winner as target and an
item-specific why (who won, from whom, value, decision date), which earns
`source_adapter` provenance honestly and grades **A**.

**Found during:** the Telegram channel + intel-sharing deep review of 2026-08-01, which
also shipped R-F3609 (card double-send) and R-F3610 (public replies dropped).

---

## The gate

A signal reaches the public channel only if it satisfies BOTH:

1. `_GOLDEN_ALLOWED_TYPES` — `lib/telegram/channelServerHooks.mjs`, added by **R-F3536**
   (2026-07-31) in response to the operator: *"we are receiving only procurement intel on
   the telegram channel… i dont see that as an intel value."* It admits
   `sanctions_change`, `contract_award`, `budget_movement`, `programme_signal`,
   `competitor_activity`, `conflict_escalation`, and bans `active_tender` and
   `natural_hazard`.
2. `_hasItemSpecificAnalysis` — **R-F2899**: `why_action_provenance === 'source_adapter'`.
   This is what stops a classifier template ("Security conditions may affect delivery
   risk…" / "Assess country risk", identical on every article of a type) being published
   under the header *decision-grade*.

Both are correct on their own. The problem is their intersection.

## The measurement

100 live signals, read from `/api/aria/intel/signals/recent` inside `aria-web`,
2026-08-01:

| signal_type | source_adapter | classifier_template | reaches the channel? |
|---|---:|---:|---|
| `active_tender` | 29 | 0 | ❌ banned by R-F3536 |
| `sanctions_change` | 11 | 7 | ✅ the 11 |
| `programme_signal` | 1 | 0 | ✅ the 1 |
| `contract_award` | 0 | 2 | ❌ **allowed but unproducible** |
| `competitor_activity` | 0 | 4 | ❌ **allowed but unproducible** |
| `conflict_escalation` | 0 | 8 | ❌ **allowed but unproducible** |
| `budget_movement` | 0 | 0 | ❌ **never emitted at all** |
| `natural_hazard` | 0 | 37 | ❌ banned |
| `political_transition` | 0 | 1 | ❌ banned |

## Why the four are unproducible

`source_adapter` provenance is stamped in exactly one place —
`aria_service/intel/golden_intel_bridge.py:352`, and only when `_is_item_specific()`
passes (R-F2930 made the flag earned rather than granted). The bridge registers four
adapters:

- `_tender_adapter` → `active_tender` — **the type R-F3536 just banned**
- `_public_watchlist_adapter` → `sanctions_change`
- `_sanctions_diff_adapter` → `sanctions_change`
- `_inbox_adapter` → whatever is POSTed to the ingest lane

Everything else comes from `news_monitor.py`, which **hardcodes**
`"why_action_provenance": "classifier_template"` at line 1236.

So R-F3536 removed the single largest supply (29/41 publishable signals) and named four
replacement categories, every one of which is produced only by the module whose output
R-F2899 refuses. **The intent of R-F3536 is unreachable with the adapters that exist.**

## Effect on the channel

- Publishable pool: **~41/100 → ~12/100**, and 11 of the 12 are `sanctions_change`.
- At the time of measurement: **0 publishable Grade A, 10 Grade B.** The 07:00 slot
  correctly held (`held_for_corroboration`); the 17:00 slot falls back to labelled
  Grade B — mostly US BIS export-control rulemaking notices.
- The 45-day dedup window (`CHANNEL_DEDUP_WINDOW_DAYS`) compounds it: the single eligible
  Grade A on 2026-08-01 ("Implementation of EAR Export Controls on Silencers…") was
  correctly suppressed as already posted, leaving nothing.

None of this is dishonest output — the gate is doing what it says and holding rather
than publishing weak intel. It is a **supply** problem, and it will read to a subscriber
as a channel that went quiet or narrow.

## What closing it requires

Add source adapters that write **item-specific** `why_it_matters` / `recommended_action`
per finding, so `_is_item_specific()` earns them `source_adapter` provenance:

1. **`contract_award`** — who WON. R-F3536 explicitly kept this type because an award is
   market intelligence (unlike an open tender, which is only that a buyer exists). TED
   and the other procurement feeds already carry award notices; the adapter must emit the
   winner, the value and the buyer per finding, not a template.
2. **`conflict_escalation`** — the highest-volume allowed type (8/100) and currently 100%
   template.
3. **`competitor_activity`** and **`budget_movement`** — lower volume; `budget_movement`
   has no emitter at all, so it is a new source, not an adapter rewrite.

### Do not close it by

- **Relaxing R-F2899.** It is the only thing stopping a classifier label being published
  as ARIA's own analysis. That was the 2026-07-23 defect (a UN News roundup published as
  decision-grade Golden Intel).
- **Re-admitting `active_tender`.** Banning it was a deliberate operator decision on
  2026-07-31; tenders belong in the procurement section where they can be actioned.
- **Shortening the dedup window.** That republishes the same item, it does not create
  supply.

Each of those makes the gate green by measuring less.

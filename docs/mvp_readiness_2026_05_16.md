# ARIA MVP-readiness fire-test report — 2026-05-16

**Probed:** live fly.io `https://aria-intel.fly.dev` at 11:09–11:24 UTC (12:09–12:24 BST). Operator was away; this is an autonomous read-only assessment.

**Build state:** seenode `c577a35` (R-F552..F565 shipped earlier this session). fly.io health `degraded` (build_rev unknown because manual `flyctl deploy` ran without `--build-arg ARIA_BUILD_GIT_SHA=...`; not a runtime bug).

## TL;DR — three priority buckets

| Priority | Surface | Verdict |
|---|---|---|
| **🔴 P0 BLOCKER** | `/dd/orchestrate` IDENTITY layer | Every entity HARD_STOP'd by fuzzy false-matches. MVP **cannot ship** until fixed. |
| 🟠 P1 | SEC filings cross-match | Same fuzzy-too-loose pattern (E.ON SE for Rosoboronexport; CME GROUP for "Acme Widgets"). |
| 🟠 P1 | Ghost-score never fires | All 4 test runs scored 0/28 GREEN including the fake "Acme Widgets" — no upstream data fed in. |
| 🟡 P2 | API parameter inconsistency | `/chat` wants `message`; `/sanctions/rca` wants `name`; `/compliance/screen` wants `entity_name`. Three different names for the same thing across endpoints. |
| 🟡 P2 | `learning/freshness/sanctions` → "never refreshed" | Despite 47,403 facts and active sweeps, freshness tracker shows untracked domains. |
| 🟢 OK | `/chat` | High-quality, well-formatted answer on EU export controls. Confidence tags ([CONFIRMED]/[PENDING CORROBORATION]) firing. MVP-grade. |
| 🟢 OK | `/cost/*` | $34.40 month-to-date, 11.47% of $300 cap, projected $66.71/mo. Well in budget. |
| 🟢 OK | `/knowledge`, `/eval/coverage`, `/quota`, `/dd/reports` (list), `/learning/coverage` | All responsive, consistent shape. |
| 🟢 OK | `/compliance/screen` | Returns `CLEAR / PERMITTED` for Embraer (with adapter degraded but failing **safe** — clean, not blocked). |

## P0 defect: every DD returns HARD_STOP regardless of entity

Four DD runs through `/api/aria/dd/orchestrate` (standard mode):

| Entity | True risk | ARIA verdict | False matches cited |
|---|---|---|---|
| **Embraer S.A.** (Brazilian aerospace, clean) | LOW | 🔴 HARD_STOP | OFAC SDN: "MARANER HOLDINGS LIMITED" |
| **Aselsan A.S.** (Turkish defence, clean) | LOW | 🔴 HARD_STOP | OFAC SDN: "Guillermo NIEBLAS NAVA"; UN SC: "ALI SADDAM HUSSEIN AL-TIKRITI" |
| **Rosoboronexport** (Russian arms, sanctioned) | CRITICAL | 🔴 HARD_STOP (correct verdict — but for ROSOBORONEKSPORT OAO match) | Bonus false: UN SC "RUBEN PESTANO LAVILLA, JR" |
| **Acme Widgets Limited** (fake, doesn't exist) | n/a | 🔴 HARD_STOP | OFAC SDN: "TAMIN KALAYE SABZ ARAS COMPANY" |

**4/4 hard-stopped. 3/4 false positive. The system currently refuses every counterparty regardless of risk.**

### Root cause — confirmed via `/sanctions/rca?name=Embraer S.A.`

The matcher returned three hits:
1. **"Embraer SA"** (list `ir_uani_business_registry`) — score 1.0, str_sim 0.833, phonetic match. ← **real signal** (United Against Nuclear Iran business registry — Embraer historically traded with Iran via E-Jets/KC-390). But it's a **watchlist topic `export.risk`**, not a sanctions list.
2. **"ES SECURITIES"** (list `iso9362_bic` — bank BIC codes) — score 0.778, **str_sim 0.154**.
3. (Equivalent low-similarity hits across other identifier registries.)

Two layered defects:
- **Defect A**: the orchestrator's IDENTITY layer treats hits on `ir_uani_business_registry`, `iso9362_bic`, and other identifier/watchlist registries as **equivalent to OFAC SDN / UN SC consolidated** → escalates to `hard_stop`. These registries are not sanctions lists — they're risk flags and identifier directories. Topic-aware classification exists in `_sanctions_classify.py` but is bypassed when the orchestrator stamps "Subject on active sanctions list | hard_stop" upstream.
- **Defect B**: the OpenSanctions `score` field cannot be trusted as ARIA's decision threshold input. "Embraer S.A." vs "ES SECURITIES" = string_similarity 0.154 (15%) but OpenSanctions returns score 0.778. The 0.78 threshold passes a 15%-similar pair. Need a guard like `min(score, string_similarity) >= 0.78` OR pre-filter by token overlap before the score check.

### Recommended fix sequence (P0)
1. **In `aria_service/intel/sanctions.py` `fuzzy_screen()` / `screen_with_aliases()`**: gate `blocking_matches` on `list ∈ {ofac_sdn, eu_fsf, un_sc, uk_hmt, ofac_consolidated, ...} AND (string_similarity >= 0.78 OR registration_number_exact)`. Non-sanctions registry hits get `severity=info` regardless of score.
2. **Add length-ratio + first-letter guard**: reject candidate matches where `min(len(a), len(b)) / max(len(a), len(b)) < 0.5` OR `first_token_initial(a) != first_token_initial(b)`. This kills Embraer→MARANER (`E` vs `M`) and Aselsan→Nieblas (`A` vs `G`).
3. **Wire `_sanctions_classify.py` topic-demotion BEFORE the orchestrator's identity-layer hard_stop stamp**, not after. The R-F351 demotion currently runs too late to influence the IDENTITY finding severity.
4. **Add 5-10 entity regression tests**: well-known clean companies (Embraer, Airbus, Lockheed Martin, BAE Systems, Aselsan, RUAG) must screen `CLEAR`. Known sanctioned (Rosoboronexport, Wagner, IRGC) must return HARD_STOP via the **correct list**, not random fuzzy hit.

This is the candidate for R-F566 (next free per registry).

## P1 defect: SEC filings cross-match also too loose

Same fuzzy pattern in the SEC EDGAR ingestion side:
- Rosoboronexport → "SEC filings found: 45 recent (E.ON SE)" — totally unrelated German utility
- Acme Widgets → "SEC filings found: 15 recent (CME GROUP INC.)" — CME ≠ ACME

`info` severity so doesn't block, but pollutes the report and degrades operator trust. Same fix pattern: tighten the SEC filer-name match.

## P1 defect: ghost-score classifier never fires

All 4 test entities (including the **completely fictional "Acme Widgets Limited"**) scored `0/28 · GREEN` with classification `Standard DD sufficient — no ghost-company concern`. The 12 ghost indicators all returned 0 points with empty `reason`. The indicator inputs (`has_website`, `age_months`, `registered_address_type`, `financials`, `staff_count_linkedin`, …) appear in `data_gaps` — meaning the upstream signals **never reached the classifier**. Either the data-collection step is silently failing or there's a wiring gap between collection and scoring.

## P2 — API parameter inconsistency

Three endpoints, three different field names for "the thing to check":
| endpoint | required field |
|---|---|
| `/chat` (POST) | `message` |
| `/sanctions/rca` (GET) | `name` |
| `/compliance/screen` (POST) | `entity_name` |

The `/dd/orchestrate` docstring documents both `name` and `entity` — the code accepts either, but no other endpoint follows that pattern. For an MVP API that external partners will hit, standardise: `name` everywhere for entity, `message` only on chat, document both.

## P2 — `learning/freshness/sanctions` reports untracked

Despite the system having 47,403 knowledge facts, 12 active sanctions sources, and recent sweeps:
```
{"domain":"sanctions","tracked":false,"narrative":"sanctions: never refreshed (or not yet tracked)."}
```
The freshness tracker isn't being written to. Doesn't break MVP function but breaks the honesty story Phase A is building.

## What works well (don't break in the fix pass)

**Chat (`/chat`):** asked "name top 3 EU export-control regimes". Got back a structured answer with:
- BOTTOM LINE banner
- 3 numbered sections (Dual-Use Reg 2021/821, Common Position 2008/944/CFSP, sanctions under CFSP)
- Each tagged `[CONFIRMED]` or `[PENDING CORROBORATION]`
- Markdown-clean for WhatsApp + web

This is **MVP-grade chat output**. Whatever rewrite the parallel session is doing on R-F557 stream-guard should preserve this.

**Cost containment:** $34.40 / $300 monthly cap (11.47%) — burn rate $1.50/day. R-F-cap working. No autonomous mode active (operator hasn't set the autonomy flag per [[cost_cap_and_autonomy_gate]]).

**Eval coverage:** 453/500 (90.6%). Phase A gate #6 needs 47 more questions across multi_lang_ar (gap 4), counter_intel (gap 19), sanctions_divergence (gap 18), 4 minor lang gaps.

**Knowledge base:** 47,403 facts. Healthy. No prune (per [[aria_infinite_memory]]).

**Compliance screen (degraded-safe):** standalone `/compliance/screen` returns CLEAR when its adapter is down ("All connection attempts failed"). Fail-safe direction is correct — it does NOT default to blocking.

## R-F-number candidates this batch surfaced

(R-F566/F567/F568 already claimed by parallel session for unrelated work; mine start at R-F569 per the registry.)

| R-F (proposed, not yet reserved) | scope |
|---|---|
| R-F569 | **Sanctions-matcher P0 fix** (list-class filter + similarity guard + topic-demotion ordering + regression tests) |
| R-F570 | SEC filer-name match tighten (mirror R-F569 pattern) |
| R-F571 | Ghost-score input wiring audit + fix |
| R-F572 | API parameter standardisation (`name` everywhere; chat keeps `message`) |
| R-F573 | learning/freshness tracker wire-through |

All operational, all aligned with Phase A honesty. R-F569 is the only ship-blocker.

## Verification gate (R-F534 Premise Verifier) signal

Worth noting: the verification gate's `verdict: CRITICAL_VERIFIED` and `recommendation: ✅ Both independent providers agree on risk + sanctions + confidence` is **rubber-stamping the false positive**. Both providers see the same upstream false match. The Premise Verifier should be asking "is the premise that *this specific entity* is on a sanctions list, falsifiable?" — and the answer is yes (Embraer isn't on any major sanctions list). R-F569 will fix the upstream; a further R-F (probably R-F574 or higher) may be needed to harden the Premise Verifier so it catches this class of error structurally.

---

*Produced autonomously while operator was away. Numbers are live, not synthetic — all 4 DD reports persisted; check `/api/aria/dd/reports?limit=5` to retrieve.*

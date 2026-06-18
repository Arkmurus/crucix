# ARIA Search Sovereignty — architecture decision + program

**Status:** binding decision record. **Owner:** operator + Claude/ARIA.
**Origin:** operator directive 2026-06-18 — *"ARIA is to be fully autonomous, have her own independency... not adding more dependencies to her"* + *"no Brave API... not an option."* See memory `aria_sovereignty_no_new_dependencies`.

## The problem this ends

ARIA's web search kept going dark / returning confidently-wrong "nothing found"
because the **general-web discovery** step depended on third-party engines
(Brave, SearXNG upstreams, DuckDuckGo) that rate-limit / CAPTCHA the Fly
datacenter IP. Each fix that added or leaned on another third-party deepened the
dependency instead of removing it.

## Decision

1. **No new third-party search dependency. Ever.** Burden of proof is on any
   proposal to add one; default is NO. Enforced by the regression guard
   `aria_service/tests/test_rf1660_search_sovereignty_guard.py` (fails the build
   if Brave/Bing/SerpAPI/Google-CSE is reintroduced into the search paths).
2. **ARIA's OWN stack is the primary path.** Third-party general-web search is
   demoted to a *last-resort discovery seed* whose output is **captured into the
   own index** so each entity is learned once and served sovereign forever
   (§15 pay-once-remember-forever).
3. **Depth, not vendors, is the lever.** The durable cure is a deeper own index
   over ARIA's authoritative beat — not a better aggregator.

## What ARIA already OWNS (verified 2026-06-18 — sovereign, zero third-party at query time)

| Layer | Code | State |
|---|---|---|
| Own web crawler | `aria_service/crawler/` (fetcher, runner, politeness, seed_list ~160 domains) | LIVE (6h loop from `main.py` lifespan), robots-respecting, identified UA |
| Own search index | `aria_service/search_index/` (SQLite + FTS5/BM25), queried via `search_engine/internal_search.py` | LIVE, fed by the crawler |
| Own memory index | `rag_store.py` (chromadb + **local all-MiniLM-L6-v2** embeddings) + `semantic_search.py` + `knowledge.py` + `intel_ledger.py` | LIVE, ~8.8K docs / 32K facts, fully local |
| Direct primary sources | `aria_service/intel/sources/` — OFAC, UN SC, FCDO, SEC EDGAR, CourtListener/Bailii, World Bank, academic (S2/OpenAlex/CrossRef), crt.sh | 10 LIVE, direct to authoritative endpoints, mostly keyless |

The **only** real remaining third-party need is *discovery of brand-new entities*
not yet in her index. Everything already seen is served sovereign.

## Honest limit

ARIA cannot replicate Google's crawl of the entire web (billions of pages). The
realistic sovereign target is a **deep vertical index of her beat** (defence /
procurement / sanctions / CPLP + her ~160 authoritative domains and their
outlinks + every entity she has investigated) plus the direct primary-source
connectors. For genuinely novel general-web entities a thin discovery seed
remains useful — minimized and captured per Decision #2.

## Program (R-numbered)

| R | Item | What | Risk | Status |
|---|---|---|---|---|
| **R-F1660** | Remove vestigial Brave path | Deleted the live Brave block in `researcher.py:web_search` (separate from the `web_search.py` R-F320 stub); added the sovereignty **regression guard** test. | low | ✅ done + tested |
| **R-F1662** | Frontier-crawl depth | Upgrade `crawler/runner.py` from homepage-only to **link-following** within ARIA's curated domains (same-domain, depth≤2, capped links/page + pages/cycle, dedup vs index, politeness already enforced). Env-gated (`ARIA_FRONTIER_CRAWL`) + tight caps; deploy gated-ON, observe one cycle, then widen. **This is the durable cure.** | medium | spec'd → ARIA (Claude verifies) |
| **R-F1661** | Sovereign-first sequencing + capture | Reorder research/search so memory → own FTS index → own on-demand crawl are consulted FIRST and authoritatively; third-party discovery is last-resort and its results are crawled+indexed (extend existing `on_demand.auto_register_domain` / `background_ensure`). Consolidate the two parallel search stacks (`web_search.py` and `researcher.web_search`) onto one sovereign-first entry to end drift. | medium | spec'd → ARIA (Claude verifies) |

### Acceptance criteria
- **R-F1662**: after one crawl cycle the own index holds materially more than
  ~160 homepages (depth pages present); no runaway crawl (caps honoured,
  politeness intact); a capability test drives `crawl_seed_homepages`/frontier
  and asserts indexed depth pages > seeds.
- **R-F1661**: a research query for an entity already in memory/own-index
  returns WITHOUT any third-party call (capability test asserts zero external
  search invocation on a memory hit); third-party output for a novel entity is
  verified to land in the own index on the next read.
- **Standing guard**: `test_rf1660_search_sovereignty_guard.py` stays green.

## Rule going forward
Any change to a search/research path must (a) keep the guard test green, (b) not
add a third-party dependency, (c) prefer/feed ARIA's own index. Map-then-change
(§8); capability test on the real path (§3c); verify live (§22).

# aria-intel Sources Subsystem — Smoke Test + Deep Review (2026-07-01)

Full smoke test + 3-agent deep review of the "add source → intel formulation" chain,
with live prod smoke tests. R-F2212/2213/2214 shipped LIVE @ bbb9ed9f (aria-intel v2218).

## Verdict: the feature is WIRED end-to-end and legit — with real bugs (now fixed)

A user-added source **does** flow into the intel engine and produce data:

```
POST /api/aria/user/sources  (aria.py:25249; user_id pinned from JWT — no IDOR)
  → agent_signup_vault (sqlite, agent_id="user:<uid>", status, site_type rss|website)
  → news_monitor._get_vault_feed_sources()  reads ALL vault entries, no agent filter
                                             (news_monitor.py:530-572)
  → poll_feeds()  HOURLY cron "0 * * * *"
       ├─ rss/atom → _parse_rss/_parse_atom
       └─ website  → _scrape_vault_website()  deep multi-page extract (:575)
  → _feed_to_brain()  (:429)
       ├─ intel_ledger.add_signal   → dashboard + signal_correlator
       └─ brain_hook.absorb (category=="vault_curated") → RAG  → chat + DD retrieval
```

Curated `NEWS_SOURCES` (80+) reach the dashboard/ledger but NOT RAG; only vault
(user/admin) sources are absorbed into RAG for chat grounding.

## Live smoke test results
- add / list / delete round-trip: **works** (verified live, all test entries cleaned up).
- Ownership/IDOR: **solid** — `user_id` pinned server-side (server.mjs:2540/2544/2548), delete owner-checked (aria.py:25296), site_id = sha1(user_id|url).
- 25-source cap + exact-URL dedup: enforced.
- **Found live:** internal-host SSRF accepted; transient add returned raw 500; uptime sweep had never run.

## Bugs found + FIXED (shipped @ bbb9ed9f)
| R# | Bug | Fix |
|---|---|---|
| **R-F2212** | `validate_url` accepted `http://aria-intel.internal:8000` (confirmed live) — SSRF into fly 6PN via the poll fetch. | Block `*.internal` + bare single-label hosts + DNS-resolve every IP vs private ranges (DNS-rebind). Verified live: now rejected. |
| **R-F2213** | `status="verified"` hardcoded with no probe (§22 fabricated label). | SSRF-safe probe on add → `verified` (2xx+body) / `pending` (else). Graceful storage-error (no raw 500). Response gains truthful `verified` bool. Verified live. |
| **R-F2214** | Dead vault feeds (404/timeout/empty) + empty website scrapes silently counted, never reaching the brain → rotted invisibly. | `wire_failure`→capability_gaps, scoped to `vault_curated` so the maintained firehose adds no noise (record_gap dedupes). |

Tests: 10 new capability tests (real function invocation) + 39 existing source/vault/security tests PASS. Full tree compiles clean.

## Routed to ARIA (§21e — coder gaps, not shipped this session)
1. **R-F2215 (biggest quality lever):** `rag_store.search` ignores `credibility_tier` — a user blog ranks == a Tier-1 .gov chunk. Tier is computed but dead at retrieval (nested metadata may be dropped at ingest; only tiers 1-3 reach RAG). Two-part fix, touches core retrieval → stage for review.
2. `source_uptime_monitor` auto-suspend **inert** — reliability hardcoded 0.5 vs `<0.3` threshold (never fires). Wire a per-ping EMA.
3. Uptime monitor covers only the defence seed, **not** vault/user sources — extend + add consecutive-fail suspend that news_monitor honours (stops the hourly rot).
4. **SSRF residual:** `news_monitor._fetch_feed` follows redirects; only the original URL is validated — a public→internal 302 is fetched unchecked. Validate each hop.
5. Only `vault_curated` reaches RAG — consider absorbing tier_1/tier_1b curated news for richer chat grounding (watch cost/noise).

## Bottom line
The bones are solid: the add-source pipeline is genuinely wired to the formulation
engine, ownership is safe, and the two security/honesty bugs (SSRF + fabricated
"verified") are fixed and proven live. The remaining upside is **quality ranking**
(credibility-aware retrieval) and **source lifecycle** (auto-suspend dead sources),
both routed to the coder loop.

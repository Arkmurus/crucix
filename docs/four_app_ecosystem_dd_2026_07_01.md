# 4-App Ecosystem DD — aria-wa · aria-app · aria-intel · aria-searxng (2026-07-01)

Full 4-step review of how the four living organisms interact + consolidated gap
analysis for delivering state-of-the-art data to users. Evidence-based (file:line),
produced by four parallel deep-dive agents + a cross-app wiring cross-check.

Live at review time: aria-intel `34677f93` (==origin/main), aria-wa 200, aria-app
200 (proxy healthy), aria-searxng public+json 200, SEARXNG_URL deployed on intel.

## The organisms

| Organism | Fly app | Tech | Role |
|---|---|---|---|
| aria-intel | `aria-intel:8000` | FastAPI, 4vCPU/8GB, **1 uvicorn worker** | The brain — only stateful/compute tier. LLM chain, RAG, DD, ~15 autonomous loops |
| aria-wa | `aria-wa:5070` | Node/Baileys | WhatsApp limb — inbound capture + outbound delivery |
| aria-app | `aria-app:3200` | Next.js 14 | New frontend — **currently an inert full-proxy to aria-web** (R-F2187 rollback) |
| aria-web | `aria-web:3117` | Node/Express `server.mjs` | The real platform today — 22 pages, auth, Stripe, brain proxy |
| aria-searxng | `aria-searxng:8080` | SearXNG | Sovereign meta-search backbone (§6 no-paid) |

**Mental-model correction:** "aria-app" is not a separate live organism yet — it
proxies 100% of non-`/preview` traffic to aria-web (`next.config.mjs:25-31`). The
live system is aria-wa + aria-web + aria-intel + aria-searxng.

## Interaction map (the nervous system)

- **aria-wa → aria-intel** (`aria-intel.internal:8000`, Bearer): `/api/aria/chat`
  (async_mode + `/chat/result/{job}`), `/read-document`, `/api/aria/outcome` (§25
  delivery), `/liveness/beat`, `/brain/signal`. Brain pushes finished jobs back to
  `aria-wa.internal:5070/api/wa-listener/callback`.
- **aria-web → aria-intel** (`ARIA_SERVICE_URL`, Bearer): `ariaProxy()` +
  catch-all `/api/aria/*` (`server.mjs:5228`). **aria-web → aria-wa**:
  `WA_LISTENER_URL=aria-wa.internal:5070`.
- **aria-app → aria-web**: transparent Next proxy. aria-app **never** hits the
  brain directly.
- **aria-intel → aria-searxng** (`SEARXNG_URL`, `search_searxng.py`): primary
  search; parallel fallback to DDG scrape + Google/Bing RSS + academic APIs.
- **Heartbeats inward**: wa/web `POST /liveness/beat` (3min); brain probes searxng
  every 15min (`main.py:2501`, searxng can't self-beat).

**What works well:** the WA request→answer→delivery-outcome loop is closed (§25
success AND failure reported); auth is uniform Bearer, fail-closed, operator-tier
scoped; search fails soft (parallel fan-out).

## Consolidated gap analysis (ranked)

### P0 — structural throughput/availability limits
1. **Single-worker brain event-loop starvation.** `WEB_CONCURRENCY=1`
   (`main.py:143`) + ~15 singleton loops on one loop; any un-offloaded sync/CPU
   call freezes every user on every channel. Multi-worker is code-complete but
   gated OFF (`ARIA_ENGINE_ELECTION`). **Operator decision + coordinated deploy.**
2. **state_store self-DOS** — one aiosqlite write conn + bounded queue; saturates
   under L3 autonomy → chat-read timeouts. Mitigated (R-F2157/2172/2185); residual
   at 100+ users. Needs Tier-2 separate read-conn.

### P1 — quality/coverage
3. ✅ **FIXED R-F2209** — Node search tier hardcoded DEAD public SearXNG instances
   (`lib/search/engine.mjs`, `lib/self/web_explorer.mjs`) → now sovereign
   `aria-searxng.internal:8080`.
4. ✅ **FIXED R-F2210** — WA 1:1 DMs were entirely ignored (`aria_wa_listener.mjs`
   group-only filter) → now handled (WA_DM_ENABLED default ON, implicit mention).
5. **send-doc-then-ask broken by R-F2061 gate** — a captionless PDF isn't cached,
   so "Aria, review it" finds nothing (`aria_wa_listener.mjs:2624`). *Partly eased
   by R-F2210 for DMs (media now proceeds); groups still affected.* → ARIA gap.
6. **Web search free-only & shallow** — SearXNG (6 engines, google/qwant flaky) +
   DDG + RSS + academic; Brave dead-stub. **Operator: premium search/news API?**
7. ✅ **PARTLY ADDRESSED R-F2211** — systemic IDOR via aria-web catch-all
   (`server.mjs:5228` forwarded client query verbatim) → central `user_id` pin for
   non-admins. (Body/query covered; remaining owner-scoped POST bodies on explicit
   routes already pinned.)
8. **RAG freshness narrow** — only vault-curated articles absorbed
   (`news_monitor.py:471`); no broad continuously-refreshed corpus. → ARIA gap.

### P2 — dark spots / drift / hardening
9. **aria-app has no Node→brain error wiring** (§21b) — hard requirement before
   any page is cut over from the proxy.
10. **aria-app `beforeFiles` regex shadows its own `/api` bridge routes**
    (`next.config.mjs:27`) — breaks cookie→Bearer SSE bridge on first migration.
11. **Re-ranker gated OFF** (`ARIA_RERANK_ENABLED=0`) — no semantic/date ranking.
12. **§21a dark spot** — `search_searxng.py:150-160` no `wire_failure` on
    JSON-decode error. → ARIA gap.
13. **searxng single-instance, `:latest`-pinned, no fly healthcheck.** → ARIA gap.
14. Smaller: WA voice-disabled silently dark; WA timeout outcome can over-report
    as `delivered`; stale `services/wa-listener/Dockerfile` crash trap; two open
    unauth brain endpoints (`/zoom/webhook`, `/coder/demo`).

## Shipped this session (@ 7a553063, live)
- **R-F2209** (aria-web v105) — Node search → sovereign aria-searxng.
- **R-F2210** (aria-wa v98) — WhatsApp 1:1 DM support.
- **R-F2211** (aria-web v105) — central IDOR guard on `/api/aria` catch-all.

## The two ceilings that define everything
Every organism is individually healthy. "State-of-the-art data" is bounded by:
- **Throughput** → single-worker brain (P0 #1/#2). Remedy code-complete, gated OFF.
- **Freshness/quality** → free-only search + narrow ingestion + no re-rank (P1 #6,
  #8; P2 #11). Node dead-instance bug now fixed; re-ranker + premium sources are
  the next levers (operator cost decision).

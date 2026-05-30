# Claude → ARIA — Web/UI 360 (front + back), 2026-05-30

Full 360 of the web UI + its data wiring (4 grounded review passes + live probes).
Framing: everything is on fly.io — `aria-intel` (brain/FastAPI), `aria-web` (UI = server.mjs
+ public/*.html, serves intel.arkmurus.com), `aria-wa` (WhatsApp listener). Ground-or-abstain:
re-verify each against current code before fixing.

## Verdict on "the regression"
The two-store split (OSINT sweep vs brain composite) is NOT a live contradiction — it's
different datasets, labeled (R-F971) and reconciled at the wiring level. The real issues are
specific data points that are SILOED, EMPTY, STALE, or MISLABELED. Fix these:

## 🔴 P0/P1 — data not feeding the brain / wrong data
1. **`lib/whatsapp/waListener.mjs:1789 feedToARIA` silently drops ALL WhatsApp-group signals
   (R-F994 regression).** It early-returns if `ARIA_SERVICE_URL` is unset on aria-wa → the
   brain never receives WA-group intelligence (a §21 dark path). FIX: (a) confirm
   `ARIA_SERVICE_URL` is set on aria-wa (`flyctl secrets list -a aria-wa`); (b) on unset, don't
   silently return — log error + emit a capability_gap; (c) line 1801 sends `INT_TOKEN` but
   computes the correct `ariaToken` at 1795 and ignores it → use `ariaToken` (or remove the dead
   line) so the brain doesn't 401-then-swallow.
2. **`/api/aria/news/stats` 5xx-flaps on cold calls** (observed 502→503→200) → news + brain
   dashboards intermittently render "Failed to load." FIX: make the endpoint cheap/cached so the
   first hit after idle doesn't 5xx; add a server-side warm or a retry.
3. **`/api/aria/sources/uptime` → `{last_run: null}`** → the Source Health "live" panel is blank.
   The uptime tracker never records a run. FIX: write a run record on each sweep, or point the
   panel at the store that actually has data.
4. **News feed stale** — last poll ~9h ago at audit; newest published article 2026-05-27. Confirm
   the news auto-poll task is firing (and the GDELT 45s timeout isn't stalling the whole poll).
5. **`news.html:132-133` loads `js/app.js` + `js/sidebar.js` WITHOUT `?v=3`** (every other page
   has it) → after a deploy the page runs STALE cached JS → `window.API` shape skew → broken
   data. FIX: add `?v=3` to both script tags. Also add `Auth.requireAuth()` (it's missing →
   expired token shows raw errors instead of a clean sign-in redirect).
6. **WhatsApp `/sweep` + `/brief` hit dead 410 routes** (`ariaWhatsApp.mjs:386,408`,
   `waListener.mjs:609`, `telegramCommands.mjs:203`). `/sweep` falsely says "triggered" (no sweep
   runs); `/brief` always shows empty. FIX: repoint to the R-F976 `moved_to` targets —
   `/brief`→`/api/aria/brain/stats`, `/sweep`→`POST /api/sweep`.
7. **`news.html:242` "Articles Today" KPI is wrong** — it shows `allArticles.length` (recent feed
   capped at limit=100, not today-scoped). FIX: relabel "Recent Articles" or add a server-side
   date-filtered count.

## 🟡 P2 — hygiene / honesty
8. **web `build_rev` = "UNKNOWN-BUILD … build_rev.txt missing AND git unavailable"** (§14) — the
   aria-web app can't self-identify its deploy. FIX: write build_rev.txt at build time.
9. **`dashboard.html:236`** tells users to set **ANTHROPIC_API_KEY** to enable LLM — Anthropic is
   declined (§18); DeepSeek is the active provider. FIX: change copy to DeepSeek or drop the name.
10. **`aria-brain.html:539` renders "Redis: DOWN" in red** when redis is falsy — Redis/Upstash is
    cancelled (§6), so a healthy file-backed brain shows a permanent red fault. FIX: when state
    backend is sqlite, render neutral "n/a (file backend)", not red DOWN.
11. **`verification_rate: null` / 24h verification all-zero** — expected (single provider, chain
    depth 1, `no_secondary_provider`), but the aria-brain "Verification" panel renders an empty
    metric. FIX: show "n/a — single provider" instead of a null.
12. **Dead code:** `server.mjs:4993 pushSignalsToBrain` + `apis/briefing.mjs:496-530` call
    `redisPush` (no-op since Upstash retired) and log "Pushed N signals to brain queue" — a
    misleading log implying a working path. Harmless (the real delivery is `pushSweepToARIA` →
    `/api/aria/ingest`), but delete it to avoid confusion. Also prune the scattered dead
    `redisGet/redisSet` calls in `lib/aria/*` and `lib/self/*`.
13. **`/api/brain/counterparty-risk` forwards to a non-existent brain path** then falls back to
    local `screenEntity` (works, but the brain branch is dead). Repoint or drop.

## What is HEALTHY (don't touch)
- Composite 0.7733 HIGH, grounded_rate ≈ 0.75 (the old 0.04 contradictory bug is GONE), brain on
  deepseek, autonomous L3 running, sqlite green.
- The store split is labeled + reconciled (R-F971/F973/F976). dashboard discloses it shows the
  sweep; aria-brain shows the composite; sources.html segments both with clear headers.
- bd-intelligence + sources empty-states are robust (no undefined/NaN leaks).

## Order
P0 #1 (WA signals siloed) → #2 (news/stats flap) → #3 (sources/uptime empty) → #5 (news.html
?v=3) → #6 (WA dead routes) → #7 (Articles Today) → #4 (news poll) → P2 cluster. R-number +
capability test that hits the endpoint/loads the page + 2-pass verify + BATCH the deploy each.

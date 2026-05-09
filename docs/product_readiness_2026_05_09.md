# ARIA — Product Readiness Assessment

**Author:** Claude (auto-generated, code+memory grounded)
**Date:** 2026-05-09
**Target release:** October 2026 (~5 months / ~22 weeks)
**Latest commit:** `79feefb` (R-F36, 2026-05-06)
**Brief reading time:** 10 minutes

---

## 0. Method & known unknowns

This assessment is grounded in three sources:

1. **Code** (`C:\code\crucix` @ `79feefb`) — what actually exists.
2. **Memory** — operator decisions, prior-session conclusions, R-Finding history.
3. **Public docs in repo** — `docs/chat_ui_scope_2026-10.md` and `docs/chat_ui_launch_decisions.md` (2026-04-24) which already pre-scoped much of the product surface; they are folded in rather than rewritten.

**What I could NOT pull live (operator must verify):**
- `/api/aria/health`, `/cost/monthly`, `/adversarial/stats`, `/verification/stats`, `/brain/stats` — all bearer-protected (HTTP 401 from `aria-intel.fly.dev`).
- fly.io / seenode 24h logs.
- Anthropic billing balance.

Where a number is stated below from memory, it is dated. **Numbers older than 7 days should be treated as directional, not authoritative.**

---

## 1. Composite score

**Weighted readiness vs Oct 2026 consumer-grade release: 58 / 100.**

Weighting reflects what *must* exist for the v1 product story (a paying user signing up at `aria.app`, running a research query, and getting a polished answer): product surface 40%, intelligence quality 30%, ops 20%, governance 10%.

| Block | Weight | Score | Contribution |
|---|---|---|---|
| Product surface | 40% | 47 | 18.8 |
| Intelligence quality | 30% | 78 | 23.4 |
| Operational stability | 20% | 65 | 13.0 |
| Self-awareness / governance | 10% | 82 | 8.2 |
| **Total** | **100%** | — | **63.4** |

(Rounded down to 58 in the headline because the missing-billing risk is not linearly captured — *no billing = no launch* regardless of intelligence quality. See §4.)

---

## 2. Per-area scoring

### A. Product surface (weight 40% — score 47/100)

| # | Area | Score | Status |
|---|---|---|---|
| 1 | Streaming | 75 | `/chat/stream` SSE end-to-end + Stop/Regenerate live (`aria.html:309-545`). **5 output guards still bypass stream** (officeholder/commitment/tool_claim/propaganda/ground_truth) — see `stream_bypass_pattern.md`. Architectural decision (log-only vs rewrite SSE) still pending. |
| 2 | Chat UI | 50 | Chat-first UI exists (`public/aria.html`, 632 lines), markdown rendering, file upload, think-mode toggle. **Sidebar element exists but conversation list isn't wired** (Gap A in `chat_ui_scope_2026-10.md` — `/api/aria/conversations` server-side, no frontend render). Mobile media query is one breakpoint at 640px; no drawer/hamburger pattern. |
| 3 | Accounts & billing | 25 | Auth: signup/signin/forgot-password/2FA/email-verify all live in `server.mjs:2978-3320`, admin-approval gate, anti-enumeration pattern. **Billing: zero.** No Stripe / Paddle / subscription tiers. `chat_ui_launch_decisions.md` recommends $20 Pro + $199 Pro-Intelligence; no code wired. |
| 4 | Public API | 20 | Internal `/api/aria/*` is JWT-protected. **No `/api/v1/*` public surface, no per-key issuance, no docs site, no SDK.** Per-user RPM + daily-cost cap exists (`intel/user_quota.py`) but unused at the route level. |
| 5 | Domain | 0 | `aria.app` decision still pending operator. Currently lives at `intel.sursec.co.uk` + `aria-intel.fly.dev` — both are subdomain/internal-tool-shaped. |

**Composite product-surface score = (75+50+25+20+0)/5 = 34**, rounded up to 47 to credit that auth/streaming/per-user-history exist as code (just not wired to UI).

### B. Intelligence quality (weight 30% — score 78/100)

| # | Area | Score | Status |
|---|---|---|---|
| 6 | Adversarial baseline | 70 | 90.9% on 2026-04-23 (memory). 11 attacks in `ATTACK_LIBRARY` (`adversarial_challenge.py:93`). Target ≥95% before launch. **Stale — needs fresh run before any further claims.** |
| 7 | Core mastery | 75 | 10 tags in `student.CORE_MASTERY_TAGS` (6 langs + sanctions/nato/strategic_geography/export_control), capability-only, region-agnostic per `aria_global_positioning.md`. Last memory note: RU/ZH at floor. |
| 8 | Verification gate | 80 | `learning/verification_gate.py` (750 lines), auto-firing on CRITICAL classification per `session_2026_04_17_18_marathon.md`. Skip rate not pulled live. |
| 9 | Hypothesis backlog | 75 | 114 OPEN at 2026-05-06 close, drain 6–8/cycle (R-F32 bumped 3→8). 100 was the floor on 2026-05-03, so trending sideways, not down. |
| 10 | Knowledge breadth | 90 | 16,313 facts, 13,785 ledger signals (2026-05-06). 4 new legal modules in last week (Turkish/Swiss/Portuguese/OHADA + Gulf, R-F25..R-F29). Knowledge modules cover all major regions per global-positioning doctrine. |

**Composite = 78.**

### C. Operational stability (weight 20% — score 65/100)

| # | Area | Score | Status |
|---|---|---|---|
| 11 | Uptime / error budget | 60 | 2 lifespan-related outages in last 30d (memory: `lifespan_smoke_test_required.md`). Single fly.io machine for chat (volume not shared, HA traded for data coherence — `fly.toml:23`). seenode auto-deploys on push. **No status page, no SLA.** |
| 12 | Cost discipline | 70 | `ARIA_MONTHLY_CAP_USD=$300` (raised from $100 on 2026-04-22), per-user RPM/daily-cost caps in code. Anthropic recurring billing exhaustion — DeepSeek fallback operational. **Top-up still pending.** Uncategorized cost bucket reduced via direct-tool dispatch wrapping (2026-04-27). |
| 13 | Memory durability | 85 | Disk-canonical knowledge + ledger in `/data` (fly volume), gzipped Redis snapshots (R-F1 + R-F36, 5.35× compression verified live), off-host email backup live. Forever-memory guarantee per `session_2026_04_21_forever_memory.md` (TTLs removed, eviction OFF). |
| 14 | R-Findings backlog | 50 | 34 R-F commits in 14 days (high reactive throughput). **R-F37 open** (sanctions prompt-fragment leak from 2 paths — chat + watchlist re-screener). Hypothesis `OPEN→None` on 0-results edge case noted, not fixed. |

**Composite = 66.**

### D. Self-awareness / governance (weight 10% — score 82/100)

| # | Area | Score | Status |
|---|---|---|---|
| 15 | Constitution + audit log | 90 | **23 clauses** in `aria_engine.py:68-110` (incident-anchored, with past-incident citations). Audit log HMAC-signed + hash-chained, production fingerprint `a39f3328d92bffe4` since 2026-04-14T11:29:05Z. |
| 16 | Autonomy gate | 75 | `ARIA_AUTONOMOUS_ENABLED=0` (default OFF, `autonomous/engine.py:61`). Still gated on 24h cost attribution. Constitution clause 20(b) explicitly bans saying "autonomy is active" while disabled. |
| 17 | Self-infra introspection guard | 80 | OpenClaw-style hallucination logged 2026-04-24 (`aria_self_introspection_hallucinations.md`) — `7d56e17` introduced a guard. Risk of fabrication on self-introspective questions remains for unguarded paths. |

**Composite = 82.**

---

## 3. Top 5 lifters (highest delta-per-effort)

If shipped, these move the composite the most relative to engineering cost.

1. **Wire conversation-history sidebar** (Gap A) — server already serves `/api/aria/conversations`, frontend only needs to render it. ~1 week. Lifts area #2 from 50 → 75.
2. **Stripe checkout + tier gating** (Gap E) — unblocks the entire revenue path. Per-user RPM/daily-cost caps already exist; Stripe is the missing wiring. ~1.5 weeks. Lifts area #3 from 25 → 65 and removes the §4(a) hidden blocker.
3. **Mobile drawer + 44px touch targets + iOS keyboard** (Gap D) — 2-day polish job that turns the chat from "works on phone" into "ships on phone". Lifts area #2 by another 10.
4. **Fresh adversarial run + push to ≥95%** — last 90.9% on 2026-04-23 is too stale to cite. Run + fix the 1–2 remaining HIGH fails before anything is communicated externally. Lifts area #6 from 70 → 90 and removes a §4(c) credibility risk at launch.
5. **`/api/v1/chat` + key issuance + docs site** (Gap F) — lifts area #4 from 20 → 60 and unlocks the developer-tier story (which justifies the $199 Professional Intelligence price point per `chat_ui_launch_decisions.md`).

Combined, these move the composite from 58 → ~78 in a sequenced 5-week window — i.e. they are exactly the work the launch-decisions doc already proposed.

---

## 4. Top 5 hidden release-blockers

These look fine in isolation but actually block launch if untouched.

a. **No billing = no launch.** Even at 100/100 intelligence quality, you cannot put a "Subscribe for $20/mo" button on `aria.app` without Stripe. This is the single biggest schedule risk: a 1.5-week build that will balloon if discovered late.
b. **No legal layer.** Privacy policy + ToS require legal review. `chat_ui_scope_2026-10.md` flagged as SMALL — it is small in lines-of-text but not in calendar time (legal review can take 2–4 weeks). Start now.
c. **No status page, no incident-response SLA.** Two lifespan outages in 30d with no public visibility. Paying customers expect a `status.aria.app`-style surface; none exists.
d. **`stream_bypass_pattern`.** 5 output guards (officeholder / commitment / tool_claim / propaganda / ground_truth) bypass `/chat/stream`. WhatsApp + web chat both go through stream. At consumer scale this means the strongest hallucination guards run on a *minority* of traffic. Architectural decision still pending.
e. **No multi-tenant resource isolation.** `chat_ui_scope_2026-10.md §4` flagged: chat path + autonomous engine + spider on the same fly machine. First 100 paying users could collide with a sweep cycle and notice slowdowns. Dedicated chat-path resource pool was called out as non-negotiable in the original vision doc; not done.

---

## 5. Honest unknowns

Items I could not measure from logs/code alone — operator must fill in:

- **Anthropic month-to-date burn vs $300 cap** — pulling `/api/aria/cost/monthly` requires bearer token. Trend over the last 14d is what tells us whether `ARIA_AUTONOMOUS_ENABLED=1` is safe.
- **Verification-gate skip-rate on production traffic** — code path exists but I have no live counter.
- **Lifespan failure rate post-`09e18d8` hotfix + `lifespan_smoke_test_required` discipline** — has the 2-incident pattern continued, or stopped?
- **R-F37 frequency** — sanctions prompt-fragment leak is logged but I don't know if it fires once a day or once a week.
- **Watchlist re-screen drift** — last memory says 6 entities, 5 changes/cycle. Is the entity count growing or static?
- **fly.io max-machines headroom** — single `min_machines_running=1`. What's the autoscale ceiling on shared-cpu-2x?
- **Calibration delta** — last reading was "underconfident by 14% (safe)" on 2026-04-17. 22 days old.

---

## 6. What I'd recommend operator does next

1. **Sign off on the 5 launch decisions** in `docs/chat_ui_launch_decisions.md` (price, free-tier, waitlist, domain, support) — only the domain (decision 4) blocks the build. The other four can defer to week 4.
2. **Authorize the 6-week build sequence** in `chat_ui_scope_2026-10.md §2` (sidebar → file UX → fork → Stripe → API → polish). It already maps to the lifters in §3 above.
3. **Fire R-F37** before the next BD drive-through phase — sanctions prompt-fragment leak from 2 paths is the most concrete data-quality risk on the table.
4. **Run a fresh adversarial suite this week** — without it, every external claim about ARIA's robustness is grounded in a 16-day-old number.
5. **Decide stream-bypass** — log-only telemetry on a guard that fired pre-stream, OR re-architect SSE to allow guard rewrites. Pick now, before paid users start sending stream traffic.

---

## 7. One-line verdict

**ARIA's intelligence engine is 78/100 release-ready; the product surface around it is 47/100, and that gap — billing + public API + domain + mobile polish + status page — is the entire story between today and an October launch.** The 6-week build sequence already scoped in `chat_ui_scope_2026-10.md` is the right plan; what's missing is sign-off on the launch decisions and a green light to switch from R-Finding bug-fix mode to product-build mode.
